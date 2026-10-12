"""The suggested places for a new installation's folders
(docs/plans/trip_create_naming_plan.md §1).

ONE JOB: name the default folders — nothing is created here. Platform
specific: Windows gets C:\\SpeciesViewer\\…, the Mac (and Linux) a folder in
the home directory, since there is no C: drive.
"""

from __future__ import annotations

import sys
from pathlib import Path


def base() -> Path:
    if sys.platform.startswith("win"):
        return Path("C:/SpeciesViewer")
    return Path.home() / "SpeciesViewer"


def master_folder() -> Path:
    return base() / "nature"


def trips_folder() -> Path:
    return base() / "trips"


def pictures_folder() -> Path:
    return base() / "CurrentPictures"


def albums_folder() -> Path:
    return base() / "albums"
