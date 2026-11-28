"""Render the Microsoft Store screenshots from fake rows.

The Store listing must not expose a real device inventory, so this reuses the
same invented rows as docs/screenshot.png and captures the real GUI at a size
that satisfies the Store requirement (at least 1366x768). Run after UI changes:

    python scripts/capture-store-screenshots.py
"""

import ctypes
import importlib.util
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

from PIL import ImageGrab

import device_warden.gui as gui

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'docs' / 'store' / 'screenshots'


def grab_client(root):
    """Capture only the client area, so the window frame adds no black border."""
    user32 = ctypes.windll.user32
    hwnd = root.winfo_id()
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return ImageGrab.grab(
        bbox=(origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom),
        all_screens=True,
    ).convert('RGB')


def load_demo_module():
    """Load capture-screenshot.py, whose file name cannot be imported directly."""
    path = Path(__file__).with_name('capture-screenshot.py')
    spec = importlib.util.spec_from_file_location('capture_screenshot', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = load_demo_module()
ROWS = demo.ROWS

MATRIX = {
    'devices': [
        {'id': 'demo-1', 'display_name': 'Redmi Pad SE', 'connection': 'connected'},
        {'id': 'demo-2', 'display_name': 'Pixel 8', 'connection': 'connected'},
        {'id': 'demo-4', 'display_name': 'emulator-5554', 'connection': 'disconnected'},
    ],
    'apps': [
        {'package': 'com.example.obtainium', 'label': 'Obtainium', 'cells': {
            'demo-1': {'version_name': '1.1.40', 'version_code': 11040},
            'demo-2': {'version_name': '1.1.40', 'version_code': 11040},
            'demo-4': None}},
        {'package': 'com.example.photoviewer', 'label': '写真ビューア', 'cells': {
            'demo-1': {'version_name': '0.2.0', 'version_code': 20},
            'demo-2': {'version_name': '0.1.0', 'version_code': 10},
            'demo-4': None}},
        {'package': 'com.example.camera', 'label': 'カメラ', 'cells': {
            'demo-1': {'version_name': '3.8.1', 'version_code': 38100},
            'demo-2': {'version_name': '3.8.1', 'version_code': 38100},
            'demo-4': {'version_name': '3.2.0', 'version_code': 32000}}},
        {'package': 'com.example.files', 'label': 'Files', 'cells': {
            'demo-1': {'error': '取得失敗'},
            'demo-2': {'version_name': '15.0', 'version_code': 15000},
            'demo-4': None}},
        {'package': 'com.example.maps', 'label': 'Maps', 'cells': {
            'demo-1': {'version_name': '11.0', 'version_code': 11000},
            'demo-2': {'version_name': '11.0', 'version_code': 11000},
            'demo-4': None}},
    ],
}


def fake_call(action, **kwargs):
    if action == 'list':
        return ROWS
    if action == 'apps-matrix':
        return MATRIX
    return None


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()
    gui.call = fake_call
    gui.stamp = lambda value: demo._fake_stamp(value)

    OUT.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    app = gui.App(root)
    root.geometry('1400x800+80+60')
    # Keep the window above others so the screen capture cannot include them.
    root.attributes('-topmost', True)
    root.lift()
    root.focus_force()
    root.update()
    time.sleep(0.8)
    root.update()

    devices_image = grab_client(root)
    devices = OUT / '01-devices.png'
    devices_image.save(devices)
    print(f'wrote {devices} ({devices_image.width}x{devices_image.height})')

    app.notebook.select(1)
    root.update()
    time.sleep(0.5)
    root.update()
    apps_image = grab_client(root)
    apps = OUT / '02-apps.png'
    apps_image.save(apps)
    print(f'wrote {apps} ({apps_image.width}x{apps_image.height})')

    root.attributes('-topmost', False)
    root.destroy()


if __name__ == '__main__':
    main()
