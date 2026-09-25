#!/usr/bin/env python3
"""Compare what the CSVs claim about colour/rating with what the SIDECARS hold.

The CSV row and the .xmp are two records of one fact. They are written by
different code at different moments, so they can drift — and when they do, the
viewer believes the CSV while PhotoLab, Lightroom and the next Read & Summarize
believe the file. A picture that shows a colour in the grid and has none on disk
is one re-read away from losing it.

Read-only. Prints every disagreement; exit code 1 when any are found, so it can
be used as a check rather than only as a report.

  python tools/svr_label_audit.py <trip-metadata-dir> [day]
  e.g. python tools/svr_label_audit.py E:/Dropbox/trips/brazil2026db/metadata 2026-09-04
"""
import csv, os, re, sys

LABEL_RE = re.compile(r'<xmp:Label>([^<]*)</xmp:Label>|xmp:Label="([^"]*)"')
RATING_RE = re.compile(r'<xmp:Rating>([^<]*)</xmp:Rating>|xmp:Rating="([^"]*)"')


def _from_file(path, rx):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            m = rx.search(fh.read())
    except OSError:
        return None                      # unreadable is not the same as empty
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip()


def audit_day(metadir, day):
    f = os.path.join(metadir, "review", day, "data_files.csv")
    if not os.path.isfile(f):
        return None
    with open(f, encoding="utf-8", errors="replace") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    checked = mismatch = missing = 0
    out = []
    for r in rows:
        if (r.get("WindowsExtension") or "").lower() != "xmp":
            continue
        path = r.get("WindowsFileName") or ""
        if not path:
            continue
        if not os.path.isfile(path):
            missing += 1
            continue
        checked += 1
        csv_lab = (r.get("XMP:Label") or "").strip()
        csv_rat = (r.get("XMP:Rating") or "").strip()
        disk_lab = _from_file(path, LABEL_RE)
        disk_rat = _from_file(path, RATING_RE)
        if disk_lab is None:
            continue
        if csv_lab != disk_lab or (csv_rat and csv_rat != "0" and csv_rat != disk_rat):
            mismatch += 1
            out.append((r.get("UniquePictureName", ""), csv_lab, disk_lab, csv_rat, disk_rat))
    return checked, mismatch, missing, out


def main(argv):
    if len(argv) < 2:
        print(__doc__); return 2
    metadir = argv[1]
    days = [argv[2]] if len(argv) > 2 else sorted(
        d for d in os.listdir(os.path.join(metadir, "review"))
        if os.path.isdir(os.path.join(metadir, "review", d)))
    total_bad = 0
    for day in days:
        res = audit_day(metadir, day)
        if res is None:
            continue
        checked, mismatch, missing, out = res
        flag = "  <-- DRIFT" if mismatch else ""
        print(f"{day}: {checked:5} sidecars checked, {mismatch:4} disagree with the CSV, "
              f"{missing} file(s) absent{flag}")
        for upn, cl, dl, cr, dr in out[:15]:
            print(f"     {upn:34} CSV label={cl!r:8} disk={dl!r:8}  "
                  f"CSV rating={cr!r:4} disk={dr!r}")
        if len(out) > 15:
            print(f"     ... and {len(out) - 15} more")
        total_bad += mismatch
    print(f"\nTOTAL disagreements: {total_bad}")
    return 1 if total_bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
