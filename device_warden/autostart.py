"""Start the Broker and tray together, and register the app for Windows sign-in."""

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

from . import msixmode
from .client import call
from .msixmode import is_development_build
from .server import data_dir
from .store import WardenError

# Keep the experimental package from sharing the production tray's window.
_DEV_MODE = is_development_build()
MUTEX_NAME = 'Local\\DeviceWardenTrayMsixDev' if _DEV_MODE else 'Local\\DeviceWardenTray'
SHOW_EVENT_NAME = 'Local\\DeviceWardenShowMsixDev' if _DEV_MODE else 'Local\\DeviceWardenShow'
ERROR_ALREADY_EXISTS = 183


def pythonw():
    """Return the windowless interpreter, or the packaged executable when bundled."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable)
    executable = Path(sys.executable)
    windowless = executable.with_name('pythonw.exe')
    return windowless if windowless.exists() else executable


def packaged_exe():
    """Prefer a built DeviceWarden.exe; it carries the app icon instead of Python's."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable)
    candidate = Path(__file__).resolve().parent.parent / 'dist' / 'DeviceWarden.exe'
    return candidate if candidate.exists() else None


def hidden_creation_flags():
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def broker_running():
    try:
        call('list')
        return True
    except WardenError:
        return False


def ensure_broker(timeout=15):
    """Start the Broker without a console window if it is not already serving."""
    if broker_running():
        return False
    if getattr(sys, 'frozen', False):
        command = [sys.executable, '--broker']
    else:
        command = [str(pythonw()), '-m', 'device_warden.server']
    subprocess.Popen(command,
                     creationflags=hidden_creation_flags(), close_fds=True,
                     cwd=str(Path(__file__).resolve().parent.parent))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if broker_running():
            return True
        time.sleep(0.5)
    return False


def acquire_single_instance(name=MUTEX_NAME):
    """Keep a second tray process from duplicating windows; return the mutex handle or None."""
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    return handle


def release_single_instance(handle):
    if handle:
        ctypes.WinDLL('kernel32').CloseHandle(handle)


def notify_existing_instance():
    """Ask a running tray to show its window; return False when none is running."""
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    event = kernel32.OpenEventW(0x0002, False, SHOW_EVENT_NAME)  # EVENT_MODIFY_STATE
    if not event:
        return False
    try:
        return bool(kernel32.SetEvent(event))
    finally:
        kernel32.CloseHandle(event)


def create_show_event():
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    return kernel32.CreateEventW(None, False, False, SHOW_EVENT_NAME)


def watch_show_events(event, show):
    """Wait for later launches to request the window, and show it each time."""
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    try:
        while kernel32.WaitForSingleObject(event, 0xFFFFFFFF) == 0:
            show()
    finally:
        kernel32.CloseHandle(event)


def startup_folder():
    return Path(os.environ['APPDATA']) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Startup'


def shortcut_path():
    return startup_folder() / 'Device Warden.lnk'


def has_package_identity():
    """Detect MSIX identity without confusing a source install for a package."""
    # Microsoft Store Python has a package identity of its own, but source
    # installs must keep their normal shortcut behavior; only a frozen app can
    # be the MSIX build this check is about.
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return False
    return msixmode.has_package_identity()


def install():
    if has_package_identity():
        raise WardenError('MSIX版の自動起動は Windows のスタートアップ設定で管理してください。')
    from .icon import icon_path
    target = packaged_exe() or pythonw()
    bundled = getattr(sys, 'frozen', False) or target.name == 'DeviceWarden.exe'
    working = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
    arguments = '' if bundled else '-m device_warden.tray'
    script = (
        "$w = New-Object -ComObject WScript.Shell; "
        f"$s = $w.CreateShortcut('{str(shortcut_path()).replace(chr(39), chr(39) * 2)}'); "
        f"$s.TargetPath = '{str(target).replace(chr(39), chr(39) * 2)}'; "
        f"$s.Arguments = '{arguments}'; "
        f"$s.WorkingDirectory = '{str(working).replace(chr(39), chr(39) * 2)}'; "
        f"$s.IconLocation = '{str(icon_path()).replace(chr(39), chr(39) * 2)},0'; "
        "$s.Description = 'Device Warden'; $s.Save()"
    )
    startup_folder().mkdir(parents=True, exist_ok=True)
    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not shortcut_path().exists():
        raise WardenError('スタートアップへの登録に失敗しました。')
    return shortcut_path()


def remove():
    if has_package_identity():
        raise WardenError('MSIX版の自動起動は Windows のスタートアップ設定で管理してください。')
    try:
        shortcut_path().unlink()
    except FileNotFoundError:
        pass
    return shortcut_path()


def status():
    if has_package_identity():
        raise WardenError('MSIX版の自動起動の状態は Windows のスタートアップ設定で確認してください。')
    return shortcut_path().exists()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog='device-warden autostart')
    parser.add_argument('action', choices=('install', 'remove', 'status'))
    args = parser.parse_args(argv)
    try:
        if args.action == 'install':
            print(f'スタートアップに登録しました: {install()}')
        elif args.action == 'remove':
            print(f'スタートアップから削除しました: {remove()}')
        else:
            print('登録済み' if status() else '未登録')
    except WardenError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
