"""Dry-run: preview how landscape / topic photos would be ingested into the master.

Today a landscape (a photo marked `landscape`/`landschap` with no species) is
EXCLUDED from the master at freeze — it is never backed up. The topics work gives
it a home under a `topics/` branch beside `biota/` (topics/landscape/<year>/).

This tool writes NOTHING. It reads a trip's frozen day CSVs and reports, per
landscape candidate, where it would land, its authoritative rating+colour (read
from the DxO .dop, not the .xmp), whether it has a rendered JPG, and any
conflicts. It is the review artifact to read before the ingest is switched on.

    # one day
    python tools/topics_dry_run.py --day "E:/Dropbox/trips/kretadb/metadata/review/2026-03-13"
    # a whole trip (all review days)
    python tools/topics_dry_run.py --trip "E:/Dropbox/trips/kretadb"
    # cross-check "already in master" against the live master DB
    python tools/topics_dry_run.py --trip "E:/Dropbox/trips/kretadb" --master "E:/Dropbox/naturedb"
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "speciesviewer"))

from engine import topics_scan            # noqa: E402


def _day_dirs(args) -> list[str]:
    if args.day:
        return [args.day]
    review = os.path.join(args.trip, "metadata", "review")
    days = sorted(
        os.path.dirname(p) for p in glob.glob(os.path.join(review, "*", "data_pictures.csv"))
    )
    return days


def _load_master_upns(master_root: str):
    from db.master_db import MasterDB
    db = MasterDB(master_root)
    db.ensure_fresh()
    con = db._connect()
    upns = {u for (u,) in con.execute("SELECT UniquePictureName FROM pictures")}
    print(f"[master] {len(upns)} UPNs loaded for the already-in-master check\n")
    return upns


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--trip", help="trip DB root (scans every metadata/review/<day>)")
    g.add_argument("--day", help="a single metadata/review/<day> directory")
    ap.add_argument("--master", help="master root, to flag already-in-master UPNs")
    args = ap.parse_args()

    master_upns = _load_master_upns(args.master) if args.master else None

    all_candidates = []
    totals = {"candidates": 0, "ready": 0, "no_jpg": 0, "no_leaf": 0,
              "in_master": 0, "mixed_kept_as_species": 0, "pictures_scanned": 0}
    for day in _day_dirs(args):
        pics = os.path.join(day, "data_pictures.csv")
        files = os.path.join(day, "data_files.csv")
        if not (os.path.exists(pics) and os.path.exists(files)):
            continue
        candidates, summary = topics_scan.scan_trip_csv(pics, files, master_upns=master_upns)
        for k in totals:
            totals[k] += summary.get(k, 0)
        all_candidates.extend(candidates)

    print(topics_scan.format_report(all_candidates, totals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
