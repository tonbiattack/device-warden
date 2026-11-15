import contextlib
import io
import json
import sys
import unittest
from unittest.mock import patch

from device_warden.cli import main
from device_warden.store import WardenError


class RecoveryCliTests(unittest.TestCase):
    def invoke(self, args, handler):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['device-warden', *args]), \
             patch('device_warden.cli.call', side_effect=handler), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main()
        return code, output.getvalue(), errors.getvalue()

    def test_release_by_device_sends_own_session(self):
        calls = []
        def call(action, **kwargs):
            calls.append((action, kwargs))
            return {'device_id': 'd1', 'state': 'released'}
        code, _, _ = self.invoke(
            ['release', '--device', 'd1', '--session-id', 'ses_one'], call)
        self.assertEqual(code, 0)
        self.assertEqual(calls[0][0], 'release')
        self.assertEqual(calls[0][1]['device_id'], 'd1')
        self.assertEqual(calls[0][1]['session_id'], 'ses_one')
        self.assertIsNone(calls[0][1]['lease_id'])

    def test_release_still_accepts_a_lease_id(self):
        calls = []
        def call(action, **kwargs):
            calls.append((action, kwargs))
            return {'device_id': 'd1', 'state': 'released'}
        code, _, _ = self.invoke(['release', 'lease-123'], call)
        self.assertEqual(code, 0)
        self.assertEqual(calls[0][1]['lease_id'], 'lease-123')

    def test_recover_requires_acknowledge(self):
        with self.assertRaises(SystemExit) as error:
            self.invoke(['recover', 'd1'], lambda action, **kwargs: None)
        self.assertNotEqual(error.exception.code, 0)

    def test_recover_passes_acknowledge(self):
        calls = []
        def call(action, **kwargs):
            calls.append((action, kwargs))
            return {'device_id': 'd1', 'state': 'released'}
        code, _, _ = self.invoke(['recover', 'd1', '--acknowledge'], call)
        self.assertEqual(code, 0)
        self.assertEqual(calls[0], ('recover', {'device_id': 'd1', 'acknowledge': True}))

    def test_force_release_passes_acknowledge(self):
        calls = []
        def call(action, **kwargs):
            calls.append((action, kwargs))
            return None
        code, _, _ = self.invoke(['force-release', 'd1', '--acknowledge'], call)
        self.assertEqual(code, 0)
        self.assertEqual(calls[0], ('force-release', {'device_id': 'd1', 'acknowledge': True}))

    def test_claim_resume_requires_session(self):
        code, _, errors = self.invoke(
            ['claim', '--tool', 'OpenCode', '--device', 'd1', '--resume'],
            lambda action, **kwargs: None)
        self.assertEqual(code, 1)
        self.assertIn('--session-id', errors)

    def test_claim_resume_forwards_flag(self):
        calls = []
        def call(action, **kwargs):
            calls.append((action, kwargs))
            return {'lease_id': 'x', 'resumed': True}
        code, _, _ = self.invoke(
            ['claim', '--tool', 'OpenCode', '--device', 'd1', '--session-id', 'ses_one', '--resume'],
            call)
        self.assertEqual(code, 0)
        self.assertTrue(calls[0][1]['resume'])
        self.assertEqual(calls[0][1]['session_id'], 'ses_one')


if __name__ == '__main__':
    unittest.main()
