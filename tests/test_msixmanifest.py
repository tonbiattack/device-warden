"""Tests for rendering the MSIX manifest from the development or Store profile."""

import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from device_warden import msixmanifest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / 'msix' / 'AppxManifest.xml').read_text(encoding='utf-8')


def rendered(profile, **options):
    values = msixmanifest.values_for(profile, ROOT, **options)
    return ET.fromstring(msixmanifest.render(TEMPLATE, values))


class ProfileTests(unittest.TestCase):
    def test_development_profile_is_isolated_and_not_submittable(self):
        root = rendered('dev')
        identity = root.find('{*}Identity')
        self.assertEqual(identity.attrib['Name'], 'Tonbiattack.DeviceWarden.Dev')
        self.assertEqual(identity.attrib['Publisher'], 'CN=Device Warden Development')
        self.assertTrue(identity.attrib['Version'].count('.') == 3)
        self.assertEqual(root.find('{*}Properties/{*}DisplayName').text,
                         'Device Warden (Development)')

    def test_store_profile_uses_the_account_wide_publisher(self):
        root = rendered('store', identity_name='tonbiattack.DeviceWarden')
        identity = root.find('{*}Identity')
        self.assertEqual(identity.attrib['Name'], 'tonbiattack.DeviceWarden')
        self.assertEqual(identity.attrib['Publisher'], 'CN=2F14A66E-02DF-43E2-93E0-5C5900B38D2E')
        properties = root.find('{*}Properties')
        self.assertEqual(properties.find('{*}PublisherDisplayName').text, 'tonbiattack')
        # The publisher display name must not become the app's display name.
        self.assertEqual(properties.find('{*}DisplayName').text, 'Device Warden')

    def test_store_profile_refuses_to_guess_the_reserved_name(self):
        with self.assertRaises(ValueError) as raised:
            msixmanifest.values_for('store', ROOT)
        self.assertIn('Identity Name', str(raised.exception))

    def test_store_profile_keeps_the_packaged_guards(self):
        root = rendered('store', identity_name='tonbiattack.DeviceWarden')
        applications = root.findall('.//*[@Executable]')
        self.assertTrue(all(app.attrib['Executable'] == 'DeviceWarden\\DeviceWarden.exe'
                            for app in applications))
        self.assertEqual(root.find('.//{*}StartupTask').attrib['Enabled'], 'false')

    def test_store_payload_does_not_use_the_development_executable_name(self):
        """The development name triggers the isolated mode, so the Store app must not use it."""
        self.assertEqual(msixmanifest.payload_name('dev'), 'DeviceWardenMsixDev')
        self.assertEqual(msixmanifest.payload_name('store'), 'DeviceWarden')
        root = rendered('store', identity_name='tonbiattack.DeviceWarden')
        for application in root.findall('.//*[@Executable]'):
            self.assertNotIn('MsixDev', application.attrib['Executable'])

    def test_version_matches_pyproject_as_four_numbers(self):
        version = msixmanifest.project_version(ROOT)
        self.assertRegex(version, r'^\d+\.\d+\.\d+\.\d+$')
        declared = re.search(r'^version\s*=\s*"([^"]+)"',
                             (ROOT / 'pyproject.toml').read_text(encoding='utf-8'),
                             re.MULTILINE).group(1)
        self.assertTrue(version.startswith(declared))

    def test_unreplaced_token_is_an_error(self):
        values = msixmanifest.values_for('dev', ROOT)
        broken = TEMPLATE.replace('{{VERSION}}', '{{DIFFERENT_TOKEN}}')
        with self.assertRaises(ValueError) as raised:
            msixmanifest.render(broken, values)
        self.assertIn('DIFFERENT_TOKEN', str(raised.exception))

    def test_explicit_values_override_the_profile_defaults(self):
        values = msixmanifest.values_for('store', ROOT, identity_name='Example.App',
                                         publisher='CN=Example', publisher_display_name='Example',
                                         display_name='Example', version='1.2.3.4')
        self.assertEqual(values['PUBLISHER'], 'CN=Example')
        self.assertEqual(values['VERSION'], '1.2.3.4')


if __name__ == '__main__':
    unittest.main()
