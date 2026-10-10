"""Test the lifer count without running a merge.

Count Species answers "which species on this trip were new to me?" by comparing
the trip against everything the master already holds from BEFORE the trip's
start date. Three things can silently corrupt that answer, and this exercises
all three against a real trip's data in a throwaway sandbox:

  1. Merging THIS trip into the master. Its species arrive in the master and,
     under a naive "is it in the master?" test, every lifer stops being new.
  2. Merging an OLDER trip afterwards. A species you had already photographed
     turns up in the master late; a baseline pegged to when the trip repo was
     created never sees it and reports it as new. This is a real bug that was
     found this way -- a shield bug shot on Kreta in March was reported new on
     a May trip, because Kreta was merged after the May trip was set up.
  3. Landscapes. Topics are stored looking exactly like species ("2026" under
     topics/landscape/) and reach the counting code through the day summaries.

Plus the archiving that has to outlive the trip, and the fallbacks used when a
trip has no start date to reckon from.

NOTHING outside the sandbox is written; the real trip and master are read only.

Usage:
    python tools/svr_test_species_baseline.py [--trip PATH] [--master PATH]
                                              [--sandbox DIR] [--keep]

Defaults for --trip / --master are read from svconfig.ini (utils.runtime_files.svconfig_path).
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
from engine import trip_verdict as tv              # noqa: E402
import batch.svr2CountSpecies as count_species     # noqa: E402
import batch.svr7ArchiveVerdict as archive_verdict  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))
SUMMARY_MARKER = "Species specified in this dir:"


# ----------------------------------------------------------------------
# sandbox construction
# ----------------------------------------------------------------------
def make_sandbox_trip(real_trip, sandbox):
    """Copy only what Count Species reads: day summaries, the name list, and
    trip.ini (for the start date and the trip's identity)."""
    trip = os.path.join(sandbox, "trip")
    active = os.path.join(trip, "metadata", "active")
    os.makedirs(active, exist_ok=True)
    shutil.copy(os.path.join(real_trip, "metadata", "active", "speciescollection.csv"), active)
    ini = os.path.join(real_trip, "trip.ini")
    if os.path.isfile(ini):
        shutil.copy(ini, os.path.join(trip, "trip.ini"))

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


def make_sandbox_master(real_master, sandbox, before_day):
    """A master holding only pictures taken BEFORE `before_day` — the master as
    it stood when the trip began, whatever order things were really merged in."""
    master = os.path.join(sandbox, "master")
    active = os.path.join(master, "metadata", "active")
    os.makedirs(active, exist_ok=True)
    shutil.copy(sb.master_species_csv(real_master), active)

    cutoff = before_day.replace("-", ":")
    kept = 0
    with open(sb.master_pictures_csv(real_master), encoding="utf-8", newline="") as src:
        reader = csv.DictReader(src, delimiter=";")
        fields = reader.fieldnames
        with open(sb.master_pictures_csv(master), "w", encoding="utf-8", newline="") as dst:
            w = csv.DictWriter(dst, fieldnames=fields, delimiter=";")
            w.writeheader()
            for row in reader:
                if (row.get("DateTimeOriginal") or "")[:10] < cutoff:
                    w.writerow(row)
                    kept += 1
    return master, fields, kept


def add_pictures(master, fields, species_dates):
    """Append pictures to the sandbox master: [(scientific_name, 'YYYY:MM:DD')].
    Stands in for a merge, without moving a single file."""
    with open(sb.master_pictures_csv(master), "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        for name, day in species_dates:
            row = {k: "" for k in fields}
            row["WetSoortNaam"] = name
            row["DateTimeOriginal"] = f"{day} 12:00:00"
            row["UniquePictureName"] = f"sandbox-{name.replace(' ', '-')}-{day}"
            w.writerow(row)


# ----------------------------------------------------------------------
# running Count Species
# ----------------------------------------------------------------------
def sandbox_config(trip, master):
    cfg = configparser.ConfigParser()
    cfg["repository"] = {"BiotaDatabase": trip,
                         "BiotaWebRoot": "/trips/Sandbox/",
                         "ReferenceSpecies": sb.master_species_csv(master)}
    cfg["master"] = {"MasterBiotaDatabase": master, "MasterBiotaWebRoot": "/nature/"}
    cfg["project"] = {"PictureDirectory": os.path.join(trip, "unused")}
    cfg["debug"] = {"Classification": "0", "SpeciesFound": "0"}
    return cfg


def run_count(trip, master):
    """Returns the report's own account of itself — never re-derived from the
    fixed-width columns, which shift when a species name overruns its field."""
    count_species.main(None, sandbox_config(trip, master))
    return tv.parse_report(tv.report_path(trip))


def trip_species(trip):
    """Every scientific name the trip's day summaries list."""
    names = set()
    review = os.path.join(trip, "metadata", "review")
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


def lifer_names(report_path):
    """The scientific names carrying a New number, read from the report's own
    species rows via the trip's summaries — used to pick a victim for the
    older-trip test."""
    return None  # not needed; see pick_a_lifer


def pick_a_lifer(trip, master):
    """A species this trip reports as new — the one an older trip merged later
    would wrongly leave as new."""
    known, _ = sb.collect_species_before(master, read_fromdate(trip))
    return sorted(trip_species(trip) - (known or set()))


def read_fromdate(trip):
    from utils.trip_config import read_trip_ini
    return (read_trip_ini(trip) or {}).get("fromdate", "")


def _fmt(ok):
    return "ok " if ok else "BAD"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trip", help="trip repository to build the sandbox from")
    ap.add_argument("--master", help="master repository (read-only)")
    ap.add_argument("--sandbox", help="sandbox directory (default: a temp dir)")
    ap.add_argument("--keep", action="store_true", help="keep the sandbox on exit")
    args = ap.parse_args()

    cfg = configparser.ConfigParser()
    from utils.runtime_files import svconfig_path
    cfg.read(svconfig_path(), encoding="utf-8")
    real_trip = args.trip or cfg.get("repository", "BiotaDatabase", fallback="")
    real_master = args.master or cfg.get("master", "MasterBiotaDatabase", fallback="")
    if not real_trip or not os.path.isdir(os.path.join(real_trip, "metadata", "review")):
        ap.error(f"not a trip repository (no metadata/review): {real_trip!r}")
    if not os.path.exists(sb.master_pictures_csv(real_master)):
        ap.error(f"no master picture index under {real_master!r}")

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
        fromdate = read_fromdate(trip)
        if not fromdate:
            ap.error("the trip has no fromdate in trip.ini — nothing to reckon from")
        master, fields, kept = make_sandbox_master(real_master, sandbox, fromdate)
        print(f"Sandbox trip: {days} days, starts {fromdate}.")
        print(f"Sandbox master: {kept} pictures, all from before the trip.\n")

        # -- 1. the baseline of record ---------------------------------------
        base = run_count(trip, master)
        n = base["new_count"]
        print(f"Verdict: {n} new of {base['species_count']} species, "
              f"{base['days_summarized']}/{base['days_total']} days summarized.")
        print(f"  {base['note']}")
        if not n:
            failures.append("the trip reports no new species at all — nothing to test")

        # -- 2. landscapes must not be counted as species --------------------
        report_text = open(tv.report_path(trip), encoding="utf-8").read()
        ignored = re.search(r"\((\d+) landscape/topic entries were ignored", report_text)
        has_topic_row = re.search(r"^ *\d+ +20\d\d\b", report_text, re.M)
        print(f"\n  {_fmt(bool(ignored))} landscapes ignored: "
              f"{ignored.group(1) if ignored else 'none reported'}")
        print(f"  {_fmt(not has_topic_row)} no topic row in the species table")
        if has_topic_row:
            failures.append(f"a landscape/topic was counted as a species: "
                            f"{has_topic_row.group(0).strip()}")

        # -- 3. merging THIS trip must not change the verdict -----------------
        own = sorted(trip_species(trip))
        add_pictures(master, fields, [(s, fromdate.replace("-", ":")) for s in own])
        after_own = run_count(trip, master)
        print(f"\n  {_fmt(after_own['new_count'] == n)} after merging this trip: "
              f"{after_own['new_count']} new (was {n})")
        if after_own["new_count"] != n:
            failures.append(f"merging the trip itself changed the verdict: "
                            f"{n} -> {after_own['new_count']}")

        # -- 4. merging an OLDER trip must retire that lifer ------------------
        # The regression test for the shield-bug bug: a species photographed
        # BEFORE this trip, reaching the master only now, is not a lifer.
        lifers = pick_a_lifer(trip, master)
        if lifers:
            victim = lifers[0]
            earlier = f"{int(fromdate[:4]) - 1}:06:15"
            add_pictures(master, fields, [(victim, earlier)])
            after_old = run_count(trip, master)
            ok = after_old["new_count"] == n - 1
            print(f"  {_fmt(ok)} after merging an OLDER trip carrying "
                  f"{victim!r}: {after_old['new_count']} new (want {n - 1})")
            if not ok:
                failures.append(f"a species photographed before the trip still counts "
                                f"as new: {after_old['new_count']}, want {n - 1}")
        else:
            failures.append("could not find a lifer to test the older-trip case with")

        # -- 5. the verdict must outlive the trip ----------------------------
        archive_master = os.path.join(sandbox, "master_archive")
        os.makedirs(os.path.join(archive_master, "admin"), exist_ok=True)
        acfg = sandbox_config(trip, master)
        acfg["master"]["MasterBiotaDatabase"] = archive_master
        row = archive_verdict.main(None, acfg)
        wanted = [os.path.join(tv.archive_dir(archive_master),
                               f"{row['trip_id']} species overview.txt"),
                  tv.summary_path(archive_master)]
        print(f"\n  archived verdict for {row['trip_id']!r}: "
              f"{row['new_count']} new of {row['species_count']} species")
        for path in wanted:
            if os.path.isfile(path):
                print(f"  ok  {os.path.relpath(path, archive_master)}")
            else:
                failures.append(f"archive did not write {path}")
        archive_verdict.main(None, acfg)      # only the last version is kept
        with open(tv.summary_path(archive_master), encoding="utf-8", newline="") as f:
            mine = [r for r in csv.DictReader(f, delimiter=";")
                    if r.get("trip_id") == row["trip_id"]]
        print(f"  {_fmt(len(mine) == 1)} summary holds {len(mine)} row after 2 runs")
        if len(mine) != 1:
            failures.append(f"re-archiving left {len(mine)} summary rows (want 1)")

        # -- 6. fallbacks, for a trip with no start date ----------------------
        # Same trip, trip.ini removed: the frozen file must take over and must
        # not claim to be something it is not.
        noini = os.path.join(sandbox, "trip_nodate")
        shutil.copytree(trip, noini)
        os.remove(os.path.join(noini, "trip.ini"))
        src, snap_date, kind = sb.source_for_date(real_master, fromdate)
        sb.write_baseline(real_master, noini, source_csv=src, taken_at=fromdate,
                          reconstructed_from=f"{snap_date}_species.csv" if snap_date else "live")
        fb = run_count(noini, master)
        ok = fb["baseline"] == "reconstructed"
        print(f"\n  {_fmt(ok)} without a start date it falls back and says so:")
        print(f"      {fb['note'][:96]}…")
        if not ok:
            failures.append(f"fallback baseline reported as {fb['baseline']!r}")

        print("\n" + "=" * 68)
        if failures:
            for f in failures:
                print(f"FAIL: {f}")
        else:
            print(f"PASS: {n} lifers, and neither this trip's merge, a later merge of "
                  f"an older trip, nor the landscapes can move that number.")
        return 1 if failures else 0
    finally:
        if args.keep or args.sandbox:
            print(f"\nSandbox kept at {sandbox}")
        else:
            shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
