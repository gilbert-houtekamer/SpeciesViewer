"""A quiet tick when navigation hits the end of the list. One job: bump().

Asked for 2026-09-29: while culling he kept pressing "next" at the last picture
without noticing he was there — nothing on screen changes when there is nothing
to go to. He does not like sounds, so this is deliberately small: a soft 60 ms
tone of our own (not the system beep, which is a full "ding" on Windows), at low
volume, and at most once per _MIN_GAP_S so a mouse wheel scrolled against the
end does not rattle. [Global] end_of_list_sound = no switches it off.

The tone is generated once into the app-data folder (a few KB WAV) and played
through QSoundEffect — QtMultimedia is already a dependency (video playback).
Anything that fails here fails silently: a missing cue must never break
navigation.
"""
import logging
import math
import struct
import time
import wave

logger = logging.getLogger("speciesviewer")

_MIN_GAP_S = 0.7
_VOLUME = 0.25
_effect = None
_last = 0.0


def _wav_path():
    from utils.cache_paths import app_data_dir
    p = app_data_dir() / "end_of_list.wav"
    if not p.exists():
        rate, dur, freq = 44100, 0.06, 660.0
        n = int(rate * dur)
        frames = bytearray()
        for i in range(n):
            t = i / rate
            env = min(1.0, i / (0.004 * rate)) * (1 - i / n) ** 2   # soft attack, quick fade
            frames += struct.pack("<h", int(12000 * env * math.sin(2 * math.pi * freq * t)))
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))
    return p


def bump():
    """Navigation hit an end: play the tick (unless off, or played just now)."""
    global _effect, _last
    try:
        from utils.config import get_setting
        if (get_setting("Global", "end_of_list_sound", fallback="yes") or "").lower() == "no":
            return
        now = time.monotonic()
        if now - _last < _MIN_GAP_S:
            return
        _last = now
        if _effect is None:
            from PyQt6.QtCore import QUrl
            from PyQt6.QtMultimedia import QSoundEffect
            _effect = QSoundEffect()
            _effect.setSource(QUrl.fromLocalFile(str(_wav_path())))
            _effect.setVolume(_VOLUME)
        _effect.play()
    except Exception as e:                      # noqa: BLE001 — a cue must never break navigation
        logger.debug(f"end-of-list cue unavailable: {e}")
