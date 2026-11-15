import tkinter as tk
import unittest
from unittest.mock import patch

import device_warden.gui as gui


def fake_call(action, **kwargs):
    if action == 'list':
        return []
    if action == 'apps-matrix':
        return {'devices': [], 'apps': []}
    return None


class TabShortcutTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with patch.object(gui, 'call', side_effect=fake_call):
            self.app = gui.App(self.root)

    def test_ctrl_tab_cycles_forward(self):
        self.assertEqual(self.app.notebook.index('current'), 0)
        self.assertEqual(self.app.next_tab(), 'break')
        self.assertEqual(self.app.notebook.index('current'), 1)
        self.app.next_tab()
        self.assertEqual(self.app.notebook.index('current'), 0)

    def test_ctrl_shift_tab_cycles_backwards(self):
        self.assertEqual(self.app.previous_tab(), 'break')
        self.assertEqual(self.app.notebook.index('current'), 1)
        self.app.previous_tab()
        self.assertEqual(self.app.notebook.index('current'), 0)

    def test_switch_does_not_refresh_apps_twice(self):
        calls = []
        self.app.run = lambda action, **kwargs: calls.append(action)
        self.app.next_tab()
        self.assertEqual(calls, ['apps-matrix'])


if __name__ == '__main__':
    unittest.main()
