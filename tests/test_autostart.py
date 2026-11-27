import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from device_warden import autostart
from device_warden.autostart import pythonw


class AutostartTests(unittest.TestCase):
    def test_pythonw_prefers_windowless_interpreter(self):
        with tempfile.TemporaryDirectory() as folder:
            python = Path(folder) / 'python.exe'
            pythonw_path = Path(folder) / 'pythonw.exe'
            python.write_text('')
            pythonw_path.write_text('')
            with patch.object(autostart.sys, 'executable', str(python)):
                self.assertEqual(pythonw(), pythonw_path)

    def test_pythonw_falls_back_to_current_interpreter(self):
        with tempfile.TemporaryDirectory() as folder:
            python = Path(folder) / 'python.exe'
            python.write_text('')
            with patch.object(autostart.sys, 'executable', str(python)):
                self.assertEqual(pythonw(), python)

    def test_frozen_build_uses_its_own_executable(self):
        with patch.object(autostart.sys, 'frozen', True, create=True), \
             patch.object(autostart.sys, 'executable', r'C:\Apps\DeviceWarden.exe'):
            self.assertEqual(str(pythonw()), r'C:\Apps\DeviceWarden.exe')
            self.assertEqual(str(autostart.packaged_exe()), r'C:\Apps\DeviceWarden.exe')

    def test_msix_identity_requires_a_frozen_packaged_process(self):
        from device_warden import msixmode
        with patch.object(msixmode, 'has_package_identity', return_value=True):
            with patch.object(autostart.sys, 'frozen', True, create=True):
                self.assertTrue(autostart.has_package_identity())
            with patch.object(autostart.sys, 'frozen', False, create=True):
                self.assertFalse(autostart.has_package_identity())

    def test_package_identity_parsing_handles_packaged_and_unpackaged(self):
        from device_warden import msixmode

        class FakeIdentity:
            def __init__(self, name=''):
                self.name = name
                self.calls = 0

            def __call__(self, length, buffer):
                self.calls += 1
                # byref() hands over a CArgObject; the real API writes through it.
                target = getattr(length, '_obj', length)
                if buffer is None:
                    if not self.name:
                        return 15700  # APPMODEL_ERROR_NO_PACKAGE
                    target.value = len(self.name) + 1
                    return msixmode.ERROR_INSUFFICIENT_BUFFER
                buffer.value = self.name
                return 0

        self.assertEqual(msixmode._read_identity(FakeIdentity('Family.Name_x')), 'Family.Name_x')
        self.assertIsNone(msixmode._read_identity(FakeIdentity()))

    def test_development_build_is_identified_by_package_identity(self):
        from device_warden import msixmode
        with patch.object(msixmode.sys, 'frozen', True, create=True), \
             patch.object(msixmode.sys, 'executable', r'C:\Apps\renamed.exe'), \
             patch.object(msixmode, 'package_family_name',
                          return_value='Tonbiattack.DeviceWarden.Dev_f66yps8pfc6q8'):
            self.assertTrue(msixmode.is_development_build())

    def test_msix_never_writes_or_removes_a_startup_shortcut(self):
        with patch.object(autostart, 'has_package_identity', return_value=True), \
             patch.object(autostart, 'shortcut_path') as shortcut:
            with self.assertRaises(autostart.WardenError):
                autostart.install()
            with self.assertRaises(autostart.WardenError):
                autostart.remove()
            with self.assertRaises(autostart.WardenError):
                autostart.status()
            shortcut.assert_not_called()

    def test_install_uses_packaged_exe_when_present(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / 'DeviceWarden.exe'
            exe.write_bytes(b'')
            with patch.object(autostart, 'packaged_exe', return_value=exe), \
                 patch.object(autostart, 'startup_folder', return_value=Path(folder)), \
                 patch.object(autostart.subprocess, 'run') as run:
                target = Path(folder) / 'Device Warden.lnk'
                run.return_value.returncode = 0
                run.side_effect = lambda *a, **k: (target.write_bytes(b''), run.return_value)[1]
                autostart.install()
                command = run.call_args.args[0][-1]
                self.assertIn(str(exe), command)
                self.assertIn("$s.Arguments = ''", command)

    def test_install_and_remove_shortcut(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'APPDATA': folder}, clear=False), \
                 patch.object(autostart, 'startup_folder',
                              return_value=Path(folder) / 'Startup'), \
                 patch.object(autostart, 'packaged_exe', return_value=None):
                with patch.object(autostart.subprocess, 'run') as run:
                    run.return_value.returncode = 0
                    target = autostart.shortcut_path()

                    def create(*args, **kwargs):
                        target.write_bytes(b'')
                        return run.return_value
                    run.side_effect = create
                    self.assertFalse(autostart.status())
                    self.assertEqual(autostart.install(), target)
                    self.assertTrue(autostart.status())
                    command = run.call_args.args[0][-1]
                    self.assertIn('-m device_warden.tray', command)
                    self.assertIn('Device Warden.lnk', command)
                    autostart.remove()
                    self.assertFalse(autostart.status())

    def test_install_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(autostart, 'startup_folder', return_value=Path(folder)), \
                 patch.object(autostart.subprocess, 'run') as run:
                run.return_value.returncode = 1
                with self.assertRaises(Exception):
                    autostart.install()

    def test_notify_existing_instance_when_none_is_running(self):
        name = f'Local\\DeviceWardenShowTest-{os.getpid()}'
        with patch.object(autostart, 'SHOW_EVENT_NAME', name):
            self.assertFalse(autostart.notify_existing_instance())

    def test_second_launch_signals_running_instance(self):
        import threading
        name = f'Local\\DeviceWardenShowTest-{os.getpid()}'
        with patch.object(autostart, 'SHOW_EVENT_NAME', name):
            event = autostart.create_show_event()
            self.assertTrue(event)
            shown = threading.Event()
            thread = threading.Thread(target=autostart.watch_show_events,
                                      args=(event, shown.set), daemon=True)
            try:
                thread.start()
                self.assertTrue(autostart.notify_existing_instance())
                self.assertTrue(shown.wait(5))
            finally:
                import ctypes
                ctypes.WinDLL('kernel32').SetEvent(event)

    def test_frozen_broker_is_not_given_a_source_checkout_as_cwd(self):
        """A packaged app cannot rely on the source directory or its extracted files."""
        with patch.object(autostart, 'broker_running', return_value=False), \
             patch.object(autostart.subprocess, 'Popen') as popen, \
             patch.object(autostart.sys, 'frozen', True, create=True), \
             patch.object(autostart.sys, 'executable', r'C:\Program Files\WindowsApps\app\DeviceWarden.exe'):
            self.assertFalse(autostart.ensure_broker(timeout=0))
            command = popen.call_args.args[0]
            self.assertEqual(command, [r'C:\Program Files\WindowsApps\app\DeviceWarden.exe', '--broker'])
            self.assertIsNone(popen.call_args.kwargs['cwd'])

    def test_source_broker_keeps_the_repository_as_cwd(self):
        with patch.object(autostart, 'broker_running', return_value=False), \
             patch.object(autostart.subprocess, 'Popen') as popen, \
             patch.object(autostart.sys, 'frozen', False, create=True):
            self.assertFalse(autostart.ensure_broker(timeout=0))
            self.assertEqual(Path(popen.call_args.kwargs['cwd']), Path(autostart.__file__).resolve().parent.parent)

    def test_broker_running_uses_client(self):
        with patch.object(autostart, 'call', return_value=[]):
            self.assertTrue(autostart.broker_running())
        with patch.object(autostart, 'call', side_effect=autostart.WardenError('x')):
            self.assertFalse(autostart.broker_running())

    def test_second_instance_is_rejected(self):
        name = f'Local\\DeviceWardenTest-{os.getpid()}'
        first = autostart.acquire_single_instance(name)
        try:
            self.assertIsNotNone(first)
            self.assertIsNone(autostart.acquire_single_instance(name))
        finally:
            autostart.release_single_instance(first)
        second = autostart.acquire_single_instance(name)
        self.assertIsNotNone(second)
        autostart.release_single_instance(second)


if __name__ == '__main__':
    unittest.main()
