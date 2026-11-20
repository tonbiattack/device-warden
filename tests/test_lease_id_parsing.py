"""A lease ID may start with '-', and the CLI must still accept it.

`secrets.token_urlsafe` produces a leading '-' about 1.5% of the time. argparse
reads such a value as an option and rejects the whole command, so
`release <lease_id>` would fail for those leases and the device would stay
held. CI found this because a generated lease ID happened to start with '-'.
"""

import contextlib
import io
import sys
import unittest
from unittest.mock import patch

from device_warden.cli import main, protect_lease_id


class ProtectLeaseIdTests(unittest.TestCase):
    def test_dash_prefixed_lease_id_gets_a_separator(self):
        self.assertEqual(protect_lease_id(['release', '-abc123']),
                         ['release', '--', '-abc123'])

    def test_normal_lease_id_is_left_alone(self):
        self.assertEqual(protect_lease_id(['release', 'abc123']), ['release', 'abc123'])

    def test_existing_separator_is_respected(self):
        self.assertEqual(protect_lease_id(['release', '--', '-abc123']),
                         ['release', '--', '-abc123'])

    def test_release_options_still_parse(self):
        argv = ['release', '--device', 'd1', '--session-id', 's1']
        self.assertEqual(protect_lease_id(argv), argv)

    def test_help_is_not_treated_as_a_lease_id(self):
        self.assertEqual(protect_lease_id(['release', '--help']), ['release', '--help'])

    def test_other_commands_are_untouched(self):
        self.assertEqual(protect_lease_id(['claim', '--tool', 'Codex']),
                         ['claim', '--tool', 'Codex'])

    def test_adb_keeps_its_arguments(self):
        self.assertEqual(protect_lease_id(['adb', '-abc123', 'shell', 'id']),
                         ['adb', '--', '-abc123', 'shell', 'id'])


class DashLeaseCliTests(unittest.TestCase):
    def invoke(self, args, handler):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['device-warden', *args]), \
             patch('device_warden.cli.call', side_effect=handler), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main()
        return code, output.getvalue(), errors.getvalue()

    def test_release_accepts_a_dash_prefixed_lease_id(self):
        calls = []
        def call(action, **kwargs):
            calls.append((action, kwargs))
            return {'device_id': 'd1', 'state': 'released'}
        code, _, errors = self.invoke(['release', '-abc123'], call)
        self.assertEqual(code, 0, errors)
        self.assertEqual(calls[0][1]['lease_id'], '-abc123')

    def test_heartbeat_accepts_a_dash_prefixed_lease_id(self):
        calls = []
        def call(action, **kwargs):
            calls.append((action, kwargs))
        code, _, errors = self.invoke(['heartbeat', '-abc123'], call)
        self.assertEqual(code, 0, errors)
        self.assertEqual(calls[0][1]['lease_id'], '-abc123')

    def test_adb_accepts_a_dash_prefixed_lease_id(self):
        calls = []
        def call(action, **kwargs):
            calls.append((action, kwargs))
            return {'exit_code': 0, 'stdout': '', 'stderr': ''}
        code, _, errors = self.invoke(['adb', '-abc123', 'shell', 'id'], call)
        self.assertEqual(code, 0, errors)
        self.assertEqual(calls[0][1]['lease_id'], '-abc123')
        self.assertEqual(calls[0][1]['args'], ['shell', 'id'])

    def test_release_by_device_still_works(self):
        calls = []
        def call(action, **kwargs):
            calls.append((action, kwargs))
            return {'device_id': 'd1', 'state': 'released'}
        code, _, errors = self.invoke(['release', '--device', 'd1', '--session-id', 's1'], call)
        self.assertEqual(code, 0, errors)
        self.assertEqual(calls[0][1]['device_id'], 'd1')


if __name__ == '__main__':
    unittest.main()
