"""Is an album currently advertised on the trip index? (read-only status check)

Advertising = a row in the master trip_admin.csv, which drives the tripindex
pages visitors browse. The authoritative copy is the one ON THE WEBSITE (a local
master copy can hold stale/draft rows), so the status is read from there — the
same source svv5AddToMaster / svv8UnadvertiseAlbum write back to.

Kept separate from the batch modules so a dialog can show the current state
without running (or being able to run) a publish.
"""

from __future__ import annotations

import csv
import io
import logging

logger = logging.getLogger("speciesviewer")


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
        logger.info(f"album_publish: could not read web trip admin: {e}")
        return None
