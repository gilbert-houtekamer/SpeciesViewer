"""Classify every GPS'd master picture by the PRECISION of its coordinate.

REPORT ONLY. This tool never writes to a picture, a sidecar or a master CSV.

Why precision is worth looking at: a coordinate that is suspiciously round did not
come out of a GPS receiver, and the master turns out to hold several different
kinds of them, which look identical until you separate them:

  whole DEGREE     (~110 km)  minutes AND seconds are zero. Not a fix at all — a
                              grid reference. In this master these are the
                              waarneming.nl observation exports: JPG-only, numeric
                              observation-ID filenames, NO camera Make/Model, and a
                              round DateTimeOriginal to match. They carry
                              valid-LOOKING GPS, so they plot as bubbles on the
                              species map at places the animal was never seen, and
                              they never surface in a no-GPS tray where they could
                              be placed properly.
  whole ARCMINUTE  (~1.8 km)  rare; worth an eyeball.
  whole ARCSECOND  (~31 m)    a REAL camera fix. The Olympus TG-3 (built-in GPS)
                              writes whole arcseconds, and caches its last fix
                              across several frames — which is why a handful of
                              pictures can share one coordinate. Nothing wrong
                              with these; do not "fix" them.

That last row is the reason precision must never be used as a PROVENANCE flag
(i.e. to decide whether we may overwrite a coordinate): a camera really does emit
round coordinates. See the GPS-provenance work — provenance lives in
GPSProcessingMethod, not in how many decimals a value happens to print with.

Usage:
    python tools/svr_gps_precision_scan.py [--db PATH] [--report-dir DIR]
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

# "52 deg 0' 0.00" N, 4 deg 0' 0.00" E" → the three numbers of each half.
_DMS = re.compile(r"(\d+) deg (\d+)' ([0-9.]+)\"")

# ASCII only: these are printed to a Windows console (cp1252), where an em-dash or
# an arrow raises UnicodeEncodeError and takes the whole report down with it.
DEGREE = "whole degree (~110 km grid - NOT a fix)"
MINUTE = "whole arcminute (~1.8 km)"
SECOND = "whole arcsecond (~31 m - a TG-3 writes these)"
FULL = "full precision (camera fix)"


def classify(gps: str) -> str | None:
    """Precision class of a GPSPosition string, or None if unparseable."""
    parts = _DMS.findall(gps or "")
    if len(parts) != 2:
        return None
    minutes = {int(p[1]) for p in parts}
    seconds = {float(p[2]) for p in parts}
    if minutes == {0} and seconds == {0.0}:
        return DEGREE
    if seconds == {0.0}:
        return MINUTE
    if all(float(p[2]) == int(float(p[2])) for p in parts):
        return SECOND
    return FULL


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="master SQLite index (default: the configured one)")
    ap.add_argument("--report-dir", default="gps_report",
                    help="where to write the CSV (default: ./gps_report)")
    args = ap.parse_args()

    if args.db:
        db = Path(args.db)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))
        from db.master_db import db_path
        db = db_path()
    if not db.is_file():
        print(f"master index not found: {db}")
        return 1

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        'SELECT "UniquePictureName","GPSPosition","CameraCreator","CameraModel",'
        '       "DateTimeOriginal","WetSoortNaam","NLSoortNaam","BestPictureFile"'
        '  FROM pictures WHERE TRIM(COALESCE("GPSPosition","")) <> ""').fetchall()

    counts: Counter = Counter()
    suspect = []
    for r in rows:
        klass = classify(r["GPSPosition"])
        if klass is None:
            continue
        counts[klass] += 1
        if klass != FULL:
            suspect.append((klass, r))

    print(f"master pictures with a coordinate: {len(rows):,}\n")
    for klass, n in counts.most_common():
        print(f"  {n:7,}  {klass}")

    # The grid points themselves: a handful of coordinates hold nearly all of it.
    grid = Counter(r["GPSPosition"] for k, r in suspect if k == DEGREE)
    if grid:
        print(f"\nwhole-degree grid points ({len(grid)} distinct):")
        for gps, n in grid.most_common(10):
            print(f"  {n:5}x  {gps}")

    cams = Counter(((r["CameraCreator"] or "").strip() or "(no camera in EXIF)")
                   for k, r in suspect if k == SECOND)
    if cams:
        print("\nwhole-arcsecond pictures — camera:")
        for cam, n in cams.most_common(5):
            print(f"  {n:5}  {cam}")

    out_dir = Path(args.report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "gps_precision_suspect.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Precision", "GPSPosition", "UniquePictureName", "Species",
                    "DutchName", "CameraCreator", "CameraModel", "DateTimeOriginal",
                    "BestPictureFile"])
        for klass, r in sorted(suspect, key=lambda x: (x[0], x[1]["GPSPosition"] or "")):
            w.writerow([klass, r["GPSPosition"], r["UniquePictureName"],
                        r["WetSoortNaam"], r["NLSoortNaam"], r["CameraCreator"],
                        r["CameraModel"], r["DateTimeOriginal"], r["BestPictureFile"]])
    print(f"\nwrote {len(suspect):,} rows -> {out}")
    print("REPORT ONLY — nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
