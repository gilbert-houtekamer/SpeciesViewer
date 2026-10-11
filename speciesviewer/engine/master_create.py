"""Create a new, empty master collection in a folder (docs/plans/setup_checklist_plan.md).

ONE JOB: turn an empty folder into a master collection (make_collection) and
point a master's settings at it — the open master's (create_master_collection,
Setup Checklist ▸ Create…) or a new one's (point_master_at, New Test Master).
It reuses Create Trip's module (batch/sv1MakeReposity.py):
with [master] MasterBiotaDatabase empty, that module makes the folder THE
master — data skeleton, starter species list, local web pages.

INVARIANT: never in a folder that holds anything. sv1MakeReposity copies the
empty CSVs into metadata/active/, so pointed at an existing collection it
would overwrite its data_pictures.csv and species list.

No website is needed: the web root is left empty, so nothing is uploaded.
"""

from __future__ import annotations

import configparser
import logging
from pathlib import Path

from utils.config import _config, persist_config

logger = logging.getLogger("speciesviewer")


class CreateMasterError(RuntimeError):
    pass


def check_folder(folder: Path) -> None:
    """Raise CreateMasterError unless `folder` is new or empty."""
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise CreateMasterError(
            f"{folder} is not empty. Choose an empty folder, or a new one — "
            "creating a master in a folder that holds files could overwrite them.")


def make_collection(folder) -> None:
    """Build the collection in `folder` — data skeleton, starter species list,
    local web pages — WITHOUT touching any master's settings, so it serves both
    the open master (create_master_collection) and a master that is only being
    registered (New Test Master). The module gets an explicit config: this
    folder is THE master (MasterBiotaDatabase empty) and has no web root, so
    nothing is uploaded."""
    from batch import sv1MakeReposity
    folder = Path(folder)
    check_folder(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cfg = configparser.ConfigParser()
    cfg["repository"] = {"BiotaDatabase": str(folder), "BiotaWebRoot": "",
                         "ReferenceSpecies": str(folder / "metadata" / "active" / "species.csv")}
    cfg["master"] = {"MasterBiotaDatabase": "", "MasterBiotaWebRoot": ""}
    cfg["project"] = {"PictureDirectory": ""}
    cfg["websettings"] = {"Sftppwd": ""}
    sv1MakeReposity.main(None, cfg)
    logger.info(f"New master collection created in {folder}.")


# What a master's settings say about its own collection. readonly "no": a
# collection you just made is yours to edit (the default "yes" is for a machine
# that holds only part of someone's master).
def collection_settings(folder) -> dict:
    return {"masterbiotadatabase": str(folder), "masterbiotawebroot": "",
            "readonly": "no"}


def create_master_collection(folder) -> None:
    """Create the collection in `folder` and point the OPEN master at it.
    The setting is only changed after the collection exists."""
    make_collection(folder)
    for key, value in collection_settings(folder).items():
        _config.set("master", key, value)
    persist_config()


def point_master_at(config_file, folder) -> None:
    """Point ANOTHER master's settings file at the collection in `folder`
    (New Test Master: the new master is not the one this process has open).
    Creates the file when the master has none yet."""
    from utils.ini_io import read_ini
    config_file = Path(config_file)
    cfg = configparser.ConfigParser()
    if config_file.exists():
        read_ini(cfg, config_file)
    if not cfg.has_section("master"):
        cfg.add_section("master")
    for key, value in collection_settings(folder).items():
        cfg.set("master", key, value)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    with open(config_file, "w", encoding="utf-8") as f:
        cfg.write(f)
