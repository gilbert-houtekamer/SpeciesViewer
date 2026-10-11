"""Which exiftool SpeciesViewer runs — one answer for every caller.

ONE JOB: the program to start, in this order:
  1. chosen — [Global] exiftool_path, set with Setup Checklist ▸ exiftool ▸ Locate…;
     FIRST, so a newer exiftool can be installed without a new SpeciesViewer;
  2. shipped — the exiftool folder beside SpeciesViewer.exe in the download
     (tools/svr_build_zip.py puts the pinned official version there);
  3. the PATH — by NAME: "exiftool.exe" on Windows, "exiftool" elsewhere.

Step 3 must be exactly pyexiftool's own default (constants.DEFAULT_EXECUTABLE),
which is what every caller got before this module existed. On Windows that is
"exiftool.EXE", not "exiftool": the bare name lets pyexiftool's lookup find a
.BAT wrapper first (Strawberry Perl ships one in site/bin), and the -stay_open
handshake through a batch file never answers — the app hangs on the first read.
Found by testing, 2026-10-11. Subprocess calls are unaffected either way:
Windows appends .exe to a bare name itself.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_EXE = "exiftool.exe" if sys.platform.startswith("win") else "exiftool"


def shipped() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    p = Path(sys.executable).parent / "exiftool" / _EXE
    return p if p.is_file() else None


def chosen() -> Path | None:
    from utils.config import _config
    value = _config.get("Global", "exiftool_path", fallback="").strip()
    return Path(value) if value and Path(value).is_file() else None


def exe() -> str:
    """What to start: a full path when shipped or chosen, else the name."""
    found = chosen() or shipped()
    return str(found) if found else _EXE


def describe() -> tuple[str | None, str]:
    """(the program that will run, or None; where it comes from) — for the
    Setup Checklist. For the PATH this is what Windows itself would start."""
    if chosen():
        return str(chosen()), "chosen by you"
    if shipped():
        return str(shipped()), "shipped with SpeciesViewer"
    on_path = shutil.which(_EXE)
    return (on_path, "found on the PATH") if on_path else (None, "")
