"""Tests for `doctor` and the console encoding helper.

`doctor` exists because the tool looked installed while nobody used it, so the
usage check is the part that matters: it must warn on a stale or absent lease
history rather than report a healthy Broker and stop there.
"""

import datetime
import io
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from device_warden import cli, console, doctor
from device_warden.server import make_handler
from device_warden.store import Store

TOKEN = 'doctor-token'


class ConsoleTests(unittest.TestCase):
    def test_configure_uses_the_console_code_page(self):
        stream = io.TextIOWrapper(io.BytesIO(), encoding='cp932')
        with patch.object(console, 'console_encoding', return_value='utf-8'):
            console.configure(stream)
        self.assertEqual(stream.encoding, 'utf-8')

    def test_configure_falls_back_to_utf8_without_a_console(self):
        stream = io.TextIOWrapper(io.BytesIO(), encoding='cp932')
        with patch.object(console, 'console_encoding', return_value=None):
            console.configure(stream)
        self.assertEqual(stream.encoding, 'utf-8')

    def test_configure_ignores_a_stream_it_cannot_reconfigure(self):
        # A test double or a plain StringIO has no reconfigure; that is not fatal.
        console.configure(io.StringIO())

    def test_cli_help_survives_a_console_that_cannot_encode_japanese(self):
        """An English Windows console (cp1252) crashed argparse while printing --help."""
        stream = io.TextIOWrapper(io.BytesIO(), encoding='cp1252')
        with patch.object(console, 'console_encoding', return_value='cp1252'), \
             patch('sys.stdout', stream), \
             patch.object(sys, 'argv', ['device-warden', '--help']):
            with self.assertRaises(SystemExit) as exit_code:
                cli.main()
            stream.flush()
        self.assertEqual(exit_code.exception.code, 0)
        # The Japanese help text must not raise; unencodable characters become '?'.
        written = stream.buffer.getvalue().decode('cp1252', errors='replace')
        self.assertIn('usage:', written)


class UsageSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'db.sqlite')
        self.store.discover('List of devices attached\nexample device model:Test\n')
        self.device = self.store.list()[0]['id']

    def test_empty_history_is_reported_as_zero(self):
        summary = self.store.usage_summary()
        self.assertEqual(summary['total'], 0)
        self.assertIsNone(summary['last_claim'])

    def test_instant_leases_are_counted_separately(self):
        """A claim released immediately is a smoke test, not real use."""
        lease = self.store.claim('ai', tool='Codex')
        self.store.release(lease_id=lease['lease_id'])
        summary = self.store.usage_summary()
        self.assertEqual(summary['total'], 1)
        self.assertEqual(summary['instant'], 1)

    def test_recovered_leases_are_counted(self):
        lease = self.store.claim('ai', tool='Codex')
        self.store.recover_stale(seconds=-1)
        summary = self.store.usage_summary()
        self.assertEqual(summary['recovered'], 1)
        self.assertEqual(summary['clean'], 0)

    def test_tools_are_grouped(self):
        first = self.store.claim('ai', tool='Codex')
        self.store.release(lease_id=first['lease_id'])
        second = self.store.claim('ai', tool='dsh')
        self.store.release(lease_id=second['lease_id'])
        tools = {row['tool']: row['count'] for row in self.store.usage_summary()['tools']}
        self.assertEqual(tools, {'Codex': 1, 'dsh': 1})


class DoctorTests(unittest.TestCase):
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

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def check(self, name):
        """Run `diagnose()` with the environment-dependent checks stubbed.

        `diagnose()` runs every check, and the real ones shell out to `adb` and
        read the user's startup folder. A slow adb server made the whole suite
        take 100s instead of 30s. The stubs are applied per call so a test can
        still exercise one real check on its own.
        """
        stubs = [patch.object(doctor, name, return_value=doctor._result(name, doctor.OK, 'stub'))
                 for name in ('check_adb', 'check_cli', 'check_autostart')]
        for stub in stubs:
            stub.start()
            self.addCleanup(stub.stop)
        checks, _ = doctor.diagnose()
        return next(item for item in checks if item['name'] == name)

    def test_broker_is_reported_healthy(self):
        self.assertEqual(self.check('Broker')['state'], doctor.OK)

    def test_unused_tool_is_flagged(self):
        """The whole point: a healthy Broker must not hide an unused tool."""
        usage = self.check('利用状況')
        self.assertEqual(usage['state'], doctor.WARN)
        self.assertIn('まだ一度も使われていません', usage['detail'])

    def test_recent_use_is_reported_as_ok(self):
        lease = self.store.claim('ai', tool='Codex')
        self.store.release(lease_id=lease['lease_id'])
        usage = self.check('利用状況')
        self.assertEqual(usage['state'], doctor.OK)

    def test_stale_use_is_flagged(self):
        lease = self.store.claim('ai', tool='Codex')
        self.store.release(lease_id=lease['lease_id'])
        old = (datetime.datetime.now() - datetime.timedelta(days=30)).timestamp()
        with self.store.connect() as db:
            db.execute('UPDATE leases SET acquired_at=?', (old,))
        usage = self.check('利用状況')
        self.assertEqual(usage['state'], doctor.WARN)
        self.assertIn('使われていません', usage['detail'])

    def test_missing_broker_is_a_failure_not_a_crash(self):
        with patch.dict(os.environ, {'DEVICE_WARDEN_PORT': '1'}):
            checks, failed = doctor.diagnose()
        self.assertTrue(failed)
        self.assertEqual(next(c for c in checks if c['name'] == 'Broker')['state'], doctor.FAIL)

    def test_missing_adb_is_reported(self):
        with patch('device_warden.doctor.shutil.which', return_value=None):
            self.assertEqual(doctor.check_adb()['state'], doctor.FAIL)

    def test_missing_cli_is_only_a_warning(self):
        with patch('device_warden.doctor.shutil.which', return_value=None):
            self.assertEqual(doctor.check_cli()['state'], doctor.WARN)

    def test_main_returns_nonzero_when_a_check_fails(self):
        with patch.dict(os.environ, {'DEVICE_WARDEN_PORT': '1'}), \
             patch('sys.stdout', io.StringIO()):
            self.assertEqual(doctor.main(), 1)


if __name__ == '__main__':
    unittest.main()
