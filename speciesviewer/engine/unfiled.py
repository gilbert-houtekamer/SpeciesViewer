"""The unfiled inbox — days off a card that belong to no trip yet.

ONE JOB: hold pictures between "off the card" and "filed under a trip", and
answer what is sitting there. It exists because emptying a stack of cards and
deciding which trip each day belongs to are different decisions taken at
different moments: the first is urgent and must ask nothing, the second needs the
dates, places and species to be visible.

INVARIANT — the inbox is a FOLDER, never a registry. Its contents are derived
from disk on every read:

    <CurrentPictures>/<year>/<year> unfiled/YYYY-MM-DD/

Nothing here writes a status file, an active pointer, or a claim of any kind.
Move a day out in Explorer and the inbox is simply smaller next time you look —
there is no second copy of the truth to fall out of sync, and nothing that can
corrupt the Days registry (a different lane, with a different meaning: an ad-hoc
day heading for the master without a website).

scan_unfiled() does READ the Days registry, to leave out a day that is already
tracked as a Day. That is not a crack in the invariant — reading someone else's
claim is what keeps this module from having to record one of its own. The inbox
means "nothing claims this yet", so a day the Days lane has claimed does not
belong in it, and un-registering that day puts it straight back. The registry
stays the Days lane's business; this module never writes to it.

A day leaves the inbox in exactly three ways, and SpeciesViewer never deletes one:
  1. filed under a trip   — move_day_to() into the trip's picture root
  2. tracked as a Day     — days_registry.add_day(), the folder stays where it is
  3. moved somewhere else — move_day_to() to a folder the user picks, or by hand
Filing (1) is cheap and safe BEFORE Read & Summarize, because nothing points at
the old path yet.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
YEAR_RE = re.compile(r"^\d{4}$")

# Pictures we count when reporting what is in a day (video is deliberately out —
# the counter answers "how many photos", the same question the trip overview asks).
PICTURE_EXTS = {".orf", ".nef", ".cr2", ".cr3", ".arw", ".rw2", ".raf", ".dng",
                ".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff"}

UNFILED_SUFFIX = "unfiled"


def unfiled_dir_name(year) -> str:
    """'2026 unfiled' — the folder name, kept next to the trip folders of that
    year rather than hidden away, so it is obvious in Explorer too."""
    return f"{year} {UNFILED_SUFFIX}"


def guess_currentpictures_root(sample_path: str = "") -> str:
    """Best guess at the per-machine pictures base (e.g. D:\\CurrentPictures).

    Layout is <base>/<year>/<trip>/<day> or <base>/<year>/<day>, so the base is
    everything ABOVE the first path segment that is a bare 4-digit year. Returns
    "" when no year segment is present (then the caller must ask)."""
    p = (sample_path or "").strip().strip('"').strip("'")
    if not p:
        return ""
    parts = Path(os.path.normpath(p)).parts
    for i, seg in enumerate(parts):
        if YEAR_RE.match(seg):
            return str(Path(*parts[:i])) if i else ""
    return ""


def currentpictures_root(fallback_sample: str = "") -> str:
    """The configured pictures base, or a guess from `fallback_sample`.

    The configured value is [currentpictures] rootdirectory — the same setting
    that makes a trip.ini's picture_root portable across machines (WP8), so
    filling it in here pays off twice."""
    from utils.config import get_currentpictures_root
    base = get_currentpictures_root()
    if base:
        return os.path.normpath(base)
    return guess_currentpictures_root(fallback_sample)


def unfiled_root(year=None, *, base: str = "") -> str:
    """<base>/<year>/<year> unfiled. `year` defaults to the current one.
    Returns "" when no base is known — the caller must ask rather than invent a
    folder somewhere unexpected."""
    base = base or currentpictures_root()
    if not base:
        return ""
    year = year or datetime.now().year
    return os.path.join(base, str(year), unfiled_dir_name(year))


def year_of_day(day_name: str):
    """2026 from '2026-05-24', else None."""
    return int(day_name[:4]) if DAY_RE.match(day_name or "") else None


def count_pictures(folder) -> int:
    try:
        return sum(1 for f in os.scandir(folder)
                   if f.is_file() and os.path.splitext(f.name)[1].lower() in PICTURE_EXTS)
    except OSError:
        return 0


def scan_unfiled(base: str = "") -> list[dict]:
    """Every UNCLAIMED day currently on disk, newest first.

    Walks <base>/<year>/<year> unfiled/ for each year folder present, so days
    from several years show up in one list. Derived from disk every time — no
    cache, no state of its own. Each entry: {folder, day, year, pictures}.

    Days already registered in the Days lane are left out: tracking a day as a
    Day does not move it, so without this the same folder would be listed by two
    panels at once and the inbox would stop meaning "unclaimed". See the module
    docstring — this reads that registry, it never writes it."""
    base = base or currentpictures_root()
    out: list[dict] = []
    if not base or not os.path.isdir(base):
        return out
    try:
        from utils.days_registry import registered_folders
        claimed = registered_folders()
    except Exception:
        # A missing or unreadable Days registry must not empty the inbox: showing
        # a day twice is a blemish, hiding pictures is a bug.
        claimed = set()
    try:
        year_dirs = [e for e in os.scandir(base) if e.is_dir() and YEAR_RE.match(e.name)]
    except OSError:
        return out
    for ydir in year_dirs:
        # Matched case-INSENSITIVELY: "2026 Unfiled" and "2026 unfiled" are the
        # same folder to a person, and to Windows. Comparing the literal name
        # would work here and then quietly find nothing on the Mac, where the
        # filesystem answers honestly. Find it by name, do not assume it.
        inbox = ""
        want = unfiled_dir_name(ydir.name).lower()
        try:
            for sub in os.scandir(ydir.path):
                if sub.is_dir() and sub.name.lower() == want:
                    inbox = sub.path
                    break
        except OSError:
            continue
        if not inbox:
            continue
        try:
            days = [e for e in os.scandir(inbox) if e.is_dir() and DAY_RE.match(e.name)]
        except OSError:
            continue
        for d in days:
            if os.path.normcase(os.path.normpath(d.path)) in claimed:
                continue
            out.append({"folder": d.path, "day": d.name, "year": int(ydir.name),
                        "pictures": count_pictures(d.path)})
    out.sort(key=lambda r: r["day"], reverse=True)
    return out


def trips_covering(day: str) -> list[dict]:
    """Trips whose date range contains `day` (a 'YYYY-MM-DD' string).

    A suggestion, never an automatic choice: overlapping trips are possible (a
    virtual trip over the same dates), so the caller offers the list. Each entry:
    {trip_id, title, root, picture_root, covers}. Trips with no usable picture
    root are dropped — there would be nowhere to move the day to."""
    from utils.config import get_trips_root
    from utils.trip_config import read_trip_ini
    from utils.trip_identity import resolve_trip_picture_root

    root = get_trips_root()
    out: list[dict] = []
    if not root or not os.path.isdir(root):
        return out
    for entry in sorted(os.scandir(root), key=lambda e: e.name.lower()):
        if not entry.is_dir():
            continue
        data = read_trip_ini(entry.path) or {}
        if not data:
            continue
        picture_root = resolve_trip_picture_root(entry.path, "trip",
                                                 active_picturedirectory="")
        if not picture_root or not os.path.isdir(picture_root):
            continue
        frm = (data.get("fromdate") or "").strip()
        to = (data.get("todate") or "").strip()
        covers = bool(frm and to and frm <= day <= to)
        out.append({
            "trip_id": entry.name,
            "title": (data.get("title_nl") or data.get("trip_name") or entry.name).strip(),
            "root": entry.path,
            "picture_root": picture_root,
            "fromdate": frm, "todate": to,
            "covers": covers,
        })
    # Date-range matches first, then the rest alphabetically — the suggestion is
    # at the top without hiding the trips that did not match.
    out.sort(key=lambda t: (not t["covers"], t["title"].lower()))
    return out


def move_day_to(day_folder: str, target_picture_root: str) -> tuple[bool, str]:
    """Move one YYYY-MM-DD folder into a trip's picture root.

    Refuses rather than merges when the target day already exists: two days with
    the same date from different cards is a real situation, and silently pouring
    one into the other would mix two shoots with no way back. Returns
    (ok, message); the message is user-facing."""
    src = Path(day_folder)
    if not src.is_dir():
        return False, f"{src.name} is no longer there."
    if not DAY_RE.match(src.name):
        return False, f"{src.name} is not a YYYY-MM-DD day folder."
    dest_root = Path(target_picture_root)
    if not dest_root.is_dir():
        return False, f"The trip's pictures folder does not exist:\n{dest_root}"
    dest = dest_root / src.name
    if dest.exists():
        return False, (f"{dest_root.name} already has a {src.name}.\n\n"
                       f"Move or rename that one first — merging two days of the "
                       f"same date would mix two shoots.")
    try:
        shutil.move(str(src), str(dest))
    except Exception as e:
        return False, f"Could not move {src.name}: {e}"
    return True, f"{src.name} moved to {dest_root.name}."
