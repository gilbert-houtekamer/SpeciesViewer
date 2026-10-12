"""The suggested places for a master's folders
(docs/plans/trip_create_naming_plan.md §1, docs/plans/force_delete_plan.md B).

ONE JOB: name the default folders — nothing is created here. Platform
specific: Windows gets C:\\SpeciesViewer\\…, the Mac (and Linux) a folder in
the home directory, since there is no C: drive.

Per master: the main master's folders sit directly in the base
(C:\\SpeciesViewer\\nature, …\\trips); every other master gets a folder of its
own (C:\\SpeciesViewer\\<master>\\nature, …\\trips, …\\albums,
…\\CurrentPictures). Test masters then never see each other's trips, and one
can be deleted as a whole. Sharing folders between masters was convenient for
testing, but is not to be encouraged (Gilbert, 2026-10-12).
"""

from __future__ import annotations

import sys
from pathlib import Path


def _root() -> Path:
    if sys.platform.startswith("win"):
        return Path("C:/SpeciesViewer")
    return Path.home() / "SpeciesViewer"


def base(master: str | None = None, main: bool | None = None) -> Path:
    """The folder that holds a master's folders. Without arguments: the open
    master's."""
    from utils import masters
    if master is None:
        master, main = masters.ACTIVE["name"], masters.IS_MAIN
    if main:
        return _root()
    return _root() / masters._folder_name(master)


def master_folder(master=None, main=None) -> Path:
    return base(master, main) / "nature"


def trips_folder(master=None, main=None) -> Path:
    return base(master, main) / "trips"


def pictures_folder(master=None, main=None) -> Path:
    return base(master, main) / "CurrentPictures"


def albums_folder(master=None, main=None) -> Path:
    return base(master, main) / "albums"
