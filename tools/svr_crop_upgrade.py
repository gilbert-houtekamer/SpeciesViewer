"""Upgrade the approved website JPGs in place, from the crop-recovery proposals.

DEFAULT = DRY RUN. Pass --apply to write.

WHY IN PLACE, WITH THE SAME FILENAME (the user's call, and it is the right one):
    Adding a SECOND file would have forced a new filename -> a new suffix -> a change
    to deriveUniquePictureName (or the next master re-read would split it into a
    phantom picture) -> and an edit to species.JPGmap, because the species page
    renders from JPGmap and NOTHING outside the full pipeline read ever appends to
    it. Four problems, all descended from one choice.
    Replacing the file keeps the picture's identity untouched: no new row, no UPN to
    derive, no JPGmap surgery, no best-pick to re-distil. The only thing that goes
    stale is the recorded ImageSize, which we fix.

NOTHING IS LOST: every original is copied to --backup first (default D:/croporig,
mirroring the species folder structure), so any picture can be put back by copying
it home. The RAW was never touched at all.

INPUT / APPROVAL
    D:/croprev500/crop_recovery.csv   the 864 proposals
    D:/croprev500/*__proposed.jpg     the images themselves
    D:/cropbad/                       the 53 the user REJECTED (exposure work needed)

    ** APPROVED = the 864 MINUS the 53 stems present in D:/cropbad. **
    The rejects were COPIED there as __compare.jpg; the proposals all still sit in
    the review directory, so "apply what is in the folder" would publish the rejects
    too. We assert the count is exactly 811.

AFTER RUNNING: regenerate the affected species pages (the tool prints and writes the
list) so the bigger JPG and a fresh -small are uploaded.

Usage:
    python tools/svr_crop_upgrade.py                      # dry run, all 811
    python tools/svr_crop_upgrade.py --species "lestes barbarus" --apply
    python tools/svr_crop_upgrade.py --apply              # the rest
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
from pathlib import Path

from PIL import Image

REVIEW = Path("D:/croprev500")
REJECTS = Path("D:/cropbad")
BACKUP = Path("D:/croporig")


def load(review: Path, rejects: Path) -> tuple[list[dict], int]:
    rows = list(csv.DictReader(open(review / "crop_recovery.csv", encoding="utf-8"),
                               delimiter=";"))
    # The reject signal is the compare-image filenames the user moved aside.
    bad_stems = [f.replace("__compare.jpg", "")
                 for f in os.listdir(rejects) if f.endswith("__compare.jpg")]
    rejected_upns = set()
    for stem in bad_stems:
        hits = [r["UPN"] for r in rows if stem.endswith(r["UPN"])]
        if hits:
            rejected_upns.add(max(hits, key=len))
    approved = [r for r in rows if r["UPN"] not in rejected_upns]
    return approved, len(rejected_upns)


def proposed_path(review: Path, row: dict) -> Path | None:
    """The __proposed.jpg for this row (the index prefix is not in the CSV, so match
    on the UPN, which every filename ends with)."""
    for f in review.glob("*__proposed.jpg"):
        if f.name[: -len("__proposed.jpg")].endswith(row["UPN"]):
            return f
    return None


def refresh(csv_path: Path, apply: bool) -> int:
    """Sync the master's recorded ImageSize to whatever is now on disk.

    The companion to the 53 rejects: they need real exposure work, so they go through
    PhotoLab — and PhotoLab must write OVER THE SAME FILENAME. A new name (its default
    `<stem>_DxO.jpg`) would create a second file, which lands straight in the JPGmap
    hole: it becomes the picture's BestJPG and the species page still never shows it.
    Overwrite in place, then run this so the master stops believing the old size."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))
    from utils.config import _config
    from db.master_db import MasterDB
    import sqlite3
    from db.master_db import db_path

    root = _config.get("master", "MasterBiotaDatabase").strip().strip('"')
    db = MasterDB(root)
    con = sqlite3.connect(str(db_path()))
    con.row_factory = sqlite3.Row

    rows = list(csv.DictReader(open(csv_path, encoding="utf-8"), delimiter=";"))
    changed = same = missing = 0
    for r in rows:
        p = Path(r.get("PublishedJPG_full_path") or r.get("PublishedFile") or "")
        upn = r.get("UPN") or ""
        if not p.is_file() or not upn:
            missing += 1
            continue
        with Image.open(p) as im:
            on_disk = f"{im.width}x{im.height}"
        db_row = con.execute(
            'SELECT "ImageSize" i FROM files WHERE "File:FileName"=? '
            '  AND "UniquePictureName"=?', (p.name, upn)).fetchone()
        recorded = db_row["i"] if db_row else ""
        if recorded == on_disk:
            same += 1
            continue
        print(f"  {p.name:44} master says {recorded:>10} -> really {on_disk:>10}")
        if apply:
            db.apply_jpg_replaced(upn, p.name, on_disk)
        changed += 1
    print(f"\n{'UPDATED' if apply else 'WOULD UPDATE'}: {changed}   already correct: "
          f"{same}   missing: {missing}")
    if not apply:
        print("nothing written. Re-run with --apply.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--review", default=str(REVIEW))
    ap.add_argument("--rejects", default=str(REJECTS))
    ap.add_argument("--backup", default=str(BACKUP))
    ap.add_argument("--species", help="do only this species (for the first careful one)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--refresh-from", metavar="CSV",
                    help="do not upgrade anything — just re-read the JPGs listed in "
                         "this CSV (column PublishedJPG_full_path or PublishedFile) "
                         "and correct the size the master has recorded for them. Use "
                         "after re-rendering the rejects in PhotoLab OVER THE SAME "
                         "FILENAME: the pixels changed, the master still believes the "
                         "old ones.")
    args = ap.parse_args()

    if args.refresh_from:
        return refresh(Path(args.refresh_from), args.apply)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))
    from utils.config import _config
    from db.master_db import MasterDB

    review, rejects, backup = Path(args.review), Path(args.rejects), Path(args.backup)
    approved, n_rejected = load(review, rejects)
    print(f"proposals: {len(approved) + n_rejected}   rejected by you: {n_rejected}   "
          f"APPROVED: {len(approved)}")
    if len(approved) != 811:
        print(f"  !! expected 811 approved, got {len(approved)} — stopping so nothing "
              f"is written on a wrong assumption.")
        return 1

    if args.species:
        approved = [r for r in approved if r["Species"] == args.species]
        print(f"  filtered to species {args.species!r}: {len(approved)}")
    if args.limit:
        approved = approved[: args.limit]

    root = _config.get("master", "MasterBiotaDatabase").strip().strip('"')
    db = MasterDB(root)

    done = skipped = 0
    species_touched = set()
    for r in approved:
        src = proposed_path(review, r)
        pub = Path(r["PublishedFile"])
        if src is None or not src.is_file():
            print(f"  SKIP {r['UPN']}: no proposed image found")
            skipped += 1
            continue
        if not pub.is_file():
            print(f"  SKIP {r['UPN']}: published JPG missing on disk")
            skipped += 1
            continue

        with Image.open(src) as im:
            new_size = f"{im.width}x{im.height}"

        if not args.apply:
            print(f"  would upgrade {pub.name:44} {r['PublishedSize']:>10} -> {new_size:>10}"
                  f"   [{r['Species']}]")
            species_touched.add(r["Species"])
            done += 1
            continue

        # 1. Keep the original. Mirror the species folder so it can be put straight back.
        rel = pub.relative_to(root) if str(pub).lower().startswith(str(root).lower()) \
            else Path(pub.name)
        dst = backup / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():                 # never overwrite a backup on a re-run
            shutil.copy2(pub, dst)

        # 2. Replace in place — same name, same path, more pixels.
        shutil.copy2(src, pub)

        # 3. The -small thumbnail is now made from a stale source. Its DIMENSIONS do
        #    not change (jpgResizeCopy always writes 600x450), so no DB row changes —
        #    only the pixels get better. Regenerate it if one exists.
        small = pub.with_name(pub.stem + "-small.jpg")
        if small.is_file():
            try:
                with Image.open(pub) as im:
                    im2 = im.convert("RGB")
                    if im2.width > im2.height:
                        im2 = im2.resize((600, 450), Image.Resampling.LANCZOS)
                    elif im2.width == im2.height:
                        im2 = im2.resize((600, 600), Image.Resampling.LANCZOS)
                    else:
                        im2 = im2.resize((450, 600), Image.Resampling.LANCZOS)
                    im2.save(small, "JPEG", quality=90, optimize=True)
            except Exception as e:
                print(f"     (could not refresh {small.name}: {e})")

        # 4. Tell the master the file grew. Nothing else about it changed.
        db.apply_jpg_replaced(r["UPN"], pub.name, new_size)

        print(f"  upgraded {pub.name:44} {r['PublishedSize']:>10} -> {new_size:>10}"
              f"   [{r['Species']}]")
        species_touched.add(r["Species"])
        done += 1

    print(f"\n{'APPLIED' if args.apply else 'DRY RUN'}: {done} picture(s), "
          f"{skipped} skipped, {len(species_touched)} species affected")
    if args.apply and species_touched:
        out = review / "species_to_regenerate.txt"
        out.write_text("\n".join(sorted(species_touched)), encoding="utf-8")
        print(f"originals backed up under: {backup}")
        print(f"species needing a page regenerate -> {out}")
    if not args.apply:
        print("\nnothing was written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
