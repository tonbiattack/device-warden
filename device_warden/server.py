import hmac
import json
import os
import secrets
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .store import Store, WardenError


def data_dir():
    override = os.environ.get('DEVICE_WARDEN_HOME')
    if override:
        return Path(override)
    # AppData may be redirected for packaged Python installs, so keep data in the
    # unredirected user profile where other runtimes (the OpenCode plugin) can find it.
    return Path(os.environ.get('USERPROFILE') or Path.home()) / '.device-warden'


def ensure_private_dir(folder):
    """Create the data folder and, when it is new, restrict it to the current user.

    Inherited permissions can be far broader than expected (a shared folder or a
    sandboxed TEMP directory), so a newly created store gets an explicit DACL.
    Existing folders are left untouched: rewriting their ACL could break a user's
    backups or deliberate sharing.
    """
    folder = Path(folder)
    created = not folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    if created and os.name == 'nt':
        try:
            user = os.environ.get('USERNAME', '')
            subprocess.run(['icacls', str(folder), '/inheritance:r',
                            '/grant:r', f'{user}:(OI)(CI)F', 'SYSTEM:(OI)(CI)F',
                            'Administrators:(OI)(CI)F'],
                           capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.SubprocessError):
            pass
    return folder


def token_file():
    return data_dir() / 'broker.token'


def make_handler(store, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Requests can contain local identifiers; never log them.

        def do_POST(self):
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                return self.reply(401, {'error': '認証に失敗しました。'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length > 65536 or length < 0:
                    return self.reply(413, {'error': 'リクエストが大きすぎます。'})
                args = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(args, dict):
                    raise ValueError()
                actions = {'list': store.list, 'refresh': store.discover, 'claim': store.claim,
                           'heartbeat': store.heartbeat, 'release': store.release, 'adb': store.adb,
                           'audit': store.audit,
                           'reconfirm': store.reconfirm, 'scrcpy': store.launch_scrcpy,
                           'recover': store.recover,
                           'apps-list': store.tracked_apps, 'apps-track': store.track_app,
                           'apps-untrack': store.untrack_app, 'apps-scan': store.scan_apps,
                           'apps-matrix': store.app_matrix, 'apps-install': store.install_apk,
                           'force-release': store.force_release,
                           'update': store.update_device, 'delete': store.delete_device,
                           'default-policy': store.default_policy,
                           'usage': store.usage_summary,
                           'catalog': store.catalog, 'catalog-add': store.catalog_add,
                           'catalog-remove': store.catalog_remove,
                           'catalog-links': store.catalog_links,
                           'catalog-register': store.catalog_register,
                           'catalog-signatures': store.catalog_signatures,
                           'catalog-import-link': store.catalog_import_link,
                           'obtainium-status': store.obtainium_status,
                           'obtainium-install': store.obtainium_install}
                action = self.path.strip('/')
                if action not in actions:
                    return self.reply(404, {'error': '不明な操作です。'})
                result = actions[action](**args)
                self.reply(200, {'result': result})
            except WardenError as exc:
                self.reply(409, {'error': str(exc)})
            except (ValueError, TypeError, json.JSONDecodeError):
                self.reply(400, {'error': '無効なリクエストです。'})
            except Exception:
                self.reply(500, {'error': '内部エラーが発生しました。'})

        def reply(self, status, body):
            raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    return Handler


class BrokerServer(ThreadingHTTPServer):
    # Windows honours SO_REUSEADDR by letting a second socket bind the same port,
    # so the old and new Broker would race for requests while using different tokens.
    allow_reuse_address = False


def main():
    folder = ensure_private_dir(data_dir())
    store = Store(folder / 'warden.db')
    # Reserve the port before replacing the token: a second broker cannot invalidate the first.
    token = secrets.token_urlsafe(32)
    port = int(os.environ.get('DEVICE_WARDEN_PORT') or 47631)
    try:
        server = BrokerServer(('127.0.0.1', port), make_handler(store, token))
    except OSError:
        print('Brokerは既に起動しています。二重起動はできません。', flush=True)
        return 1
    store.recover_restart()
    token_file().write_text(token, encoding='utf-8')
    try:
        os.chmod(token_file(), 0o600)
    except OSError:
        pass

    def monitor():
        while True:
            # A transient SQLite lock must not kill the monitor thread: without it,
            # connection state and heartbeat timeouts stop being updated for good.
            try:
                store.recover_stale()
                store.discover()
            except (WardenError, sqlite3.OperationalError):
                pass
            time.sleep(10)
    threading.Thread(target=monitor, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        token_file().unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
