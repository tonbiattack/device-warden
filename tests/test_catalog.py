"""Tests for the Obtainium catalog.

Obtainium does the fetching and installing; Device Warden only stores which
package comes from which repository and turns that into the deep link Obtainium
understands. The link format is asserted against the shapes read from
Obtainium's `lib/pages/home.dart`.
"""

import hashlib
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

from device_warden import obtainium
from device_warden.server import make_handler
from device_warden.store import Store, WardenError

TOKEN = 'catalog-token'
DEVICES = 'List of devices attached\nserial-one device model:Phone\n'


class LinkTests(unittest.TestCase):
    def test_add_link_uses_the_repository_url(self):
        self.assertEqual(obtainium.add_link('https://github.com/owner/repo'),
                         'obtainium://add/https://github.com/owner/repo')

    def test_repository_url_is_normalized(self):
        self.assertEqual(obtainium.normalize_repo_url('https://github.com/owner/repo/'),
                         'https://github.com/owner/repo')
        self.assertEqual(obtainium.normalize_repo_url('  https://github.com/owner/repo  '),
                         'https://github.com/owner/repo')

    def test_non_repository_urls_are_rejected(self):
        for url in ('https://gitlab.com/owner/repo', 'not a url', 'https://github.com/owner'):
            with self.assertRaises(obtainium.CatalogError):
                obtainium.normalize_repo_url(url)

    def test_package_names_are_validated(self):
        self.assertEqual(obtainium.validate_package('com.example.app'), 'com.example.app')
        for package in ('com', 'com..app', 'com.example.app;rm', '', None):
            with self.assertRaises(obtainium.CatalogError):
                obtainium.validate_package(package)

    def test_import_payload_has_the_fields_obtainium_requires(self):
        apps = obtainium.import_payload([
            {'package': 'com.example.app', 'url': 'https://github.com/owner/repo', 'label': 'Example'}])
        app = apps[0]
        # App.fromJson requires id, url, author, name and additionalSettings.
        for field in ('id', 'url', 'author', 'name', 'additionalSettings'):
            self.assertIn(field, app)
        self.assertEqual(app['id'], 'com.example.app')
        self.assertEqual(app['name'], 'Example')
        self.assertEqual(app['author'], 'owner')
        self.assertEqual(json.loads(app['additionalSettings']), {})

    def test_import_link_carries_a_bare_list(self):
        """Obtainium wraps the decoded JSON itself; an `apps` key breaks the cast."""
        link = obtainium.import_link([{'package': 'com.example.app',
                                       'url': 'https://github.com/owner/repo'}])
        self.assertTrue(link.startswith('obtainium://apps/'))
        decoded = json.loads(unquote(link[len('obtainium://apps/'):]))
        self.assertIsInstance(decoded, list)
        self.assertEqual(decoded[0]['id'], 'com.example.app')

    def test_import_link_rejects_an_empty_catalog(self):
        with self.assertRaises(obtainium.CatalogError):
            obtainium.import_link([])

    def test_adb_args_open_the_link_in_obtainium(self):
        args = obtainium.adb_open_args('obtainium://add/https://github.com/o/r')
        self.assertEqual(args[:5], ['shell', 'am', 'start', '-W', '-a'])
        self.assertIn('obtainium://add/https://github.com/o/r', args)
        self.assertEqual(args[-1], obtainium.OBTAINIUM_PACKAGE)

    def test_repo_slug(self):
        self.assertEqual(obtainium.repo_slug('https://github.com/owner/repo/'), 'owner/repo')


class ReleaseCheckTests(unittest.TestCase):
    """A repository without a release cannot be registered; say so up front."""

    def runner(self, stdout, returncode=0):
        def run(command, **kwargs):
            return type('Done', (), {'returncode': returncode, 'stdout': stdout, 'stderr': ''})()
        return run

    def test_repository_with_a_release(self):
        result = obtainium.check_releases('https://github.com/owner/repo',
                                          runner=self.runner('[{"tagName": "v1.0.0"}]'))
        self.assertTrue(result['checked'])
        self.assertTrue(result['has_release'])
        self.assertEqual(result['tag'], 'v1.0.0')

    def test_repository_without_a_release_is_reported(self):
        result = obtainium.check_releases('https://github.com/owner/repo',
                                          runner=self.runner('[]'))
        self.assertTrue(result['checked'])
        self.assertFalse(result['has_release'])
        self.assertIn('リリースがありません', result['reason'])

    def test_gh_failure_is_unknown_not_a_mismatch(self):
        result = obtainium.check_releases('https://github.com/owner/repo',
                                          runner=self.runner('', returncode=1))
        self.assertFalse(result['checked'])

    def test_missing_gh_is_unknown(self):
        def run(command, **kwargs):
            raise OSError('gh not found')
        result = obtainium.check_releases('https://github.com/owner/repo', runner=run)
        self.assertFalse(result['checked'])


class CatalogStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'db.sqlite')
        self.store.discover(DEVICES)
        self.device = self.store.list()[0]['id']

    def test_add_and_list(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo', 'Owner')
        rows = self.store.catalog()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['package'], 'com.example.app')
        self.assertEqual(rows[0]['url'], 'https://github.com/owner/repo')

    def test_adding_twice_updates_instead_of_duplicating(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        self.store.catalog_add('com.example.app', 'https://github.com/owner/other')
        rows = self.store.catalog()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['url'], 'https://github.com/owner/other')

    def test_catalog_entry_is_also_tracked_for_version_scans(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        self.assertIn('com.example.app', [row['package'] for row in self.store.tracked_apps()])

    def test_invalid_input_is_rejected(self):
        with self.assertRaises(obtainium.CatalogError):
            self.store.catalog_add('bad package', 'https://github.com/owner/repo')
        with self.assertRaises(obtainium.CatalogError):
            self.store.catalog_add('com.example.app', 'https://gitlab.com/owner/repo')

    def test_remove(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        self.store.catalog_remove('com.example.app')
        self.assertEqual(self.store.catalog(), [])
        with self.assertRaises(WardenError):
            self.store.catalog_remove('com.example.app')

    def test_links_include_the_device_and_apps(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        plan = self.store.catalog_links(self.device)
        self.assertEqual(plan['serial'], 'serial-one')
        self.assertEqual([app['package'] for app in plan['apps']], ['com.example.app'])

    def test_links_reject_an_unknown_package(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        with self.assertRaises(WardenError):
            self.store.catalog_links(self.device, ['com.other.app'])

    def test_links_reject_an_empty_catalog(self):
        with self.assertRaises(WardenError):
            self.store.catalog_links(self.device)

    def test_register_requires_acknowledgement(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        with self.assertRaises(WardenError):
            self.store.catalog_register(self.device)

    def test_register_refuses_a_disconnected_device(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        self.store.discover('List of devices attached\n')
        result = self.store.catalog_register(self.device, acknowledge=True)
        self.assertFalse(result['devices'][0]['ok'])
        self.assertIn('接続されていません', result['devices'][0]['error'])

    def test_register_opens_one_link_per_app(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        self.store.catalog_add('com.other.app', 'https://github.com/owner/other')
        with patch('device_warden.store.run_adb') as run_adb, \
             patch('device_warden.obtainium.check_releases',
                   return_value={'checked': True, 'has_release': True, 'tag': 'v1'}):
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': 'Starting: Intent', 'stderr': ''})()
            result = self.store.catalog_register(self.device, acknowledge=True)
        results = result['devices'][0]['results']
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item['ok'] for item in results))
        # Each call must address the device by serial and open an add link.
        for call in run_adb.call_args_list:
            command = call.args[0]
            self.assertEqual(command[:2], ['adb', '-s'])
            self.assertIn('obtainium://add/', ' '.join(command))

    def test_register_skips_a_repository_without_releases(self):
        """Obtainium's Add screen does nothing for such a repo; do not pretend it worked."""
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        with patch('device_warden.store.run_adb') as run_adb, \
             patch('device_warden.obtainium.check_releases',
                   return_value={'checked': True, 'has_release': False,
                                 'reason': 'リリースがありません'}):
            result = self.store.catalog_register(self.device, acknowledge=True)
        results = result['devices'][0]['results']
        self.assertFalse(results[0]['ok'])
        self.assertTrue(results[0]['skipped'])
        run_adb.assert_not_called()

    def test_register_proceeds_when_the_release_check_is_unknown(self):
        """A failed check must not block a registration that might work."""
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        with patch('device_warden.store.run_adb') as run_adb, \
             patch('device_warden.obtainium.check_releases',
                   return_value={'checked': False, 'reason': 'gh が失敗'}):
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': '', 'stderr': ''})()
            result = self.store.catalog_register(self.device, acknowledge=True)
        self.assertTrue(result['devices'][0]['results'][0]['ok'])

    def test_register_reports_a_failure_per_app(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        with patch('device_warden.store.run_adb') as run_adb:
            run_adb.return_value = type('Done', (), {
                'returncode': 1, 'stdout': '', 'stderr': 'Error: Activity not started'})()
            result = self.store.catalog_register(self.device, acknowledge=True)
        self.assertFalse(result['devices'][0]['results'][0]['ok'])

    def test_register_is_audited(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        with patch('device_warden.store.run_adb') as run_adb:
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': '', 'stderr': ''})()
            self.store.catalog_register(self.device, acknowledge=True)
        actions = [row['action'] for row in self.store.audit()]
        self.assertIn('obtainium_register', actions)

    def test_import_link_covers_the_catalog(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        self.store.catalog_add('com.other.app', 'https://github.com/owner/other')
        result = self.store.catalog_import_link()
        self.assertEqual(result['count'], 2)
        self.assertTrue(result['link'].startswith('obtainium://apps/'))

    def test_signatures_report_the_installed_certificate(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        dump = ('Packages:\n  Package [com.example.app] (abc):\n'
                '    signatures=PackageSignatures{d534165 version:3, '
                'signatures:[b2d95fc0], past signatures:[]}\n')
        apksigner = ('Signer #1 certificate DN: C=US, O=Android, CN=Android Debug\n'
                     'Signer #1 certificate SHA-256 digest: '
                     '3a29d6024957f48d5433b9003de747dc3ebcee10c1ad3eeaf14a4756840d6363\n')

        def adb(command, **kwargs):
            if 'pm' in command:
                return type('Done', (), {'returncode': 0,
                                         'stdout': 'package:/data/app/base.apk\n', 'stderr': ''})()
            if 'dumpsys' in command:
                return type('Done', (), {'returncode': 0, 'stdout': dump, 'stderr': ''})()
            if 'pull' in command:
                # `_apk_digest` checks that the pull produced a file.
                Path(command[-1]).write_bytes(b'fake apk')
                return type('Done', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
            return type('Done', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        with patch('device_warden.store.run_adb', side_effect=adb), \
             patch('device_warden.store.shutil.which', return_value='apksigner'), \
             patch('device_warden.store.subprocess.run') as host_run:
            host_run.return_value = type('Done', (), {
                'returncode': 0, 'stdout': apksigner, 'stderr': ''})()
            result = self.store.catalog_signatures(self.device)
        self.assertTrue(result['results'][0]['installed'])
        # The digest must be the SHA-256 Obtainium shows, not the dumpsys short hash.
        self.assertEqual(result['results'][0]['digest'],
                         '3a29d6024957f48d5433b9003de747dc3ebcee10c1ad3eeaf14a4756840d6363')
        self.assertEqual(result['results'][0]['signatures'], ['b2d95fc0'])

    def test_signatures_report_an_uninstalled_app(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        with patch('device_warden.store.run_adb') as run_adb:
            run_adb.return_value = type('Done', (), {
                'returncode': 1, 'stdout': 'Unable to find package: com.example.app',
                'stderr': ''})()
            result = self.store.catalog_signatures(self.device)
        self.assertFalse(result['results'][0]['installed'])
        self.assertIsNone(result['results'][0]['digest'])

    def test_signatures_never_uninstall_or_clear_data(self):
        """A mismatch is reported, never repaired: no uninstall command is run."""
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        with patch('device_warden.store.run_adb') as run_adb, \
             patch('device_warden.store.shutil.which', return_value=None), \
             patch.dict(os.environ, {'ANDROID_HOME': '', 'ANDROID_SDK_ROOT': ''}):
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': 'package:/data/app/base.apk', 'stderr': ''})()
            self.store.catalog_signatures(self.device)
        for call in run_adb.call_args_list:
            command = ' '.join(call.args[0])
            self.assertNotIn('uninstall', command)
            self.assertNotIn('pm clear', command)

    def test_missing_apksigner_reports_unknown_not_a_mismatch(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        with patch('device_warden.store.run_adb') as run_adb, \
             patch('device_warden.store.shutil.which', return_value=None), \
             patch.dict(os.environ, {'ANDROID_HOME': '', 'ANDROID_SDK_ROOT': ''}):
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': 'package:/data/app/base.apk', 'stderr': ''})()
            result = self.store.catalog_signatures(self.device)
        self.assertIsNone(result['results'][0]['digest'])


class ObtainiumInstallTests(unittest.TestCase):
    """Obtainium is an Android app, so every device needs its own copy."""

    def test_asset_is_chosen_per_abi(self):
        self.assertEqual(obtainium.choose_asset('arm64-v8a'), 'app-arm64-v8a-release.apk')
        self.assertEqual(obtainium.choose_asset('x86_64'), 'app-x86_64-release.apk')
        self.assertEqual(obtainium.choose_asset('armeabi-v7a'), 'app-armeabi-v7a-release.apk')

    def test_unknown_abi_falls_back_to_the_universal_build(self):
        self.assertEqual(obtainium.choose_asset('mips'), obtainium.OBTAINIUM_UNIVERSAL)
        self.assertEqual(obtainium.choose_asset(None), obtainium.OBTAINIUM_UNIVERSAL)

    def test_release_assets_are_indexed_by_name(self):
        payload = {'assets': [
            {'name': 'app-x86_64-release.apk', 'browser_download_url': 'https://x/a.apk',
             'digest': 'sha256:ABC123', 'size': 10},
            {'name': 'app-release.apk', 'browser_download_url': 'https://x/b.apk',
             'digest': None, 'size': 20},
        ]}
        assets = obtainium.release_assets(payload)
        self.assertEqual(assets['app-x86_64-release.apk']['digest'], 'abc123')
        self.assertEqual(assets['app-x86_64-release.apk']['url'], 'https://x/a.apk')
        self.assertIsNone(assets['app-release.apk']['digest'])

    def test_download_is_verified_against_the_published_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            apk = Path(folder) / 'app.apk'
            apk.write_bytes(b'fake apk')
            digest = hashlib.sha256(b'fake apk').hexdigest()
            self.assertTrue(obtainium.verify_download(apk, digest)['verified'])
            self.assertFalse(obtainium.verify_download(apk, 'deadbeef')['verified'])

    def test_missing_digest_is_not_treated_as_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            apk = Path(folder) / 'app.apk'
            apk.write_bytes(b'fake apk')
            result = obtainium.verify_download(apk, None)
        self.assertFalse(result['verified'])

    def test_fetch_release_uses_gh(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return type('Done', (), {'returncode': 0,
                                     'stdout': '{"tag_name": "v1.7.4", "assets": []}',
                                     'stderr': ''})()
        payload = obtainium.fetch_release(runner=runner)
        self.assertEqual(payload['tag_name'], 'v1.7.4')
        self.assertIn('repos/ImranR98/Obtainium/releases/latest', calls[0])

    def test_fetch_release_failure_is_a_catalog_error(self):
        def runner(command, **kwargs):
            return type('Done', (), {'returncode': 1, 'stdout': '', 'stderr': 'boom'})()
        with self.assertRaises(obtainium.CatalogError):
            obtainium.fetch_release(runner=runner)


class ObtainiumInstallStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'db.sqlite')
        self.store.discover('List of devices attached\nserial-one device model:Phone\n')
        self.device = self.store.list()[0]['id']

    def test_install_requires_acknowledgement(self):
        with self.assertRaises(WardenError):
            self.store.obtainium_install([self.device])

    def test_install_picks_the_abi_asset_and_verifies_it(self):
        release = {'tag_name': 'v1.7.4', 'assets': [
            {'name': 'app-x86_64-release.apk', 'browser_download_url': 'https://x/a.apk',
             'digest': 'sha256:' + hashlib.sha256(b'apk').hexdigest(), 'size': 3}]}

        def adb(command, **kwargs):
            if 'getprop' in command:
                return type('Done', (), {'returncode': 0, 'stdout': 'x86_64\n', 'stderr': ''})()
            if 'install' in command:
                return type('Done', (), {'returncode': 0, 'stdout': 'Success', 'stderr': ''})()
            return type('Done', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        def host(command, **kwargs):
            if command[0] == 'gh':
                # `gh release download` writes the file into --dir.
                folder = command[command.index('--dir') + 1]
                (Path(folder) / 'app-x86_64-release.apk').write_bytes(b'apk')
                return type('Done', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
            return type('Done', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        with patch('device_warden.store.run_adb', side_effect=adb), \
             patch('device_warden.obtainium.fetch_release', return_value=release), \
             patch('device_warden.store.subprocess.run', side_effect=host):
            result = self.store.obtainium_install([self.device], acknowledge=True)
        device = result['devices'][0]
        self.assertTrue(device['ok'])
        self.assertEqual(device['abi'], 'x86_64')
        self.assertEqual(device['asset'], 'app-x86_64-release.apk')

    def test_install_refuses_a_digest_mismatch(self):
        release = {'tag_name': 'v1.7.4', 'assets': [
            {'name': 'app-x86_64-release.apk', 'browser_download_url': 'https://x/a.apk',
             'digest': 'sha256:' + '0' * 64, 'size': 3}]}

        def adb(command, **kwargs):
            if 'getprop' in command:
                return type('Done', (), {'returncode': 0, 'stdout': 'x86_64\n', 'stderr': ''})()
            return type('Done', (), {'returncode': 0, 'stdout': 'Success', 'stderr': ''})()

        def host(command, **kwargs):
            if command[0] == 'gh':
                folder = command[command.index('--dir') + 1]
                (Path(folder) / 'app-x86_64-release.apk').write_bytes(b'apk')
            return type('Done', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        with patch('device_warden.store.run_adb', side_effect=adb) as run_adb, \
             patch('device_warden.obtainium.fetch_release', return_value=release), \
             patch('device_warden.store.subprocess.run', side_effect=host):
            result = self.store.obtainium_install([self.device], acknowledge=True)
        self.assertFalse(result['devices'][0]['ok'])
        self.assertIn('SHA-256', result['devices'][0]['error'])
        # A mismatched APK must never reach `adb install`.
        for call in run_adb.call_args_list:
            self.assertNotIn('install', call.args[0])

    def test_status_reports_installed_and_version(self):
        dump = ('Packages:\n  Package [dev.imranr.obtainium] (abc):\n'
                '    versionName=1.7.4\n')
        with patch('device_warden.store.run_adb') as run_adb:
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': dump, 'stderr': ''})()
            result = self.store.obtainium_status([self.device])
        self.assertTrue(result['devices'][0]['installed'])
        self.assertEqual(result['devices'][0]['version'], '1.7.4')

    def test_status_reports_a_missing_app(self):
        with patch('device_warden.store.run_adb') as run_adb:
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': 'Unable to find package', 'stderr': ''})()
            result = self.store.obtainium_status([self.device])
        self.assertFalse(result['devices'][0]['installed'])


class MultiDeviceRegisterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'db.sqlite')
        self.store.discover('List of devices attached\nserial-one device model:One\n'
                            'serial-two device model:Two\n')
        self.devices = [row['id'] for row in self.store.list()]
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')

    def test_register_reaches_every_device(self):
        with patch('device_warden.store.run_adb') as run_adb, \
             patch('device_warden.obtainium.check_releases',
                   return_value={'checked': True, 'has_release': True}):
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': 'Status: ok', 'stderr': ''})()
            result = self.store.catalog_register(self.devices, acknowledge=True)
        self.assertEqual(len(result['devices']), 2)
        self.assertTrue(all(device['ok'] for device in result['devices']))
        serials = {call.args[0][2] for call in run_adb.call_args_list}
        self.assertEqual(serials, {'serial-one', 'serial-two'})

    def test_one_unreachable_device_does_not_stop_the_others(self):
        self.store.discover('List of devices attached\nserial-one device model:One\n')
        with patch('device_warden.store.run_adb') as run_adb, \
             patch('device_warden.obtainium.check_releases',
                   return_value={'checked': True, 'has_release': True}):
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': 'Status: ok', 'stderr': ''})()
            result = self.store.catalog_register(self.devices, acknowledge=True)
        self.assertTrue(result['devices'][0]['ok'])
        self.assertFalse(result['devices'][1]['ok'])
        self.assertIn('接続されていません', result['devices'][1]['error'])

    def test_bulk_uses_one_import_link(self):
        with patch('device_warden.store.run_adb') as run_adb, \
             patch('device_warden.obtainium.check_releases',
                   return_value={'checked': True, 'has_release': True}):
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': 'Status: ok', 'stderr': ''})()
            result = self.store.catalog_register(self.devices[:1], acknowledge=True, bulk=True)
        device = result['devices'][0]
        self.assertTrue(device['bulk'])
        self.assertTrue(device['link'].startswith('obtainium://apps/'))
        # One link for the whole catalog, not one per app.
        self.assertEqual(run_adb.call_count, 1)

    def test_bulk_skips_a_repository_without_releases(self):
        self.store.catalog_add('com.other.app', 'https://github.com/owner/other')
        def releases(url, **kwargs):
            if 'other' in url:
                return {'checked': True, 'has_release': False, 'reason': 'リリースがありません'}
            return {'checked': True, 'has_release': True}
        with patch('device_warden.store.run_adb') as run_adb, \
             patch('device_warden.obtainium.check_releases', side_effect=releases):
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': 'Status: ok', 'stderr': ''})()
            result = self.store.catalog_register(self.devices[:1], acknowledge=True, bulk=True)
        skipped = [r for r in result['devices'][0]['results'] if r.get('skipped')]
        self.assertEqual([r['package'] for r in skipped], ['com.other.app'])


class ClientTimeoutTests(unittest.TestCase):
    """A slow install must not be reported as a connection failure."""

    def test_slow_actions_get_a_longer_timeout(self):
        from device_warden import client
        self.assertGreaterEqual(client.timeout_for('obtainium-install'), 600)
        self.assertGreaterEqual(client.timeout_for('apps-install'), 600)

    def test_fast_actions_keep_the_short_timeout(self):
        from device_warden import client
        self.assertEqual(client.timeout_for('list'), 15)
        self.assertEqual(client.timeout_for('claim'), 15)


class CatalogBrokerTests(unittest.TestCase):
    """The catalog must be reachable over the Broker, not only in-process."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / 'broker.token').write_text(TOKEN, encoding='utf-8')
        self.store = Store(self.home / 'warden.db')
        self.store.discover(DEVICES)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store, TOKEN))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        env = patch.dict(os.environ, {'DEVICE_WARDEN_HOME': str(self.home),
                                      'DEVICE_WARDEN_PORT': str(self.httpd.server_port)})
        env.start()
        self.addCleanup(env.stop)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def test_catalog_round_trip_over_the_broker(self):
        from device_warden.client import call
        call('catalog-add', package='com.example.app', url='https://github.com/owner/repo')
        rows = call('catalog')
        self.assertEqual(rows[0]['package'], 'com.example.app')
        link = call('catalog-import-link')
        self.assertTrue(link['link'].startswith('obtainium://apps/'))


if __name__ == '__main__':
    unittest.main()
