"""Tests for the stdio MCP server.

The protocol surface is small enough to exercise directly, and the lease
handling is exercised against a real Broker so the heartbeat and ownership
rules are not mocked away.
"""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from device_warden.mcp import LeaseKeeper, Server, TOOLS, serve
from device_warden.server import make_handler
from device_warden.store import Store

TOKEN = 'mcp-test-token'


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.server = Server(LeaseKeeper())

    def test_initialize_echoes_a_supported_version(self):
        response = self.server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                       'params': {'protocolVersion': '2025-03-26'}})
        self.assertEqual(response['result']['protocolVersion'], '2025-03-26')
        self.assertEqual(response['result']['serverInfo']['name'], 'device-warden')

    def test_initialize_falls_back_for_an_unknown_version(self):
        response = self.server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                       'params': {'protocolVersion': '1999-01-01'}})
        self.assertEqual(response['result']['protocolVersion'], '2025-06-18')

    def test_notifications_get_no_reply(self):
        self.assertIsNone(self.server.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))

    def test_tools_list_advertises_every_tool(self):
        response = self.server.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        names = [tool['name'] for tool in response['result']['tools']]
        self.assertEqual(names, ['list', 'claim', 'release', 'adb', 'status'])
        for tool in TOOLS:
            self.assertIn('inputSchema', tool)
            self.assertTrue(tool['description'])

    def test_unknown_method_is_an_error_not_a_crash(self):
        response = self.server.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'nope'})
        self.assertEqual(response['error']['code'], -32601)

    def test_unknown_tool_is_an_error_result(self):
        response = self.server.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                                       'params': {'name': 'nope', 'arguments': {}}})
        self.assertTrue(response['result']['isError'])

    def test_serve_skips_malformed_lines(self):
        stdin = io.StringIO('not json\n{"jsonrpc":"2.0","id":9,"method":"ping"}\n')
        stdout = io.StringIO()
        with patch('sys.stderr', io.StringIO()):
            serve(self.server, stdin=stdin, stdout=stdout)
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]['id'], 9)


class LeaseTests(unittest.TestCase):
    """Exercise claim/release/adb against a real Broker over HTTP."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / 'broker.token').write_text(TOKEN, encoding='utf-8')
        self.store = Store(self.home / 'warden.db')
        self.store.discover('List of devices attached\nexample device model:Test\n')
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store, TOKEN))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        env = patch.dict(os.environ, {'DEVICE_WARDEN_HOME': str(self.home),
                                      'DEVICE_WARDEN_PORT': str(self.httpd.server_port)})
        env.start()
        self.addCleanup(env.stop)
        self.keeper = LeaseKeeper()
        self.server = Server(self.keeper)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def call(self, name, arguments=None):
        response = self.server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                       'params': {'name': name, 'arguments': arguments or {}}})
        return response['result']

    def payload(self, result):
        return json.loads(result['content'][0]['text'])

    def test_claim_then_release_round_trip(self):
        claimed = self.payload(self.call('claim', {'title': 'test'}))
        self.assertEqual(self.store.list()[0]['kind'], 'ai')
        self.assertIn(claimed['device_id'], self.keeper.snapshot())
        released = self.payload(self.call('release', {'device_id': claimed['device_id']}))
        self.assertEqual(released['state'], 'released')
        self.assertEqual(self.keeper.snapshot(), {})

    def test_lease_id_never_reaches_the_agent(self):
        claimed = self.payload(self.call('claim'))
        self.assertNotIn('lease_id', claimed)
        listed = self.call('list')
        self.assertNotIn('lease_id', listed['content'][0]['text'])

    def test_release_of_an_unheld_device_is_refused(self):
        result = self.call('release', {'device_id': 'not-mine'})
        self.assertTrue(result['isError'])
        self.assertIn('このセッションのリースではありません', result['content'][0]['text'])

    def test_adb_requires_a_lease_held_here(self):
        result = self.call('adb', {'device_id': 'not-mine', 'args': ['shell', 'id']})
        self.assertTrue(result['isError'])

    def test_adb_runs_against_the_claimed_device(self):
        claimed = self.payload(self.call('claim'))
        with patch('device_warden.store.run_adb') as run_adb:
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': 'ok', 'stderr': ''})()
            result = self.payload(self.call('adb', {'device_id': claimed['device_id'],
                                                    'args': ['shell', 'getprop']}))
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(run_adb.call_args.args[0][:2], ['adb', '-s'])

    def test_second_claim_of_the_same_device_is_refused(self):
        claimed = self.payload(self.call('claim'))
        result = self.call('claim', {'device_id': claimed['device_id']})
        self.assertTrue(result['isError'])

    def test_status_reports_held_devices(self):
        claimed = self.payload(self.call('claim'))
        status = self.payload(self.call('status'))
        self.assertEqual([row['device_id'] for row in status], [claimed['device_id']])

    def test_heartbeat_loop_keeps_the_lease_active(self):
        """A lease must survive longer than the 120s timeout without agent help."""
        claimed = self.payload(self.call('claim'))
        keeper = LeaseKeeper(interval=0.05)
        keeper.add(claimed['device_id'], self.keeper.get(claimed['device_id'])['lease_id'],
                   claimed['serial'], None)
        keeper.start()
        try:
            import time
            time.sleep(0.3)
        finally:
            keeper.stop()
        entry = keeper.get(claimed['device_id'])
        self.assertGreater(entry['last_beat'], entry['claimed_at'])
        self.assertEqual(self.store.list()[0]['lease_state'], 'active')


if __name__ == '__main__':
    unittest.main()
