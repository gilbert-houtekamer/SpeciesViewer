#!/usr/bin/env python3
"""Dutch bird names from the IOC multilingual list — dry run, review, apply.

ONE JOB: make the Dutch name of every bird in a master the one the IOC World
Bird List gives (multilingual file, CC BY 4.0, worldbirdnames.org,
"Multiling IOC <version>.xlsx"). The author's names came from an old IOC list;
most differences since are committee decisions, and some old names were plainly
odd (Grey Thornbill as "witstuitmangrovezanger"), so IOC wins (decided
2026-10-17). An empty name is filled; a different name is replaced and the old
one goes into the alias table, so keywords typed with it still find the bird.

    python tools/svr_names_from_ioc.py --master E:/Dropbox/naturedb --ioc "Multiling IOC 15.2.xlsx"
        dry run: writes <out>/ioc_dutch_review.csv, changes nothing
    ... --apply
        writes speciescollection.csv and species.csv (backups first) and adds
        the old names to speciesnames.csv. Close SpeciesViewer first.

Matching: the bird's scientific name, else any scientific alias in
speciesnames.csv that points at it (a name IOC still uses for the same bird).
Only species under the class Aves are looked at.
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))
from utils.atomic_replace import replace_with_retry  # noqa: E402

ALIAS_HEADER = ["Name", "Language", "PointsTo", "Kind", "Source"]


def load_ioc(xlsx):
    """{scientific name lower: Dutch name lower} from the IOC multilingual sheet."""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    rows = wb.worksheets[0].iter_rows(values_only=True)
    header = [str(h or "").strip() for h in next(rows)]
    sci_col = next(i for i, h in enumerate(header) if h.startswith("IOC_"))
    nl_col = header.index("Dutch")
    out = {}
    for r in rows:
        sci, nl = r[sci_col], r[nl_col]
        if sci and nl and str(nl).strip():
            out[str(sci).strip().lower()] = str(nl).strip().lower()   # the list keeps names in lower case
    return out


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh, delimiter=";")
        return rd.fieldnames, list(rd)


def write_csv(path, header, rows, backup_dir, stamp):
    backup_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, backup_dir / f"pre-ioc-dutch-{stamp}_{path.name}")
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    replace_with_retry(tmp, path)


def plan(active, ioc):
    """The review rows: fill / replace / not in IOC."""
    _h, rows = read_csv(active / "speciescollection.csv")
    by = {r["ScientificName"]: r for r in rows}
    aliases = {}
    names_csv = active / "speciesnames.csv"
    if names_csv.exists():
        for a in read_csv(names_csv)[1]:
            if a.get("Language") == "sci":
                aliases.setdefault(a["PointsTo"].strip().lower(), []).append(a["Name"].strip().lower())

    def is_bird(r):
        for _ in range(30):
            if r is None:
                return False
            if r["Level"] == "Level.CLASS":
                return r["ScientificName"].strip().lower() == "aves"
            r = by.get(r["Up"])
        return False

    review = []
    for r in rows:
        if r["Level"] != "Level.SPECIES" or not is_bird(r):
            continue
        sci = r["ScientificName"].strip().lower()
        have = r["DutchName"].strip()
        found, via = ioc.get(sci), "same name"
        if found is None:
            for alt in aliases.get(sci, []):
                if alt in ioc:
                    found, via = ioc[alt], f"alias {alt}"
                    break
        if not found:
            if not have:
                review.append({"Action": "not in IOC", "ScientificName": r["ScientificName"],
                               "EnglishName": r["EnglishName"], "DutchName": "", "IOC_Dutch": "", "Via": ""})
            continue
        if have.lower() == found:
            continue
        review.append({"Action": "fill" if not have else "replace", "ScientificName": r["ScientificName"],
                       "EnglishName": r["EnglishName"], "DutchName": have, "IOC_Dutch": found, "Via": via})
    return review


def apply(active, review):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = active / "backup"
    new = {x["ScientificName"]: x["IOC_Dutch"] for x in review if x["Action"] in ("fill", "replace")}

    # 1. the species list and the master's species.csv: the same names
    for name in ("speciescollection.csv", "species.csv"):
        path = active / name
        header, rows = read_csv(path)
        n = 0
        for r in rows:
            if r["Level"] == "Level.SPECIES" and r["ScientificName"] in new:
                r["DutchName"] = new[r["ScientificName"]]
                n += 1
        write_csv(path, header, rows, backup, stamp)
        print(f"  {name}: {n} Dutch names written")

    # 2. the old names keep working as aliases. One that IOC gave to ANOTHER
    #    bird is written too: the reader then sees it is contested and uses it
    #    for neither (speciesclassification._readAliases).
    path = active / "speciesnames.csv"
    header, rows = read_csv(path) if path.exists() else (ALIAS_HEADER, [])
    keys = {(r["Name"], r["Language"], r["PointsTo"]) for r in rows}
    added = 0
    for x in review:
        old = x["DutchName"].strip().lower()
        if x["Action"] != "replace" or not old:
            continue
        key = (old, "nl", x["ScientificName"].strip().lower())
        if key not in keys:
            rows.append({"Name": old, "Language": "nl", "PointsTo": key[2],
                         "Kind": "older Dutch name", "Source": "before IOC 15.2"})
            keys.add(key)
            added += 1
    write_csv(path, header, rows, backup, stamp)
    print(f"  speciesnames.csv: {added} old Dutch names kept as aliases")
    print(f"  backups: {backup}\\pre-ioc-dutch-{stamp}_*")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--master", required=True)
    ap.add_argument("--ioc", required=True, help="the IOC multilingual .xlsx")
    ap.add_argument("--out", default=".", help="folder for ioc_dutch_review.csv")
    ap.add_argument("--apply", action="store_true", help="write the names (close SpeciesViewer first)")
    a = ap.parse_args()

    active = Path(a.master) / "metadata" / "active"
    review = plan(active, load_ioc(a.ioc))
    out = Path(a.out) / "ioc_dutch_review.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["Action", "ScientificName", "EnglishName", "DutchName", "IOC_Dutch", "Via"],
                           delimiter=";")
        w.writeheader()
        w.writerows(sorted(review, key=lambda x: (x["Action"], x["ScientificName"])))
    counts = {}
    for x in review:
        counts[x["Action"]] = counts.get(x["Action"], 0) + 1
    print(f"Review: {out}")
    for k, v in sorted(counts.items()):
        print(f"  {k:12} {v}")
    if not a.apply:
        print("Dry run — nothing changed. Add --apply to write the names.")
        return 0
    apply(active, review)
    return 0


if __name__ == "__main__":
    sys.exit(main())
