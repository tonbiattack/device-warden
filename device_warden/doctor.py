"""Self-diagnosis: is Device Warden running, and is anyone actually using it?

The tray and Broker can be healthy while the tool sits unused, so a status view
that only reports "Broker OK" hides the problem this project hit: it looked
installed and was not in use. `doctor` therefore checks the runtime as well as
the lease history and says plainly when the tool has not been used.
"""

import datetime
import os
import shutil
import subprocess
import sys

from . import autostart
from .client import broker_port, call
from .console import configure as _configure_console
from .server import data_dir, token_file
from .store import WardenError, run_adb

OK = 'OK'
WARN = '注意'
FAIL = '失敗'

# A tool that has not leased anything for this long is probably not adopted.
STALE_DAYS = 7


def _result(name, state, detail):
    return {'name': name, 'state': state, 'detail': detail}


def check_python():
    version = sys.version_info
    state = OK if version >= (3, 11) else FAIL
    return _result('Python', state, f'{version.major}.{version.minor}.{version.micro}（3.11以上が必要）')


def check_data_dir():
    folder = data_dir()
    if not folder.exists():
        return _result('データフォルダ', FAIL, f'{folder} がありません。Brokerを一度起動してください。')
    writable = os.access(folder, os.W_OK)
    return _result('データフォルダ', OK if writable else FAIL,
                   f'{folder}（{"書き込み可" if writable else "書き込み不可"}）')


def check_token():
    path = token_file()
    if not path.exists():
        return _result('Brokerトークン', FAIL, f'{path} がありません。Brokerが起動していません。')
    return _result('Brokerトークン', OK, f'{path}（{path.stat().st_size} バイト）')


def check_broker():
    port = broker_port()
    try:
        rows = call('list')
    except WardenError as exc:
        return _result('Broker', FAIL, f'127.0.0.1:{port} に接続できません: {exc}'), None
    connected = sum(1 for row in rows if row['connection'] == 'connected')
    return _result('Broker', OK, f'127.0.0.1:{port} で応答（端末 {len(rows)} 台、接続中 {connected} 台）'), rows


def check_adb():
    path = shutil.which('adb')
    if not path:
        return _result('adb', FAIL, 'PATHに adb がありません。Android SDK Platform Tools を導入してください。')
    try:
        result = run_adb(['adb', 'devices', '-l'], timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return _result('adb', FAIL, f'{path}（実行に失敗: {exc}）')
    if result.returncode != 0:
        return _result('adb', FAIL, f'{path}（adb devices が {result.returncode} で失敗）')
    lines = [line for line in result.stdout.splitlines() if line.strip() and not line.startswith('List of')]
    return _result('adb', OK, f'{path}（端末 {len(lines)} 台を検出）')


def check_cli():
    path = shutil.which('device-warden')
    if not path:
        return _result('CLI', WARN,
                       'device-warden がPATHにありません。`python -m device_warden.cli` で実行できます。')
    return _result('CLI', OK, path)


def check_autostart():
    try:
        registered = autostart.status()
    except OSError as exc:
        return _result('自動起動', WARN, f'確認できません: {exc}')
    if registered:
        return _result('自動起動', OK, str(autostart.shortcut_path()))
    return _result('自動起動', WARN, '未登録です。`device-warden-autostart install` かGUIから登録できます。')


def check_usage(rows):
    if rows is None:
        return _result('利用状況', WARN, 'Brokerに接続できないため確認できません。')
    try:
        summary = call('usage')
    except WardenError as exc:
        return _result('利用状況', WARN, f'集計できません: {exc}')
    total = summary['total']
    if not total:
        return _result('利用状況', WARN, 'リースの記録がありません。まだ一度も使われていません。')
    last = summary['last_claim']
    if last:
        when = datetime.datetime.fromtimestamp(last)
        age = datetime.datetime.now() - when
        detail = (f'累計 {total} 件（うち1秒未満 {summary["instant"]} 件）、'
                  f'最終利用 {when:%Y-%m-%d %H:%M}（{age.days}日前）')
        if age.days >= STALE_DAYS:
            return _result('利用状況', WARN, detail + f'。{STALE_DAYS}日以上使われていません。')
        return _result('利用状況', OK, detail)
    return _result('利用状況', WARN, f'累計 {total} 件')


def diagnose():
    """Run every check; return the results and whether any of them failed."""
    checks = [check_python(), check_data_dir(), check_token()]
    broker, rows = check_broker()
    checks.append(broker)
    checks.extend([check_adb(), check_cli(), check_autostart(), check_usage(rows)])
    return checks, any(check['state'] == FAIL for check in checks)


def main(argv=None):
    _configure_console()
    checks, failed = diagnose()
    for check in checks:
        print(f'[{check["state"]}] {check["name"]}: {check["detail"]}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
