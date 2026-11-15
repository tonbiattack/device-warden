import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from device_warden.store import Store, WardenError

DEVICES = '''List of devices attached
serial-one device model:Phone
serial-two device model:Tablet
'''
PACKAGES = 'package:com.example.app\npackage:com.example.other\n'
DETAILS = 'Package [com.example.app]\n versionCode=42\n versionName=1.4.2\n'


def fake_adb(args, timeout):
    kind = type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})
    serial = args[args.index('-s') + 1] if '-s' in args else None
    result = kind()
    if args[:2] == ['adb', 'devices']:
        result.stdout = DEVICES
    elif 'pm' in args:
        result.stdout = PACKAGES if serial == 'serial-one' else 'package:com.example.app\n'
    elif 'dumpsys' in args:
        result.stdout = DETAILS if serial == 'serial-one' else 'Package [com.example.app]\n versionCode=41\n'
    return result


class AppMatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'db.sqlite')
        self.patch = patch('device_warden.store.run_adb', side_effect=fake_adb)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.store.discover()
        self.devices = self.store.list()

    def device(self, model):
        return next(d['id'] for d in self.devices if d['model'] == model)

    def test_matrix_reports_versions_and_missing_apps(self):
        self.store.track_app('com.example.app', 'Example')
        self.store.track_app('com.example.other')
        self.store.scan_apps()
        matrix = self.store.app_matrix()
        app = next(a for a in matrix['apps'] if a['package'] == 'com.example.app')
        self.assertEqual(app['baseline'], 42)
        self.assertEqual(app['cells'][self.device('Phone')]['status'], 'latest')
        self.assertEqual(app['cells'][self.device('Tablet')]['status'], 'old')
        other = next(a for a in matrix['apps'] if a['package'] == 'com.example.other')
        self.assertIsNone(other['cells'][self.device('Tablet')]['version_code'])
        self.assertIsNone(other['cells'][self.device('Tablet')]['error'])

    def test_one_device_failure_does_not_block_others(self):
        self.store.track_app('com.example.app')
        def flaky(args, timeout):
            if args[:2] == ['adb', 'devices']:
                return fake_adb(args, timeout)
            if '-s' in args and args[args.index('-s') + 1] == 'serial-one':
                raise OSError('device offline')
            return fake_adb(args, timeout)
        with patch('device_warden.store.run_adb', side_effect=flaky):
            self.store.scan_apps()
        matrix = self.store.app_matrix()
        app = matrix['apps'][0]
        failed = app['cells'][self.device('Phone')]
        healthy = app['cells'][self.device('Tablet')]
        self.assertEqual(failed['error'], 'device offline')
        self.assertEqual(healthy['version_code'], 41)

    def test_disconnected_device_keeps_last_snapshot(self):
        self.store.track_app('com.example.app')
        self.store.scan_apps()
        self.store.discover('List of devices attached\nserial-one device model:Phone\n')
        result = self.store.scan_apps(device_id=self.device('Tablet'))
        self.assertEqual(result['skipped'], [self.device('Tablet')])
        matrix = self.store.app_matrix()
        cell = matrix['apps'][0]['cells'][self.device('Tablet')]
        self.assertEqual(cell['version_code'], 41)
        self.assertIsNotNone(cell['scanned_at'])

    def test_untracking_removes_snapshots(self):
        self.store.track_app('com.example.app')
        self.store.scan_apps()
        self.store.untrack_app('com.example.app')
        self.assertEqual(self.store.app_matrix()['apps'], [])
        with self.assertRaises(WardenError):
            self.store.untrack_app('com.example.app')

    def test_invalid_package_name_is_rejected(self):
        with self.assertRaises(WardenError):
            self.store.track_app('bad name; rm -rf')

    def test_install_requires_acknowledgement(self):
        with self.assertRaises(WardenError):
            self.store.install_apk('x.apk')

    def test_install_reports_per_device_results(self):
        self.store.track_app('com.example.app')
        with tempfile.TemporaryDirectory() as folder:
            apk = Path(folder) / 'app.apk'
            apk.write_bytes(b'apk')
            calls = []
            def fake(args, timeout):
                calls.append(args)
                if args[:2] == ['adb', 'devices']:
                    return fake_adb(args, timeout)
                if 'install' in args:
                    success = args[args.index('-s') + 1] == 'serial-one'
                    result = type('R', (), {'returncode': 0 if success else 1, 'stdout':
                                            'Success' if success else 'Failure [INSTALL_FAILED_VERSION_DOWNGRADE]',
                                            'stderr': ''})()
                    return result
                return fake_adb(args, timeout)
            with patch('device_warden.store.run_adb', side_effect=fake):
                result = self.store.install_apk(str(apk), acknowledge=True)
        self.assertEqual(len(result['results']), 2)
        installs = [c for c in calls if 'install' in c]
        self.assertTrue(all(c[1] == '-s' for c in installs))
        self.assertEqual(sorted(c[2] for c in installs), ['serial-one', 'serial-two'])
        failed = next(r for r in result['results'] if not r['ok'])
        self.assertIn('ダウングレード', failed['hint'])

    def test_install_skips_disconnected_devices(self):
        self.store.discover('List of devices attached\nserial-one device model:Phone\n')
        device = next(d['id'] for d in self.store.list() if d['model'] == 'Tablet')
        with tempfile.TemporaryDirectory() as folder:
            apk = Path(folder) / 'app.apk'
            apk.write_bytes(b'apk')
            result = self.store.install_apk(str(apk), device_ids=[device], acknowledge=True)
        self.assertFalse(result['results'][0]['ok'])
        self.assertIn('接続', result['results'][0]['error'])


if __name__ == '__main__':
    unittest.main()
