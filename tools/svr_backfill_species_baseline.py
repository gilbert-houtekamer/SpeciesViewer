"""Give trips created before the frozen-baseline feature a baseline anyway.

Count Species tells you which species on a trip were new to you by comparing
the trip against the master's species list frozen at trip creation. Trips
created before that existed have no such file, so they fall back to comparing
against the LIVE master — which reports zero new species the moment the trip
has been merged. This reconstructs the missing baseline from the master's own
dated snapshots.

HOW THE SNAPSHOT IS CHOSEN — and why it is not the obvious one:

sv7WriteCommit copies the master's active/species.csv to
active/backup/<date>_species.csv *before* overwriting it, once per commit day.
So a snapshot dated D holds the master as it stood up to the first commit on D.
The master's state at trip creation is therefore the snapshot of the EARLIEST
commit day AFTER the trip was created — not the latest one before it.

Getting that backwards is not symmetric. Too NEW a baseline contains species
the trip itself contributed and hides real lifers, silently. Too OLD only
invents visible false ones you would notice. So when the two candidates cannot
be told apart, the older one wins. Each row is labelled:

    snapshot   a snapshot taken after the trip was created: the master as the
               trip found it, up to the nearest commit.
    same-day   a snapshot exists from the creation date itself, and whether the
               commit behind it came before or after the trip was created is
               unknowable — so this is either exact or slightly old, never new.
    current    nothing has been committed since, so the live master still is
               the state at creation.

CAVEAT: only sv7 commits leave dated snapshots. Master edits made through the
database (Add Pictures to Master, deletes, move-to-species) change species.csv
without one, so a reconstruction is good to the nearest commit, not the minute.
A reconstructed baseline says so in its header, and Count Species repeats it in
the report rather than claiming the trip froze it itself.

DEFAULT = DRY RUN. Reports what it would write and changes nothing. Pass
--apply to write the baselines. Existing baselines are never overwritten.

Usage:
    python tools/svr_backfill_species_baseline.py [--trips-root DIR]
        [--master DIR] [--trip PATH] [--apply]

Defaults come from svconfig.ini / sv_config_<hostname>.ini in the repo root.
"""
import argparse
import configparser
import os
import socket
import sys
from configparser import ConfigParser

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "speciesviewer"))

from engine import species_baseline as sb          # noqa: E402


def read_created(trip_repo):
    """The trip's creation stamp from trip.ini, e.g. '2026-07-16 18:53'."""
    ini = os.path.join(trip_repo, "trip.ini")
    if not os.path.isfile(ini):
        return ""
    cfg = ConfigParser()
    try:
        with open(ini, encoding="utf-8") as f:
            cfg.read_string(f.read())
    except Exception:
        return ""
    return cfg.get("trip_config", "created", fallback="").strip()


def find_trips(trips_root):
    if not os.path.isdir(trips_root):
        return []
    out = []
    for name in sorted(os.listdir(trips_root)):
        path = os.path.join(trips_root, name)
        if os.path.isfile(os.path.join(path, "trip.ini")):
            out.append(path)
    return out


def default_paths():
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(REPO, "svconfig.ini"))
    master = cfg.get("master", "MasterBiotaDatabase", fallback="")
    host_ini = os.path.join(REPO, f"sv_config_{socket.gethostname().lower()}.ini")
    trips_root = ""
    if os.path.isfile(host_ini):
        hc = configparser.ConfigParser()
        hc.read(host_ini)
        trips_root = hc.get("trip", "tripsrootdirectory", fallback="")
    if not trips_root:
        trip_db = cfg.get("repository", "BiotaDatabase", fallback="")
        trips_root = os.path.dirname(trip_db) if trip_db else ""
    return master, trips_root


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--master", help="master repository")
    ap.add_argument("--trips-root", help="folder holding the trip repositories")
    ap.add_argument("--trip", action="append",
                    help="a single trip repository (repeatable); skips --trips-root")
    ap.add_argument("--apply", action="store_true",
                    help="actually write the baselines (default: dry run)")
    args = ap.parse_args()

    cfg_master, cfg_trips_root = default_paths()
    master = args.master or cfg_master
    if not os.path.exists(sb.master_species_csv(master)):
        ap.error(f"no master species list under {master!r}")

    trips = args.trip or find_trips(args.trips_root or cfg_trips_root)
    if not trips:
        ap.error("no trips found — pass --trip or --trips-root")

    snaps = sb.snapshots(master)
    print(f"master        {master}")
    print(f"snapshots     {len(snaps)}"
          + (f"  ({snaps[0][0]} … {snaps[-1][0]})" if snaps else ""))
    print(f"mode          {'APPLY' if args.apply else 'DRY RUN — nothing is written'}\n")

    header = f"{'trip':<24} {'created':<17} {'source':<22} {'kind':<9} species"
    print(header)
    print("-" * len(header))

    written = skipped = problems = candidates = 0
    for trip in trips:
        name = os.path.basename(os.path.normpath(trip))
        existing = sb.baseline_path(trip)
        if os.path.exists(existing):
            _, meta = sb.read_baseline(existing)
            print(f"{name:<24} {'-':<17} {'(has one already)':<22} "
                  f"{'skip':<9} {meta.get('species_count', '?')}")
            skipped += 1
            continue

        created = read_created(trip)
        if not created:
            print(f"{name:<24} {'?':<17} {'no created= in trip.ini':<22} {'SKIP':<9} -")
            problems += 1
            continue

        src, snap_date, kind = sb.source_for_date(master, created)
        if not src:
            print(f"{name:<24} {created:<17} {'no usable snapshot':<22} {'SKIP':<9} -")
            problems += 1
            continue

        try:
            names, _zero, _topics = sb.collect_species_from_csv(src)
        except Exception as e:
            print(f"{name:<24} {created:<17} {'unreadable':<22} {'SKIP':<9} {e}")
            problems += 1
            continue

        label = (f"{snap_date}_species.csv" if snap_date else "live master")
        print(f"{name:<24} {created:<17} {label:<22} {kind:<9} {len(names)}")
        candidates += 1

        if args.apply:
            origin = (f"{snap_date}_species.csv" if snap_date
                      else "the live master (nothing committed since)")
            path = sb.write_baseline(master, trip, source_csv=src,
                                     taken_at=created, reconstructed_from=origin)
            if path:
                written += 1
            else:
                problems += 1

    print()
    if args.apply:
        print(f"Wrote {written} baseline(s); {skipped} already had one; "
              f"{problems} could not be done.")
    else:
        print(f"Would write {candidates} baseline(s); {skipped} already have one; "
              f"{problems} could not be done.")
        print("Re-run with --apply to write them.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
