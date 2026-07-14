"""Find (and, when told to, delete) banner-shaped JPGs in the master.

A banner is a photograph cropped to a web-header strip — 2000x270, 2000x400 — that
was exported for a page header and then swept into the master with the rest of the
trip. It is a JPG, so the master's never-trash-a-JPG rule protects it, and it
therefore goes on representing its species: one of them is currently the picture the
whole site uses for the Great Bustard.

Aspect ratio is a HINT, not a verdict. A 3:1 frame is just as likely to be a
deliberate panoramic crop of a crane in flight as it is a banner, and this tool
cannot tell the difference — a human has to look. So the scan reports, and --apply
deletes ONLY the files named on the command line. It will not delete "everything the
scan found": that turns a report into an approval, which is precisely how the wrong
file gets published.

    python tools/svr_banner_scan.py                          # report
    python tools/svr_banner_scan.py --min-aspect 5           # only the extreme ones
    python tools/svr_banner_scan.py --apply --file "a.jpg" --file "b.jpg"

Deletes go through master_mutate.delete_jpg: the bytes move to <master>/.trash (with
a manifest and an undo snapshot), and the species' JPGmap, the picture's BestJPG and
every species/ancestor best-pick that named the file are re-derived in one durable
write. The picture itself survives on its RAW unless the JPG was its only file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "speciesviewer"))

from db.master_db import MasterDB              # noqa: E402
from db import master_mutate                   # noqa: E402
from utils.config import _config               # noqa: E402


def _wh(size: str) -> tuple[int, int]:
    nums = re.findall(r"\d+", size or "")
    return (int(nums[0]), int(nums[1])) if len(nums) >= 2 else (0, 0)


def scan(db, con, min_aspect: float) -> list[dict]:
    rows = con.execute(
        'SELECT f."File:FileName" AS fname, f."UniquePictureName" AS upn,'
        '       f."ImageSize" AS size, p."WetSoortNaam" AS sci,'
        '       p."Rating" AS rating, p."Rate" AS rate'
        '  FROM files f LEFT JOIN pictures p'
        '    ON p."UniquePictureName" = f."UniquePictureName"'
        ' WHERE LOWER(f."WindowsExtension") IN ("jpg","jpeg")').fetchall()

    out = []
    for r in rows:
        w, h = _wh(r["size"])
        if not w or not h:
            continue
        ar = w / h
        if ar < min_aspect:
            continue
        upn = r["upn"] or ""
        siblings = con.execute(
            'SELECT "File:FileName" AS fname, "ImageSize" AS size FROM files'
            ' WHERE "UniquePictureName"=?', (upn,)).fetchall()
        others = [s for s in siblings if s["fname"] != r["fname"]]
        stem = Path(r["fname"]).stem.lower()
        smalls = [s["fname"] for s in others
                  if s["fname"].lower().startswith(stem + "-small")]
        jpgs_left = [s["fname"] for s in others
                     if s["fname"].lower().endswith((".jpg", ".jpeg"))
                     and s["fname"] not in smalls
                     and "-small" not in s["fname"].lower()]
        # any species/ancestor whose best-pick names this file?
        pins = []
        for a in db._species_chain(con, r["sci"] or ""):
            if (a["BestJPG"] or "") == r["fname"] or (a["BestUPN"] or "") == upn:
                pins.append(a["ScientificName"])
        out.append({
            "fname": r["fname"], "upn": upn, "size": r["size"], "ar": round(ar, 2),
            "sci": r["sci"] or "", "rating": r["rating"], "rate": r["rate"],
            "only_file": not others, "smalls": smalls, "jpgs_left": jpgs_left,
            "pins": pins,
        })
    out.sort(key=lambda d: -d["ar"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-aspect", type=float, default=3.0,
                    help="report JPGs at least this many times wider than tall (3.0)")
    ap.add_argument("--file", action="append", default=[],
                    help="filename to DELETE (repeatable); required with --apply")
    ap.add_argument("--apply", action="store_true",
                    help="actually trash the --file names (default: report only)")
    args = ap.parse_args()

    master = _config.get("master", "masterbiotadatabase", fallback="")
    if not master:
        print("No [master] masterbiotadatabase in config.")
        return 2
    db = MasterDB(master)
    db.ensure_fresh()
    con = db._connect()

    found = scan(db, con, args.min_aspect)
    print(f"\nmaster: {master}")
    print(f"JPGs at aspect >= {args.min_aspect}: {len(found)}\n")
    for d in found:
        print(f"  {d['size']:>10}  ar={d['ar']:<5}  {d['sci']}  "
              f"(rating {d['rating']}, rate {d['rate']})")
        print(f"      {d['fname']}")
        if d["only_file"]:
            print("      ONLY file of its picture -> deleting it deletes the picture")
        elif d["jpgs_left"]:
            print(f"      picture keeps: {', '.join(d['jpgs_left'])}")
        else:
            print("      picture keeps its RAW only -> becomes a raw-only picture")
        if d["smalls"]:
            print(f"      thumbnail goes too: {', '.join(d['smalls'])}")
        if d["pins"]:
            print(f"      *** REPRESENTS {', '.join(d['pins'])} across the site")
        print()

    if not args.apply:
        if args.file:
            print("--file given without --apply; nothing deleted.")
        print("Report only. Re-run with --apply --file \"<name>\" to delete.")
        return 0

    if not args.file:
        print("--apply needs at least one --file. Refusing to delete the whole scan: "
              "aspect ratio is a hint, not a verdict.")
        return 2

    by_name = {d["fname"]: d for d in found}
    unknown = [f for f in args.file if f not in by_name]
    if unknown:
        print("These --file names were not in the scan (typo? already deleted?):")
        for u in unknown:
            print(f"  {u}")
        print("Refusing to delete anything.")
        return 2

    print(f"DELETING {len(args.file)} file(s) -> {master}/.trash\n")
    for f in args.file:
        d = by_name[f]
        try:
            res = master_mutate.delete_jpg(db, master, d["upn"], f)
        except master_mutate.MasterMutateError as exc:
            print(f"  FAILED {f}: {exc}")
            continue
        print(f"  trashed {f}")
        for t in res["trashed"]:
            print(f"          -> {t}")
    print("\nFlushing the master CSVs…")
    from db.master_db import MasterCheckpointWorker
    n = MasterCheckpointWorker.get(master).flush()
    print(f"checkpoint: {n} pending entry/entries written.")
    print("\nThe live website still shows the old pictures until it is rebuilt; the "
          "rebuild also regenerates any -small thumbnail a new best-pick needs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
