#!/usr/bin/env python3
"""
svv5AddToMaster — Virtual Trip: register the virtual trip in the master trip index.

Reads virttrip.ini, adds (or refreshes) its row in the master trip_admin.csv,
regenerates the master tripindex HTML pages (NL + EN), and uploads them together
with the updated trip_admin.csv and the trip picture/banner images.

This is the virtual-trip equivalent of svr4UpdateTrips for regular trips.
Key differences from a regular trip:
  - Reads virttrip.ini instead of trip.ini.
  - Fetches the current trip_admin.csv from the web (authoritative) rather than
    from local disk, to avoid publishing stale or draft trips that may exist
    in the local master copy on SmallBoy.
  - Uploads trip picture and banner to the virtual trip's web root (sv7 does
    this for regular trips; virtual trips skip sv7).
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
import engine.publish_index
import engine.triplib
from engine import SVException
from utils.ini_io import read_ini
from utils.runtime_files import svconfig_path

logger = logging.getLogger("speciesviewer")


def main(args, config):
    starttime = time.time()

    virttrip_root = config.get("repository", "BiotaDatabase")
    web_root      = config.get("repository", "BiotaWebRoot")
    master_db     = config.get("master",     "MasterBiotaDatabase", fallback="")
    master_web    = config.get("master",     "MasterBiotaWebRoot",  fallback="/nature/")

    if not master_db or not os.path.isdir(master_db):
        raise SVException(f"Master database not found: {master_db!r}")

    logger.info(f"AddToMaster: {virttrip_root}")

    project = engine.projectlib.Project(virttrip_root, "", web_root)
    project.initMasterAndTrip(config, inittrip=False)
    project.master_biota_web_root = master_web
    if project.Trip is not None:
        project.Trip.master_repository_web = master_web

    trip = project.Trip

    # Read virttrip.ini (not trip.ini — that is the regular-trip ini file).
    virttrip_ini = os.path.join(virttrip_root, "virttrip.ini")
    if not os.path.isfile(virttrip_ini):
        raise SVException(f"virttrip.ini not found in {virttrip_root!r}. "
                          "Create it via Virtual Trips → Create Virtual Trip first.")

    # Base the index on the live website rather than local disk: the local master
    # copy on SmallBoy may contain draft or stale trips that should not be
    # published. Shared with svr4UpdateTrips and the un-advertise path — see
    # engine.publish_index.load_admin_base (it also carries the read_from_local
    # recovery escape hatch).
    engine.publish_index.load_admin_base(trip, config)

    trip_row = trip.readTrip(virttrip_ini)
    if not trip_row:
        raise SVException("Could not read trip information from virttrip.ini.")

    trip.addTrip(trip_row)
    trip.write_TripAdmin()
    logger.info(f"The master's trip list is updated with '{trip_row.get('trip_name', '?')}'.")

    trip.updateTripIndexPage('nl')
    trip.updateTripIndexPage('en')
    logger.info("Master tripindex pages regenerated.")

    pwd = config.get("websettings", "Sftppwd", fallback="").strip("'")
    sftp = trip.openSFTP(pwd)
    if sftp is None:
        raise SVException("Could not connect to SFTP — upload skipped.")

    # Upload trip picture and banner to the virtual trip's web root.
    # For regular trips sv7WriteCommit copies the whole trip tree; virtual trips
    # skip sv7, so these files must be uploaded explicitly here.
    prefix = sftp.remotePrefix()
    for field in ("trip_picture", "trip_banner"):
        filename = trip_row.get(field, "").strip()
        if not filename:
            continue
        local_path = os.path.join(virttrip_root, filename)
        if os.path.isfile(local_path):
            remote_path = prefix + web_root.rstrip("/") + "/" + filename
            sftp.upload(local_path, remote_path)
            logger.info(f"  Uploaded {filename}")
        else:
            logger.warning(f"  {field} file not found locally: {local_path}")

    trip.uploadTrip(sftp)
    trip.uploadTripAdmin(sftp, trip.getTripAdminWeb())
    sftp.disconnect()

    logger.info(f"✅ AddToMaster complete ({time.time() - starttime:.1f}s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AddToMaster")
    parser.add_argument("--config", type=str,
                        default=os.environ.get("SVCONFIG") or str(svconfig_path()))
    args = parser.parse_args()
    config = configparser.ConfigParser()
    read_ini(config, args.config)
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.error(f"AddToMaster failed: {e}")
    else:
        logger.debug("AddToMaster succeeded.")
