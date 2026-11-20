import contextlib
import io
import sys
import unittest
from unittest.mock import patch

from device_warden.cli import main
from device_warden.cli import keep_alive as real_keep_alive
from device_warden.store import WardenError


class RunCommandTests(unittest.TestCase):
    """`run` keeps a lease alive for the whole command, not just one ADB call."""

    def invoke(self, args, handler, run=None):
        output, errors = io.StringIO(), io.StringIO()
        completed = run or (lambda command: type('Done', (), {'returncode': 0})())
        with patch.object(sys, 'argv', ['device-warden', *args]), \
             patch('device_warden.cli.call', side_effect=handler), \
             patch('device_warden.cli.subprocess.run', side_effect=completed), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main()
        return code, output.getvalue(), errors.getvalue()

    def test_success_claims_runs_and_releases(self):
        actions = []
        def call(action, **kwargs):
            actions.append((action, kwargs))
            if action == 'claim':
                return {'lease_id': 'secret', 'device_id': 'd1'}
        code, output, errors = self.invoke(['run', '--tool', 'dsh', '--', 'gradlew.bat', 'test'], call)
        self.assertEqual(code, 0)
        self.assertEqual([a for a, _ in actions], ['claim', 'release'])
        self.assertEqual(actions[1][1]['lease_id'], 'secret')
        self.assertNotIn('secret', output)
        self.assertEqual(errors, '')

    def test_claim_failure_never_runs_the_command(self):
        actions, ran = [], []
        def call(action, **kwargs):
            actions.append(action)
            raise WardenError('no device')
        code, _, errors = self.invoke(
            ['run', '--tool', 'dsh', '--', 'gradlew.bat', 'test'], call,
            run=lambda command: ran.append(command))
        self.assertEqual(code, 1)
        self.assertEqual(actions, ['claim'])
        self.assertEqual(ran, [])
        self.assertIn('no device', errors)

    def test_failed_command_retains_the_lease(self):
        actions = []
        def call(action, **kwargs):
            actions.append(action)
            if action == 'claim':
                return {'lease_id': 'secret', 'device_id': 'd1'}
        code, _, errors = self.invoke(
            ['run', '--tool', 'dsh', '--', 'gradlew.bat', 'test'], call,
            run=lambda command: type('Done', (), {'returncode': 3})())
        self.assertEqual(code, 3)
        self.assertEqual(actions, ['claim'])
        self.assertIn('secret', errors)

    def test_missing_command_never_claims(self):
        actions = []
        code, _, errors = self.invoke(
            ['run', '--tool', 'dsh'], lambda action, **kwargs: actions.append(action))
        self.assertEqual(code, 1)
        self.assertEqual(actions, [])
        self.assertIn('コマンド', errors)

    def test_unlaunchable_command_releases_the_lease(self):
        actions = []
        def call(action, **kwargs):
            actions.append(action)
            if action == 'claim':
                return {'lease_id': 'secret', 'device_id': 'd1'}
        def boom(command):
            raise OSError('not found')
        code, _, errors = self.invoke(
            ['run', '--tool', 'dsh', '--', 'missing.exe'], call, run=boom)
        self.assertEqual(code, 1)
        self.assertEqual(actions, ['claim', 'release'])
        self.assertIn('実行できませんでした', errors)

    def test_heartbeat_runs_while_the_command_is_running(self):
        """The lease must be refreshed during the work, not only at the end."""
        heartbeats = []
        def call(action, **kwargs):
            if action == 'claim':
                return {'lease_id': 'secret', 'device_id': 'd1'}
            if action == 'heartbeat':
                heartbeats.append(kwargs['lease_id'])
        def slow(command):
            import time
            time.sleep(0.3)
            return type('Done', (), {'returncode': 0})()
        # Shorten the interval so the test does not wait 30 seconds. The real
        # keep_alive is captured before patching, otherwise the patch recurses.
        with patch('device_warden.cli.keep_alive',
                   side_effect=lambda lease_id, interval=30: real_keep_alive(lease_id, interval=0.05)):
            code, _, _ = self.invoke(['run', '--tool', 'dsh', '--', 'slow.exe'], call, run=slow)
        self.assertEqual(code, 0)
        self.assertTrue(heartbeats, 'コマンド実行中にheartbeatが送られていない')
        self.assertEqual(set(heartbeats), {'secret'})


if __name__ == '__main__':
    unittest.main()
