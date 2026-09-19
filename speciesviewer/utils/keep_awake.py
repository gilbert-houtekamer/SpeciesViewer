"""Keep Windows from idle-sleeping the machine during a long file operation.

Windows only knows an app is "busy" from user input or a handful of narrow
exceptions (video playback, some downloads) — a background worker thread
copying files is invisible to it, so the machine's own idle-sleep timer can
cut a multi-minute RAW backup to a slow SD card off mid-copy, same as if the
user had walked away. SetThreadExecutionState is the Win32 call apps use to
say "don't sleep while I'm doing this."

Windows only; a no-op everywhere else (this app also targets macOS — see
utils/config's cross-platform notes). macOS has no equivalent worth wiring up
yet: `caffeinate` is a separate process to launch, not a library call, and
nothing here has needed it so far.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager

# Win32 EXECUTION_STATE flags (winbase.h). ES_SYSTEM_REQUIRED resets the
# system idle timer; ES_DISPLAY_REQUIRED is deliberately NOT set, so the
# screen can still turn off during a long unattended copy — only the sleep
# that would abort the copy is suppressed.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


@contextmanager
def prevent_sleep():
    """While the block runs, ask Windows not to idle-sleep the system.

    Safe to use even if a call fails (e.g. running under an odd sandbox) —
    the operation it wraps still runs, just without the guarantee."""
    if sys.platform != "win32":
        yield
        return
    import ctypes
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
    except Exception:
        pass
    try:
        yield
    finally:
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        except Exception:
            pass
