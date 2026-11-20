"""Keep console output readable on Windows.

Python picks the ANSI code page (cp932 on a Japanese Windows) for a redirected
stdout, while the console itself may be running UTF-8. The result is mojibake
for every Japanese message. Match the console's code page when one is attached,
and fall back to UTF-8 when output is redirected to a file or a pipe.
"""

import ctypes
import sys


def console_encoding():
    """Return the code page the attached console uses, or None when there is none."""
    try:
        code_page = ctypes.windll.kernel32.GetConsoleOutputCP()
    except (AttributeError, OSError):
        return None
    if not code_page:
        return None
    # 65001 is UTF-8; 0 means no console is attached.
    return 'utf-8' if code_page == 65001 else f'cp{code_page}'


def configure(stream=None):
    """Re-encode a text stream so Japanese output survives the console."""
    stream = stream or sys.stdout
    encoding = console_encoding() or 'utf-8'
    try:
        stream.reconfigure(encoding=encoding, errors='replace')
    except (AttributeError, ValueError):
        # Not a TextIOWrapper (a test double, for example); leave it alone.
        pass
    return stream
