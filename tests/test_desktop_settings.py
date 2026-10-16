import json
import tempfile
import unittest
from types import SimpleNamespace
from datetime import datetime
from pathlib import Path

from astock_hub.desktop_settings import display_allowed, strategy_config, validate_settings
from astock_hub.intraday import CHINA_TZ


class DesktopSettingsTest(unittest.TestCase):
    def test_after_hours_visible_by_default(self):
        now = datetime(2026, 10, 15, 16, 31, tzinfo=CHINA_TZ)
        self.assertTrue(display_allowed(validate_settings({}), now))
        self.assertFalse(display_allowed(validate_settings({'displayMode': 'scheduled'}), now))

    def test_overnight_schedule_and_weekdays(self):
        settings = validate_settings({'displayMode': 'scheduled', 'displayStart': '22:00', 'displayEnd': '06:00'})
        self.assertTrue(display_allowed(settings, datetime(2026, 10, 16, 1, 0)))
        self.assertFalse(display_allowed(settings, datetime(2026, 10, 16, 12, 0)))
        settings = validate_settings({'weekdaysOnly': True})
        self.assertFalse(display_allowed(settings, datetime(2026, 10, 17, 12, 0)))

    def test_percentages_and_invalid_settings(self):
        config = strategy_config(validate_settings({'buybackDiscount': 1.2, 'maxCycles': 4}))
        self.assertEqual(config.buyback_discount, 0.012)
        self.assertEqual(config.max_cycles, 4)
        for value in ({'poll': 2}, {'sound': 'false'}, {'displayEnd': '99:00'}, {'sellStart': '15:00'}, {'quoteTimeout': float('nan')}):
            with self.assertRaises(ValueError):
                validate_settings(value)

    def test_real_windows_acknowledge_and_reload_settings(self):
        from tools.intraday_macd_alert import MacdDashboard, MacdTaskbarBar
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'bar.json'
            data = {'symbol': '002837', 'requestId': 'initial', 'dockMode': 'floating', 'windowX': 100, 'windowY': 100}
            config.write_text(json.dumps(data), encoding='utf-8')
            window = MacdTaskbarBar('002837', 5, config)
            try:
                window.root.update()
                window._reload_settings()
                status = json.loads(config.with_suffix('.status.json').read_text(encoding='utf-8'))
                self.assertTrue(status['visible'])
                self.assertEqual(status['requestId'], 'initial')
                data.update(symbol='600519', candidateMode='steady', buybackDiscount=1.2, poll=9, requestId='updated', orderBook=True)
                config.write_text(json.dumps(data), encoding='utf-8')
                window._reload_settings()
                self.assertEqual(window.code, '600519')
                self.assertEqual(window.poll_seconds, 9)
                self.assertEqual(window.candidate_mode, 'steady')
                self.assertEqual(window.strategy_config.buyback_discount, 0.012)
                self.assertTrue(window.order_book_enabled)
                window.root.withdraw()
                data['requestId'] = 'show-again'
                config.write_text(json.dumps(data), encoding='utf-8')
                window._reload_settings()
                self.assertTrue(window.root.winfo_viewable())
                window._start_drag(SimpleNamespace(widget=window.price_canvas, x_root=window.root.winfo_rootx()+10, y_root=window.root.winfo_rooty()+10))
                window._drag(SimpleNamespace(x_root=210, y_root=160))
                window.root.update()
                window._end_drag(SimpleNamespace())
                self.assertEqual(window.desktop_settings['dockMode'], 'floating')
                self.assertEqual(window.desktop_settings['windowX'], 200)
                self.assertEqual(window.desktop_settings['windowY'], 150)
                window._dock_inside_taskbar()
                window.root.update()
                self.assertEqual(window.root.winfo_rootx(), 200)
                self.assertEqual(json.loads(config.read_text(encoding='utf-8'))['windowY'], 150)
                window._restore_docking(SimpleNamespace(widget=window.price_canvas))
                self.assertEqual(window.desktop_settings['dockMode'], 'taskbar')
            finally:
                window.close()
            config = Path(directory) / 'gui.json'
            config.write_text(json.dumps({'requestId': 'gui', 'notifications': False}), encoding='utf-8')
            window = MacdDashboard('002837', 5, True, config)
            try:
                window.root.update()
                self.assertTrue(window.root.winfo_viewable())
                self.assertFalse(window.show_notifications)
            finally:
                window.close()
