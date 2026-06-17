#!/usr/bin/env python3
# svr3TripRegistry.py: Phase-1 runner for the cross-machine trip lifecycle
# registry. Scans this machine's trips + virttrips (roots come from the
# per-machine sv_config_<machine>.ini) and writes a trip_registry_<machine>.csv
# shard plus a console summary. No network yet — eyeball the CSV against trips
# you know, then we add the master species count and FTP sync.
#
# Successor concept to svr3StatusProjects.py (which printed a per-day grid for
# the master only); this rolls each trip up to one lifecycle row across machines.

import os
import sys
import logging
import argparse

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    reporoot = os.path.abspath(os.path.join(scriptpath, os.pardir, os.pardir))
    os.chdir(reporoot)
    sys.path.append(os.path.join(reporoot, "speciesviewer"))  # makes 'import engine'/'utils' work

import engine.trip_registry as trip_registry
import engine.trip_registry_html as trip_registry_html
import engine.trip_thumbs as trip_thumbs
from utils.config import MACHINE_NAME, get_trips_root, get_virttrips_root, get_setting

logger = logging.getLogger("speciesviewer")


def _print_table(rows):
    if not rows:
        print("No trips or virttrips found on this machine.")
        return
    print(f"\n{'Trip':20} {'Days':>4} {'Cmt':>4} {'pics':>6} {'inMast':>6} {'miss':>5} "
          f"{'newSp':>5} {'Verdict':22} {'Status'}")
    print("-" * 104)
    for r in rows:
        print(f"{r['trip_name'][:20]:20} {r['num_days']:>4} {r['days_committed']:>4} "
              f"{str(r['local_pics']):>6} {str(r['local_in_master']):>6} "
              f"{str(r['local_missing']):>5} {str(r['new_species']):>5} "
              f"{str(r['delete_local']):22} {r['status']}")


def main(args):
    machine = args.machine or MACHINE_NAME
    trips_root = args.trips_root or get_trips_root()
    virttrips_root = args.virttrips_root or get_virttrips_root()
    master = args.master or get_setting("master", "masterbiotadatabase", fallback="")

    print(f"Machine        : {machine}")
    print(f"Trips root     : {trips_root or '(none)'}")
    print(f"VirtTrips root : {virttrips_root or '(none)'}")

    print(f"Master         : {master or '(none)'}")
    rows = trip_registry.build_registry(machine, trips_root, virttrips_root, master)
    _print_table(rows)

    out_dir = args.out or (os.path.join(master, "admin", "status") if master else
                           os.path.join(reporoot, "data", "status"))
    out_path = os.path.join(out_dir, f"trip_registry_{machine}.csv")
    written = trip_registry.write_registry_csv(rows, out_path)
    print(f"\nWrote {len(rows)} rows to {written}")

    # Local private management console (index + per-trip drill-downs).
    console_dir = os.path.join(out_dir, "console")
    thumb_map = None
    if args.thumbs:
        print(f"Generating thumbnails (cap {args.max_thumbs})...")
        thumb_map = trip_thumbs.generate_thumbs(rows, console_dir,
                                                max_thumbs=args.max_thumbs,
                                                per_trip=args.per_trip_thumbs)
        print(f"  {len(thumb_map)} thumbnails ready")
    index = trip_registry_html.render_console(rows, console_dir, machine, thumb_map)
    print(f"Console: {index}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svr3TripRegistry (Phase 1: local scan)")
    parser.add_argument("--machine", default=None, help="override machine name")
    parser.add_argument("--trips-root", default=None, help="override trips root dir")
    parser.add_argument("--virttrips-root", default=None, help="override virttrips root dir")
    parser.add_argument("--master", default=None, help="override master repository dir")
    parser.add_argument("--out", default=None, help="override output directory")
    parser.add_argument("--thumbs", action="store_true",
                        help="generate drill-down thumbnails for missing pictures")
    parser.add_argument("--max-thumbs", type=int, default=300,
                        help="global cap on thumbnails generated")
    parser.add_argument("--per-trip-thumbs", type=int, default=60,
                        help="max thumbnails per trip (NEW-species first)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    try:
        main(args)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
        raise
