"""Render docs/screenshot.png from fake devices.

The README must not expose real device inventory, so this script feeds the GUI
invented rows and captures the window. Run it after changing the UI:

    python scripts/capture-screenshot.py
"""

import ctypes
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from PIL import ImageGrab

import device_warden.gui as gui

NOW = 1794700000.0
ROWS = [
    {
        'id': 'demo-1', 'display_name': 'Redmi Pad SE', 'alias': None, 'hidden': 0,
        'connection': 'connected', 'transport': 'usb', 'ai_allowed': 1, 'last_seen': NOW,
        'kind': None, 'tool': None, 'session_id': None, 'title': None, 'project': None,
        'workdir': None, 'lease_id': None, 'acquired_at': None, 'heartbeat_at': None,
        'lease_state': None,
    },
    {
        'id': 'demo-2', 'display_name': 'Pixel 8', 'alias': None, 'hidden': 0,
        'connection': 'connected', 'transport': 'wifi', 'ai_allowed': 1, 'last_seen': NOW - 4,
        'kind': 'ai', 'tool': 'OpenCode', 'session_id': 'ses_example', 'title': 'Fix camera flow',
        'project': 'C:\\work\\gallery-app', 'workdir': 'C:\\work\\gallery-app', 'lease_id': None,
        'acquired_at': NOW - 640, 'heartbeat_at': NOW - 12, 'lease_state': 'active',
    },
    {
        'id': 'demo-3', 'display_name': 'Galaxy S24', 'alias': None, 'hidden': 0,
        'connection': 'connected', 'transport': 'usb', 'ai_allowed': 0, 'last_seen': NOW - 1,
        'kind': 'human', 'tool': None, 'session_id': None, 'title': None, 'project': None,
        'workdir': None, 'lease_id': 'demo-lease', 'acquired_at': NOW - 180,
        'heartbeat_at': NOW - 180, 'lease_state': 'active',
    },
    {
        'id': 'demo-4', 'display_name': 'emulator-5554', 'alias': None, 'hidden': 0,
        'connection': 'disconnected', 'transport': 'emulator', 'ai_allowed': 1,
        'last_seen': NOW - 9000, 'kind': 'ai', 'tool': 'Codex', 'session_id': None,
        'title': None, 'project': None, 'workdir': None, 'lease_id': None,
        'acquired_at': NOW - 12000, 'heartbeat_at': NOW - 9100, 'lease_state': 'recovery_required',
    },
    {
        'id': 'demo-5', 'display_name': 'OPPO CPH2699', 'alias': None, 'hidden': 0,
        'connection': 'disconnected', 'transport': 'usb', 'ai_allowed': 1,
        'last_seen': NOW - 86000, 'kind': None, 'tool': None, 'session_id': None,
        'title': None, 'project': None, 'workdir': None, 'lease_id': None,
        'acquired_at': None, 'heartbeat_at': None, 'lease_state': None,
    },
]


def fake_call(action, **kwargs):
    if action == 'list':
        return ROWS
    return None


def _fake_stamp(value):
    """Show stable, obviously-fake times in the documentation screenshot."""
    if not value:
        return '不明'
    base = __import__('datetime').datetime(2026, 11, 15, 14, 32, 0)
    offset = int(value - (NOW - 600))
    return (base + __import__('datetime').timedelta(seconds=offset)).strftime('%Y-%m-%d %H:%M:%S')


def grab_window(hwnd):
    """Capture just this window, avoiding overlapping windows and desktop bleed."""
    from PIL import Image
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    hwnd_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    gdi32.SelectObject(mem_dc, bitmap)
    # PW_RENDERFULLCONTENT (0x2) includes content drawn with hardware acceleration.
    user32.PrintWindow(hwnd, mem_dc, 0x2)
    buffer = ctypes.create_string_buffer(width * height * 4)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                    ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                    ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                    ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                    ('biClrImportant', wintypes.DWORD)]

    info = BITMAPINFOHEADER()
    info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.biWidth, info.biHeight = width, -height
    info.biPlanes, info.biBitCount = 1, 32
    gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, hwnd_dc)
    return Image.frombuffer('RGBA', (width, height), buffer, 'raw', 'BGRA', 0, 1).convert('RGB')


def main():
    # Without DPI awareness the window rect and the grabbed pixels disagree at
    # 125%/150% scaling, which crops the capture.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()
    gui.call = fake_call
    gui.stamp = lambda value: _fake_stamp(value)
    root = tk.Tk()
    app = gui.App(root)
    root.geometry('1040x560+80+80')
    root.update()
    time.sleep(0.8)
    root.update()

    image = grab_window(int(root.wm_frame(), 16))
    target = Path(__file__).resolve().parent.parent / 'docs' / 'screenshot.png'
    target.parent.mkdir(exist_ok=True)
    image.save(target)
    root.destroy()
    print(f'wrote {target} ({image.width}x{image.height})')


if __name__ == '__main__':
    main()
