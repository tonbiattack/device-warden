import hmac
import json
import os
import secrets
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
                           'update': store.update_device, 'delete': store.delete_device,
                           'default-policy': store.default_policy}
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
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    store = Store(folder / 'warden.db')
    # Reserve the port before replacing the token: a second broker cannot invalidate the first.
    token = secrets.token_urlsafe(32)
    try:
        server = BrokerServer(('127.0.0.1', 47631), make_handler(store, token))
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
            try:
                store.recover_stale()
                store.discover()
            except WardenError:
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
