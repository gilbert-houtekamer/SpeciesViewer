#!/usr/bin/env python3
"""
svv8UnadvertiseAlbum — Album: remove the album from the master trip index.

The mirror of svv5AddToMaster: instead of adding the album's row to the master
trip_admin.csv, it REMOVES it, regenerates the tripindex HTML (NL + EN), and
uploads the updated trip_admin.csv + index pages. The album's own published
website (/trips/<slug>/) is left untouched — only its advertising row goes away,
so the album stays reachable by its URL but is no longer linked from the trip
index. This is the "un-advertise" half that Delete Album flags as a separate step.

Like svv5 it fetches trip_admin.csv from the LIVE website (authoritative), not
local disk, so a stale local master copy cannot re-publish trips that were meant
to stay hidden.
"""
import argparse
import configparser
import logging
import os
import sys
import time

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import engine.projectlib
import engine.triplib
from engine import SVException

logger = logging.getLogger("speciesviewer")


def main(args, config):
    starttime = time.time()

    virttrip_root = config.get("repository", "BiotaDatabase")
    web_root      = config.get("repository", "BiotaWebRoot")
    master_db     = config.get("master",     "MasterBiotaDatabase", fallback="")
    master_web    = config.get("master",     "MasterBiotaWebRoot",  fallback="/nature/")
    base_url      = config.get("websettings", "base_url",           fallback="").rstrip("/")

    if not master_db or not os.path.isdir(master_db):
        raise SVException(f"Master database not found: {master_db!r}")

    logger.info(f"svv8UnadvertiseAlbum: {virttrip_root}")

    project = engine.projectlib.Project(virttrip_root, "", web_root)
    project.initMasterAndTrip(config, inittrip=False)
    project.master_biota_web_root = master_web
    if project.Trip is not None:
        project.Trip.master_repository_web = master_web
    trip = project.Trip

    virttrip_ini = os.path.join(virttrip_root, "virttrip.ini")
    if not os.path.isfile(virttrip_ini):
        raise SVException(f"virttrip.ini not found in {virttrip_root!r}.")

    # Fetch the authoritative admin from the web (same rule as svv5).
    read_from_local = config.getboolean("virttrip_addtomaster", "read_from_local",
                                        fallback=False)
    if read_from_local:
        trip.read_TripAdmin()
    elif base_url:
        admin_web_url = (base_url + master_web.rstrip("/")
                         + "/includes%20web/trip_admin.csv")
        logger.info(f"Fetching trip admin from web: {admin_web_url}")
        trip.read_TripAdmin_from_web(admin_web_url)
    else:
        logger.warning("base_url not configured — falling back to local trip_admin.csv.")
        trip.read_TripAdmin()

    trip_row = trip.readTrip(virttrip_ini)
    if not trip_row:
        raise SVException("Could not read trip information from virttrip.ini.")
    name = trip_row.get("trip_name", "").strip()

    removed = trip.removeTrip(name)
    if not removed:
        logger.info(f"'{name}' was not in the trip index — nothing to remove.")
    trip.write_TripAdmin()
    trip.updateTripIndexPage("nl")
    trip.updateTripIndexPage("en")
    logger.info(f"Master tripindex regenerated {'without' if removed else '(unchanged, no'} "
                f"'{name}'{')' if not removed else ''}.")

    pwd = config.get("websettings", "Sftppwd", fallback="").strip("'")
    sftp = trip.openSFTP(pwd)
    if sftp is None:
        raise SVException("Could not connect to SFTP — upload skipped.")
    # Upload the regenerated index pages + admin. NOT the picture/banner (svv5
    # uploads those when advertising; there is nothing to add when un-advertising).
    trip.uploadTrip(sftp)
    trip.uploadTripAdmin(sftp, trip.getTripAdminWeb())
    sftp.disconnect()

    logger.info(f"✅ svv8UnadvertiseAlbum complete ({time.time() - starttime:.1f}s).")
    return (f"✅ '{name}' removed from the trip index."
            if removed else f"✅ '{name}' was not advertised — index unchanged.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UnadvertiseAlbum")
    parser.add_argument("--config", type=str,
                        default=os.environ.get("SVCONFIG", "svconfig.ini"))
    args = parser.parse_args()
    config = configparser.ConfigParser()
    config.read(args.config)
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"UnadvertiseAlbum failed: {e}")
        sys.exit(1)
