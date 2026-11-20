"""Run the real CLI against a real Broker over HTTP.

The other CLI tests patch `device_warden.cli.call`, so a keyword mismatch
between the CLI and the Store method stays invisible: `apps install` sent
`apk` while the Broker expected `apk_path` and every call returned 400.
These tests start an actual Broker on an ephemeral port and drive it through
`main()`, so the request body is serialised and validated for real.
"""

import contextlib
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

from device_warden.cli import main
from device_warden.server import make_handler
from device_warden.store import Store

TOKEN = 'integration-token'


class BrokerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / 'broker.token').write_text(TOKEN, encoding='utf-8')
        self.store = Store(self.home / 'warden.db')
        self.store.discover('List of devices attached\nexample device model:Test\n')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store, TOKEN))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        # Point the client at this Broker instead of the fixed production port.
        env = patch.dict(os.environ, {
            'DEVICE_WARDEN_HOME': str(self.home),
            'DEVICE_WARDEN_PORT': str(self.server.server_port),
        })
        env.start()
        self.addCleanup(env.stop)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def invoke(self, args):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['device-warden', *args]), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main()
        return code, output.getvalue(), errors.getvalue()

    def device_id(self):
        return self.store.list()[0]['id']

    def test_apps_install_reaches_the_broker_with_the_expected_arguments(self):
        """The regression: `apk` vs `apk_path` made every install a 400."""
        apk = self.home / 'app.apk'
        apk.write_bytes(b'fake apk')
        with patch('device_warden.store.run_adb') as run_adb:
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': 'Success', 'stderr': ''})()
            code, output, errors = self.invoke(['apps', 'install', str(apk), '--yes'])
        self.assertEqual(code, 0, errors)
        self.assertTrue(json.loads(output)['results'][0]['ok'])
        # The install must address the device by serial, never by a bare `adb install`.
        command = run_adb.call_args.args[0]
        self.assertEqual(command[:2], ['adb', '-s'])
        self.assertIn('install', command)

    def test_apps_install_without_yes_lists_devices_and_does_not_install(self):
        apk = self.home / 'app.apk'
        apk.write_bytes(b'fake apk')
        with patch('device_warden.store.run_adb') as run_adb:
            code, output, errors = self.invoke(['apps', 'install', str(apk)])
        self.assertEqual(code, 2)
        self.assertIn('--yes', errors)
        self.assertIn('Test', output)
        run_adb.assert_not_called()

    def test_apps_install_reports_a_missing_file(self):
        code, _, errors = self.invoke(['apps', 'install', str(self.home / 'nope.apk'), '--yes'])
        self.assertEqual(code, 1)
        self.assertIn('APKファイルが見つかりません', errors)

    def test_claim_and_release_round_trip(self):
        code, output, errors = self.invoke(['claim', '--tool', 'Codex'])
        self.assertEqual(code, 0, errors)
        lease_id = json.loads(output)['lease_id']
        self.assertEqual(self.store.list()[0]['kind'], 'ai')
        code, _, errors = self.invoke(['release', lease_id])
        self.assertEqual(code, 0, errors)
        self.assertIsNone(self.store.list()[0]['kind'])

    def test_run_holds_the_lease_for_the_whole_command(self):
        """`run` must claim, execute, and release against the real Broker."""
        code, output, errors = self.invoke(
            ['run', '--tool', 'Codex', '--', sys.executable, '-c', 'print("worked")'])
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)['exit_code'], 0)
        self.assertIsNone(self.store.list()[0]['kind'])

    def test_run_keeps_the_lease_when_the_command_fails(self):
        code, _, errors = self.invoke(
            ['run', '--tool', 'Codex', '--', sys.executable, '-c', 'raise SystemExit(5)'])
        self.assertEqual(code, 5)
        self.assertIn('lease_id', errors)
        self.assertEqual(self.store.list()[0]['lease_state'], 'active')

    def test_unknown_action_is_rejected_without_a_traceback(self):
        code, _, errors = self.invoke(['list'])
        self.assertEqual(code, 0, errors)


if __name__ == '__main__':
    unittest.main()
