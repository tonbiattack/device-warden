import json
import urllib.error
import urllib.request
from .server import token_file
from .store import WardenError


def call(action, **args):
    try:
        token = token_file().read_text(encoding='utf-8').strip()
        request = urllib.request.Request('http://127.0.0.1:47631/' + action,
            data=json.dumps(args).encode(), headers={'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/json'}, method='POST')
        # Bypass configured proxies for the local authenticated endpoint.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=15) as response:
            return json.load(response)['result']
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc)['error']
        except (ValueError, KeyError):
            message = 'Brokerへの操作に失敗しました。'
        raise WardenError(message) from exc
    except (OSError, ValueError, KeyError) as exc:
        raise WardenError('Brokerに接続できません。device-warden-brokerを起動してください。') from exc
