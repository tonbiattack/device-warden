"""A stdio MCP server that exposes Device Warden leases to an AI agent.

The Broker already speaks HTTP, but an agent needs a tool it can call without
building a CLI invocation, and something has to send heartbeats while the agent
works. This process is that something: it stays alive for the whole session,
holds the leases it claimed, and refreshes them in the background.

Lease IDs are release credentials, so they never leave this process. The agent
addresses a device by its `device_id` and this server maps that back to the
lease it holds.

The MCP Python SDK pulls in uvicorn, starlette, and pydantic for transports this
server does not use, so the small JSON-RPC surface is implemented directly.
"""

import json
import sys
import threading
import time

from .client import call
from .store import WardenError

PROTOCOL_VERSION = '2025-06-18'
# Revisions this server can speak. The client offers one; echo it when known.
SUPPORTED_VERSIONS = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')
SERVER_INFO = {'name': 'device-warden', 'version': '0.1.0'}
HEARTBEAT_SECONDS = 30

TOOLS = [
    {
        'name': 'list',
        'description': (
            'List Android devices known to Device Warden with their ADB connection, '
            'lease owner, and AI policy. Call this before claiming to see what is free. '
            '"管理内では未確保" means no lease is held here; it does not prove the device '
            'is physically idle.'
        ),
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    },
    {
        'name': 'claim',
        'description': (
            'Claim a device for this session. Omit device_id to take any free, connected, '
            'AI-allowed device. The lease is refreshed automatically while this session '
            'runs, so long work does not expire. Release it when done.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'device_id': {'type': 'string', 'description': 'Device to claim; omit for any free device.'},
                'title': {'type': 'string', 'description': 'Short label shown in the Device Warden UI.'},
                'project': {'type': 'string', 'description': 'Project name shown in the UI.'},
                'workdir': {'type': 'string', 'description': 'Working directory shown in the UI.'},
            },
            'additionalProperties': False,
        },
    },
    {
        'name': 'release',
        'description': 'Release a device this session claimed. Only devices claimed here can be released.',
        'inputSchema': {
            'type': 'object',
            'properties': {'device_id': {'type': 'string'}},
            'required': ['device_id'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'adb',
        'description': (
            'Run one ADB command against a device this session claimed. The target serial is '
            'fixed by the Broker, so the command cannot address another device. Allowed '
            'subcommands: shell, install, uninstall, push, pull, logcat, forward, reverse.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'device_id': {'type': 'string'},
                'args': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'ADB arguments, for example ["shell", "getprop"].'},
            },
            'required': ['device_id', 'args'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'status',
        'description': 'Show the devices this session currently holds and when each was last refreshed.',
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    },
]


class LeaseKeeper:
    """Hold leases for this process and keep them alive.

    The Broker moves a lease to `recovery_required` after 120s without a
    heartbeat, and a device in that state is not offered to the next caller. An
    agent that claims and then works for several minutes would therefore block
    the device for everyone else, so the heartbeat has to run here rather than
    depend on the agent remembering to send it.
    """

    def __init__(self, interval=HEARTBEAT_SECONDS):
        self.interval = interval
        self.held = {}  # device_id -> {'lease_id', 'serial', 'title', 'claimed_at', 'last_beat'}
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()

    def _loop(self):
        while not self.stopped.wait(self.interval):
            with self.lock:
                leases = list(self.held.items())
            for device_id, entry in leases:
                try:
                    call('heartbeat', lease_id=entry['lease_id'])
                except WardenError:
                    # The lease was released elsewhere or the Broker is down.
                    # Keep the entry so `status` can report it; the next release
                    # attempt surfaces the real error.
                    continue
                with self.lock:
                    if device_id in self.held:
                        self.held[device_id]['last_beat'] = time.time()

    def add(self, device_id, lease_id, serial, title):
        with self.lock:
            self.held[device_id] = {'lease_id': lease_id, 'serial': serial, 'title': title,
                                    'claimed_at': time.time(), 'last_beat': time.time()}

    def get(self, device_id):
        with self.lock:
            return self.held.get(device_id)

    def drop(self, device_id):
        with self.lock:
            return self.held.pop(device_id, None)

    def snapshot(self):
        with self.lock:
            return dict(self.held)


def _text(payload):
    return {'content': [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False, indent=2)}]}


def _error(message):
    return {'content': [{'type': 'text', 'text': message}], 'isError': True}


class Server:
    def __init__(self, keeper=None):
        self.keeper = keeper or LeaseKeeper()

    def tool_list(self, arguments):
        rows = call('list')
        return _text([{
            'device_id': row['id'],
            'name': row['display_name'],
            'connection': row['connection'],
            'transport': row['transport'],
            'ai_allowed': bool(row['ai_allowed']),
            'lease_state': row['lease_state'],
            'owner': row['tool'] or row['kind'],
            'held_by_this_session': row['id'] in self.keeper.snapshot(),
        } for row in rows])

    def tool_claim(self, arguments):
        device_id = arguments.get('device_id')
        if device_id and self.keeper.get(device_id):
            return _error(f'{device_id} はこのセッションが既に保持しています。')
        payload = {'kind': 'ai', 'tool': 'dsh'}
        if device_id:
            payload['device_id'] = device_id
        for key in ('title', 'project', 'workdir'):
            if arguments.get(key):
                payload[key] = arguments[key]
        try:
            claimed = call('claim', **payload)
        except WardenError as exc:
            return _error(str(exc))
        self.keeper.add(claimed['device_id'], claimed['lease_id'], claimed['serial'],
                        arguments.get('title'))
        return _text({'device_id': claimed['device_id'], 'serial': claimed['serial'],
                      'note': 'リースは自動で更新されます。作業後は release してください。'})

    def tool_release(self, arguments):
        device_id = arguments.get('device_id')
        entry = self.keeper.get(device_id)
        if not entry:
            return _error(f'{device_id} はこのセッションのリースではありません。')
        try:
            call('release', lease_id=entry['lease_id'])
        except WardenError as exc:
            return _error(str(exc))
        self.keeper.drop(device_id)
        return _text({'device_id': device_id, 'state': 'released'})

    def tool_adb(self, arguments):
        device_id = arguments.get('device_id')
        entry = self.keeper.get(device_id)
        if not entry:
            return _error(f'{device_id} はこのセッションのリースではありません。先に claim してください。')
        args = arguments.get('args')
        if not isinstance(args, list) or not args:
            return _error('args にADBの引数を指定してください。')
        try:
            result = call('adb', lease_id=entry['lease_id'], args=args)
        except WardenError as exc:
            return _error(str(exc))
        return _text(result)

    def tool_status(self, arguments):
        held = self.keeper.snapshot()
        return _text([{
            'device_id': device_id,
            'serial': entry['serial'],
            'title': entry['title'],
            'claimed_at': entry['claimed_at'],
            'last_heartbeat': entry['last_beat'],
        } for device_id, entry in held.items()])

    def call_tool(self, name, arguments):
        handlers = {'list': self.tool_list, 'claim': self.tool_claim,
                    'release': self.tool_release, 'adb': self.tool_adb,
                    'status': self.tool_status}
        handler = handlers.get(name)
        if not handler:
            return _error(f'不明なツールです: {name}')
        try:
            return handler(arguments or {})
        except WardenError as exc:
            return _error(str(exc))

    def handle(self, request):
        """Answer one JSON-RPC request; return None for notifications."""
        method = request.get('method')
        request_id = request.get('id')
        params = request.get('params') or {}
        if request_id is None:
            # Notifications (notifications/initialized, cancelled) need no reply.
            return None
        if method == 'initialize':
            offered = params.get('protocolVersion')
            version = offered if offered in SUPPORTED_VERSIONS else PROTOCOL_VERSION
            return {'jsonrpc': '2.0', 'id': request_id, 'result': {
                'protocolVersion': version,
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': SERVER_INFO,
            }}
        if method == 'ping':
            return {'jsonrpc': '2.0', 'id': request_id, 'result': {}}
        if method == 'tools/list':
            return {'jsonrpc': '2.0', 'id': request_id, 'result': {'tools': TOOLS}}
        if method == 'tools/call':
            result = self.call_tool(params.get('name'), params.get('arguments'))
            return {'jsonrpc': '2.0', 'id': request_id, 'result': result}
        return {'jsonrpc': '2.0', 'id': request_id,
                'error': {'code': -32601, 'message': f'Method not found: {method}'}}


def serve(server, stdin=None, stdout=None):
    """Read newline-delimited JSON-RPC from stdin and write replies to stdout."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            # A malformed line has no id to answer; report and keep serving.
            print('Device Warden MCP: 不正なJSONを受信しました。', file=sys.stderr)
            continue
        response = server.handle(request)
        if response is None:
            continue
        stdout.write(json.dumps(response, ensure_ascii=False) + '\n')
        stdout.flush()


def main():
    keeper = LeaseKeeper()
    keeper.start()
    try:
        serve(Server(keeper))
    except KeyboardInterrupt:
        pass
    finally:
        keeper.stop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
