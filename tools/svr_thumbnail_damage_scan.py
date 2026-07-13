"""List the master JPGs that were overwritten by their own thumbnail.

REPORT ONLY. Writes a CSV; changes nothing.

The bug (fixed in engine/websupport.py): `getSmallJPG` derived a thumbnail name
with `filename.replace('.jpg', '-small.jpg')` — CASE-SENSITIVE. An uppercase
`X.JPG` therefore came back UNCHANGED, and `jpgResizeCopy` then saved the 600x450
render to that same name: **on top of the original file**. The full-resolution
out-of-camera JPEG was replaced by its own thumbnail, in place.

It hid its own tracks. The summary pages ask for `getSmallJPG(BestJPG)` and got the
file itself — which by then really WAS 600x450, so it rendered as a perfectly
plausible thumbnail and nothing looked wrong.

What is (and isn't) lost: these are out-of-camera JPEGs, and the RAW survives for
every one of them, so no PHOTOGRAPH is gone — only its JPEG rendition. The species
pages are unaffected (they show the _DxO renders). The harm is to the master as an
archive, and to the best-pick logic, which now treats a 600px file as a picture's
best available image.

A file is reported when it is an uppercase `.JPG` AND its recorded pixel size is one
of jpgResizeCopy's exact outputs (600x450 / 450x600 / 600x600) — the fingerprint of
the overwrite, not a guess about quality.

Usage:
    python tools/svr_thumbnail_damage_scan.py [--report-dir DIR]
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from pathlib import Path

# The exact sizes jpgResizeCopy writes — its signature.
THUMB_SIZES = ("600x450", "450x600", "600x600")

RAW_EXTS = {"orf", "nef", "cr2", "cr3", "arw", "rw2", "raf"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report-dir", default="thumbnail_damage_report")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))
    from db.master_db import db_path

    con = sqlite3.connect(str(db_path()))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        'SELECT f."File:FileName" AS fn, f."WindowsFileName" AS path,'
        '       f."ImageSize" AS size, f."UniquePictureName" AS upn,'
        '       p."WetSoortNaam" AS sci, p."NLSoortNaam" AS nl, p."BestJPG" AS best'
        '  FROM files f JOIN pictures p'
        '    ON p."UniquePictureName" = f."UniquePictureName"'
        ' WHERE f."File:FileName" GLOB ?', ("*.JPG",)).fetchall()

    damaged = [r for r in rows if (r["size"] or "") in THUMB_SIZES]
    at_risk = [r for r in rows if (r["size"] or "") not in THUMB_SIZES]

    out_dir = Path(args.report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "thumbnail_damage.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Species", "DutchName", "UniquePictureName", "DamagedFile",
                    "SizeNow", "IsPictureBestJPG", "RawOnDisk", "Path"])
        for r in damaged:
            raws = [x["f"] for x in con.execute(
                'SELECT "File:FileName" AS f FROM files WHERE "UniquePictureName"=?',
                (r["upn"],))
                if (x["f"] or "").rsplit(".", 1)[-1].lower() in RAW_EXTS]
            w.writerow([r["sci"], r["nl"], r["upn"], r["fn"], r["size"],
                        "yes" if r["best"] == r["fn"] else "",
                        raws[0] if raws else "NO RAW",
                        os.path.dirname(r["path"] or "")])

    species = {r["sci"] for r in damaged}
    no_raw = 0
    for r in damaged:
        has = any((x["f"] or "").rsplit(".", 1)[-1].lower() in RAW_EXTS
                  for x in con.execute(
                      'SELECT "File:FileName" AS f FROM files '
                      ' WHERE "UniquePictureName"=?', (r["upn"],)))
        no_raw += (not has)

    print(f"DAMAGED (overwritten by their own thumbnail): {len(damaged):,} files"
          f" across {len(species):,} species")
    print(f"  ...with NO surviving RAW (unrecoverable): {no_raw}")
    print(f"AT RISK (uppercase .JPG, still full size)  : {len(at_risk):,} files")
    print(f"    -> the fix in engine/websupport.py stops these being hit.")
    print(f"\nwrote {len(damaged):,} rows -> {out}")
    print("REPORT ONLY - nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
