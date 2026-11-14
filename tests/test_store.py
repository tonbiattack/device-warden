import tempfile
import unittest
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from device_warden.store import Store, WardenError
from device_warden.store import display_name
from device_warden.gui import status
from device_warden.server import data_dir

DEVICES = 'List of devices attached\nserial-one device model:Phone transport_id:1\n'
EMPTY = 'List of devices attached\n\n'


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'db.sqlite')
        self.store.discover(DEVICES)
        self.device = self.store.list()[0]['id']

    def test_parallel_claim(self):
        def claim(_):
            try:
                return self.store.claim('ai', tool='Codex')
            except WardenError:
                return None
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(claim, range(10)))
        self.assertEqual(sum(x is not None for x in results), 1)

    def test_disconnect_reconnect_preserves_owner(self):
        lease = self.store.claim('ai', tool='OpenCode', session_id='session-one')
        self.store.discover(EMPTY)
        row = self.store.list()[0]
        self.assertIsNone(row['lease_id'])
        self.assertEqual(status(row), '接続切れ・要確認')
        self.store.discover(DEVICES)
        row = self.store.list()[0]
        self.assertEqual(row['session_id'], 'session-one')
        self.assertEqual(row['lease_state'], 'recovery_required')
        with self.assertRaises(WardenError):
            self.store.claim('ai', tool='Codex')

    def test_policy_and_ownership(self):
        self.store.update_device(self.device, ai_allowed=0)
        with self.assertRaises(WardenError):
            self.store.claim('ai', tool='Codex')
        lease = self.store.claim('human', device_id=self.device)
        with self.assertRaises(WardenError):
            self.store.release(lease_id='wrong')
        self.store.release(lease_id=lease['lease_id'])
        self.assertIsNone(self.store.list()[0]['kind'])

    def test_restart_and_timeout_never_free(self):
        self.store.claim('ai', tool='Codex')
        self.store.recover_stale(seconds=-1)
        self.assertEqual(self.store.list()[0]['lease_state'], 'recovery_required')
        self.store.recover_restart()
        self.assertEqual(self.store.list()[0]['connection'], 'unknown')

    def test_adb_error_and_unauthorized(self):
        with self.assertRaises(WardenError):
            self.store.discover('broken output')
        self.assertEqual(self.store.list()[0]['connection'], 'unknown')
        self.store.discover('List of devices attached\nserial-one unauthorized\n')
        self.assertEqual(self.store.list()[0]['connection'], 'unauthorized')
        with self.assertRaises(WardenError):
            self.store.claim('ai', tool='Codex')

    def test_adb_uses_target_and_lease(self):
        lease = self.store.claim('ai', tool='Codex')
        with patch('device_warden.store.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ''
            run.return_value.stderr = ''
            self.store.adb(lease['lease_id'], ['shell', 'getprop'])
            self.assertEqual(run.call_args.args[0], ['adb', '-s', 'serial-one', 'shell', 'getprop'])
        with self.assertRaises(WardenError):
            self.store.adb('wrong', ['shell', 'getprop'])

    def test_persistence_and_force_audit(self):
        self.store.claim('ai', tool='Codex')
        self.store.release(device_id=self.device, force=True)
        reopened = Store(self.store.path)
        with reopened.connect() as db:
            owner = db.execute("SELECT owner FROM audit WHERE action='force_release'").fetchone()[0]
            self.assertEqual(json.loads(owner)['tool'], 'Codex')

    def test_two_opencode_sessions_and_release(self):
        self.store.discover(DEVICES + 'serial-two device model:Other\n')
        first = self.store.claim('ai', tool='OpenCode', session_id='session-one', title='First', project='Project')
        second = self.store.claim('ai', tool='OpenCode', session_id='session-two')
        self.assertNotEqual(first['device_id'], second['device_id'])
        owners = {row['session_id']: row for row in self.store.list()}
        self.assertEqual(owners['session-one']['title'], 'First')
        self.assertIsNone(owners['session-two']['title'])
        self.store.release(lease_id=first['lease_id'])
        self.assertEqual(sum(row['kind'] == 'ai' for row in self.store.list()), 1)

    def test_audit_lists_owner_and_operations(self):
        self.store.claim('ai', tool='Codex', session_id='session-one')
        self.store.release(device_id=self.device, force=True)
        rows = self.store.audit()
        self.assertEqual([row['action'] for row in rows], ['force_release', 'claim'])
        self.assertEqual(rows[0]['device'], 'Phone')
        owner = json.loads(rows[0]['owner'])
        self.assertEqual((owner['tool'], owner['session_id']), ('Codex', 'session-one'))

    def test_data_dir_is_shared_across_runtimes(self):
        with patch.dict(os.environ, {'USERPROFILE': r'C:\Users\example'}, clear=False):
            os.environ.pop('DEVICE_WARDEN_HOME', None)
            self.assertEqual(data_dir(), Path(r'C:\Users\example\.device-warden'))
        with patch.dict(os.environ, {'DEVICE_WARDEN_HOME': r'D:\warden'}, clear=False):
            self.assertEqual(data_dir(), Path(r'D:\warden'))

    def test_display_name_prefers_readable_names(self):
        self.assertEqual(display_name({'alias': 'テスト機', 'market_name': 'Redmi Pad SE'}), 'テスト機')
        self.assertEqual(display_name({'market_name': 'Redmi Pad SE 8.7', 'model': '24075RP89G'}), 'Redmi Pad SE 8.7')
        self.assertEqual(display_name({'brand': 'OPPO', 'model': 'CPH2699'}), 'OPPO CPH2699')
        self.assertEqual(display_name({'brand': 'Redmi', 'model': 'Redmi Pad SE'}), 'Redmi Pad SE')
        self.assertEqual(display_name({'model': 'CPH2699'}), 'CPH2699')
        self.assertEqual(display_name({'serial': '6ad7aa65'}), '6ad7aa65')
        self.assertEqual(display_name({}), '不明な端末')

    def test_discovery_reads_marketing_name(self):
        def fake(args, timeout):
            if args[:2] == ['adb', 'devices']:
                return type('R', (), {'returncode': 0, 'stdout': DEVICES, 'stderr': ''})()
            return type('R', (), {'returncode': 0, 'stdout': '[ro.product.marketname]: [Redmi Pad SE]\n'
                                  '[ro.product.brand]: [Redmi]\n', 'stderr': ''})()
        with patch('device_warden.store.run_adb', side_effect=fake):
            self.store.discover()
        row = self.store.list()[0]
        self.assertEqual(row['display_name'], 'Redmi Pad SE')
        self.assertEqual(row['brand'], 'Redmi')

    def test_discovery_survives_unreadable_props(self):
        def fake(args, timeout):
            if args[:2] == ['adb', 'devices']:
                return type('R', (), {'returncode': 0, 'stdout': DEVICES, 'stderr': ''})()
            raise OSError('device offline')
        with patch('device_warden.store.run_adb', side_effect=fake):
            self.store.discover()
        self.assertEqual(self.store.list()[0]['display_name'], 'Phone')


if __name__ == '__main__':
    unittest.main()
