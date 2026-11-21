"""Where a picture deleted on a trip day goes.

ONE job: name the trash folder for a day of pictures.

Invariant: the trash sits on the SAME disk as the day's pictures —
<pictures folder>/.trash/<day>/<picture>/ — so a delete is a rename: instant,
and it never moves anything into the trip repository. Until 2026-10-07 a trip-day
delete went to <trip repository>/.trash/<day>, i.e. from D:\\CurrentPictures into
Dropbox: deleting a picture ADDED it to Dropbox (212 GB of Brazil deletes) and
cost a full cross-disk copy each time. Old trash in that legacy place is still
read (legacy_day_trash_dir) by whatever looks for deleted pictures.
"""
import logging
import os
import re
import shutil
from pathlib import Path

logger = logging.getLogger("speciesviewer")

TRASH_DIRNAME = ".trash"


def day_trash_dir(day_folder) -> Path:
    """The trash for one day folder: its sibling `.trash/<day name>`."""
    day = Path(day_folder)
    return day.parent / TRASH_DIRNAME / day.name


def legacy_day_trash_dir(trip_root, day_name: str) -> Path:
    """Where trip-day deletes went before 2026-10-07 (inside the trip repository)."""
    return Path(trip_root) / TRASH_DIRNAME / day_name


# ── The trash as a list of pictures: look, restore, discard ──────────────────
# One picture = one folder <pictures>/.trash/<day>/<picture>/ holding all its
# files (RAW, .xmp, .dop, ...). Restoring moves those files back into the day
# folder; it NEVER overwrites a file already there. Discarding deletes for good.

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}$")


def trash_root(pictures_root) -> Path:
    """The trip's trash: <pictures root>/.trash (one folder per day inside)."""
    return Path(pictures_root) / TRASH_DIRNAME


def trash_days(pictures_root) -> list:
    """The day folders in the trash that hold at least one picture, oldest first."""
    root = trash_root(pictures_root)
    if not root.is_dir():
        return []
    days = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and _DAY.match(d.name) and any(p.is_dir() for p in d.iterdir()):
            days.append(d.name)
    return days


def trash_pictures(pictures_root, day: str) -> list:
    """[(picture folder, [its files])] for one trashed day, by folder name."""
    day_dir = trash_root(pictures_root) / day
    if not day_dir.is_dir():
        return []
    out = []
    for p in sorted(day_dir.iterdir(), key=lambda x: x.name.casefold()):
        if p.is_dir():
            files = sorted(f for f in p.iterdir() if f.is_file())
            if files:
                out.append((p, files))
    return out


def restore_picture(picture_dir, day_folder) -> tuple:
    """Move one trashed picture's files back into its day folder.

    Returns (moved names, conflicting names). All or nothing: when ANY of its
    files already exists in the day folder, nothing moves (never overwrite, and
    never split a picture between the day and the trash) and the conflicting
    names are returned. On success the empty picture folder is removed."""
    picture_dir, day_folder = Path(picture_dir), Path(day_folder)
    files = sorted(f for f in picture_dir.iterdir() if f.is_file())
    conflicts = [f.name for f in files if (day_folder / f.name).exists()]
    if conflicts:
        return [], conflicts
    day_folder.mkdir(parents=True, exist_ok=True)
    moved = []
    for f in files:
        shutil.move(str(f), str(day_folder / f.name))
        moved.append(f.name)
    if not any(picture_dir.iterdir()):
        picture_dir.rmdir()
    return moved, []


def trash_size(pictures_root) -> tuple:
    """(pictures, bytes) in the whole trip trash. Walks every file, so on a big
    trash run it off the GUI thread."""
    root = trash_root(pictures_root)
    pictures = total = 0
    if not root.is_dir():
        return 0, 0
    for day in os.scandir(root):
        if not (day.is_dir() and _DAY.match(day.name)):
            continue
        for pic in os.scandir(day.path):
            if not pic.is_dir():
                continue
            pictures += 1
            for f in os.scandir(pic.path):
                if f.is_file():
                    total += f.stat().st_size
    return pictures, total


def trash_day_count(pictures_root, day: str) -> int:
    """How many pictures one trashed day holds — folders only, no file listing."""
    day_dir = trash_root(pictures_root) / day
    if not day_dir.is_dir():
        return 0
    return sum(1 for p in os.scandir(day_dir) if p.is_dir())


def trash_day_size(pictures_root, day: str) -> int:
    """Bytes in one trashed day."""
    day_dir = trash_root(pictures_root) / day
    total = 0
    if day_dir.is_dir():
        for pic in os.scandir(day_dir):
            if pic.is_dir():
                total += sum(f.stat().st_size for f in os.scandir(pic.path) if f.is_file())
    return total


def discard_day(pictures_root, day: str, progress_cb=None) -> int:
    """PERMANENTLY delete every picture of one trashed day. Returns the number
    of picture folders removed. `progress_cb(done, total)` may return False to
    stop; what is already deleted stays deleted. The day folder goes when it
    ends up empty."""
    day_dir = trash_root(pictures_root) / day
    if not day_dir.is_dir():
        return 0
    folders = [Path(p.path) for p in os.scandir(day_dir) if p.is_dir()]
    done = 0
    for i, pic in enumerate(folders):
        shutil.rmtree(pic)
        done += 1
        if progress_cb and progress_cb(i + 1, len(folders)) is False:
            break
    if not any(os.scandir(day_dir)):
        os.rmdir(day_dir)
    logger.info(f"Trip trash: {done} picture(s) of {day} permanently deleted from {day_dir}.")
    return done


# ── A Day's own trash (Days / Unfiled lanes) ─────────────────────────────────
# A day outside a trip is culled in a viewer that has no trip around it, so its
# deletes go INSIDE the day folder: <day>/.trash/<picture>/ (some older ones one
# level deeper, <day>/.trash/<day>/<picture>/). Same one-folder-per-picture
# shape, so restore_picture() works unchanged; these list, measure and discard it.

def own_trash_dir(day_folder) -> Path:
    """A Day's own trash: <day>/.trash."""
    return Path(day_folder) / TRASH_DIRNAME


def own_trash_pictures(day_folder) -> list:
    """[(picture folder, [its files])] in a Day's own trash, by folder name.
    A folder holding only folders (the older <day>/.trash/<day>/ level) is
    looked into, not listed."""
    root = own_trash_dir(day_folder)
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir(), key=lambda x: x.name.casefold()):
        if not p.is_dir():
            continue
        files = sorted(f for f in p.iterdir() if f.is_file())
        if files:
            out.append((p, files))
        elif p.name == Path(day_folder).name:
            for q in sorted(p.iterdir(), key=lambda x: x.name.casefold()):
                if q.is_dir():
                    qf = sorted(f for f in q.iterdir() if f.is_file())
                    if qf:
                        out.append((q, qf))
    return sorted(out, key=lambda pf: pf[0].name.casefold())


def own_trash_size(day_folder) -> tuple:
    """(pictures, bytes) in a Day's own trash."""
    pics = own_trash_pictures(day_folder)
    return len(pics), sum(f.stat().st_size for _p, files in pics for f in files)


def discard_own_trash(day_folder, progress_cb=None) -> int:
    """PERMANENTLY delete every picture in a Day's own trash. Same contract as
    discard_day(); empty folders left behind go too, the day folder never."""
    pics = [p for p, _f in own_trash_pictures(day_folder)]
    done = 0
    for i, pic in enumerate(pics):
        shutil.rmtree(pic)
        done += 1
        if progress_cb and progress_cb(i + 1, len(pics)) is False:
            break
    root = own_trash_dir(day_folder)
    for sub in (root / Path(day_folder).name, root):
        try:
            if sub.is_dir() and not any(sub.iterdir()):
                sub.rmdir()
        except OSError:
            pass
    logger.info(f"Day trash: {done} picture(s) permanently deleted from {root}.")
    return done
