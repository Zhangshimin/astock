import unittest

from astock_hub.taskbar_position import calculate_taskbar_position


class TaskbarPositionTest(unittest.TestCase):
    def test_bottom_taskbar_tracks_notification_area(self):
        taskbar, monitor = (0, 1032, 1920, 1080), (0, 0, 1920, 1080)
        self.assertEqual(calculate_taskbar_position(taskbar, (1618, 1032, 1920, 1080), monitor, 565, 32), (1049, 1040))
        self.assertEqual(calculate_taskbar_position(taskbar, (1500, 1032, 1920, 1080), monitor, 565, 32), (931, 1040))

    def test_negative_monitor_coordinates_are_preserved(self):
        self.assertEqual(calculate_taskbar_position((-1920, 1032, 0, 1080), (-300, 1032, 0, 1080), (-1920, 0, 0, 1080), 565, 32), (-869, 1040))

    def test_top_taskbar_adjacent_mode_stays_inside_screen(self):
        self.assertEqual(calculate_taskbar_position((0, 0, 1920, 48), None, (0, 0, 1920, 1080), 565, 32, 'above'), (1351, 52))

    def test_missing_or_stale_notification_falls_back_to_taskbar_end(self):
        for tray in (None, (2000, 1032, 2100, 1080), (1600, 0, 1920, 48)):
            self.assertEqual(calculate_taskbar_position((0, 1032, 1920, 1080), tray, (0, 0, 1920, 1080), 565, 32), (1351, 1040))

    def test_vertical_taskbar_extends_inward(self):
        self.assertEqual(calculate_taskbar_position((1872, 0, 1920, 1080), None, (0, 0, 1920, 1080), 565, 32), (1355, 1044))
