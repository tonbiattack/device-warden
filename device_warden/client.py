import json
import os
import urllib.error
import urllib.request
from .server import token_file
from .store import WardenError

# The Broker listens on a fixed port so every client finds it without config.
# Tests point this at an ephemeral port instead of starting a second Broker.
DEFAULT_PORT = 47631

# Most actions answer immediately. These run a download or an install on the
# device, so a short timeout would report a slow success as a connection
# failure and invite the caller to retry work that is still running.
SLOW_ACTIONS = {
    'obtainium-install': 900,
    'apps-install': 900,
    'apps-scan': 300,
    'catalog-signatures': 300,
    'catalog-register': 300,
    'adb': 120,
}


def broker_port():
    return int(os.environ.get('DEVICE_WARDEN_PORT') or DEFAULT_PORT)


def timeout_for(action):
    return SLOW_ACTIONS.get(action, 15)


def call(action, **args):
    try:
        token = token_file().read_text(encoding='utf-8').strip()
        request = urllib.request.Request(f'http://127.0.0.1:{broker_port()}/' + action,
            data=json.dumps(args).encode(), headers={'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/json'}, method='POST')
        # Bypass configured proxies for the local authenticated endpoint.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout_for(action)) as response:
            return json.load(response)['result']
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc)['error']
        except (ValueError, KeyError):
            message = 'Brokerへの操作に失敗しました。'
        raise WardenError(message) from exc
    except (OSError, ValueError, KeyError) as exc:
        raise WardenError('Brokerに接続できません。device-warden-brokerを起動してください。') from exc
