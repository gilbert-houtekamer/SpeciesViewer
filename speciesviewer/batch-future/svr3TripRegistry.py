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
from utils.config import MACHINE_NAME, get_trips_root, get_virttrips_root, get_setting

logger = logging.getLogger("speciesviewer")


def _print_table(rows):
    if not rows:
        print("No trips or virttrips found on this machine.")
        return
    print(f"\n{'Trip':24} {'Type':9} {'Days':>4} {'Commit':>6} {'inMaster':>8} {'?plac':>6} {'Status':20} {'Range'}")
    print("-" * 108)
    for r in rows:
        rng = f"{r['fromdate']}..{r['todate']}".strip(".")
        print(f"{r['trip_name'][:24]:24} {r['type']:9} {r['num_days']:>4} "
              f"{r['days_committed']:>6} {str(r['species_files_in_master']):>8} "
              f"{str(r['placeholder_in_master']):>6} {r['status']:20} {rng}")


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svr3TripRegistry (Phase 1: local scan)")
    parser.add_argument("--machine", default=None, help="override machine name")
    parser.add_argument("--trips-root", default=None, help="override trips root dir")
    parser.add_argument("--virttrips-root", default=None, help="override virttrips root dir")
    parser.add_argument("--master", default=None, help="override master repository dir")
    parser.add_argument("--out", default=None, help="override output directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    try:
        main(args)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
        raise
