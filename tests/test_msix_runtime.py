"""Optional smoke test for a locally built, non-installed MSIX development exe."""

import os
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from device_warden import client
from device_warden.store import WardenError


@unittest.skipUnless(os.name == 'nt' and os.environ.get('DEVICE_WARDEN_MSIX_DEV_EXE'),
                     'Set DEVICE_WARDEN_MSIX_DEV_EXE to test the built dev executable')
class MsixRuntimeSmokeTests(unittest.TestCase):
    def test_dev_broker_is_isolated_and_refuses_device_claims(self):
        executable = Path(os.environ['DEVICE_WARDEN_MSIX_DEV_EXE']).resolve(strict=True)
        self.assertEqual(executable.name.lower(), 'devicewardenmsixdev.exe')
        env = os.environ.copy()
        env['DEVICE_WARDEN_HOME'] = str(Path.home() / '.device-warden-msix-dev')
        env['DEVICE_WARDEN_PORT'] = '47632'
        with patch.dict(os.environ, env):
            try:
                client.call('list')
            except WardenError:
                pass
            else:
                self.fail('Port 47632 already has a Broker; refusing to interfere')
            process = subprocess.Popen([str(executable), '--broker'], env=env,
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
                with self.assertRaises(WardenError):
                    client.call('claim', kind='ai', tool='probe')
            finally:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                process.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
