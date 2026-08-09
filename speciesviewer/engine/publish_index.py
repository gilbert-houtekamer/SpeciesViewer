"""The site's trip index: is a repository advertised on it, and take it off.

Advertising = a row in the master trip_admin.csv, which drives the tripindex
pages visitors browse. The authoritative copy is the one ON THE WEBSITE (a local
master copy can hold stale/draft rows), so the status is read from there — the
same source svv5AddToMaster / svr4UpdateTrips write back to.

TRIP-GENERIC on purpose: the index does not care whether a row was written by a
real trip or by an album, so both publication dialogs share this module. Only
the ini file that names the repository differs (trip.ini vs virttrip.ini).

Kept out of the batch modules so a dialog can show the current state without
running (or being able to run) a publish.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import time

logger = logging.getLogger("speciesviewer")

# The ini that names a repository, per kind. Both are tried either way: the file
# present on disk decides, so a mis-set kind cannot silently unadvertise nothing.
_INI_FOR_KIND = {
    "album": ("virttrip.ini", "trip.ini"),
    "trip":  ("trip.ini", "virttrip.ini"),
}


def advertised_names(base_url: str, master_web: str = "/nature/", timeout: int = 10):
    """The set of trip_names currently advertised on the site, or None if the
    web trip_admin.csv could not be read (offline / not configured)."""
    base = (base_url or "").rstrip("/")
    if not base:
        return None
    url = base + (master_web or "/nature/").rstrip("/") + "/includes%20web/trip_admin.csv"
    try:
        import requests
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        r.encoding = "utf-8"          # server sends no charset; the file is UTF-8
        reader = csv.DictReader(io.StringIO(r.text), delimiter=";")
        return {(row.get("trip_name") or "").strip()
                for row in reader if (row.get("trip_name") or "").strip()}
    except Exception as e:
        logger.info(f"publish_index: could not read web trip admin: {e}")
        return None


def load_admin_base(trip, config) -> str:
    """Load the CURRENT trip index into `trip` as the base for a change.

    The LIVE website is authoritative, not local disk. A second machine's master
    copy can be behind (or hold rows for trips still in draft there), and every
    publish rewrites the WHOLE index from whatever base was loaded — so basing it
    on a stale local copy silently drops entries another machine published, or
    resurrects ones that were removed. Reading the web first keeps a publish to
    what it is actually about: the one row the caller then adds or removes.

    `[virttrip_addtomaster] read_from_local = true` is the recovery escape hatch
    (hand-edit the local trip_admin.csv, then re-upload from that cleaned copy).
    Falls back to local when offline or when base_url is not configured.
    Returns the source description, for logging by the caller.
    """
    master_web = config.get("master", "MasterBiotaWebRoot", fallback="/nature/")
    base_url = config.get("websettings", "base_url", fallback="").rstrip("/")
    read_from_local = config.getboolean("virttrip_addtomaster", "read_from_local",
                                        fallback=False)
    if read_from_local:
        logger.info("Reading trip admin from local file (cleanup mode).")
        trip.read_TripAdmin()
        source = "local (cleanup mode)"
    elif base_url:
        url = base_url + master_web.rstrip("/") + "/includes%20web/trip_admin.csv"
        logger.info(f"Fetching trip admin from web: {url}")
        trip.read_TripAdmin_from_web(url)
        source = "web"
        if not trip.trip_dict:
            # read_TripAdmin_from_web swallows its errors, so "offline" and "the
            # site really lists nothing" arrive here identically — as an EMPTY
            # base. Writing that out would rewrite the local trip_admin.csv down
            # to the single row the caller is about to add, wiping every other
            # trip and album from this machine's copy. Fall back to local, and
            # only accept an empty base when local is empty too.
            logger.warning("Live trip index unreachable or empty — "
                           "falling back to the local trip_admin.csv.")
            trip.read_TripAdmin()
            source = "local (live index unreachable)"
    else:
        logger.warning("base_url not configured — falling back to local trip_admin.csv.")
        trip.read_TripAdmin()
        source = "local (no base_url)"
    logger.info(f"  {len(trip.trip_dict)} existing trip(s) loaded from {source}.")
    return source


def unadvertise(config, kind: str = "album") -> str:
    """Remove the configured repository's row from the master trip index.

    The mirror of svv5AddToMaster / svr4UpdateTrips: instead of adding the row it
    REMOVES it, regenerates the tripindex HTML (NL + EN), and uploads the updated
    trip_admin.csv + index pages. The repository's own published website
    (/trips/<slug>/) is left untouched — only its advertising row goes away, so
    the site stays reachable by its URL but is no longer linked from the index.

    Reads trip_admin.csv from the LIVE website (authoritative), not local disk,
    so a stale local master copy cannot re-publish entries meant to stay hidden.
    """
    from engine import SVException
    import engine.projectlib

    starttime = time.time()

    repo_root  = config.get("repository", "BiotaDatabase")
    web_root   = config.get("repository", "BiotaWebRoot")
    master_db  = config.get("master",     "MasterBiotaDatabase", fallback="")
    master_web = config.get("master",     "MasterBiotaWebRoot",  fallback="/nature/")

    if not master_db or not os.path.isdir(master_db):
        raise SVException(f"Master database not found: {master_db!r}")

    logger.info(f"unadvertise {kind}: {repo_root}")

    project = engine.projectlib.Project(repo_root, "", web_root)
    project.initMasterAndTrip(config, inittrip=False)
    project.master_biota_web_root = master_web
    if project.Trip is not None:
        project.Trip.master_repository_web = master_web
    trip = project.Trip

    ini_path = ""
    for candidate in _INI_FOR_KIND.get(kind, _INI_FOR_KIND["album"]):
        path = os.path.join(repo_root, candidate)
        if os.path.isfile(path):
            ini_path = path
            break
    if not ini_path:
        raise SVException(
            f"No trip.ini / virttrip.ini found in {repo_root!r} — "
            f"cannot tell which {kind} to remove from the index.")

    # Base the change on the live index, never on a possibly stale local copy.
    load_admin_base(trip, config)

    trip_row = trip.readTrip(ini_path)
    if not trip_row:
        raise SVException(f"Could not read trip information from {os.path.basename(ini_path)}.")
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
    # Upload the regenerated index pages + admin. NOT the picture/banner (the
    # advertise side uploads those; there is nothing to add when un-advertising).
    trip.uploadTrip(sftp)
    trip.uploadTripAdmin(sftp, trip.getTripAdminWeb())
    sftp.disconnect()

    logger.info(f"✅ unadvertise {kind} complete ({time.time() - starttime:.1f}s).")
    return (f"✅ '{name}' removed from the trip index."
            if removed else f"✅ '{name}' was not listed — the index is unchanged.")
