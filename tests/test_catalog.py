"""Tests for the Obtainium catalog.

Obtainium does the fetching and installing; Device Warden only stores which
package comes from which repository and turns that into the deep link Obtainium
understands. The link format is asserted against the shapes read from
Obtainium's `lib/pages/home.dart`.
"""

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
        payload = obtainium.import_payload([
            {'package': 'com.example.app', 'url': 'https://github.com/owner/repo', 'label': 'Example'}])
        app = payload['apps'][0]
        # App.fromJson requires id, url, author, name and additionalSettings.
        for field in ('id', 'url', 'author', 'name', 'additionalSettings'):
            self.assertIn(field, app)
        self.assertEqual(app['id'], 'com.example.app')
        self.assertEqual(app['name'], 'Example')
        self.assertEqual(app['author'], 'owner')
        self.assertEqual(json.loads(app['additionalSettings']), {})

    def test_import_link_is_percent_encoded(self):
        link = obtainium.import_link([{'package': 'com.example.app',
                                       'url': 'https://github.com/owner/repo'}])
        self.assertTrue(link.startswith('obtainium://apps/'))
        decoded = json.loads(unquote(link[len('obtainium://apps/'):]))
        self.assertEqual(decoded['apps'][0]['id'], 'com.example.app')

    def test_import_link_rejects_an_empty_catalog(self):
        with self.assertRaises(obtainium.CatalogError):
            obtainium.import_link([])

    def test_adb_args_open_the_link_in_obtainium(self):
        args = obtainium.adb_open_args('obtainium://add/https://github.com/o/r')
        self.assertEqual(args[:5], ['shell', 'am', 'start', '-W', '-a'])
        self.assertIn('obtainium://add/https://github.com/o/r', args)
        self.assertEqual(args[-1], obtainium.OBTAINIUM_PACKAGE)


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
        with self.assertRaises(WardenError):
            self.store.catalog_register(self.device, acknowledge=True)

    def test_register_opens_one_link_per_app(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        self.store.catalog_add('com.other.app', 'https://github.com/owner/other')
        with patch('device_warden.store.run_adb') as run_adb:
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': 'Starting: Intent', 'stderr': ''})()
            result = self.store.catalog_register(self.device, acknowledge=True)
        self.assertEqual(len(result['results']), 2)
        self.assertTrue(all(item['ok'] for item in result['results']))
        # Each call must address the device by serial and open an add link.
        for call in run_adb.call_args_list:
            command = call.args[0]
            self.assertEqual(command[:2], ['adb', '-s'])
            self.assertIn('obtainium://add/', ' '.join(command))

    def test_register_reports_a_failure_per_app(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        with patch('device_warden.store.run_adb') as run_adb:
            run_adb.return_value = type('Done', (), {
                'returncode': 1, 'stdout': '', 'stderr': 'Error: Activity not started'})()
            result = self.store.catalog_register(self.device, acknowledge=True)
        self.assertFalse(result['results'][0]['ok'])

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
        with patch('device_warden.store.run_adb') as run_adb:
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': dump, 'stderr': ''})()
            result = self.store.catalog_signatures(self.device)
        self.assertTrue(result['results'][0]['installed'])
        self.assertEqual(result['results'][0]['signatures'], ['b2d95fc0'])

    def test_signatures_report_an_uninstalled_app(self):
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        with patch('device_warden.store.run_adb') as run_adb:
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': 'Unable to find package: com.example.app',
                'stderr': ''})()
            result = self.store.catalog_signatures(self.device)
        self.assertFalse(result['results'][0]['installed'])
        self.assertEqual(result['results'][0]['signatures'], [])

    def test_signatures_never_uninstall_or_clear_data(self):
        """A mismatch is reported, never repaired: no uninstall command is run."""
        self.store.catalog_add('com.example.app', 'https://github.com/owner/repo')
        with patch('device_warden.store.run_adb') as run_adb:
            run_adb.return_value = type('Done', (), {
                'returncode': 0, 'stdout': '', 'stderr': ''})()
            self.store.catalog_signatures(self.device)
        for call in run_adb.call_args_list:
            command = ' '.join(call.args[0])
            self.assertNotIn('uninstall', command)
            self.assertNotIn('pm clear', command)


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
