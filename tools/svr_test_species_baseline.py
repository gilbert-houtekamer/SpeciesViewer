"""Test the frozen lifer baseline without running a merge.

Count Species answers "which species on this trip were new to me?" by comparing
the trip against the master's species list. Once the trip's own pictures are in
the master that comparison silently returns nothing -- every lifer looks like a
species you already had. engine/species_baseline.py fixes that by freezing the
master's list into the trip at creation time; this tool proves the fix without
merging anything.

WHAT IT DOES
  1. Copies a real trip's day summaries into a throwaway sandbox (read-only on
     the source: only metadata/review/<day>/*.txt and speciescollection.csv).
  2. Synthesizes a "post-merge master" -- the real master's species list PLUS
     every species the trip saw. That is the state continuous updates leave you
     in, reached without touching a single picture.
  3. Runs Count Species twice against that master: with the frozen baseline
     present, and with it hidden.

EXPECTED
  with baseline     lifers > 0   (the verdict the trip actually had)
  without baseline  lifers == 0  (the bug this exists to prevent)

Also checks that the frozen baseline reproduces the LIVE master's answer while
the trip is still unmerged -- the baseline must not change today's numbers, only
preserve them.

NOTHING outside the sandbox is written. The real trip and the real master are
opened read-only.

Usage:
    python tools/svr_test_species_baseline.py [--trip PATH] [--master PATH]
                                              [--sandbox DIR] [--keep]

Defaults for --trip / --master are read from svconfig.ini in the repo root.
"""
import argparse
import configparser
import csv
import logging
import os
import re
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "speciesviewer"))

from engine import species_baseline as sb          # noqa: E402
import batch.svr2CountSpecies as count_species     # noqa: E402

SUMMARY_MARKER = "Species specified in this dir:"


# ----------------------------------------------------------------------
# The New column sits at a fixed offset in the report:
#   counter(3) + ' ' + english(32) + dutch(40) = 76
# Blank there means "already known". Do NOT pattern-match loosely -- the
# first day column is also a digit and will be read as a lifer number.
# ----------------------------------------------------------------------
def count_lifers(report_path):
    rows = new = 0
    with open(report_path, encoding="utf-8") as f:
        for line in f:
            if not re.match(r"^ *\d+ ", line) or len(line) < 79:
                continue
            rows += 1
            if line[76:79].strip().isdigit():
                new += 1
    return rows, new


def report_note(report_path):
    with open(report_path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("Lifer baseline"):
                return line.strip()
    return "(no baseline note in report)"


def trip_species(trip_repo):
    """Every scientific name the trip's day summaries list."""
    names = set()
    review = os.path.join(trip_repo, "metadata", "review")
    for day in sorted(os.listdir(review)):
        day_dir = os.path.join(review, day)
        if not os.path.isdir(day_dir):
            continue
        for fn in sorted(os.listdir(day_dir)):
            if not fn.endswith(".txt"):
                continue
            with open(os.path.join(day_dir, fn), encoding="utf-8") as f:
                lines = f.read().split("\n")
            for i, line in enumerate(lines):
                if SUMMARY_MARKER in line:
                    for row in lines[i + 3:]:
                        if row.strip():
                            name = re.split(r"[()/]", row[31:])[0].strip().lower()
                            if name:
                                names.add(name)
    return names


def make_sandbox_trip(real_trip, sandbox):
    """Copy only what Count Species reads: day summaries + the name list."""
    trip = os.path.join(sandbox, "trip")
    active = os.path.join(trip, "metadata", "active")
    os.makedirs(active, exist_ok=True)
    shutil.copy(os.path.join(real_trip, "metadata", "active", "speciescollection.csv"), active)

    review_src = os.path.join(real_trip, "metadata", "review")
    days = 0
    for day in sorted(os.listdir(review_src)):
        src_day = os.path.join(review_src, day)
        if not os.path.isdir(src_day):
            continue
        dst_day = os.path.join(trip, "metadata", "review", day)
        os.makedirs(dst_day, exist_ok=True)
        for fn in os.listdir(src_day):
            if fn.endswith(".txt") and os.path.isfile(os.path.join(src_day, fn)):
                shutil.copy(os.path.join(src_day, fn), dst_day)
        days += 1
    return trip, days


def make_postmerge_master(real_master, trip, sandbox):
    """The real master's species list PLUS the trip's species -- i.e. the master
    as it looks AFTER Merge Pictures into Master has run."""
    fake = os.path.join(sandbox, "master_postmerge")
    active = os.path.join(fake, "metadata", "active")
    os.makedirs(active, exist_ok=True)

    with open(sb.master_species_csv(real_master), encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    fields = list(rows[0].keys())
    have = {(r.get("ScientificName") or "").strip().lower() for r in rows}

    added = 0
    for name in sorted(trip_species(trip)):
        if name in have:
            continue
        blank = {k: "" for k in fields}
        blank["Level"] = "Level.SPECIES"
        blank["ScientificName"] = name
        blank["iPictureCollectionCount"] = "1"
        rows.append(blank)
        added += 1

    with open(os.path.join(active, "species.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    return fake, added


def run_count(trip, master):
    cfg = configparser.ConfigParser()
    cfg["repository"] = {"BiotaDatabase": trip,
                         "BiotaWebRoot": "/trips/Sandbox/",
                         "ReferenceSpecies": sb.master_species_csv(master)}
    cfg["master"] = {"MasterBiotaDatabase": master, "MasterBiotaWebRoot": "/nature/"}
    cfg["project"] = {"PictureDirectory": os.path.join(trip, "unused")}
    cfg["debug"] = {"Classification": "0", "SpeciesFound": "0"}
    count_species.main(None, cfg)
    report = os.path.join(trip, "metadata", "reports", "Species overview.txt")
    rows, lifers = count_lifers(report)
    return report_note(report), rows, lifers


def without_baseline(trip, master):
    """Run with the baseline temporarily hidden -- today's behaviour."""
    path = sb.baseline_path(trip)
    hidden = path + ".hidden"
    os.rename(path, hidden)
    try:
        return run_count(trip, master)
    finally:
        os.rename(hidden, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trip", help="trip repository to build the sandbox from")
    ap.add_argument("--master", help="master repository (read-only)")
    ap.add_argument("--sandbox", help="sandbox directory (default: a temp dir)")
    ap.add_argument("--keep", action="store_true", help="keep the sandbox on exit")
    args = ap.parse_args()

    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(REPO, "svconfig.ini"))
    real_trip = args.trip or cfg.get("repository", "BiotaDatabase", fallback="")
    real_master = args.master or cfg.get("master", "MasterBiotaDatabase", fallback="")
    if not real_trip or not os.path.isdir(os.path.join(real_trip, "metadata", "review")):
        ap.error(f"not a trip repository (no metadata/review): {real_trip!r}")
    if not os.path.exists(sb.master_species_csv(real_master)):
        ap.error(f"no master species list under {real_master!r}")

    # The engine logs every speciescollection read at INFO; four Count Species
    # runs bury the result. Warnings still come through.
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("speciesviewer").setLevel(logging.WARNING)
    sandbox = args.sandbox or tempfile.mkdtemp(prefix="sv_baseline_test_")
    os.makedirs(sandbox, exist_ok=True)
    failures = []
    try:
        print(f"trip     {real_trip}")
        print(f"master   {real_master}   (read-only)")
        print(f"sandbox  {sandbox}\n")

        trip, days = make_sandbox_trip(real_trip, sandbox)
        print(f"Sandbox trip built: {days} day directories.")

        # -- 1. the baseline must not change today's answer -----------------
        if sb.write_baseline(real_master, trip) is None:
            failures.append("baseline could not be written")
            return 1
        frozen_names, meta = sb.read_baseline(sb.baseline_path(trip))
        print(f"Baseline frozen: {len(frozen_names)} master species "
              f"({meta.get('zero_picture_rows_skipped', '?')} rows skipped for having no pictures).")
        note_live, _, lifers_live_now = without_baseline(trip, real_master)
        note_frozen, rows, lifers_frozen_now = run_count(trip, real_master)
        print(f"\nAgainst the REAL master (trip not merged) -- must agree:")
        print(f"  live master      {lifers_live_now:4} lifers of {rows} species")
        print(f"  frozen baseline  {lifers_frozen_now:4} lifers of {rows} species")
        if lifers_frozen_now != lifers_live_now:
            failures.append(f"baseline changed today's verdict: "
                            f"{lifers_live_now} -> {lifers_frozen_now}")

        # -- 2. after the merge, only the baseline still knows ---------------
        fake_master, added = make_postmerge_master(real_master, trip, sandbox)
        print(f"\nSynthetic post-merge master: added {added} trip species.")
        note_frozen, _, lifers_frozen = run_count(trip, fake_master)
        note_live, _, lifers_live = without_baseline(trip, fake_master)
        print(f"  WITH baseline     {lifers_frozen:4} lifers   {note_frozen}")
        print(f"  WITHOUT baseline  {lifers_live:4} lifers   {note_live}")
        if lifers_frozen != lifers_frozen_now:
            failures.append(f"baseline verdict drifted after the merge: "
                            f"{lifers_frozen_now} -> {lifers_frozen}")
        if lifers_live != 0:
            failures.append(f"expected the live master to lose every lifer, got {lifers_live}")

        print("\n" + "=" * 66)
        if failures:
            for f in failures:
                print(f"FAIL: {f}")
        else:
            print(f"PASS: the frozen baseline holds {lifers_frozen} lifers where the "
                  f"live master reports {lifers_live}.")
        return 1 if failures else 0
    finally:
        if args.keep or args.sandbox:
            print(f"\nSandbox kept at {sandbox}")
        else:
            shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
