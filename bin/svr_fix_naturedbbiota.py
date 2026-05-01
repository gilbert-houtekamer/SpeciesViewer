"""One-shot fix for the 'naturedbbiota' path corruption in master's
active/data_files.csv.

Earlier versions of sv6Freeze concatenated repository_root + 'biota/'
with raw '+', which produced 'E:\\Dropbox\\naturedbbiota/...' for any
.ini that omitted a trailing separator. Those malformed paths were
then committed into master/active/data_files.csv via sv7. The code
has since been fixed (see commit e83f6e9 / 9250a1f), but the rows
already in the CSV still carry the bad prefix.

This script rewrites the WindowsFileName and File:Directory columns
in place: any occurrence of 'naturedbbiota' is replaced with
'naturedb\\biota'. The original file is backed up to
'<csv>.bak-naturedbbiota' before the rewrite. Idempotent — running
twice is a no-op on the second run.
"""
import csv
import shutil
import sys
from pathlib import Path

MASTER_ACTIVE_CSV = Path(r"E:/Dropbox/naturedb/metadata/active/data_files.csv")
BAD = "naturedbbiota"
GOOD = "naturedb\\biota"
COLUMNS_TO_FIX = ["WindowsFileName", "File:Directory"]


def main():
    if not MASTER_ACTIVE_CSV.exists():
        print(f"ERROR: not found: {MASTER_ACTIVE_CSV}", file=sys.stderr)
        sys.exit(1)

    # Probe before doing anything destructive.
    with open(MASTER_ACTIVE_CSV, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    bad_count = text.count(BAD)
    print(f"File:        {MASTER_ACTIVE_CSV}")
    print(f"Bad string:  '{BAD}' → '{GOOD}'")
    print(f"Occurrences: {bad_count}")
    if bad_count == 0:
        print("Nothing to fix. Exiting.")
        return

    backup = MASTER_ACTIVE_CSV.with_suffix(MASTER_ACTIVE_CSV.suffix + ".bak-naturedbbiota")
    if not backup.exists():
        print(f"Backup:      {backup}")
        shutil.copy(MASTER_ACTIVE_CSV, backup)
    else:
        print(f"Backup already exists: {backup} (leaving it alone)")

    # Rewrite row-by-row, only touching the two columns. We avoid a
    # blind text replace because the bad string could in theory appear
    # in a description field; doing it column-scoped is safer.
    rows_changed = 0
    fields_changed = 0
    with open(MASTER_ACTIVE_CSV, "r", encoding="utf-8", newline="") as fin:
        reader = csv.DictReader(fin, delimiter=";")
        fieldnames = reader.fieldnames
        rows = []
        for row in reader:
            row_touched = False
            for col in COLUMNS_TO_FIX:
                val = row.get(col, "") or ""
                if BAD in val:
                    row[col] = val.replace(BAD, GOOD)
                    fields_changed += 1
                    row_touched = True
            if row_touched:
                rows_changed += 1
            rows.append(row)

    with open(MASTER_ACTIVE_CSV, "w", encoding="utf-8", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames, delimiter=";",
                                quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Rows touched: {rows_changed}")
    print(f"Field rewrites: {fields_changed}")
    print("Done.")


if __name__ == "__main__":
    main()
