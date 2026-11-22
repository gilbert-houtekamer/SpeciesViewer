"""Inventory of the master's non-picture files — what is an only copy, what is
a backup, and what is left over (backlog 1.23).

ONE JOB: walk the master (everything except `biota/`, the pictures) and sort
every file into a category from the RULES table below, with its size and date.
Writes a CSV + prints a summary. READ-ONLY: it never moves, renames or deletes.

INVARIANT: every file lands in exactly one category; a file no rule knows is
UNKNOWN and listed by name, never silently counted as something else.

The categories (worst to lose first):

  KEEP        the only copy — canonical, cannot be rebuilt
  TRASH       deleted by the program, waiting for you to empty it
  BACKUP      the rotating daily backup (gzipped, 14 kept, checked first)
  COPY        a backup copy with NO retention: grows forever
  STATE       rebuildable, but at a cost (e.g. what is on the web server)
  WEB         the master's website files
  SETUP       starter files shipped with the program
  REPORT      output of a report or a review; re-run it to get it back
  LEFTOVER    dry-run / one-off / test output that nothing reads
  OTHER       belongs to another program
  UNKNOWN     no rule matches — look at it

Usage (from the repository root):
  python tools/svr_admin_inventory.py                 # the configured master
  python tools/svr_admin_inventory.py --master E:/Dropbox/naturedb
  python tools/svr_admin_inventory.py --list LEFTOVER  # every file of one category
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime
from fnmatch import fnmatchcase
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))

ORDER = ["KEEP", "TRASH", "BACKUP", "COPY", "STATE", "WEB", "SETUP", "REPORT",
         "LEFTOVER", "OTHER", "UNKNOWN"]

# (pattern on the lower-case path relative to the master, category, why).
# First match wins, so the specific rules come before the general ones.
RULES = [
    # ── the only copies ──────────────────────────────────────────────────────
    ("metadata/active/data_files.csv",        "KEEP", "master list of files"),
    ("metadata/active/data_pictures.csv",     "KEEP", "master list of pictures"),
    ("metadata/active/species.csv",           "KEEP", "master species list"),
    ("metadata/active/speciescollection.csv", "KEEP", "the names SpeciesViewer recognises"),
    ("metadata/active/speciesnames.csv",      "KEEP", "other species names (local names)"),
    ("admin/trip_admin.csv",                  "KEEP", "every trip and album: names, folders, web address"),
    ("admin/blog_admin.csv",                  "KEEP", "blog settings per trip"),
    ("admin/master_commits.csv",              "KEEP", "which trip days went to the master"),
    ("admin/transcache.csv",                  "KEEP", "translations you checked or typed"),
    ("topics/*",                              "KEEP", "landscape/topic pictures"),
    # ── deleted, not yet final ───────────────────────────────────────────────
    ("admin/status/orphan_trash/*",           "TRASH", "orphan sidecars moved aside by a repair"),
    (".trash/*",                              "TRASH", "master trash"),
    ("*/.trash/*",                            "TRASH", "master trash"),
    # ── backups ──────────────────────────────────────────────────────────────
    ("metadata/active/backup/????-??-??/*",   "BACKUP", "daily backup (gzipped, last 14 kept)"),
    ("metadata/active/backup/pre-restore-*/*", "BACKUP", "lists kept before a restore"),
    ("metadata/active/backup/*",              "COPY", "copy made when a trip was committed (one per day, never pruned)"),
    ("admin/status/backup/*",                 "COPY", "copy made before a repair (never pruned)"),
    ("metadata/active/*bak*",                 "COPY", "hand/one-off copy"),
    ("metadata/active/*backup*",              "COPY", "hand/one-off copy"),
    ("metadata/active/*.before*",             "COPY", "copy before a one-off fix"),
    ("metadata/active/*pre-*",                "COPY", "copy before a one-off fix"),
    ("metadata/active/*pre_*",                "COPY", "copy before a one-off fix"),
    ("metadata/active/*with-test-species",    "COPY", "copy before a test"),
    ("admin/trip_admin - copy*",              "COPY", "hand copy of trip_admin.csv"),
    ("admin/trip_admin.before*",              "COPY", "copy before a one-off fix"),
    # ── rebuildable at a cost ────────────────────────────────────────────────
    ("admin/ftpcache.json",                   "STATE", "what is on the web server; without it every page is uploaded again"),
    ("metadata/cache/*",                      "STATE", "rebuildable cache"),
    # ── website ──────────────────────────────────────────────────────────────
    ("web/*",                                 "WEB", "master website pages and styles"),
    ("tripindex-*.html",                      "WEB", "trip index page"),
    ("metadata/initialize/*",                 "SETUP", "empty lists a new trip starts from"),
    # ── reports (re-run to regenerate) ───────────────────────────────────────
    ("admin/trip_reports/*",                  "REPORT", "trip species overview"),
    ("admin/trip_species_summary.csv",        "REPORT", "trip verdict summary"),
    ("admin/status/console/*",                "REPORT", "Trip Status Console pages"),
    ("admin/status/shards/*",                 "REPORT", "status downloaded from other computers"),
    ("admin/status/trip_registry_*.csv",      "REPORT", "Trip Status Console data"),
    ("admin/status/species_counter_audit.txt", "REPORT", "species counter check"),
    ("admin/status/species_no_display.txt",   "REPORT", "Species Without a Website Picture"),
    ("admin/status/orphan_sidecars.csv",      "REPORT", "orphan sidecar check"),
    ("admin/status/display_status.csv",       "REPORT", "website picture check"),
    ("admin/status/rebuild_*.csv",            "REPORT", "last master review (input of its repair)"),
    # ── left over ────────────────────────────────────────────────────────────
    ("admin/status/review_*/*",               "LEFTOVER", "old master review (dry run)"),
    ("admin/status/preview_*/*",              "LEFTOVER", "old repair preview (dry run)"),
    ("admin/status/ghost_files.csv",          "LEFTOVER", "one-off ghost-row report (July)"),
    ("admin/status/refile_split_species_*",   "LEFTOVER", "one-off re-file report"),
    ("admin/taxonomy_convert_*.csv",          "LEFTOVER", "one-off species-order conversion log"),
    ("admin/temp/*",                          "LEFTOVER", "temporary files"),
    ("metadata/active/speciescollection-*.csv", "LEFTOVER", "old side copy of the names list"),
    ("metadata/active/speciescollection_*.csv", "LEFTOVER", "old side copy of the names list"),
    ("metadata/active/*.xlsx",                "LEFTOVER", "spreadsheet made by hand"),
    ("metadata/review/statemachine.ini",      "STATE", "progress of the current commit to the master"),
    ("metadata/review/master_rebuild/*",      "LEFTOVER", "work folder of Rebuild Websites (made again on each run)"),
    ("metadata/review/sv_droppool_*",         "LEFTOVER", "temporary folder of Add Pictures to Master"),
    ("metadata/review/*/frozen/biota/*",      "LEFTOVER", "pages + JPGs of an earlier commit to the master (copies; nothing reads them)"),
    ("metadata/review/*",                     "LEFTOVER", "lists of an earlier commit to the master (nothing reads them)"),
    # ── not ours ─────────────────────────────────────────────────────────────
    ("lightroom/*",                           "OTHER", "Lightroom catalog"),
]

SKIP_TOP = {"biota"}          # the pictures themselves: not inventoried here


def classify(rel: str) -> tuple[str, str]:
    r = rel.replace("\\", "/").lower()
    for pat, cat, why in RULES:
        if fnmatchcase(r, pat):
            return cat, why
    return "UNKNOWN", ""


def walk(master: Path):
    for dirpath, dirs, files in os.walk(master):
        rel_dir = Path(dirpath).relative_to(master)
        if rel_dir == Path("."):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_TOP]
        for f in files:
            p = Path(dirpath) / f
            try:
                st = p.stat()
            except OSError:
                continue
            rel = (rel_dir / f).as_posix()
            cat, why = classify(rel)
            yield rel, cat, why, st.st_size, datetime.fromtimestamp(st.st_mtime)


def _mb(n: int) -> str:
    return f"{n / 1e6:,.0f} MB" if n >= 1e6 else f"{n / 1e3:,.0f} KB"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--master", help="master folder (default: the configured one)")
    ap.add_argument("--list", metavar="CATEGORY", help="also print every file of this category")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")   # a cp1252 console
    except AttributeError:
        pass

    master = a.master
    if not master:
        from utils.config import _config
        master = _config.get("master", "masterbiotadatabase", fallback="")
    master = Path(master.strip().strip('"'))
    if not master.is_dir():
        print(f"Master folder not found: {master}")
        return 1

    rows = sorted(walk(master), key=lambda r: (ORDER.index(r[1]), r[0].lower()))

    from utils.cache_paths import app_data_dir
    out_dir = app_data_dir() / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"master_inventory_{datetime.now():%Y%m%d-%H%M%S}.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["category", "path", "bytes", "modified", "why"])
        for rel, cat, why, size, mt in rows:
            w.writerow([cat, rel, size, f"{mt:%Y-%m-%d %H:%M}", why])

    print(f"Master: {master}   (pictures in biota/ not counted)\n")
    total = sum(r[3] for r in rows)
    for cat in ORDER:
        sub = [r for r in rows if r[1] == cat]
        if not sub:
            continue
        size = sum(r[3] for r in sub)
        print(f"{cat:<9} {len(sub):>6} files {_mb(size):>10}")
        groups: dict[str, list] = {}
        for rel, _c, why, sz, _m in sub:
            groups.setdefault(why or rel, []).append(sz)
        for why, sizes in sorted(groups.items(), key=lambda g: -sum(g[1]))[:8]:
            print(f"            {len(sizes):>5} x {_mb(sum(sizes)):>9}  {why}")
    print(f"\nTotal     {len(rows):>6} files {_mb(total):>10}")
    print(f"Report:   {out}")

    if a.list:
        want = a.list.upper()
        print(f"\n── {want} ──")
        for rel, cat, why, size, mt in rows:
            if cat == want:
                print(f"{_mb(size):>9}  {mt:%Y-%m-%d}  {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
