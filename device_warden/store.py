import json
import os
import secrets
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path

from .apps import package_details, user_packages, version_status

# The Broker runs without a console, but adb.exe is a console program and would
# otherwise flash a new window every time it is invoked.
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


def run_adb(args, timeout):
    result = subprocess.run(args, capture_output=True, timeout=timeout, check=False,
                            creationflags=HIDDEN)
    # Device props contain arbitrary bytes; ADB emits UTF-8, but the Windows
    # console code page (cp932) would raise on some values.
    def text(value):
        return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value
    return subprocess.CompletedProcess(args, result.returncode,
                                       text(result.stdout), text(result.stderr))


def redact_lease(lease_id):
    """Short, non-reversible reference to a lease for audit output.

    Lease IDs are release credentials. Audit history only needs to correlate
    events, so it keeps a prefixed fragment instead of the whole token.
    """
    return f"{lease_id[:8]}…" if lease_id else None


def scrub_audit_owner(owner):
    """Remove release credentials from an audit owner payload.

    Older rows stored the raw lease ID. Audit output is readable by any CLI
    caller, so those values must not leak a usable release credential.
    """
    try:
        data = json.loads(owner)
    except (TypeError, ValueError):
        return owner
    if not isinstance(data, dict):
        return owner
    if 'lease_id' in data:
        data['lease'] = redact_lease(data.pop('lease_id'))
    if 'lease' in data and data['lease'] and '…' not in str(data['lease']):
        data['lease'] = redact_lease(str(data['lease']))
    return json.dumps(data, ensure_ascii=False)


def install_hint(output):
    """Translate common `adb install` failures into an actionable next step.

    MIUI/HyperOS often reports USER_RESTRICTED even when the usual settings are
    correct; pushing the APK lets the owner accept the install on the device
    instead of the tool trying to bypass the vendor's approval dialog.
    """
    text = output or ''
    if 'INSTALL_FAILED_USER_RESTRICTED' in text:
        return ('端末側でインストールが承認されませんでした。APKを /sdcard/Download へ push し、'
                '端末のファイルアプリから開いてインストールしてください。')
    if 'INSTALL_FAILED_UPDATE_INCOMPATIBLE' in text or 'signatures do not match' in text:
        return '署名が一致しないため更新できません。既存アプリと同じ鍵で署名したAPKを使用してください。'
    if 'INSTALL_FAILED_VERSION_DOWNGRADE' in text:
        return 'ダウングレードは拒否されました。versionCodeを上げたAPKを使用してください。'
    if 'INSTALL_FAILED_INSUFFICIENT_STORAGE' in text:
        return '端末の空き容量が不足しています。'
    if 'device offline' in text or 'device not found' in text:
        return '端末が接続されていません。接続を確認して再取得してください。'
    return None


def display_name(device):
    """Prefer the owner's alias, then the marketing name, then brand + model.

    Marketing names ("Redmi Pad SE 8.7") read far better than bare model codes
    ("24075RP89G"), and the branded fallback keeps the vendor visible when the
    device does not expose one.
    """
    if device.get('alias'):
        return device['alias']
    if device.get('market_name'):
        return device['market_name']
    brand, model = device.get('brand'), device.get('model')
    if brand and model and not model.upper().startswith(brand.upper()):
        return f'{brand} {model}'
    return model or device.get('serial') or '不明な端末'


class WardenError(Exception):
    pass


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
              id TEXT PRIMARY KEY, serial TEXT UNIQUE NOT NULL, alias TEXT,
              model TEXT, transport TEXT, connection TEXT NOT NULL,
              ai_allowed INTEGER NOT NULL DEFAULT 1, hidden INTEGER NOT NULL DEFAULT 0,
              last_seen REAL);
            CREATE TABLE IF NOT EXISTS leases (
              id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id),
              kind TEXT NOT NULL, tool TEXT, session_id TEXT, project TEXT,
              title TEXT, workdir TEXT, pid INTEGER, acquired_at REAL NOT NULL,
              heartbeat_at REAL NOT NULL, state TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_lease_per_device ON leases(device_id)
              WHERE state IN ('active', 'recovery_required');
            CREATE TABLE IF NOT EXISTS audit (at REAL NOT NULL, action TEXT NOT NULL,
              device_id TEXT NOT NULL, owner TEXT);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tracked_apps (
              package TEXT PRIMARY KEY, label TEXT, added_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS app_snapshots (
              device_id TEXT NOT NULL REFERENCES devices(id), package TEXT NOT NULL,
              version_name TEXT, version_code INTEGER, first_install_time TEXT,
              last_update_time TEXT, scanned_at REAL NOT NULL, error TEXT,
              PRIMARY KEY (device_id, package));
            """)
            # Older databases predate the friendly-name columns.
            columns = {row['name'] for row in db.execute('PRAGMA table_info(devices)')}
            for column in ('brand', 'market_name'):
                if column not in columns:
                    db.execute(f'ALTER TABLE devices ADD COLUMN {column} TEXT')
            lease_columns = {row['name'] for row in db.execute('PRAGMA table_info(leases)')}
            for column, kind in (('recovery_reason', 'TEXT'), ('recovery_at', 'REAL')):
                if column not in lease_columns:
                    db.execute(f'ALTER TABLE leases ADD COLUMN {column} {kind}')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def discover(self, output=None):
        if output is None:
            try:
                result = run_adb(['adb', 'devices', '-l'], timeout=10)
                if result.returncode != 0:
                    raise subprocess.CalledProcessError(result.returncode, 'adb devices')
                output = result.stdout
            except (OSError, subprocess.SubprocessError) as exc:
                self.adb_unavailable()
                raise WardenError('ADBの実行に失敗しました。ADBの導入と接続を確認してください。') from exc
        lines = output.splitlines()
        if not any(line.strip() == 'List of devices attached' for line in lines):
            self.adb_unavailable()
            raise WardenError('ADBの応答を解釈できません。')
        found = []
        for line in lines:
            parts = line.split()
            if len(parts) < 2 or parts[1] not in ('device', 'offline', 'unauthorized'):
                continue
            serial, state = parts[:2]
            model = next((p[6:] for p in parts[2:] if p.startswith('model:')), None)
            product = next((p[8:] for p in parts[2:] if p.startswith('product:')), None)
            transport = 'emulator' if serial.startswith('emulator-') else ('wifi' if ':' in serial else 'usb')
            found.append((serial, {'device': 'connected', 'offline': 'disconnected', 'unauthorized': 'unauthorized'}[state], model, transport, product))
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE devices SET connection='disconnected'")
            default = db.execute("SELECT value FROM settings WHERE key='default_ai_allowed'").fetchone()
            for serial, state, model, transport, product in found:
                was_connected = db.execute(
                    "SELECT connection FROM devices WHERE serial=?", (serial,)).fetchone()
                db.execute('''INSERT INTO devices(id, serial, connection, model, transport, last_seen, ai_allowed)
                  VALUES(?,?,?,?,?,?,?) ON CONFLICT(serial) DO UPDATE SET
                  connection=excluded.connection, model=COALESCE(excluded.model, devices.model),
                  transport=excluded.transport, last_seen=excluded.last_seen''',
                  (str(uuid.uuid4()), serial, state, model, transport, now, int(default[0]) if default else 1))
                row = db.execute('SELECT id, brand, market_name FROM devices WHERE serial=?', (serial,)).fetchone()
                if state == 'connected' and (not row['brand'] or was_connected is None or was_connected['connection'] != 'connected'):
                    self._identify(db, row['id'], serial, model, product)
            self._mark_recovery(db, "device_id IN (SELECT id FROM devices WHERE connection != 'connected')",
                                (), 'connection_lost', now)
            db.commit()

    def _identify(self, db, device_id, serial, model, product):
        """Read the marketing name once per connection; it is not in `adb devices` output."""
        try:
            result = run_adb(['adb', '-s', serial, 'shell', 'getprop'], timeout=10)
        except (OSError, subprocess.SubprocessError):
            return
        if result.returncode != 0:
            return
        values = {}
        for line in result.stdout.splitlines():
            if line.startswith('[') and ']: [' in line and line.endswith(']'):
                key, value = line[1:-1].split(']: [', 1)
                if value:
                    values[key] = value
        brand = values.get('ro.product.brand') or values.get('ro.product.manufacturer')
        market = (values.get('ro.product.marketname') or values.get('ro.config.marketing_name')
                  or values.get('ro.product.vendor.marketname'))
        db.execute('UPDATE devices SET brand=COALESCE(?, brand), market_name=COALESCE(?, market_name) WHERE id=?',
                   (brand, market, device_id))

    def adb_unavailable(self):
        """Record that ADB itself could not be run.

        This is a broker-level fault, not evidence that any device disconnected,
        so it must not move live leases: only a per-device observation may do
        that. The connection axis still falls to `unknown` because a claim on an
        unverifiable device must not succeed, and the next successful discovery
        restores the real state.
        """
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE devices SET connection='unknown'")
            db.commit()

    def recover_restart(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE devices SET connection='unknown'")
            self._mark_recovery(db, '1=1', (), 'broker_restart', time.time())
            db.commit()

    @staticmethod
    def _mark_recovery(db, predicate, params, reason, now):
        # All predicates are internal constants; caller holds a write transaction.
        rows = db.execute("SELECT id, device_id, kind, tool, session_id FROM leases "
                          "WHERE state='active' AND " + predicate, params).fetchall()
        for row in rows:
            owner = json.dumps({'kind': row['kind'], 'tool': row['tool'],
                                'session_id': row['session_id'], 'reason': reason}, ensure_ascii=False)
            db.execute('INSERT INTO audit VALUES(?,?,?,?)', (now, 'recovery_required', row['device_id'], owner))
            db.execute("UPDATE leases SET state='recovery_required', recovery_reason=?, recovery_at=? WHERE id=?",
                       (reason, now, row['id']))

    def list(self, include_hidden=False, session_id=None):
        with self.connect() as db:
            # `last_used_at` comes from the whole lease history, not just the
            # active lease: the UI needs to show that a device has never been
            # used, which is the signal that the tool is not being adopted.
            rows = db.execute('''SELECT d.*, l.id AS lease_id, l.kind, l.tool, l.session_id,
                l.title, l.project, l.workdir, l.acquired_at, l.heartbeat_at, l.state AS lease_state,
                l.recovery_reason, l.recovery_at,
                (SELECT MAX(h.acquired_at) FROM leases h WHERE h.device_id=d.id) AS last_used_at
                FROM devices d LEFT JOIN leases l ON l.device_id=d.id
                AND l.state IN ('active','recovery_required') WHERE (? OR d.hidden=0)
                ORDER BY COALESCE(d.alias,d.market_name,d.brand || ' ' || d.model,d.model,d.serial)''', (include_hidden,)).fetchall()
            devices = [dict(row) for row in rows]
            for device in devices:
                # A session may release its own lease by device_id, but lease IDs of
                # AI leases are release credentials and stay hidden from `list`.
                device['owns_lease'] = bool(device['lease_id']) and bool(session_id) \
                    and device['session_id'] == session_id
                if device['kind'] == 'ai':
                    device['lease_id'] = None
                device['display_name'] = display_name(device)
            return devices

    def claim(self, kind, device_id=None, tool=None, session_id=None, title=None, project=None, workdir=None, pid=None, resume=False):
        if kind not in ('human', 'ai') or (kind == 'ai' and not tool):
            raise WardenError('所有者の種類とAIツール名を指定してください。')
        if tool and tool.lower() == 'opencode' and not session_id:
            raise WardenError('OpenCodeのセッションIDが必要です。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # A session resuming after an interruption may take its own
            # recovery_required lease back. Another session's lease is left alone.
            if resume and session_id and device_id:
                own = db.execute("""SELECT id FROM leases WHERE device_id=? AND session_id=?
                    AND state='recovery_required'""", (device_id, session_id)).fetchone()
                if own:
                    db.execute("UPDATE leases SET state='active', recovery_reason=NULL, "
                               "recovery_at=NULL, heartbeat_at=? WHERE id=?", (time.time(), own['id']))
                    db.execute('INSERT INTO audit VALUES(?,?,?,?)',
                               (time.time(), 'resumed', device_id,
                                json.dumps({'session_id': session_id}, ensure_ascii=False)))
                    db.commit()
                    device = db.execute('SELECT * FROM devices WHERE id=?', (device_id,)).fetchone()
                    return {'lease_id': own['id'], 'device_id': device_id,
                            'serial': device['serial'], 'resumed': True}
            query = """SELECT * FROM devices d WHERE d.hidden=0 AND (? IS NULL OR d.id=?)
              AND NOT EXISTS (SELECT 1 FROM leases l WHERE l.device_id=d.id AND l.state IN ('active','recovery_required'))"""
            if kind == 'ai':
                query += " AND d.ai_allowed=1 AND d.connection='connected'"
            device = db.execute(query + ' ORDER BY d.id LIMIT 1', (device_id, device_id)).fetchone()
            if not device:
                raise WardenError('条件に合う空き端末がありません。接続、AI許可、確保状態を確認してください。')
            lease_id, now = secrets.token_urlsafe(32), time.time()
            db.execute('''INSERT INTO leases(id, device_id, kind, tool, session_id, project, title, workdir,
                       pid, acquired_at, heartbeat_at, state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (lease_id, device['id'], kind, tool, session_id, project, title, workdir, pid, now, now, 'active'))
            owner = json.dumps({'kind': kind, 'tool': tool, 'session_id': session_id}, ensure_ascii=False)
            db.execute('INSERT INTO audit VALUES(?,?,?,?)', (now, 'claim', device['id'], owner))
            db.commit()
            return {'lease_id': lease_id, 'device_id': device['id'], 'serial': device['serial']}

    def heartbeat(self, lease_id):
        with self.connect() as db:
            cur = db.execute("UPDATE leases SET heartbeat_at=? WHERE id=? AND state='active'", (time.time(), lease_id))
            if not cur.rowcount:
                raise WardenError('有効なリースが見つかりません。解放済みか、要確認状態です。')

    def adb(self, lease_id, args):
        if not isinstance(args, list) or not args or any(not isinstance(x, str) for x in args):
            raise WardenError('ADBコマンドの引数が必要です。')
        # Do not permit a second target selector or server-wide commands.
        if args[0] not in ('shell', 'install', 'uninstall', 'push', 'pull', 'logcat', 'forward', 'reverse'):
            raise WardenError('このADBコマンドは仲介対象外です。')
        # Read the target in a short transaction, then release the write lock before
        # running adb. Holding BEGIN IMMEDIATE across a subprocess that can take up
        # to a minute made the monitor thread fail with "database is locked" and
        # stop updating connection state and heartbeat timeouts.
        with self.connect() as db:
            row = db.execute("""SELECT d.serial FROM leases l JOIN devices d ON d.id=l.device_id
              WHERE l.id=? AND l.state='active' AND d.connection='connected'""", (lease_id,)).fetchone()
        if not row:
            raise WardenError('接続中の有効なリースが必要です。')
        try:
            result = run_adb(['adb', '-s', row['serial'], *args], timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise WardenError('ADBコマンドを実行できませんでした。') from exc
        return {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}

    def release(self, lease_id=None, device_id=None, session_id=None):
        """Release a lease the caller holds.

        Either the lease ID or the device ID may be used. The device form is for
        the owning session: it must also pass its `session_id`, and the lease is
        only released when that session actually owns it. Without this, an
        interrupted session could see `recovery_required` but never learn its own
        release credential, and the device would stay blocked.
        """
        if not lease_id and not device_id:
            raise WardenError('lease_id または device_id が必要です。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if lease_id:
                row = db.execute("SELECT * FROM leases WHERE id=? AND state IN ('active','recovery_required')",
                                 (lease_id,)).fetchone()
            else:
                row = db.execute("SELECT * FROM leases WHERE device_id=? AND state IN ('active','recovery_required')",
                                 (device_id,)).fetchone()
                if row and (not session_id or row['session_id'] != session_id):
                    # Do not reveal whether another session holds the device.
                    raise WardenError('自分のリースではありません。')
            if not row:
                raise WardenError('リースが見つかりません。')
            db.execute("UPDATE leases SET state='released' WHERE id=?", (row['id'],))
            owner = json.dumps({'kind': row['kind'], 'tool': row['tool'], 'session_id': row['session_id']},
                               ensure_ascii=False)
            db.execute('INSERT INTO audit VALUES(?,?,?,?)', (time.time(), 'release', row['device_id'], owner))
            db.commit()
            return {'device_id': row['device_id'], 'state': 'released'}

    def recover(self, device_id, acknowledge=False, session_id=None):
        """Clear a `recovery_required` lease without interrupting a live one.

        This is the operator path from the CLI. It only accepts leases that are
        already in recovery, so it cannot take a healthy lease away from another
        session, and it never runs automatically.
        """
        if acknowledge is not True:
            raise WardenError('復旧には acknowledge が必要です。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM leases WHERE device_id=? AND state='recovery_required'",
                             (device_id,)).fetchone()
            if not row:
                active = db.execute("SELECT 1 FROM leases WHERE device_id=? AND state='active'",
                                    (device_id,)).fetchone()
                if active:
                    raise WardenError('この端末は現在も有効なリースです。強制解放を使用してください。')
                raise WardenError('要確認のリースが見つかりません。')
            if session_id and row['session_id'] != session_id:
                raise WardenError('自分のリースではありません。')
            db.execute("UPDATE leases SET state='released' WHERE id=?", (row['id'],))
            owner = json.dumps({'kind': row['kind'], 'tool': row['tool'], 'session_id': row['session_id'],
                                'reason': row['recovery_reason'], 'by': session_id or 'operator'},
                               ensure_ascii=False)
            db.execute('INSERT INTO audit VALUES(?,?,?,?)', (time.time(), 'recovered', device_id, owner))
            db.commit()
            return {'device_id': device_id, 'state': 'released'}

    def force_release(self, device_id, acknowledge=False):
        """Interrupt whoever holds a device. Separate from `release` on purpose.

        Interrupting another session is an operator action, not routine cleanup,
        so it is not reachable through the generic `release` action that agents
        and plugins call. Callers must pass `acknowledge` explicitly, and every
        use is audited.
        """
        if acknowledge is not True:
            raise WardenError('強制解放には acknowledge が必要です。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM leases WHERE device_id=? AND state IN ('active','recovery_required')",
                             (device_id,)).fetchone()
            if not row:
                raise WardenError('リースが見つかりません。')
            db.execute("UPDATE leases SET state='released' WHERE id=?", (row['id'],))
            owner = json.dumps({'kind': row['kind'], 'tool': row['tool'], 'session_id': row['session_id']},
                               ensure_ascii=False)
            db.execute('INSERT INTO audit VALUES(?,?,?,?)', (time.time(), 'force_release', row['device_id'], owner))
            db.commit()

    def reconfirm(self, device_id):
        """Re-check one device without ever freeing its lease automatically.

        The lease ID is a release credential, so it is not returned: any caller
        that could read it here could release another session's lease. Callers
        only need the connection and lease state.
        """
        with self.connect() as db:
            row = db.execute('SELECT serial FROM devices WHERE id=?', (device_id,)).fetchone()
        if not row:
            raise WardenError('端末が見つかりません。')
        self.discover()
        with self.connect() as db:
            row = db.execute('''SELECT d.connection, d.serial, l.id AS lease_id, l.state,
                  l.recovery_reason FROM devices d LEFT JOIN leases l ON l.device_id=d.id
                  AND l.state IN ('active','recovery_required') WHERE d.id=?''', (device_id,)).fetchone()
            if not row:
                raise WardenError('端末が見つかりません。')
            if row['lease_id']:
                db.execute('INSERT INTO audit VALUES(?,?,?,?)',
                           (time.time(), 'reconfirm', device_id,
                            json.dumps({'lease': redact_lease(row['lease_id']), 'state': row['state']},
                                       ensure_ascii=False)))
                db.commit()
        return {'connection': row['connection'], 'lease_state': row['state'],
                'recovery_reason': row['recovery_reason'],
                'has_lease': bool(row['lease_id'])}

    def launch_scrcpy(self, device_id):
        """Start scrcpy only for a connected device the caller already holds."""
        with self.connect() as db:
            row = db.execute('''SELECT d.serial, l.kind FROM devices d JOIN leases l ON l.device_id=d.id
              WHERE d.id=? AND l.state='active' AND d.connection='connected' ''', (device_id,)).fetchone()
        if not row:
            raise WardenError('接続中の端末を自分で確保してから起動してください。')
        if row['kind'] != 'human':
            raise WardenError('scrcpyは人間が確保した端末でのみ起動できます。')
        try:
            subprocess.Popen(['scrcpy', '-s', row['serial']], creationflags=HIDDEN)
        except OSError as exc:
            raise WardenError('scrcpyが見つかりません。PATHを確認してください。') from exc
        with self.connect() as db:
            db.execute('INSERT INTO audit VALUES(?,?,?,?)',
                       (time.time(), 'scrcpy', device_id, json.dumps({'kind': 'human'}, ensure_ascii=False)))
            db.commit()
        return {'started': True}

    def update_device(self, device_id, **changes):
        allowed = {'alias', 'ai_allowed', 'hidden'}
        if not changes or not set(changes) <= allowed:
            raise WardenError('無効な設定です。')
        with self.connect() as db:
            cur = db.execute('UPDATE devices SET ' + ','.join(k+'=?' for k in changes) + ' WHERE id=?', (*changes.values(), device_id))
            if not cur.rowcount:
                raise WardenError('端末が見つかりません。')

    def delete_device(self, device_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM leases WHERE device_id=? AND state IN ('active','recovery_required')", (device_id,)).fetchone():
                raise WardenError('確保中の端末は削除できません。')
            db.execute('DELETE FROM leases WHERE device_id=?', (device_id,))
            if not db.execute('DELETE FROM devices WHERE id=?', (device_id,)).rowcount:
                raise WardenError('端末が見つかりません。')
            db.commit()

    def recover_stale(self, seconds=120):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            now = time.time()
            self._mark_recovery(db, "kind='ai' AND heartbeat_at<?", (now-seconds,), 'heartbeat_timeout', now)
            db.commit()

    def audit(self, limit=100, device_id=None):
        with self.connect() as db:
            rows = db.execute('''SELECT a.at, a.action, COALESCE(d.alias, d.model, d.serial) AS device, a.owner
                FROM audit a LEFT JOIN devices d ON d.id=a.device_id
                WHERE (? IS NULL OR a.device_id=?) ORDER BY a.at DESC LIMIT ?''',
                (device_id, device_id, limit)).fetchall()
            return [dict(row, owner=scrub_audit_owner(row['owner'])) for row in rows]

    def default_policy(self, allowed):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES('default_ai_allowed',?)", ('1' if allowed else '0',))

    def usage_summary(self):
        """Report whether the tool is actually being used.

        A working Broker and tray prove the app runs, not that anyone uses it.
        The lease history is the only evidence of real use, so `doctor` reports
        it: a long gap or a pile of zero-second leases means the tool is being
        started but not adopted.
        """
        with self.connect() as db:
            row = db.execute('''SELECT COUNT(*) AS total,
                  SUM(CASE WHEN state='released' AND recovery_reason IS NULL THEN 1 ELSE 0 END) AS clean,
                  SUM(CASE WHEN recovery_reason IS NOT NULL THEN 1 ELSE 0 END) AS recovered,
                  MAX(acquired_at) AS last_claim
                FROM leases''').fetchone()
            # A lease that lived under a second is a claim immediately followed by
            # a release: a smoke test, not work that used the device.
            instant = db.execute('''SELECT COUNT(*) FROM leases
                WHERE heartbeat_at - acquired_at < 1''').fetchone()[0]
            tools = [dict(row) for row in db.execute(
                'SELECT tool, COUNT(*) AS count FROM leases GROUP BY tool ORDER BY count DESC')]
        return {'total': row['total'] or 0, 'clean': row['clean'] or 0,
                'recovered': row['recovered'] or 0, 'instant': instant,
                'last_claim': row['last_claim'], 'tools': tools}


    # --- Tracked applications -------------------------------------------------
    #
    # Only explicitly registered packages are collected. Scanning every installed
    # app would put unrelated personal app names into the database for no reason.

    def track_app(self, package, label=None):
        if not package or not package.replace('.', '').replace('_', '').isalnum():
            raise WardenError('パッケージ名が不正です。')
        with self.connect() as db:
            db.execute('INSERT INTO tracked_apps(package, label, added_at) VALUES(?,?,?) '
                       'ON CONFLICT(package) DO UPDATE SET label=COALESCE(excluded.label, tracked_apps.label)',
                       (package, label, time.time()))

    def untrack_app(self, package):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cur = db.execute('DELETE FROM tracked_apps WHERE package=?', (package,))
            if not cur.rowcount:
                raise WardenError('対象アプリが見つかりません。')
            db.execute('DELETE FROM app_snapshots WHERE package=?', (package,))
            db.commit()

    def tracked_apps(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                'SELECT package, label, added_at FROM tracked_apps ORDER BY package')]

    def scan_apps(self, device_id=None):
        """Collect versions of tracked apps per connected device.

        Each device is scanned independently: one offline or slow device records
        its own error and never blocks the rest. Disconnected devices keep their
        previous snapshot so the UI can show a last-known value with its time.
        """
        with self.connect() as db:
            packages = [row['package'] for row in db.execute('SELECT package FROM tracked_apps')]
            query = "SELECT id, serial, connection FROM devices WHERE hidden=0"
            params = ()
            if device_id is not None:
                query += " AND id=?"
                params = (device_id,)
            devices = [dict(row) for row in db.execute(query, params)]
        if not packages:
            return {'scanned': [], 'skipped': [d['id'] for d in devices]}
        scanned, skipped = [], []
        for device in devices:
            if device['connection'] != 'connected':
                skipped.append(device['id'])
                continue
            self._scan_device(device, packages)
            scanned.append(device['id'])
        return {'scanned': scanned, 'skipped': skipped}

    def _scan_device(self, device, packages):
        now = time.time()
        rows = []
        try:
            listed = run_adb(['adb', '-s', device['serial'], 'shell', 'pm', 'list', 'packages', '-3'],
                             timeout=30)
            if listed.returncode != 0:
                raise WardenError(listed.stderr.strip() or 'アプリ一覧の取得に失敗しました。')
            installed = set(user_packages(listed.stdout))
        except (OSError, subprocess.SubprocessError, WardenError) as exc:
            reason = str(exc) or 'ADBの実行に失敗しました。'
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for package in packages:
                    db.execute('INSERT INTO app_snapshots VALUES(?,?,?,?,?,?,?,?) '
                               'ON CONFLICT(device_id,package) DO UPDATE SET scanned_at=excluded.scanned_at, error=excluded.error',
                               (device['id'], package, None, None, None, None, now, reason))
                db.commit()
            return
        for package in packages:
            if package not in installed:
                rows.append((device['id'], package, None, None, None, None, now, None))
                continue
            try:
                detail = run_adb(['adb', '-s', device['serial'], 'shell', 'dumpsys', 'package', package],
                                 timeout=30)
                info = package_details(detail.stdout) if detail.returncode == 0 else {}
                error = None if detail.returncode == 0 else 'バージョン情報の取得に失敗しました。'
            except (OSError, subprocess.SubprocessError):
                info, error = {}, 'バージョン情報の取得に失敗しました。'
            rows.append((device['id'], package, info.get('version_name'), info.get('version_code'),
                         info.get('first_install_time'), info.get('last_update_time'), now, error))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in rows:
                db.execute('INSERT INTO app_snapshots VALUES(?,?,?,?,?,?,?,?) '
                           'ON CONFLICT(device_id,package) DO UPDATE SET '
                           'version_name=excluded.version_name, version_code=excluded.version_code, '
                           'first_install_time=excluded.first_install_time, last_update_time=excluded.last_update_time, '
                           'scanned_at=excluded.scanned_at, error=excluded.error', row)
            db.commit()

    def app_matrix(self):
        """Rows are tracked apps, columns are devices, cells are version snapshots."""
        with self.connect() as db:
            devices = [dict(row) for row in db.execute(
                '''SELECT id, serial, alias, model, brand, market_name, connection, last_seen
                   FROM devices WHERE hidden=0 ORDER BY COALESCE(alias, market_name, model, serial)''')]
            apps = [dict(row) for row in db.execute(
                'SELECT package, label FROM tracked_apps ORDER BY label, package')]
            snapshots = {(row['device_id'], row['package']): dict(row) for row in db.execute(
                'SELECT * FROM app_snapshots')}
        matrix = []
        for app in apps:
            cells = {}
            codes = []
            for device in devices:
                snapshot = snapshots.get((device['id'], app['package']))
                if snapshot:
                    cells[device['id']] = snapshot
                    if snapshot['version_code'] is not None:
                        codes.append(snapshot['version_code'])
                else:
                    cells[device['id']] = None
            baseline = max(codes) if codes else None
            for snapshot in cells.values():
                if snapshot:
                    snapshot['status'] = version_status(snapshot['version_code'], baseline)
            matrix.append({'package': app['package'], 'label': app['label'] or app['package'],
                           'baseline': baseline, 'cells': cells})
        return {'devices': [dict(device, display_name=display_name(device)) for device in devices],
                'apps': matrix}

    def install_apk(self, apk_path, device_ids=None, acknowledge=False):
        """Install a local APK with `adb install -r` on the requested devices.

        Installing writes to a device, so it is a separate operator action: the
        caller must pass `acknowledge`, each target is addressed with an explicit
        serial, and the result is reported per device.
        """
        if acknowledge is not True:
            raise WardenError('APKのインストールには acknowledge が必要です。')
        path = Path(apk_path)
        if not path.is_file():
            raise WardenError('APKファイルが見つかりません。')
        with self.connect() as db:
            query = "SELECT id, serial, connection FROM devices WHERE hidden=0"
            params = ()
            if device_ids:
                if any(not isinstance(item, str) for item in device_ids):
                    raise WardenError('端末IDが不正です。')
                query += ' AND id IN (%s)' % ','.join('?' * len(device_ids))
                params = tuple(device_ids)
            devices = [dict(row) for row in db.execute(query, params)]
        if device_ids and len(devices) != len(set(device_ids)):
            raise WardenError('端末が見つかりません。')
        results = []
        for device in devices:
            if device['connection'] != 'connected':
                results.append({'device_id': device['id'], 'ok': False,
                                'error': '端末が接続されていません。'})
                continue
            try:
                run = run_adb(['adb', '-s', device['serial'], 'install', '-r', str(path)], timeout=300)
                ok = run.returncode == 0 and 'Success' in run.stdout
                output = (run.stdout or run.stderr).strip()[:2000]
                result = {'device_id': device['id'], 'ok': ok, 'output': output}
                if not ok:
                    hint = install_hint(output)
                    if hint:
                        result['hint'] = hint
                results.append(result)
            except (OSError, subprocess.SubprocessError) as exc:
                results.append({'device_id': device['id'], 'ok': False, 'error': str(exc)})
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO audit VALUES(?,?,?,?)',
                       (time.time(), 'install_apk', results[0]['device_id'] if results else '',
                        json.dumps({'file': path.name, 'results': [
                            {'ok': result['ok']} for result in results]}, ensure_ascii=False)))
            db.commit()
        successful = [result['device_id'] for result in results if result['ok']]
        if successful:
            self.scan_apps()
        return {'results': results, 'scanned': bool(successful)}
