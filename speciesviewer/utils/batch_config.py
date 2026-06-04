import os
from configparser import ConfigParser
from pathlib import Path
import logging
from utils.config import _config, persist_config

def generate_svconfig_ini(svconfig_path="svconfig.ini", master_mode=False):
    """Write svconfig.ini for the next batch step.

    When master_mode is True, the script runs against the master
    database itself (no trip context). BiotaDatabase becomes the
    master path and MasterBiotaDatabase is left empty — the same
    "no trip" signal the scripts already understand from CLI runs.
    """
    logger = logging.getLogger("speciesviewer")
    cfg = ConfigParser()

    master_db = _config.get("master", "masterbiotadatabase", fallback="")
    if not master_db:
        logger.warning("Missing 'masterbiotadatabase' path in config; cannot build svconfig.ini.")
        return

    species_csv = os.path.join(master_db, "metadata", "active", "species.csv")

    if master_mode:
        cfg["repository"] = {
            "BiotaDatabase":    master_db,
            "BiotaWebRoot":     _config.get("master", "masterbiotawebroot", fallback="/nature/"),
            "ReferenceSpecies": species_csv,
        }
        cfg["master"] = {"MasterBiotaDatabase": ""}
    else:
        cfg["repository"] = {
            "BiotaDatabase":    _config.get("trip", "tripbiotadatabase", fallback=""),
            "BiotaWebRoot":     _config.get("trip", "tripbiotawebroot", fallback=""),
            "ReferenceSpecies": species_csv,
        }
        cfg["master"] = {"MasterBiotaDatabase": master_db}
    pic_dir = _config.get("project", "picturedirectory", fallback="")
    if master_mode and pic_dir:
        # The GUI's picturedirectory points at the active day inside a
        # trip; for master ingest we want every day-subdir under the
        # trip root, so step up one level. sv2 walks recursively from
        # there.
        pic_dir = str(Path(pic_dir).parent)
    cfg["project"] = {"PictureDirectory": pic_dir}
    cfg["debug"] = {"Classification": "0", "SpeciesFound": "0"}
    cfg["websettings"] = {"Sftppwd": _config.get("websettings", "Sftppwd", fallback="? still needed")}
    cfg["avibase"] = {
        "source_format":     _config.get("avibase", "source_format",     fallback="avibase"),
        "phone_layout":      _config.get("avibase", "phone_layout",      fallback="false"),
        "first_column_lang": _config.get("avibase", "first_column_lang", fallback="nl"),
    }

    try:
        with open(svconfig_path, "w", encoding="utf-8") as f:
            cfg.write(f)
        # logger.info(f"✅ svconfig.ini written to {svconfig_path}")
    except Exception as err:
        logger.error(f"❌ Failed to write svconfig.ini: {err}")
