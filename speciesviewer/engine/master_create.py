"""Create a new, empty master collection in a folder (docs/plans/setup_checklist_plan.md).

ONE JOB: turn an empty folder into a master collection and make it the open
master's database. It reuses Create Trip's module (batch/sv1MakeReposity.py):
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


def create_master_collection(folder) -> None:
    """Create the collection in `folder` and point the open master at it.
    On failure the open master's setting is put back as it was."""
    from batch import sv1MakeReposity
    from utils.batch_config import generate_svconfig_ini
    from utils.ini_io import read_ini
    from utils.runtime_files import svconfig_path

    folder = Path(folder)
    check_folder(folder)
    previous = {k: _config.get("master", k, fallback=None)
                for k in ("masterbiotadatabase", "masterbiotawebroot", "readonly")}
    try:
        # generate_svconfig_ini builds nothing without a master path, so the
        # setting goes in first; master_mode makes the folder THE master.
        _config.set("master", "masterbiotadatabase", str(folder))
        _config.set("master", "masterbiotawebroot", "")
        generate_svconfig_ini(master_mode=True)
        module_config = configparser.ConfigParser()
        read_ini(module_config, svconfig_path())
        module_config.set("repository", "BiotaDatabase", str(folder))
        module_config.set("repository", "BiotaWebRoot", "")     # nothing uploaded
        folder.mkdir(parents=True, exist_ok=True)
        sv1MakeReposity.main(None, module_config)
        # A collection you just made is yours to edit (the default "yes" is for
        # a machine that holds only part of someone's master).
        _config.set("master", "readonly", "no")
    except Exception:
        for key, value in previous.items():
            if value is None:
                _config.remove_option("master", key)
            else:
                _config.set("master", key, value)
        raise
    finally:
        persist_config()
    logger.info(f"New master collection created in {folder}.")
