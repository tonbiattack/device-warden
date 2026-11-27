"""Identify the isolated MSIX development build.

The development package must never reach the production Broker, database or
Android devices, so detection lives in one dependency-free module instead of
being repeated as an executable-name check in the launcher and the tray.
"""

import ctypes
import os
import sys
from pathlib import Path

DEV_EXE = 'devicewardenmsixdev.exe'
DEV_HOME = '.device-warden-msix-dev'
DEV_PORT = '47632'
DEV_MARKER = '.dev'

ERROR_INSUFFICIENT_BUFFER = 122


def _read_identity(function):
    """Read a package identity string from a GetCurrentPackage*Name function.

    The API reports the required buffer size on the first call, then fills the
    buffer on the second; anything else means the process has no identity.
    """
    length = ctypes.c_uint32(0)
    if function(ctypes.byref(length), None) != ERROR_INSUFFICIENT_BUFFER:
        return None
    buffer = ctypes.create_unicode_buffer(length.value)
    if function(ctypes.byref(length), buffer) != 0:
        return None
    return buffer.value or None


def _package_name(getter):
    """Return a package identity string, or None when the process has none."""
    if os.name != 'nt':
        return None
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        return _read_identity(getattr(kernel32, getter))
    except (AttributeError, OSError):
        return None


def package_family_name():
    return _package_name('GetCurrentPackageFamilyName')


def has_package_identity():
    return package_family_name() is not None


def is_development_build():
    """True for the packaged or unpackaged development experiment only."""
    if not getattr(sys, 'frozen', False):
        return False
    if Path(sys.executable).name.lower() == DEV_EXE:
        return True
    # A renamed executable still behaves safely when the package identity says
    # it is the development package.
    return DEV_MARKER in (package_family_name() or '').lower()


def apply_isolation(environ=None):
    """Point a development build at its own state, port and blocked ADB."""
    environ = os.environ if environ is None else environ
    environ['DEVICE_WARDEN_HOME'] = str(Path.home() / DEV_HOME)
    environ['DEVICE_WARDEN_PORT'] = DEV_PORT
    environ['DEVICE_WARDEN_MSIX_DEV_NO_ADB'] = '1'
