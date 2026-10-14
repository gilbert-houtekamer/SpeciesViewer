"""A trip or album belongs to ONE master.

Its trip.ini / virttrip.ini records the master's collection folder
(master_repository_disk). Opening another master's trip — which multiple masters
made easy — let test masters see and change each other's trips. This module
answers one question for the places that open a trip: does it belong to another
master on this computer, and if so which one?

The rule is engine.force_delete's, so the two can never disagree: only a master
that EXISTS on this computer counts as another owner. A trip carried over from
BigBoy names BigBoy's collection path, which does not exist on the laptop — that
is the normal export/import round trip, not someone else's trip.

There is deliberately no "clone a trip into another master" (decided
2026-10-14): it is complex and encourages messy habits. Work on a trip in the
master it belongs to.
"""
from __future__ import annotations

from pathlib import Path

from engine.force_delete import _ini, _other_master

INI_NAMES = {"trip": "trip.ini", "album": "virttrip.ini"}


def _master_name_for(collection: str) -> str:
    """The name of the master whose collection is `collection`, else ""."""
    try:
        from engine.master_purge import _config_file, _paths_of, _read_config
        from utils import masters
        for entry in masters.load()["masters"]:
            got = _paths_of(_read_config(_config_file(entry)))["collection"]
            if got and str(Path(got)).lower() == str(Path(collection)).lower():
                return entry["name"]
    except Exception:
        pass
    return ""


def foreign_owner(folder, kind: str, master_db: str) -> str:
    """'' when the trip/album at `folder` may be opened in the master whose
    collection is `master_db`; otherwise a plain description of its owner
    ("the master “SVlocal3”" or its collection folder)."""
    ini = Path(folder) / INI_NAMES[kind]
    if not ini.is_file():
        return ""
    owner = (_ini(ini).get("master_repository_disk") or "").strip()
    if not _other_master(owner, master_db):
        return ""
    name = _master_name_for(owner)
    return f"the master “{name}”" if name else f"the master collection in {owner}"


def refusal_text(folder, kind: str, owner: str) -> str:
    what = "trip" if kind == "trip" else "album"
    return (f"This {what} belongs to {owner}, not to the master that is open:\n"
            f"{folder}\n\n"
            f"A {what} belongs to one master. To work on it, switch to that master "
            f"(Master ▸ Masters).")
