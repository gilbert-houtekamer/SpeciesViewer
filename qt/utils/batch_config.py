import os
from configparser import ConfigParser
import logging
from utils.config import _config, persist_config

def generate_svconfig_ini(svconfig_path="svconfig.ini"):
    logger = logging.getLogger("speciesviewer")
    cfg = ConfigParser()

    master_db = _config.get("master", "masterbiotadatabase", fallback="")
    if not master_db:
        logger.warning("Missing 'masterbiotadatabase' path in config; cannot build svconfig.ini.")
        return

    species_csv = os.path.join(master_db, "metadata", "active", "species.csv")

    cfg["repository"] = {
        "BiotaDatabase": _config.get("trip", "tripbiotadatabase", fallback=""),
        "BiotaWebRoot": _config.get("trip", "tripbiotawebroot", fallback=""),
        "ReferenceSpecies": species_csv
    }
    cfg["master"] = {"MasterBiotaDatabase": master_db}
    cfg["project"] = {"PictureDirectory": _config.get("project", "picturedirectory", fallback="")}
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
