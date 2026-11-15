import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from device_warden.server import BrokerServer, ensure_private_dir, make_handler
from device_warden.store import Store


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'db.sqlite')
        self.store.discover('List of devices attached\nexample device model:Test\n')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store, 'test-token'))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def post(self, action, body, token='test-token'):
        req = urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}/{action}',
            data=json.dumps(body).encode(), method='POST',
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as response:
            return json.load(response)['result']

    def test_authentication_and_claim_release(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('list', {}, token='wrong')
        self.assertEqual(error.exception.code, 401)
        claim = self.post('claim', {'kind': 'ai', 'tool': 'Codex'})
        self.assertIsNone(self.post('list', {})[0]['lease_id'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('release', {'lease_id': 'not-the-owner'})
        self.assertEqual(error.exception.code, 409)
        self.post('release', {'lease_id': claim['lease_id']})
        self.assertIsNone(self.post('list', {})[0]['kind'])


    def test_second_broker_cannot_share_the_port(self):
        with self.assertRaises(OSError):
            BrokerServer(('127.0.0.1', self.server.server_port), make_handler(self.store, 'other')).server_close()

    def test_audit_is_available_over_broker(self):
        claim = self.post('claim', {'kind': 'ai', 'tool': 'Codex', 'session_id': 'ses-a'})
        self.post('release', {'lease_id': claim['lease_id']})
        rows = self.post('audit', {})
        self.assertEqual([row['action'] for row in rows], ['release', 'claim'])
        self.assertEqual(json.loads(rows[0]['owner'])['session_id'], 'ses-a')
        self.assertEqual([row['action'] for row in self.post('audit', {'device_id': 'missing'})], [])

    def test_new_data_folder_gets_private_acl(self):
        with tempfile.TemporaryDirectory() as parent:
            folder = Path(parent) / 'new'
            with patch('device_warden.server.subprocess.run') as run:
                ensure_private_dir(folder)
            self.assertTrue(folder.exists())
            args = run.call_args.args[0]
            self.assertEqual(args[:3], ['icacls', str(folder), '/inheritance:r'])
            self.assertIn('SYSTEM:(OI)(CI)F', args)

    def test_existing_data_folder_acl_is_left_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('device_warden.server.subprocess.run') as run:
                ensure_private_dir(folder)
            run.assert_not_called()

    def test_acl_failure_does_not_stop_the_broker(self):
        with tempfile.TemporaryDirectory() as parent:
            with patch('device_warden.server.subprocess.run', side_effect=OSError('denied')):
                self.assertTrue(ensure_private_dir(Path(parent) / 'new').exists())


if __name__ == '__main__':
    unittest.main()
