"""Add a clickable SpeciesURL column to a report CSV that names species.

A worklist is only useful if you can act on it. The crop-reject list says which 53
pictures need re-cropping by hand in PhotoLab, but it takes a species name and a file
path to a species page a human then has to find. This puts the page's URL in the row.

The species folder is looked up in the MASTER (TargetPath), never derived from the
file path in the CSV: the taxonomy carries sequence prefixes ('006 galliformes') that
no amount of string work can reconstruct from a species name.

    python tools/svr_add_species_url.py                      # report (default file)
    python tools/svr_add_species_url.py --apply
    python tools/svr_add_species_url.py --csv path.csv --column Species --apply

--apply rewrites the CSV in place, keeping a .bak. A row whose species is not in the
master gets an empty cell and is listed — a silent blank in a worklist is worse than
a loud one.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "speciesviewer"))

from db.master_db import MasterDB           # noqa: E402
from engine.weburl import species_page_url  # noqa: E402
from utils.config import _config            # noqa: E402

DEFAULT_CSV = Path("D:/cropbad/REJECTS_for_PhotoLab.csv")
URL_COL = "SpeciesURL"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--column", default="Species",
                    help="column holding the scientific name (default: Species)")
    ap.add_argument("--lang", default="nl")
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the CSV in place (keeps a .bak)")
    args = ap.parse_args()

    path = Path(args.csv)
    if not path.exists():
        print(f"No such CSV: {path}")
        return 2

    master = _config.get("master", "masterbiotadatabase", fallback="")
    db = MasterDB(master)
    db.ensure_fresh()
    con = db._connect()

    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        rows = list(reader)
        fields = list(reader.fieldnames or [])

    if args.column not in fields:
        print(f"Column {args.column!r} not in {path.name}. Columns: {fields}")
        return 2

    missing = []
    for r in rows:
        sci = (r.get(args.column) or "").strip()
        srow = con.execute(
            'SELECT "ScientificName","TargetPath" FROM species'
            ' WHERE LOWER("ScientificName")=?', (sci.lower(),)).fetchone()
        if srow is None or not (srow["TargetPath"] or "").strip():
            r[URL_COL] = ""
            missing.append(sci)
            continue
        r[URL_COL] = species_page_url(srow["TargetPath"], srow["ScientificName"],
                                      args.lang)

    if URL_COL not in fields:
        fields.append(URL_COL)

    print(f"\n{path}  ({len(rows)} rows)")
    for r in rows[:3]:
        print(f"  {r.get(args.column)}\n    {r[URL_COL] or '(no page)'}")
    if len(rows) > 3:
        print(f"  … {len(rows) - 3} more")
    if missing:
        print(f"\n{len(missing)} row(s) with no page in the master: "
              f"{', '.join(sorted(set(missing)))}")

    if not args.apply:
        print("\nReport only. Re-run with --apply to write the column.")
        return 0

    # Write a sibling first, then swap it in: a half-written worklist is worse than
    # none, and this file is routinely open in Excel — which locks it, and would
    # otherwise leave a truncated CSV behind.
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    bak = path.with_suffix(path.suffix + ".bak")
    try:
        shutil.copy2(path, bak)
        os.replace(tmp, path)
    except PermissionError:
        tmp.unlink(missing_ok=True)
        print(f"\n{path.name} is locked — close it in Excel and re-run.")
        return 1
    print(f"\nWrote {URL_COL} to {path}\nBackup: {bak}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
