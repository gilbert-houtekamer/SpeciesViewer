"""Restore, from Dropbox version history, the master JPGs that were overwritten by
their own thumbnail.

Background: `getSmallJPG` derived a thumbnail name with a CASE-SENSITIVE
`replace('.jpg', ...)`, so an uppercase `X.JPG` came back unchanged and
`jpgResizeCopy` saved the 600x450 render ON TOP OF THE ORIGINAL. See
tools/svr_thumbnail_damage_scan.py and the fix in engine/websupport.py.

The master lives in Dropbox, which keeps per-file version history, so the
originals of the RECENTLY overwritten files are still there and can be rolled back
without re-rendering anything.

DEFAULT = DRY RUN. It prints, per file, the size now against the revision it would
roll back to, and restores nothing until you pass --apply.

⚠ WHY THIS TOUCHES FILES AND NEVER A FOLDER
Dropbox's "Rewind" would restore the whole folder to a point in time — and that
would ALSO undo the 2,422 sidecars we have just stamped with place names, and the
master CSVs. This tool restores ONLY the exact paths that the damage scan
identified, one at a time, so nothing else can be rolled back by accident.

⚠ GUARDRAIL
A file is only restored when the target revision is genuinely LARGER than what is
on disk now. If the history does not actually hold a bigger original, restoring
would achieve nothing (or make things worse), so the file is skipped and said so.

Setup (see the chat, or Dropbox App Console):
    an app with scopes files.metadata.read + files.content.read + files.content.write
    then:  $env:DROPBOX_TOKEN = 'sl.xxxx'

Usage:
    python tools/svr_dropbox_restore_thumbs.py            # dry run
    python tools/svr_dropbox_restore_thumbs.py --apply    # actually restore
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.dropboxapi.com/2"

# jpgResizeCopy's exact output sizes — the fingerprint of the overwrite.
THUMB_SIZES = ("600x450", "450x600", "600x600")

# A restore is only worth doing if the revision is a REAL original: a 5184x3888
# camera JPEG is megabytes, a 600x450 thumbnail is tens of KB, so the genuine
# article is many times larger — never a few percent larger.
_REAL_ORIGINAL = 3.0


def _api(token: str, endpoint: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{API}/{endpoint}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 401:
            raise SystemExit("Dropbox says the token is invalid or expired "
                             "(short-lived tokens last 4 hours — generate a new one).")
        if "missing_scope" in body:
            raise SystemExit(
                "Dropbox says the token lacks a scope. The token carries whatever "
                "permissions existed WHEN IT WAS GENERATED — tick the three "
                "files.* scopes, press Submit, THEN generate a new token.")
        raise SystemExit(f"Dropbox error {e.code} on {endpoint}: {body}")


def _dropbox_path(local: str, dropbox_root: str) -> str:
    """E:/Dropbox/naturedb/biota/x.JPG -> /naturedb/biota/x.JPG"""
    p = Path(local).resolve()
    root = Path(dropbox_root).resolve()
    rel = p.relative_to(root)
    return "/" + str(rel).replace("\\", "/")


def damaged_files(max_age_days: int) -> list[dict]:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))
    from db.master_db import db_path
    con = sqlite3.connect(str(db_path()))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        'SELECT f."WindowsFileName" AS path, f."File:FileName" AS fn,'
        '       f."ImageSize" AS size, p."WetSoortNaam" AS sci'
        '  FROM files f JOIN pictures p'
        '    ON p."UniquePictureName" = f."UniquePictureName"'
        ' WHERE f."File:FileName" GLOB ?', ("*.JPG",)).fetchall()
    cutoff = datetime.datetime.now() - datetime.timedelta(days=max_age_days)
    out = []
    for r in rows:
        if (r["size"] or "") not in THUMB_SIZES:
            continue
        p = r["path"]
        if not p or not os.path.isfile(p):
            continue
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(p))
        if mtime < cutoff:
            continue                       # older than Dropbox keeps
        out.append({"path": p, "fn": r["fn"], "sci": r["sci"],
                    "size_now": os.path.getsize(p), "overwritten": mtime})
    return sorted(out, key=lambda d: d["overwritten"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dropbox-root", default=r"E:\Dropbox")
    ap.add_argument("--max-age-days", type=int, default=180,
                    help="ignore files overwritten longer ago than Dropbox keeps "
                         "history (default 180)")
    ap.add_argument("--apply", action="store_true",
                    help="actually restore (default: dry run, changes nothing)")
    args = ap.parse_args()

    token = os.environ.get("DROPBOX_TOKEN", "").strip()
    if not token:
        raise SystemExit("Set DROPBOX_TOKEN first:  $env:DROPBOX_TOKEN = 'sl.xxxx'")

    files = damaged_files(args.max_age_days)
    if not files:
        print("No recently-overwritten files found. Nothing to restore.")
        return 0

    print(f"{'APPLY' if args.apply else 'DRY RUN'} - {len(files)} damaged file(s) "
          f"overwritten within {args.max_age_days} days\n")

    restored = skipped = failed = 0
    for f in files:
        try:
            dbx = _dropbox_path(f["path"], args.dropbox_root)
        except ValueError:
            print(f"  SKIP  {f['fn']}: not under {args.dropbox_root}")
            skipped += 1
            continue

        revs = _api(token, "files/list_revisions",
                    {"path": dbx, "limit": 10}).get("entries", [])
        # A REAL original is many times bigger, not a few bytes bigger. The first
        # version of this guard only asked for "larger", and Dropbox duly offered
        # revisions like 55 KB -> 55 KB: the SAME damaged file, re-encoded. (The
        # website build renders the NL page and then the EN page, so jpgResizeCopy
        # ran twice — shrinking the file, then re-encoding the shrunken copy.)
        # Restoring those would achieve precisely nothing while reporting success.
        bigger = [r for r in revs if r.get("size", 0) >= f["size_now"] * _REAL_ORIGINAL]
        if not bigger:
            best = max((r.get("size", 0) for r in revs), default=0)
            print(f"  SKIP  {f['sci']:26} {f['fn']}")
            print(f"        on disk {f['size_now']/1024:.0f} KB; best revision in "
                  f"history {best/1024:.0f} KB — the original has aged out of "
                  f"Dropbox's version history. Re-render from the RAW instead.")
            skipped += 1
            continue
        target = max(bigger, key=lambda r: r.get("server_modified", ""))

        when = target.get("server_modified", "?")[:10]
        print(f"  {f['sci']:26} {f['fn']}")
        print(f"        now {f['size_now']/1024:>6.0f} KB (overwritten "
              f"{f['overwritten']:%Y-%m-%d})  ->  restore {target['size']/1024:>7.0f} KB "
              f"from {when}")

        if args.apply:
            try:
                _api(token, "files/restore", {"path": dbx, "rev": target["rev"]})
                restored += 1
            except SystemExit as e:
                print(f"        FAILED: {e}")
                failed += 1

    print()
    if args.apply:
        print(f"restored {restored}, skipped {skipped}, failed {failed}")
        print("Dropbox will now sync the originals back down. Afterwards run "
              "Refresh Master Database so the index picks up their real size.")
    else:
        print(f"would restore {len(files) - skipped}, skip {skipped}")
        print("DRY RUN - nothing was changed. Re-run with --apply to restore.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
