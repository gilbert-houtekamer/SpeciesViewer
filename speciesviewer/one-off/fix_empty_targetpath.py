# fix_empty_targetpath.py — fill in the master's missing species TargetPath values.
#
# WHAT IS WRONG
# A handful of SPECIES rows in the master's species.csv have an empty
# TargetPath. TargetPath is the species' folder inside the biota tree
# ("biota/animalia/chordata/aves/040 passeriformes/127 thraupidae/nemosia
# pileata/") and everything that locates a species' pictures or builds its web
# page derives from it. Empty, the species sorts oddly in the flat all-species
# index and its page would be written to the wrong place.
#
# WHY IT HAPPENED — and why it will not happen again
# All the affected rows have zero JPGs. The species node used to get its
# TargetPath only from the "best JPG" block, which never runs for a species that
# has no rendered JPG. projectlib.py (see the comment at "Ensure the species NODE
# also carries its path") already fixes that on ingest. These rows are simply
# older than the fix and have not been re-ingested since.
#
# THE VALUE IS NOT INVENTED
# It is recomputed the way engine.specieslib.getPathToSpecies() does: walk `Up`
# to the root and join the scientific names. Identical to what every populated
# sibling row holds.
#
# SAFETY — species.csv has previously been lost, so this is deliberately timid:
#   * Dry run is the DEFAULT; --apply is required to write.
#   * A timestamped backup is written next to the file before any change.
#   * ONLY empty TargetPath cells on SPECIES rows are touched. The row count,
#     the column set, and every other cell are asserted unchanged afterwards;
#     any mismatch aborts BEFORE the file is replaced.
#   * The new file is written to a temp file and moved into place, so an
#     interrupted run cannot leave a half-written master CSV.
#
# USAGE (from the speciesviewer/ directory, with it on the import path)
#   set PYTHONPATH=.
#   python one-off/fix_empty_targetpath.py                 # dry run
#   python one-off/fix_empty_targetpath.py --apply

import argparse
import csv
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from utils.config import _config

DELIM = ";"


def _species_csv():
    master = (_config.get("master", "masterbiotadatabase", fallback="")
              or "").replace('"', "").replace("'", "").strip()
    if not master:
        sys.exit("No master database configured.")
    p = Path(master) / "metadata" / "active" / "species.csv"
    if not p.is_file():
        sys.exit(f"Not found: {p}")
    return p


def _derive(row, by_sci):
    """getPathToSpecies(): walk Up to the root, join the scientific names."""
    parts, cur, seen = [row["ScientificName"].strip()], row, set()
    while (cur["Up"] or "").strip():
        key = cur["Up"].strip().lower()
        if key in seen:                       # a cycle in Up — refuse to guess
            return ""
        seen.add(key)
        cur = by_sci.get(key)
        if cur is None:                       # broken parent chain — refuse
            return ""
        parts.insert(0, cur["ScientificName"].strip())
    return "/".join(parts) + "/"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the fix (default is a dry run)")
    args = ap.parse_args()

    path = _species_csv()
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=DELIM)
        fieldnames = reader.fieldnames
        rows = list(reader)
    print(f"{path}\n{len(rows)} rows, {len(fieldnames)} columns\n")
    if not rows:
        sys.exit("Refusing to touch an empty species.csv.")

    by_sci = {r["ScientificName"].strip().lower(): r for r in rows}
    planned = []
    for r in rows:
        is_species = r["Level"].strip().upper().endswith("SPECIES")
        if not is_species or (r["TargetPath"] or "").strip():
            continue
        derived = _derive(r, by_sci)
        if not derived:
            print(f"  SKIP {r['ScientificName']:30} cannot derive (broken Up chain)")
            continue
        planned.append((r, derived))

    if not planned:
        print("Nothing to fix — every species row already has a TargetPath.")
        return 0
    print(f"{len(planned)} species row(s) would get a TargetPath:\n")
    for r, derived in planned:
        print(f"  {r['ScientificName']:30} -> {derived}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    before = [dict(r) for r in rows]           # for the after-the-fact assertion
    for r, derived in planned:
        r["TargetPath"] = derived

    # Invariants: same rows, same columns, and ONLY the planned cells changed.
    assert len(rows) == len(before), "row count changed"
    changed = []
    for old, new in zip(before, rows):
        for k in fieldnames:
            if (old.get(k) or "") != (new.get(k) or ""):
                changed.append((new["ScientificName"], k))
    expected = {(r["ScientificName"], "TargetPath") for r, _ in planned}
    assert set(changed) == expected, f"unexpected edits: {set(changed) - expected}"
    print(f"\nVerified: {len(changed)} cell(s) changed, all of them TargetPath.")

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup = path.with_name(f"species.csv.backup_{stamp}")
    shutil.copy2(path, backup)
    print(f"Backup: {backup}")

    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, delimiter=DELIM)
        w.writeheader()
        w.writerows(rows)
    # Re-read the temp file before trusting it — a short write must never
    # become the master species list.
    with open(tmp, encoding="utf-8", newline="") as f:
        check = list(csv.DictReader(f, delimiter=DELIM))
    assert len(check) == len(rows), f"temp file has {len(check)} rows, expected {len(rows)}"
    os.replace(tmp, path)
    print(f"Written. {len(planned)} row(s) fixed; {len(rows)} rows total.")
    print("The master DB cache rebuilds from this file on next open.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
