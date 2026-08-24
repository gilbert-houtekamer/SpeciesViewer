"""Remove pictures from the master that are not pictures of anything.

Two kinds, and they are the same kind:

  * **No species at all.** Blog extras that leaked in — a dromedary, a goat, a donkey, a
    date palm, a driveway. They were never wildlife, they have no TargetPath, and their
    only tracked file is a `-small` THUMBNAIL.
  * **`? phacelia`.** A placeholder filed under *aphididae* (aphids) because the photo was
    named after the PLANT the insect was sitting on. It is that family's only leaf.

Both turned out to be **phantoms**: their `WindowsFileName` points at the master ROOT
rather than a species folder, and no such file exists there or anywhere else. There is
nothing to look at later — only rows describing photographs that are not on this disk.

So the "move to a folder" this was meant to do writes a MANIFEST instead: a record, in the
place you would go looking, of exactly what was removed and that its file was already
gone. Any file that DOES exist is moved rather than deleted.

    python tools/svr_purge_phantom_pictures.py                    # report
    python tools/svr_purge_phantom_pictures.py --apply

HARD GUARDRAIL: a picture that still has a RAW (NEF/ORF/CR2/…) on disk is REFUSED, always.
Not every species-less picture is junk — the master also holds a Maghreb magpie, a Barbary
ground squirrel and a Bronze Sunbird that simply never got classified, and three
Ueckermünde frames that still carry their NEF. Those are unclassified photographs, not
phantoms, and deleting them would destroy real work. Selection alone must never be trusted
to tell the difference.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "speciesviewer"))

from db.master_db import MasterDB, MasterCheckpointWorker   # noqa: E402
from db import master_mutate                                # noqa: E402
from utils.config import _config                            # noqa: E402
from utils.paths import to_fs_path                          # noqa: E402
from utils.raw_extensions import RAW, bare                  # noqa: E402

DEST = Path(r"D:\maybe look at\pictures without species, removed from master")
_RAW_EXTS = bare(RAW)  # canonical table; a UPN WITH a raw is kept, so this widens
                       # what is protected from purge, never what is purged


def _abs(fr: dict) -> Path | None:
    win = (fr.get("WindowsFileName") or "").strip()
    if win:
        return to_fs_path(win)
    d = (fr.get("File:Directory") or "").strip()
    fn = (fr.get("File:FileName") or "").strip()
    return Path(d) / fn if d and fn else None


def collect(db, con) -> list[dict]:
    """The phantoms: species-less pictures whose BestJPG has no file row, plus the
    ? placeholder species. Both are named explicitly — this never deletes 'whatever the
    scan found'."""
    rows = con.execute(
        'SELECT "UniquePictureName" u FROM pictures p'
        ' WHERE p."BestJPG" != "" AND TRIM(p."WetSoortNaam") = ""'
        '   AND NOT EXISTS (SELECT 1 FROM files f'
        '                    WHERE f."File:FileName" = p."BestJPG"'
        '                      AND f."UniquePictureName" = p."UniquePictureName")'
    ).fetchall()
    upns = [r["u"] for r in rows]
    upns += [r["u"] for r in con.execute(
        'SELECT "UniquePictureName" u FROM pictures WHERE "WetSoortNaam" LIKE "?%"')]

    out = []
    for upn in dict.fromkeys(upns):
        pic = db.picture(upn)
        if pic is None:
            continue
        files = db.files_for_upns([upn])
        entries, on_disk, raws = [], 0, []
        for fn, fr in files.items():
            p = _abs(fr)
            exists = bool(p and p.exists())
            on_disk += exists
            ext = (fr.get("WindowsExtension") or Path(fn).suffix.lstrip(".")).lower()
            if exists and ext in _RAW_EXTS:
                raws.append(fn)
            entries.append({"fname": fn, "path": p, "exists": exists})
        out.append({"upn": upn, "species": pic.get("WetSoortNaam", ""),
                    "subject": pic.get("subject", ""), "best": pic.get("BestJPG", ""),
                    "files": entries, "on_disk": on_disk, "raws": raws})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", default=str(DEST))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    master = _config.get("master", "masterbiotadatabase", fallback="")
    db = MasterDB(master)
    db.ensure_fresh()
    con = db._connect()

    found = collect(db, con)
    keep, purge = [], []
    for d in found:
        (keep if d["raws"] else purge).append(d)

    print(f"\nmaster: {master}\n")
    print(f"{len(purge)} picture(s) to remove:\n")
    for d in purge:
        what = d["species"] or f"(no species) subject={d['subject'] or '—'}"
        print(f"  {d['upn']}")
        print(f"      {what}")
        for e in d["files"]:
            print(f"      {'ON DISK' if e['exists'] else 'not on disk'}  {e['fname']}")
        if not d["on_disk"]:
            print(f"      -> phantom: BestJPG {d['best']!r} names a file that is nowhere")
        print()

    if keep:
        print(f"REFUSED — {len(keep)} picture(s) still have a RAW on disk. These are "
              f"UNCLASSIFIED PHOTOGRAPHS, not phantoms:\n")
        for d in keep:
            print(f"  {d['upn']}  subject={d['subject'] or '—'}  raw={d['raws']}")
        print()

    if not args.apply:
        print("Report only. Re-run with --apply to remove them.")
        return 0
    if not purge:
        print("Nothing to remove.")
        return 0

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    moved, removed = [], []
    for d in purge:
        # Move anything that actually exists BEFORE the rows go, so a failure here leaves
        # the master intact rather than pointing at a file we already carried off.
        for e in d["files"]:
            if e["exists"]:
                target = dest / e["fname"]
                n = 1
                while target.exists():
                    target = dest / f"{Path(e['fname']).stem}__{n}{Path(e['fname']).suffix}"
                    n += 1
                shutil.move(str(e["path"]), str(target))
                moved.append(str(target))
        try:
            master_mutate.delete_picture(db, master, d["upn"], allow_jpg=True)
            removed.append(d)
        except master_mutate.MasterMutateError as exc:
            print(f"  FAILED {d['upn']}: {exc}")

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    man = dest / "REMOVED.csv"
    new = not man.exists()
    with open(man, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        if new:
            w.writerow(["removed_at", "UPN", "species", "subject", "BestJPG",
                        "file", "was_on_disk", "moved_to"])
        for d in removed:
            for e in d["files"]:
                w.writerow([stamp, d["upn"], d["species"], d["subject"], d["best"],
                            e["fname"], "yes" if e["exists"] else "NO — file was already gone",
                            str(dest / e["fname"]) if e["exists"] else ""])

    print(f"\nRemoved {len(removed)} picture(s) from the master.")
    print(f"Moved {len(moved)} file(s) that actually existed.")
    print(f"Manifest: {man}")
    n = MasterCheckpointWorker.get(master).flush()
    print(f"checkpoint: {n} entry/entries written to the master CSVs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
