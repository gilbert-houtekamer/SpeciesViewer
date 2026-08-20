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
#
# report_volumes() is the other half: instead of only refusing at the moment a
# copy would fail, state the situation once at start-up — every local and
# removable volume, how big, how free, what percentage. Answers the question
# before it is asked, on a day when a filling drive is still cheap to fix.

import os
import shutil
import sys
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


# ── Volume report ─────────────────────────────────────────────────────────────
# Thresholds for the start-up report. Percentages, not absolutes: 10 GB free is
# comfortable on a 128 GB laptop disk and alarming on a 4 TB picture drive.
WARN_FREE_PCT  = 10.0
ALERT_FREE_PCT = 5.0

if sys.platform == "win32":
    import ctypes

    _DRIVE_REMOVABLE = 2
    _DRIVE_FIXED     = 3
    _DRIVE_REMOTE    = 4

    def _win_drive_kind(root: str) -> int:
        return ctypes.windll.kernel32.GetDriveTypeW(root)

    def _win_label(root: str) -> str:
        """Volume label ('Data Lightroom'), '' when it cannot be read. Purely
        cosmetic — but on a five-drive machine the letter alone says nothing."""
        buf = ctypes.create_unicode_buffer(261)
        try:
            ok = ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(root), buf, ctypes.sizeof(buf) // 2,
                None, None, None, None, 0)
        except OSError:
            return ""
        return buf.value if ok else ""


def is_network_path(path) -> bool:
    """Is this path on a network share? False when it cannot be told cheaply.

    Worth knowing before a long write: a job that takes two minutes to a local
    disk can take an hour over wifi, and a dropped connection halfway leaves a
    part-written file. Only the answer "yes, certainly" is acted on — anything
    unclear is treated as local, because a false warning about the drive you
    always use is worse than no warning at all.
    """
    if not path:
        return False
    text = str(path)
    if text.startswith("\\\\") or text.startswith("//"):
        return True                       # UNC, e.g. //server/share
    if sys.platform != "win32":
        return False                      # no cheap equivalent worth the risk
    root = os.path.splitdrive(os.path.abspath(text))[0]
    if not root:
        return False
    try:
        return _win_drive_kind(root + "\\") == _DRIVE_REMOTE
    except OSError:
        return False


def _candidate_volumes() -> list[tuple[str, str]]:
    """(root, label) for every volume worth reporting.

    Windows: local and removable drives only — network shares and empty optical
    drives are skipped, because stat'ing a disconnected share can hang for
    seconds and that must never happen on the start-up path. The Windows error
    mode is silenced across the sweep so an empty card-reader slot cannot raise
    the modal "There is no disk in the drive" box.
    macOS/Linux: the root filesystem plus whatever is mounted for the user.
    """
    if sys.platform != "win32":
        out = [("/", "")]
        for base in ("/Volumes", "/media", "/mnt"):
            b = Path(base)
            if b.is_dir():
                try:
                    out += [(str(p), p.name) for p in sorted(b.iterdir()) if p.is_dir()]
                except OSError:
                    pass
        return out

    try:
        roots = list(os.listdrives())          # Python 3.12+
    except (AttributeError, OSError):
        import string
        roots = [f"{c}:\\" for c in string.ascii_uppercase]

    # SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX
    old_mode = ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x8000)
    try:
        out = []
        for root in roots:
            try:
                if _win_drive_kind(root) not in (_DRIVE_FIXED, _DRIVE_REMOVABLE):
                    continue
                out.append((root, _win_label(root)))
            except OSError:
                continue
        return out
    finally:
        ctypes.windll.kernel32.SetErrorMode(old_mode)


def report_volumes() -> list[dict]:
    """One dict per readable volume: root, label, total, free, pct_free, level.

    level is 'ok' | 'warn' | 'alert' — the caller turns that into a log level or
    a colour. A volume that cannot be stat'ed (empty card slot, drive spun down,
    permission wall) is simply left out; this is a report, never a gate.
    """
    rows = []
    for root, label in _candidate_volumes():
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        if not usage.total:
            continue
        pct = usage.free * 100.0 / usage.total
        level = "alert" if pct < ALERT_FREE_PCT else                 "warn" if pct < WARN_FREE_PCT else "ok"
        rows.append({"root": root, "label": label, "total": usage.total,
                     "free": usage.free, "pct_free": pct, "level": level})
    return rows


def format_volume_row(row: dict) -> str:
    """'D: Data Lightroom — 1.8 TB, 725.2 GB free (39%)', plus a plain-language
    tail when the volume is low. One line, meant for the log panel."""
    where = row["root"].rstrip("\\/") or row["root"]
    name = f"{where} {row['label']}" if row["label"] else where
    line = (f"{name} — {human_bytes(row['total'])}, "
            f"{human_bytes(row['free'])} free ({row['pct_free']:.0f}%)")
    if row["level"] == "alert":
        line += f"  ← under {ALERT_FREE_PCT:.0f}% free, nearly full"
    elif row["level"] == "warn":
        line += f"  ← under {WARN_FREE_PCT:.0f}% free"
    return line
