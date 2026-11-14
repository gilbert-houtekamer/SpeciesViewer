"""ONE job: give a picture that has NO capture date one, chosen by a person.

WhatsApp downloads and pictures received from others arrive without a capture
date, and a dateless picture is left out of the blog, out of every Album (an
Album is a date range) and out of the time-based GPS backfill.

INVARIANTS
- A date the CAMERA recorded is NEVER replaced. The files are re-read right
  before writing (engine.capture_time, the same rule Read & Summarize uses), and
  any file that states a capture time is skipped — unless that time was itself
  set by hand (XMP-sv:CaptureDateSource = MANUAL): an estimate, which a better
  one may replace.
- Only the date tag (EXIF:DateTimeOriginal, the tag Read & Summarize reads) and
  its MANUAL mark are written, into the JPG/HEIC/PNG itself — metadata only,
  the pixels are untouched, the same way paste-GPS writes into a JPG. A RAW is
  never written.
- Two pictures never get the same second: spread_times() steps past every time
  already taken.
- ExifTool can report success on a refused write, so every write is read back.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger("speciesviewer")

_FORMAT = "%Y:%m:%d %H:%M:%S"
MANUAL = "MANUAL"
_SOURCE_TAG = "XMP-sv:CaptureDateSource"


def _config_args():
    from gui.geolocate import _exiftool_config
    return ["-config", _exiftool_config()]


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def read_capture_info(files) -> dict:
    """{normalised path: (datetime | None, set_by_hand)} for many files in ONE
    exiftool call, by the same rule (and tags) Read & Summarize uses."""
    from engine.capture_time import EXIFTOOL_ARGS, capture_datetime
    from engine.exif_writer import run_exiftool_files
    files = [str(f) for f in files if f]
    if not files:
        return {}
    out = run_exiftool_files(["-G", "-j"] + EXIFTOOL_ARGS + [f"-{_SOURCE_TAG}"],
                             files, pre_args=_config_args()).stdout
    rows = json.loads(out) if out.strip() else []
    res = {_norm(f): (None, False) for f in files}
    for r in rows:
        # -G = family-0 group names, as capture_datetime expects; our mark reads
        # back as "XMP:CaptureDateSource".
        by_hand = str(r.get("XMP:CaptureDateSource", "") or "").strip().upper() == MANUAL
        res[_norm(r.get("SourceFile", ""))] = (capture_datetime(r), by_hand)
    return res


def read_capture_dates(files) -> dict:
    """{normalised path: datetime | None} — read_capture_info without the mark."""
    return {k: v[0] for k, v in read_capture_info(files).items()}


def spread_times(start: datetime, n: int, taken=()) -> list:
    """n distinct times from `start`, one second apart, skipping every second in
    `taken` (times other pictures of the day already have)."""
    taken = {t.replace(microsecond=0) for t in taken if t}
    out, t = [], start.replace(microsecond=0)
    while len(out) < n:
        if t not in taken:
            out.append(t)
            taken.add(t)
        t += timedelta(seconds=1)
    return out


def write_capture_dates(files_by_time: dict) -> tuple:
    """{datetime: [files of ONE picture]} → (written, had_date, failed), each a
    list of files. Files with a capture time the camera recorded are skipped
    (had_date); a time set by hand earlier is replaced. A write that does not
    read back is reported (failed)."""
    from engine.exif_writer import run_exiftool_files
    all_files = [str(f) for fs in files_by_time.values() for f in fs if f]
    before = read_capture_info(all_files)
    written, had_date, failed = [], [], []
    for when, fs in files_by_time.items():
        todo = []
        for f in (str(x) for x in fs if x):
            dt, by_hand = before.get(_norm(f), (None, False))
            (had_date if dt and not by_hand else todo).append(f)
        if not todo:
            continue
        stamp = when.strftime(_FORMAT)
        try:
            r = run_exiftool_files([f"-EXIF:DateTimeOriginal={stamp}",
                                    f"-{_SOURCE_TAG}={MANUAL}",
                                    "-overwrite_original"], todo,
                                   pre_args=_config_args())
            if r.returncode != 0:
                logger.warning(f"Date write returned {r.returncode}: {r.stderr.strip()}")
        except Exception as e:
            logger.warning(f"Date write failed: {e}")
        after = read_capture_info(todo)
        for f in todo:
            got, by_hand = after.get(_norm(f), (None, False))
            (written if by_hand and got == when.replace(microsecond=0)
             else failed).append(f)
    return written, had_date, failed
