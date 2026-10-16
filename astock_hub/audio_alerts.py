"""Sound-specific gates; muting audio does not disable visual reminders."""


def audio_event_enabled(settings: dict, event: str, guarded: bool = True) -> bool:
    if not settings["notifications"] or not settings["audioEnabled"]:
        return False
    if event in ("BUY", "SELL"):
        return (settings["sound"] or settings["speech"]) and settings[
            "audioBuy" if event == "BUY" else "audioSell"
        ] and (guarded or settings["audioObservation"])
    volume_conditions = {
        "confirm": "audioVolumeConfirm", "weak": "audioVolumeWeak",
        "sell_pressure": "audioVolumeSellPressure", "divergence": "audioVolumeDivergence",
    }
    key = volume_conditions.get(event)
    return bool(key and settings["volumeSpeech"] and settings[key])


class AudioGate:
    def __init__(self):
        self.last_played = float('-inf')

    def allow(self, settings: dict, event: str, guarded: bool, now: float) -> bool:
        if not audio_event_enabled(settings, event, guarded):
            return False
        if now - self.last_played < settings["audioCooldown"]:
            return False
        self.last_played = now
        return True
