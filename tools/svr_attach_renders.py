#!/usr/bin/env python3
"""Attach JPG renders the master does not know about — dry run, then apply.

ONE JOB: for each 'attach' row of the admin check's rebuild_add.csv (Master ▸
Maintain Master Database ▸ Refresh Master Database — Review), add the JPG to the
master: a data_files.csv row, and the file in its picture's Files list. These
are renders (…_dxo.jpg) of a shot the master already has, so the new row is a
copy of that shot's own file row (camera, lens, exposure, GPS) with the
JPG's name, folder, type, size and software read from the JPG itself.

The JPG becomes the picture's BestJPG only when the picture has none, or when
its BestJPG file is missing from the folder; a working choice is kept.
Only .jpg/.jpeg; other 'attach' files (a .dng) and 'new?'/'junk?' rows are
listed, never touched. So are trip-page crops that only carry a render's
name: a banner (wider than 3:1, e.g. 2000x270) or a trip picture (600x450).

    python tools/svr_attach_renders.py --master E:/Dropbox/naturedb --review <dir with rebuild_add.csv>
        dry run: prints what would change
    ... --apply
        backups to admin/status/backup first; close SpeciesViewer first.

INVARIANT: rows and columns not listed above stay byte-identical (checked
after writing against the backups).
"""
from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))
from utils.atomic_replace import replace_with_retry  # noqa: E402
from utils.exiftool_path import exe as exiftool_exe  # noqa: E402

RAW_FIRST = (".nef", ".orf", ".cr2", ".cr3", ".arw", ".rw2", ".raf", ".dng")
# what the JPG says about itself (data_files column <- exiftool tag)
OWN_FIELDS = {"ImageSize": "Composite:ImageSize", "EXIF:Software": "EXIF:Software",
              "XMP:CreatorTool": "XMP:CreatorTool",
              "ICC_Profile:ProfileDescription": "ICC_Profile:ProfileDescription"}


def read_csv(path):
    text = path.read_bytes().decode("utf-8")
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=";"))
    return rows[0], rows[1:]


def write_csv(path, header, body):
    out = io.StringIO()
    csv.writer(out, delimiter=";", lineterminator="\r\n").writerows([header] + body)
    tmp = path.with_suffix(".csv.tmp")
    tmp.write_bytes(out.getvalue().encode("utf-8"))
    replace_with_retry(tmp, path)


def exif(paths):
    out = subprocess.run([exiftool_exe(), "-j", "-G1", "-a", "-charset", "filename=utf8",
                          "-Composite:ImageSize", "-EXIF:Software", "-XMP:CreatorTool",
                          "-ICC_Profile:ProfileDescription", *map(str, paths)],
                         capture_output=True, text=True, encoding="utf-8")
    result = {}
    for d in json.loads(out.stdout or "[]"):
        result[os.path.normcase(d["SourceFile"].replace("/", os.sep))] = {
            k.split(":", 1)[1] if ":" in k else k: v for k, v in d.items()}
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--master", required=True, type=Path)
    ap.add_argument("--review", required=True, type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    active = args.master / "metadata" / "active"
    files_csv, pics_csv = active / "data_files.csv", active / "data_pictures.csv"

    with open(args.review / "rebuild_add.csv", encoding="utf-8", newline="") as f:
        add = list(csv.DictReader(f, delimiter=";"))
    fh, fbody = read_csv(files_csv)
    ph, pbody = read_csv(pics_csv)
    fi, pi = {c: i for i, c in enumerate(fh)}, {c: i for i, c in enumerate(ph)}
    pics = {r[pi["UniquePictureName"]]: r for r in pbody}
    known = {os.path.normcase(r[fi["WindowsFileName"]]) for r in fbody}

    todo, skipped = [], []
    for a in add:
        ext = os.path.splitext(a["File:FileName"])[1].lower()
        if a["class"] != "attach" or ext not in (".jpg", ".jpeg"):
            skipped.append(f"{a['class']:7} {a['File:FileName']}")
            continue
        folder = Path(a["path"]).parent
        real = next((n for n in os.listdir(folder) if n.lower() == a["File:FileName"].lower()), None)
        pic = pics.get(a["UPN"])
        donors = [r for r in fbody if r[fi["UniquePictureName"]] == a["UPN"]]
        if not real or pic is None or not donors:
            skipped.append(f"cannot  {a['File:FileName']} (file/picture/row missing)")
            continue
        donors.sort(key=lambda r: (r[fi["WindowsExtension"]] not in [e[1:] for e in RAW_FIRST]))
        todo.append((a, folder, real, pic, donors[0]))

    tags = exif([folder / real for _, folder, real, _, _ in todo])
    new_rows, plan = [], []
    for a, folder, real, pic, donor in todo:
        directory = donor[fi["File:Directory"]]
        full = directory + "/" + real
        if os.path.normcase(full) in known:
            skipped.append(f"known   {real}")
            continue
        size = str(tags.get(os.path.normcase(str(folder / real)), {}).get("ImageSize", ""))
        try:
            w, h = (int(v) for v in size.lower().replace(" ", "x").split("x"))
        except ValueError:
            w = h = 0
        if not w or w / h > 3 or (w, h) == (600, 450):
            skipped.append(f"crop?   {real} ({size or 'no size'}: banner or trip picture, not a render)")
            continue
        row = list(donor)
        row[fi["File:FileName"]] = real
        row[fi["WindowsFileName"]] = full
        row[fi["WindowsExtension"]] = os.path.splitext(real)[1][1:].lower()
        row[fi["File:FileType"]], row[fi["File:FileTypeExtension"]] = "JPEG", "jpg"
        row[fi["File:MIMEType"]] = "image/jpeg"
        t = tags.get(os.path.normcase(str(folder / real)), {})
        for col, tag in OWN_FIELDS.items():
            if col in fi:
                row[fi[col]] = str(t.get(tag.split(":", 1)[1], ""))
        new_rows.append(row)
        files = ast.literal_eval(pic[pi["Files"]]) if pic[pi["Files"]].startswith("[") else []
        if real not in files:
            pic[pi["Files"]] = str(sorted(files + [real]))
        best = pic[pi["BestJPG"]]
        best_ok = bool(best) and (folder / best).exists()
        if not best_ok:
            pic[pi["BestJPG"]] = real
            pic[pi["BestJPGPathName"]] = pic[pi["TargetPath"]]
        plan.append(f"attach  {real}  {t.get('ImageSize', '?')}"
                    + ("" if best_ok else f"  = picture's JPG now (was {best or 'none'})"))

    print("\n".join(plan))
    print(f"\n{len(plan)} to attach; left alone:\n  " + "\n  ".join(skipped))
    if not args.apply:
        print("dry run — nothing written (add --apply)")
        return 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = args.master / "admin" / "status" / "backup"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(files_csv, backup / f"{stamp}_data_files.csv")
    shutil.copy2(pics_csv, backup / f"{stamp}_data_pictures.csv")
    write_csv(files_csv, fh, fbody + new_rows)
    write_csv(pics_csv, ph, pbody)
    # verify: old files rows unchanged + the new ones; pictures differ only in 3 columns
    _, fb_old = read_csv(backup / f"{stamp}_data_files.csv")
    _, fb_new = read_csv(files_csv)
    assert fb_new[:len(fb_old)] == fb_old and len(fb_new) == len(fb_old) + len(new_rows)
    _, pb_old = read_csv(backup / f"{stamp}_data_pictures.csv")
    _, pb_new = read_csv(pics_csv)
    keep = [i for i, c in enumerate(ph) if c not in ("Files", "BestJPG", "BestJPGPathName")]
    assert len(pb_old) == len(pb_new)
    assert all([o[i] for i in keep] == [n[i] for i in keep] for o, n in zip(pb_old, pb_new))
    print(f"written and verified; backups {backup / stamp}_*.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
