"""Optional smoke test for a locally built, non-installed MSIX development exe.

Run it with DEVICE_WARDEN_MSIX_DEV_EXE pointing at a build produced by
scripts/build-msix.ps1. It starts the development Broker next to a normally
running one, so it also checks that the production Broker is left alone.
"""

import os
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from device_warden import client
from device_warden.store import WardenError

DEV_HOME = Path.home() / '.device-warden-msix-dev'
DEV_PORT = '47632'


@unittest.skipUnless(os.name == 'nt' and os.environ.get('DEVICE_WARDEN_MSIX_DEV_EXE'),
                     'Set DEVICE_WARDEN_MSIX_DEV_EXE to test the built dev executable')
class MsixRuntimeSmokeTests(unittest.TestCase):
    def test_launcher_isolation_holds_without_environment_overrides(self):
        executable = Path(os.environ['DEVICE_WARDEN_MSIX_DEV_EXE']).resolve(strict=True)
        self.assertEqual(executable.name.lower(), 'devicewardenmsixdev.exe')
        # Start the child without any DEVICE_WARDEN_* variable: the launcher
        # itself must choose its own home, port and blocked ADB.
        child_env = {key: value for key, value in os.environ.items()
                     if not key.startswith('DEVICE_WARDEN_')}
        with patch.dict(os.environ, {'DEVICE_WARDEN_HOME': str(DEV_HOME),
                                     'DEVICE_WARDEN_PORT': DEV_PORT}):
            try:
                client.call('list')
            except WardenError:
                pass
            else:
                self.fail('Port 47632 already has a Broker; refusing to interfere')
            process = subprocess.Popen([str(executable), '--broker'], env=child_env,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        self.fail('Dev Broker exited unexpectedly')
                    try:
                        rows = client.call('list')
                        break
                    except WardenError:
                        if time.monotonic() >= deadline:
                            self.fail('Dev Broker did not respond')
                        time.sleep(0.25)
                self.assertIsInstance(rows, list)
                # The development store starts empty even if the normal one has
                # devices, which proves the two databases stay separate.
                self.assertEqual(rows, [])
                self.assertTrue((DEV_HOME / 'warden.db').exists())
                with self.assertRaises(WardenError):
                    client.call('claim', kind='ai', tool='probe')
            finally:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                process.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
