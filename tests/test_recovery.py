import json
import tempfile
import unittest
from pathlib import Path

from device_warden.store import Store, WardenError

DEVICES = '''List of devices attached
serial-one device model:Phone
'''


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'db.sqlite')
        self.store.discover(DEVICES)
        self.device = self.store.list()[0]['id']

    def interrupted(self, session_id='ses_one'):
        lease = self.store.claim('ai', tool='OpenCode', session_id=session_id)
        self.store.recover_restart()
        # A restart marks connections unknown until the next discovery, exactly as
        # the Broker's monitor would do a few seconds later.
        self.store.discover(DEVICES)
        return lease

    def test_owning_session_sees_the_lease_without_the_credential(self):
        lease = self.interrupted()
        row = self.store.list(session_id='ses_one')[0]
        self.assertTrue(row['owns_lease'])
        self.assertIsNone(row['lease_id'])
        other = self.store.list(session_id='ses_other')[0]
        self.assertFalse(other['owns_lease'])
        self.assertIsNone(other['lease_id'])
        self.assertEqual(lease['device_id'], self.device)

    def test_owning_session_can_release_by_device_id(self):
        self.interrupted()
        self.store.release(device_id=self.device, session_id='ses_one')
        row = self.store.list()[0]
        self.assertIsNone(row['kind'])
        self.assertEqual(row['connection'], 'connected')

    def test_another_session_cannot_release_the_device(self):
        self.interrupted()
        with self.assertRaises(WardenError):
            self.store.release(device_id=self.device, session_id='ses_other')
        with self.assertRaises(WardenError):
            self.store.release(device_id=self.device)
        self.assertEqual(self.store.list()[0]['lease_state'], 'recovery_required')

    def test_device_after_release_can_be_claimed_again(self):
        self.interrupted()
        self.store.release(device_id=self.device, session_id='ses_one')
        again = self.store.claim('ai', tool='Codex', session_id='ses_two')
        self.assertEqual(again['device_id'], self.device)

    def test_resume_takes_back_only_own_recovery_lease(self):
        lease = self.interrupted()
        with self.assertRaises(WardenError):
            self.store.claim('ai', tool='OpenCode', session_id='ses_other',
                             device_id=self.device, resume=True)
        resumed = self.store.claim('ai', tool='OpenCode', session_id='ses_one',
                                   device_id=self.device, resume=True)
        self.assertTrue(resumed['resumed'])
        self.assertEqual(resumed['lease_id'], lease['lease_id'])
        row = self.store.list()[0]
        self.assertEqual(row['lease_state'], 'active')
        self.assertIsNone(row['recovery_reason'])

    def test_resume_never_takes_an_active_lease(self):
        self.store.claim('ai', tool='OpenCode', session_id='ses_one')
        with self.assertRaises(WardenError):
            self.store.claim('ai', tool='OpenCode', session_id='ses_one',
                             device_id=self.device, resume=True)
        self.assertEqual(self.store.list()[0]['lease_state'], 'active')

    def test_operator_recover_needs_acknowledgement(self):
        self.interrupted()
        with self.assertRaises(WardenError):
            self.store.recover(self.device)
        self.store.recover(self.device, acknowledge=True)
        self.assertIsNone(self.store.list()[0]['kind'])
        row = next(r for r in self.store.audit() if r['action'] == 'recovered')
        self.assertEqual(json.loads(row['owner'])['reason'], 'broker_restart')

    def test_recover_refuses_a_healthy_lease(self):
        self.store.claim('ai', tool='Codex', session_id='ses_one')
        with self.assertRaises(WardenError):
            self.store.recover(self.device, acknowledge=True)
        with self.assertRaises(WardenError):
            self.store.recover(self.device, acknowledge=True, session_id='ses_other')

    def test_heartbeat_timeout_failure_is_recoverable(self):
        self.store.claim('ai', tool='OpenCode', session_id='ses_one')
        self.store.recover_stale(seconds=-1)
        row = self.store.list(session_id='ses_one')[0]
        self.assertEqual(row['recovery_reason'], 'heartbeat_timeout')
        self.assertTrue(row['owns_lease'])
        self.store.release(device_id=self.device, session_id='ses_one')
        self.assertIsNone(self.store.list()[0]['kind'])


if __name__ == '__main__':
    unittest.main()
