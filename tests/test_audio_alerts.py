import unittest

from astock_hub.audio_alerts import AudioGate, audio_event_enabled
from astock_hub.desktop_settings import validate_settings


class AudioAlertsTest(unittest.TestCase):
    def test_master_switch_mutes_every_audio_event_without_disabling_visuals(self):
        settings = validate_settings({'audioEnabled': False})
        self.assertTrue(settings['notifications'])
        for event in ('BUY', 'SELL', 'confirm', 'weak', 'sell_pressure', 'divergence'):
            self.assertFalse(audio_event_enabled(settings, event))

    def test_direction_and_observation_filters_both_apply(self):
        settings = validate_settings({'audioBuy': False, 'audioObservation': False})
        self.assertFalse(audio_event_enabled(settings, 'BUY', True))
        self.assertFalse(audio_event_enabled(settings, 'SELL', False))
        self.assertTrue(audio_event_enabled(settings, 'SELL', True))

    def test_volume_conditions_are_independent_of_buy_sell_voice(self):
        settings = validate_settings({'speech': False, 'audioVolumeWeak': False})
        self.assertFalse(audio_event_enabled(settings, 'weak'))
        self.assertTrue(audio_event_enabled(settings, 'sell_pressure'))
        settings['volumeSpeech'] = False
        self.assertFalse(audio_event_enabled(settings, 'sell_pressure'))

    def test_channels_disabled_do_not_consume_cooldown(self):
        gate = AudioGate()
        settings = validate_settings({'sound': False, 'speech': False})
        self.assertFalse(gate.allow(settings, 'BUY', True, 100))
        settings['speech'] = True
        self.assertTrue(gate.allow(settings, 'BUY', True, 101))

    def test_cooldown_suppresses_overlapping_events_and_can_be_disabled(self):
        gate = AudioGate()
        settings = validate_settings({'audioCooldown': 30})
        self.assertTrue(gate.allow(settings, 'SELL', True, 100))
        self.assertFalse(gate.allow(settings, 'confirm', True, 101))
        self.assertTrue(gate.allow(settings, 'BUY', True, 130))
        settings['audioCooldown'] = 0
        self.assertTrue(gate.allow(settings, 'BUY', True, 130))

    def test_disabled_notifications_and_unknown_events_never_make_sound(self):
        self.assertFalse(audio_event_enabled(validate_settings({'notifications': False}), 'SELL'))
        self.assertFalse(audio_event_enabled(validate_settings({}), 'normal'))
