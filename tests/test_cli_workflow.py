import contextlib
import io
import sys
import unittest
from unittest.mock import patch

from device_warden.cli import main
from device_warden.store import WardenError


class ScopedAdbTests(unittest.TestCase):
    def invoke(self, args, handler):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['device-warden', *args]), \
             patch('device_warden.cli.call', side_effect=handler), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main()
        return code, output.getvalue(), errors.getvalue()

    def test_success_claims_runs_and_releases(self):
        actions = []
        def call(action, **kwargs):
            actions.append((action, kwargs))
            if action == 'claim':
                return {'lease_id': 'secret'}
            if action == 'adb':
                return {'exit_code': 0, 'stdout': 'ok', 'stderr': ''}
        code, output, errors = self.invoke(['with-device', '--tool', 'Codex', '--', 'shell', 'id'], call)
        self.assertEqual(code, 0)
        self.assertEqual([a for a, _ in actions], ['claim', 'adb', 'release'])
        self.assertEqual(actions[1][1]['lease_id'], 'secret')
        self.assertNotIn('secret', output)

    def test_failed_claim_never_runs_adb(self):
        actions = []
        def call(action, **kwargs):
            actions.append(action)
            raise WardenError('no device')
        code, _, _ = self.invoke(['with-device', '--tool', 'Codex', '--', 'shell', 'id'], call)
        self.assertEqual(code, 1)
        self.assertEqual(actions, ['claim'])

    def test_failed_adb_retains_lease(self):
        actions = []
        def call(action, **kwargs):
            actions.append(action)
            if action == 'claim':
                return {'lease_id': 'secret'}
            return {'exit_code': 2, 'stdout': '', 'stderr': 'error'}
        code, _, errors = self.invoke(['with-device', '--tool', 'Codex', '--', 'shell', 'id'], call)
        self.assertEqual(code, 2)
        self.assertEqual(actions, ['claim', 'adb'])
        self.assertIn('secret', errors)

    def test_interrupted_adb_retains_lease(self):
        actions = []
        def call(action, **kwargs):
            actions.append(action)
            if action == 'claim':
                return {'lease_id': 'secret'}
            raise WardenError('timeout')
        code, _, errors = self.invoke(['with-device', '--tool', 'Codex', '--', 'shell', 'id'], call)
        self.assertEqual(code, 1)
        self.assertEqual(actions, ['claim', 'adb'])
        self.assertIn('secret', errors)


if __name__ == '__main__':
    unittest.main()
