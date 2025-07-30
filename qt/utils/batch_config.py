import os
from configparser import ConfigParser
import logging

def generate_svconfig_ini(config, svconfig_path="svconfig.ini"):
    logger = logging.getLogger("speciesviewer")
    cfg = ConfigParser()

    master_db = config.get("master", "masterbiotadatabase", fallback="")
    if not master_db:
        logger.warning("Missing 'masterbiotadatabase' path in config; cannot build svconfig.ini.")
        return

    species_csv = os.path.join(master_db, "metadata", "active", "species.csv")

    cfg["repository"] = {
        "BiotaDatabase": config.get("trip", "tripbiotadatabase", fallback=""),
        "BiotaWebRoot": config.get("trip", "tripbiotawebroot", fallback=""),
        "ReferenceSpecies": species_csv
    }
    cfg["master"] = {"MasterBiotaDatabase": master_db}
    cfg["project"] = {"PictureDirectory": config.get("project", "picturedirectory", fallback="")}
    cfg["debug"] = {"Classification": "0", "SpeciesFound": "0"}
    cfg["websettings"] = {"Sftppwd": config.get("websettings", "Sftppwd", fallback="? still needed")}

    try:
        with open(svconfig_path, "w", encoding="utf-8") as f:
            cfg.write(f)
        logger.info(f"✅ svconfig.ini written to {svconfig_path}")
    except Exception as err:
        logger.error(f"❌ Failed to write svconfig.ini: {err}")
