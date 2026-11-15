import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from device_warden.cli import main
from device_warden.store import WardenError


class AppsCliTests(unittest.TestCase):
    def invoke(self, args, handler):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['device-warden', 'apps', *args]), \
             patch('device_warden.cli.call', side_effect=handler), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main()
        return code, output.getvalue(), errors.getvalue()

    def test_matrix_reads_over_the_broker(self):
        def call(action, **kwargs):
            self.assertEqual(action, 'apps-matrix')
            return {'devices': [], 'apps': []}
        code, output, _ = self.invoke(['matrix'], call)
        self.assertEqual(code, 0)
        self.assertIn('"apps"', output)

    def test_install_without_confirmation_only_lists_devices(self):
        actions = []
        def call(action, **kwargs):
            actions.append(action)
            return {'devices': [{'id': 'd1', 'display_name': 'Phone', 'connection': 'connected'}],
                    'apps': []}
        code, output, errors = self.invoke(['install', 'app.apk'], call)
        self.assertEqual(code, 2)
        self.assertEqual(actions, ['apps-matrix'])
        self.assertIn('Phone', output)
        self.assertIn('--yes', errors)

    def test_install_requires_an_explicit_flag(self):
        calls = []
        def call(action, **kwargs):
            calls.append((action, kwargs))
            return {'results': [{'device_id': 'd1', 'ok': True}], 'scanned': True}
        with tempfile.TemporaryDirectory() as folder:
            apk = Path(folder) / 'app.apk'
            apk.write_bytes(b'fake apk')
            code, _, _ = self.invoke(['install', str(apk), '--yes'], call)
        self.assertEqual(code, 0)
        self.assertEqual(calls[0][0], 'apps-install')
        self.assertTrue(calls[0][1]['acknowledge'])

    def test_track_forwards_package(self):
        def call(action, **kwargs):
            self.assertEqual(action, 'apps-track')
            self.assertEqual(kwargs['package'], 'com.example.app')
            return None
        self.assertEqual(self.invoke(['track', 'com.example.app'], call)[0], 0)


if __name__ == '__main__':
    unittest.main()
