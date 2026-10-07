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
from pathlib import Path

TRASH_DIRNAME = ".trash"


def day_trash_dir(day_folder) -> Path:
    """The trash for one day folder: its sibling `.trash/<day name>`."""
    day = Path(day_folder)
    return day.parent / TRASH_DIRNAME / day.name


def legacy_day_trash_dir(trip_root, day_name: str) -> Path:
    """Where trip-day deletes went before 2026-10-07 (inside the trip repository)."""
    return Path(trip_root) / TRASH_DIRNAME / day_name
