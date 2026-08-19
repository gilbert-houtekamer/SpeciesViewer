#!/usr/bin/env python3

# Free-space guard.
#
# ONE job: answer "is there room on this disk for the N bytes I am about to
# write?" — before the write starts, not after it has half-finished.
#
# Invariant: check_free_space() returns None when the work may proceed, and a
# ready-to-show sentence when it may not. It never raises on a path it cannot
# stat (an unplugged drive, a permission wall) — an unknown disk is treated as
# "proceed", because refusing to copy on a stat failure would be a worse bug
# than the one this module exists to catch.
#
# Callers: batch/sv0ReadSDCard.copy_card (card read). Intended for the other
# bulk writers too — freeze, add-to-master, preview build — each of which knows
# its own planned byte total.

import shutil
from pathlib import Path

# Room left over after the planned bytes: XMP sidecars, the ingest/dest caches,
# preview thumbnails, and simply not running a machine to zero.
DEFAULT_MARGIN_BYTES = 1 << 30      # 1 GB


def human_bytes(n: int) -> str:
    """'42.3 GB' — for messages the user reads, not for arithmetic."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _existing_ancestor(path) -> Path | None:
    """The nearest existing directory at or above `path` — the destination
    folder itself often does not exist yet when the check runs."""
    p = Path(path).resolve()
    while True:
        if p.is_dir():
            return p
        if p.parent == p:
            return None
        p = p.parent


def free_bytes(path) -> int | None:
    """Free bytes on the volume holding `path`, or None if it cannot be read."""
    anchor = _existing_ancestor(path)
    if anchor is None:
        return None
    try:
        return shutil.disk_usage(anchor).free
    except OSError:
        return None


def check_free_space(dest, needed_bytes: int,
                     margin_bytes: int = DEFAULT_MARGIN_BYTES,
                     what: str = "This step") -> str | None:
    """None when `needed_bytes` (+ margin) fit on `dest`'s volume; otherwise a
    plain-language sentence naming what is needed and what is there.

    what - how the caller names itself in the message, e.g. 'The card read'.
    """
    if needed_bytes <= 0:
        return None
    free = free_bytes(dest)
    if free is None:
        return None                      # unknown disk — do not block the work
    if free >= needed_bytes + margin_bytes:
        return None
    anchor = _existing_ancestor(dest)
    where = str(anchor) if anchor else str(dest)
    return (f"Not enough free space on {where}. "
            f"{what} needs {human_bytes(needed_bytes)} "
            f"(plus {human_bytes(margin_bytes)} working room), "
            f"but only {human_bytes(free)} is free.")
