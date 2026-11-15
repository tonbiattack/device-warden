"""Render docs/apps-screenshot.png from fictional data (Issue #19).

The README must not expose real package names or versions, so the matrix is
rendered with invented devices and apps.

    python scripts/capture-apps-screenshot.py
"""

import ctypes
import tkinter as tk
from pathlib import Path

from device_warden.gui import App

NOW = 1794700000.0
DEVICES = [
    {'id': 'demo-1', 'display_name': 'Pixel 8', 'connection': 'connected'},
    {'id': 'demo-2', 'display_name': 'Redmi Pad SE', 'connection': 'connected'},
    {'id': 'demo-3', 'display_name': 'Galaxy S24', 'connection': 'disconnected'},
]
MATRIX = {
    'devices': DEVICES,
    'apps': [
        {'package': 'com.example.gallery', 'label': 'Gallery', 'baseline': 142,
         'cells': {
             'demo-1': {'device_id': 'demo-1', 'version_name': '1.4.2', 'version_code': 142,
                        'status': 'latest', 'error': None, 'scanned_at': NOW},
             'demo-2': {'device_id': 'demo-2', 'version_name': '1.4.2', 'version_code': 142,
                        'status': 'latest', 'error': None, 'scanned_at': NOW},
             'demo-3': {'device_id': 'demo-3', 'version_name': '1.3.9', 'version_code': 139,
                        'status': 'old', 'error': None, 'scanned_at': NOW - 9000},
         }},
        {'package': 'com.example.player', 'label': 'Player', 'baseline': 58,
         'cells': {
             'demo-1': {'device_id': 'demo-1', 'version_name': '0.5.8', 'version_code': 58,
                        'status': 'latest', 'error': None, 'scanned_at': NOW},
             'demo-2': {'device_id': 'demo-2', 'version_name': None, 'version_code': None,
                        'status': 'unknown', 'error': None, 'scanned_at': NOW},
             'demo-3': None,
         }},
    ],
}


def fake_run(action, **kwargs):
    if action in ('apps-matrix', 'apps-scan'):
        return MATRIX
    if action == 'list':
        # Keep the device tab populated so the empty-state message does not show.
        return [{'id': device['id'], 'display_name': device['display_name'],
                 'connection': device['connection'], 'transport': 'usb',
                 'kind': None, 'tool': None, 'title': None, 'session_id': None,
                 'lease_state': None, 'ai_allowed': 1, 'hidden': 0, 'last_seen': NOW}
                for device in DEVICES]
    return None


def grab(hwnd):
    from PIL import Image
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    hwnd_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    gdi32.SelectObject(mem_dc, bitmap)
    user32.PrintWindow(hwnd, mem_dc, 0x2)
    buffer = ctypes.create_string_buffer(width * height * 4)

    class BIH(ctypes.Structure):
        _fields_ = [('s', wintypes.DWORD), ('w', wintypes.LONG), ('h', wintypes.LONG),
                    ('p', wintypes.WORD), ('b', wintypes.WORD), ('c', wintypes.DWORD),
                    ('si', wintypes.DWORD), ('x', wintypes.LONG), ('y', wintypes.LONG),
                    ('cu', wintypes.DWORD), ('ci', wintypes.DWORD)]

    info = BIH()
    info.s, info.w, info.h, info.p, info.b = ctypes.sizeof(BIH), width, -height, 1, 32
    gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, hwnd_dc)
    return Image.frombuffer('RGBA', (width, height), buffer, 'raw', 'BGRA', 0, 1).convert('RGB')


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()
    import device_warden.gui as gui
    gui.call = fake_run
    root = tk.Tk()
    app = App(root)
    root.geometry('1100x470+120+120')
    root.update()
    app.notebook.select(1)
    root.update()
    target = Path(__file__).resolve().parent.parent / 'docs' / 'apps-screenshot.png'
    grab(int(root.wm_frame(), 16)).save(target)
    root.destroy()
    print(f'wrote {target}')


if __name__ == '__main__':
    main()
