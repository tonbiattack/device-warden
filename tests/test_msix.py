"""Guardrails for the isolated, unsigned MSIX feasibility build."""

import os
import runpy
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from device_warden import autostart
from device_warden.store import Store, WardenError, run_adb

ROOT = Path(__file__).resolve().parent.parent


class MsixGuardTests(unittest.TestCase):
    def test_manifest_template_leaves_the_executable_to_the_profile(self):
        root = ET.parse(ROOT / 'msix' / 'AppxManifest.xml').getroot()
        apps = root.findall('.//*[@Executable]')
        self.assertTrue(apps)
        # The development build and the Store build must not share an
        # executable name, because the name selects the isolated mode.
        self.assertTrue(all(app.attrib['Executable'] == '{{EXECUTABLE}}' for app in apps))
        startup = root.find('.//{*}StartupTask')
        self.assertEqual(startup.attrib['Enabled'], 'false')

    def test_packaged_store_build_is_not_the_development_mode(self):
        """A Store build must use the normal data folder and real ADB."""
        from device_warden import msixmode
        with patch.object(msixmode.sys, 'frozen', True, create=True), \
             patch.object(msixmode.sys, 'executable', r'C:\Program Files\WindowsApps\app\DeviceWarden\DeviceWarden.exe'), \
             patch.object(msixmode, 'package_family_name',
                          return_value='tonbiattack.DeviceWarden_e28ksxe3fc9w4'):
            self.assertFalse(msixmode.is_development_build())

    def test_development_launcher_forces_a_separate_home_and_port(self):
        executable = r'C:\test\DeviceWardenMsixDev.exe'
        with patch.object(sys, 'frozen', True, create=True), \
             patch.object(sys, 'executable', executable), \
             patch.dict(os.environ, {'DEVICE_WARDEN_HOME': 'unsafe', 'DEVICE_WARDEN_PORT': '47631'}):
            runpy.run_path(str(ROOT / 'launcher.py'), run_name='msix_guard_test')
            self.assertEqual(Path(os.environ['DEVICE_WARDEN_HOME']).name, '.device-warden-msix-dev')
            self.assertEqual(os.environ['DEVICE_WARDEN_PORT'], '47632')
            self.assertEqual(os.environ['DEVICE_WARDEN_MSIX_DEV_NO_ADB'], '1')

    def test_unpacked_launcher_does_not_override_runtime_configuration(self):
        with patch.object(sys, 'frozen', False, create=True), \
             patch.dict(os.environ, {'DEVICE_WARDEN_HOME': 'original', 'DEVICE_WARDEN_PORT': '1234'}):
            runpy.run_path(str(ROOT / 'launcher.py'), run_name='msix_guard_test')
            self.assertEqual(os.environ['DEVICE_WARDEN_HOME'], 'original')
            self.assertEqual(os.environ['DEVICE_WARDEN_PORT'], '1234')

    def test_development_guard_refuses_real_adb_and_device_claims(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'DEVICE_WARDEN_MSIX_DEV_NO_ADB': '1'}), \
             patch('device_warden.store.subprocess.run') as run:
            store = Store(Path(folder) / 'warden.db')
            with self.assertRaises(WardenError):
                store.discover('List of devices attached\nexample device model:Test\n')
            with self.assertRaises(WardenError):
                store.claim('ai', tool='test')
            with self.assertRaises(WardenError):
                run_adb(['adb', 'devices'], timeout=1)
            run.assert_not_called()

    def test_store_python_is_not_mistaken_for_frozen_msix(self):
        with patch.object(sys, 'frozen', False, create=True):
            self.assertFalse(autostart.has_package_identity())


if __name__ == '__main__':
    unittest.main()
