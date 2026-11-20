import tkinter as tk
import unittest
from unittest.mock import patch

import device_warden.gui as gui

NOW = 1794700000.0


def device(**overrides):
    row = {
        'id': 'demo-1', 'display_name': 'Pixel 8', 'alias': None, 'hidden': 0,
        'connection': 'connected', 'transport': 'usb', 'ai_allowed': 1,
        'last_seen': NOW, 'kind': None, 'tool': None, 'session_id': None,
        'title': None, 'project': None, 'workdir': None, 'lease_id': None,
        'acquired_at': None, 'heartbeat_at': None, 'lease_state': None,
    }
    row.update(overrides)
    return row


class LastUsedColumnTests(unittest.TestCase):
    """The device list must show whether a device has ever been leased."""

    def test_never_leased_device_says_unused(self):
        self.assertEqual(gui.last_used(device()), '未使用')

    def test_leased_device_shows_the_claim_time(self):
        value = gui.last_used(device(acquired_at=NOW))
        self.assertNotEqual(value, '未使用')
        self.assertIn('2026-', value)


class DeviceListTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def build(self, rows):
        def fake_call(action, **kwargs):
            if action == 'list':
                return rows
            if action == 'apps-matrix':
                return {'devices': [], 'apps': []}
            return None
        with patch.object(gui, 'call', side_effect=fake_call):
            return gui.App(self.root)

    def test_last_used_column_is_present(self):
        app = self.build([device()])
        self.assertIn('used', app.tree['columns'])
        self.assertEqual(app.tree.heading('used')['text'], '最終利用')

    def test_unused_device_renders_as_unused(self):
        app = self.build([device()])
        values = app.tree.item('demo-1')['values']
        self.assertIn('未使用', values)

    def test_leased_device_renders_its_claim_time(self):
        app = self.build([device(acquired_at=NOW, kind='ai', tool='Codex')])
        values = app.tree.item('demo-1')['values']
        self.assertFalse(any(value == '未使用' for value in values))


if __name__ == '__main__':
    unittest.main()
