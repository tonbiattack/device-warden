import unittest

from device_warden.icon import COLORS, image, state_for

FREE = {'connection': 'connected', 'kind': None, 'lease_state': None}


def device(**changes):
    return {**FREE, **changes}


class IconTests(unittest.TestCase):
    def test_renders_every_state_at_tray_sizes(self):
        for state in COLORS:
            for size in (16, 32, 64):
                self.assertEqual(image(state, size).size, (size, size))

    def test_state_priority(self):
        self.assertEqual(state_for([]), 'idle')
        self.assertEqual(state_for([device()]), 'free')
        self.assertEqual(state_for([device(kind='human')]), 'human')
        self.assertEqual(state_for([device(kind='ai')]), 'ai')
        self.assertEqual(state_for([device(kind='ai', lease_state='recovery_required')]), 'recovery')
        self.assertEqual(state_for([device(connection='disconnected')]), 'idle')
        self.assertEqual(state_for([device(connection='unknown')]), 'recovery')

    def test_recovery_outranks_an_available_device(self):
        self.assertEqual(state_for([device(), device(connection='disconnected', kind='ai',
                                                   lease_state='recovery_required')]), 'recovery')


if __name__ == '__main__':
    unittest.main()
