import tempfile
import unittest
import json
import os
import sqlite3
from contextlib import closing
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

    def test_parallel_claim_distributes_two_devices(self):
        self.store.discover(DEVICES + 'serial-two device model:Other\n')
        def claim(session):
            try:
                return self.store.claim('ai', tool='OpenCode', session_id=session)['device_id']
            except WardenError:
                return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(claim, [f'session-{i}' for i in range(12)]))
        claimed = [device for device in results if device]
        self.assertEqual(len(claimed), 2)
        self.assertEqual(len(set(claimed)), 2)
        self.assertNotEqual(*[row['session_id'] for row in self.store.list()])

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
        self.assertEqual(row['recovery_reason'], 'connection_lost')
        self.assertIsNotNone(row['recovery_at'])
        events = [entry for entry in self.store.audit() if entry['action'] == 'recovery_required']
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0]['owner'])['reason'], 'connection_lost')
        self.store.discover(EMPTY)
        self.assertEqual(len([entry for entry in self.store.audit() if entry['action'] == 'recovery_required']), 1)
        with self.assertRaises(WardenError):
            self.store.claim('ai', tool='Codex')

    def test_unleased_labels_do_not_assert_physical_availability(self):
        row = self.store.list()[0]
        self.assertEqual(status(row), '管理内では未確保')
        for connection, expected in (
            ('disconnected', '管理内では未確保（ADB未接続）'),
            ('unauthorized', '管理内では未確保（ADB要確認）'),
            ('unknown', '管理内では未確保（ADB要確認）'),
        ):
            self.assertEqual(status({**row, 'connection': connection}), expected)
        self.assertEqual(status({**row, 'kind': 'human'}), '自分が使用中')
        self.assertEqual(status({**row, 'kind': 'ai'}), 'AI使用中')
        self.assertEqual(status({**row, 'ai_allowed': 0}), '管理内では未確保')

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
        lease = self.store.claim('ai', tool='Codex')
        self.store.recover_stale(seconds=-1)
        self.assertEqual(self.store.list()[0]['lease_state'], 'recovery_required')
        self.assertEqual(self.store.list()[0]['recovery_reason'], 'heartbeat_timeout')
        with self.assertRaises(WardenError):
            self.store.heartbeat(lease['lease_id'])
        self.store.recover_restart()
        self.assertEqual(self.store.list()[0]['connection'], 'unknown')

    def test_legacy_lease_schema_is_migrated_without_losing_data(self):
        path = Path(self.temp.name) / 'legacy.db'
        with closing(sqlite3.connect(path)) as db:
            db.execute('''CREATE TABLE leases (id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                kind TEXT NOT NULL, tool TEXT, session_id TEXT, project TEXT, title TEXT,
                workdir TEXT, pid INTEGER, acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL,
                state TEXT NOT NULL)''')
            db.execute("INSERT INTO leases VALUES ('old','device','ai','Codex',NULL,NULL,NULL,NULL,NULL,1,2,'recovery_required')")
            db.commit()
        migrated = Store(path)
        with migrated.connect() as db:
            row = db.execute("SELECT * FROM leases WHERE id='old'").fetchone()
            self.assertEqual(row['state'], 'recovery_required')
            self.assertIsNone(row['recovery_reason'])

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
        self.assertEqual([row['action'] for row in self.store.audit(device_id='missing')], [])

    def test_reconfirm_never_frees_a_lease_and_logs_it(self):
        lease = self.store.claim('ai', tool='Codex')
        with patch.object(self.store, 'discover', side_effect=lambda output=None: Store.discover(self.store, DEVICES)):
            before = self.store.reconfirm(self.device)
        self.assertEqual(before['lease_state'], 'active')
        with patch.object(self.store, 'discover', side_effect=lambda output=None: Store.discover(self.store, EMPTY)):
            after = self.store.reconfirm(self.device)
        self.assertEqual(after['lease_state'], 'recovery_required')
        self.assertEqual(after['recovery_reason'], 'connection_lost')
        self.assertEqual(after['lease_id'], lease['lease_id'])
        self.assertIn('reconfirm', [row['action'] for row in self.store.audit()])
        with self.assertRaises(WardenError):
            self.store.reconfirm('missing')

    def test_scrcpy_requires_a_human_lease_on_a_connected_device(self):
        with self.assertRaises(WardenError):
            self.store.launch_scrcpy(self.device)
        self.store.claim('ai', tool='Codex')
        with self.assertRaises(WardenError):
            self.store.launch_scrcpy(self.device)
        self.store.release(device_id=self.device, force=True)
        self.store.claim('human', device_id=self.device)
        with patch('device_warden.store.subprocess.Popen') as popen:
            self.assertTrue(self.store.launch_scrcpy(self.device)['started'])
            self.assertEqual(popen.call_args.args[0], ['scrcpy', '-s', 'serial-one'])
        self.assertIn('scrcpy', [row['action'] for row in self.store.audit()])

    def test_scrcpy_missing_binary_is_reported(self):
        self.store.claim('human', device_id=self.device)
        with patch('device_warden.store.subprocess.Popen', side_effect=OSError('missing')):
            with self.assertRaises(WardenError):
                self.store.launch_scrcpy(self.device)

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
