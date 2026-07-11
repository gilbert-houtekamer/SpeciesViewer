"""Prune dead file/picture links from the master CSVs.

The master metadata (metadata/active/data_files.csv and data_pictures.csv)
accumulates rows that point at files no longer on disk -- mostly dangling
.dop sidecars and pictures that never actually made it into master (their
persisted paths carry a mangled mixed-separator signature from an old
path-construction bug). This tool finds those rows and, on --apply, removes
them, keeping the two CSVs consistent with each other.

DEFAULT = DRY RUN. It only reports and writes review CSVs; it changes
nothing. Pass --apply to rewrite the master CSVs (a dated backup of both is
taken first). Species counters in species.csv are deliberately NOT touched
-- run the existing "Audit Species Counters" / recount afterwards; the delta
is reported here so you know what to expect.

Two cases the tool distinguishes (verified they are the only two):
  Case A  the WHOLE picture is gone  -> drop the picture row + all its file
          rows. These are real removals (13.9k raw/sidecar-only, ~1.1k that
          include a JPG -- eyeball dead_pictures_with_jpg.csv before apply).
  Case B  the picture is ALIVE, only some file rows are dead (almost all
          dangling .dop) -> keep the picture, drop the dead file rows and the
          dead filenames from its Files list. No counter impact.

Usage:
    python tools/svr_prune_dead_links.py [--master PATH] [--report-dir DIR] [--apply]
"""
import argparse
import ast
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# ----------------------------------------------------------------------
# existence oracle -- one per-directory listing, case-insensitive, cached.
# A file "exists" iff it is present under the master root at the location
# the data implies. We check (in order) the normalized WindowsFileName and,
# for picture files, MASTER/TargetPath/filename. Conservative by design:
# if ANY candidate resolves, the row is kept.
# ----------------------------------------------------------------------
_dir_cache = {}


def _dset(directory):
    key = os.path.normcase(os.path.normpath(directory))
    s = _dir_cache.get(key)
    if s is None:
        try:
            s = frozenset(os.path.normcase(n) for n in os.listdir(directory))
        except OSError:  # missing, not-a-dir, or illegal name ("? placeholder")
            s = frozenset()
        _dir_cache[key] = s
    return s


def _norm(path):
    """Collapse any run of / or \\ into a single forward slash."""
    return re.sub(r"[\\/]+", "/", path or "")


def _exists(path):
    path = _norm(path)
    directory, name = path.rsplit("/", 1) if "/" in path else ("", path)
    return bool(directory) and os.path.normcase(name) in _dset(directory)


def parse_files_list(raw):
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        val = ast.literal_eval(raw)
        if isinstance(val, (list, tuple)):
            return [str(x) for x in val]
    except (ValueError, SyntaxError):
        pass
    return [raw]


# ----------------------------------------------------------------------
# analysis
# ----------------------------------------------------------------------
def analyze(master):
    active = os.path.join(master, "metadata", "active")
    files_csv = os.path.join(active, "data_files.csv")
    pics_csv = os.path.join(active, "data_pictures.csv")

    # Build the authoritative existence map keyed by (target_dir, filename)
    # from the picture rows -- that is where master actually stores files.
    # target_by_upn lets file rows resolve their real folder.
    target_by_upn = {}
    pic_rows = []
    with open(pics_csv, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        pic_header = reader.fieldnames
        for row in reader:
            upn = (row.get("UniquePictureName") or "").strip().lower()
            target = (row.get("TargetPath") or "").strip().replace("\\", "/")
            target_by_upn[upn] = target
            pic_rows.append(row)

    def file_alive(upn, filename, windows_path):
        # 1) real master location for this picture
        target = target_by_upn.get(upn)
        if target and _exists(os.path.join(master, target, filename)):
            return True
        # 2) fall back to the row's own (normalized) path
        return _exists(windows_path)

    # ---- classify file rows ----
    file_rows = []
    dead_file_keys = set()   # (upn, filename_lower) that are dead
    with open(files_csv, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        file_header = reader.fieldnames
        for row in reader:
            upn = (row.get("UniquePictureName") or "").strip().lower()
            fn = row.get("File:FileName") or _norm(row.get("WindowsFileName")).rsplit("/", 1)[-1]
            wp = row.get("WindowsFileName")
            alive = file_alive(upn, fn, wp)
            file_rows.append((row, upn, fn, alive))
            if not alive:
                dead_file_keys.add((upn, fn.lower()))

    # ---- classify picture rows ----
    # a picture is fully dead iff every file in its Files list is dead
    case_a_upns = set()      # drop whole picture
    caseb_dead_by_upn = defaultdict(set)  # upn -> {dead filenames} (picture kept)
    counter_impact = Counter()   # species scientific name -> pictures lost (Case A)
    dead_pics_with_jpg = []
    for row in pic_rows:
        upn = (row.get("UniquePictureName") or "").strip().lower()
        target = target_by_upn.get(upn, "")
        files = parse_files_list(row.get("Files"))
        if not files:
            continue
        dead = [f for f in files
                if not _exists(os.path.join(master, target, f))]
        if not dead:
            continue
        if len(dead) == len(files):
            case_a_upns.add(upn)
            sci = (row.get("WetSoortNaam") or "").strip().lower()
            counter_impact[sci] += 1
            if any(f.lower().endswith((".jpg", ".jpeg")) for f in dead):
                dead_pics_with_jpg.append((upn, target, dead))
        else:
            caseb_dead_by_upn[upn] = {f.lower() for f in dead}

    return {
        "file_header": file_header, "pic_header": pic_header,
        "file_rows": file_rows, "pic_rows": pic_rows,
        "dead_file_keys": dead_file_keys,
        "case_a_upns": case_a_upns,
        "caseb_dead_by_upn": caseb_dead_by_upn,
        "counter_impact": counter_impact,
        "dead_pics_with_jpg": dead_pics_with_jpg,
        "files_csv": files_csv, "pics_csv": pics_csv, "active": active,
    }


# ----------------------------------------------------------------------
# reporting
# ----------------------------------------------------------------------
def write_reports(a, report_dir):
    os.makedirs(report_dir, exist_ok=True)

    dead_files = [(r, upn, fn) for (r, upn, fn, alive) in a["file_rows"] if not alive]
    with open(os.path.join(report_dir, "prune_dead_files.csv"), "w",
              encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["WindowsFileName", "UniquePictureName", "FileType"])
        for r, upn, fn in dead_files:
            w.writerow([r.get("WindowsFileName", ""), upn, r.get("File:FileType", "")])

    with open(os.path.join(report_dir, "prune_dead_pictures.csv"), "w",
              encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Case", "UniquePictureName", "WetSoortNaam", "DeadFiles"])
        for row in a["pic_rows"]:
            upn = (row.get("UniquePictureName") or "").strip().lower()
            if upn in a["case_a_upns"]:
                w.writerow(["A_drop_picture", upn, row.get("WetSoortNaam", ""),
                            "|".join(parse_files_list(row.get("Files")))])
            elif upn in a["caseb_dead_by_upn"]:
                w.writerow(["B_trim_files", upn, row.get("WetSoortNaam", ""),
                            "|".join(sorted(a["caseb_dead_by_upn"][upn]))])

    with open(os.path.join(report_dir, "prune_dead_pictures_with_jpg.csv"), "w",
              encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["UniquePictureName", "TargetPath", "DeadFiles"])
        for upn, target, dead in a["dead_pics_with_jpg"]:
            w.writerow([upn, target, "|".join(dead)])

    with open(os.path.join(report_dir, "prune_counter_impact.csv"), "w",
              encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["WetSoortNaam", "PicturesLost"])
        for sci, n in a["counter_impact"].most_common():
            w.writerow([sci, n])
    return len(dead_files)


def print_summary(a, n_dead_files, report_dir, applied):
    print("=" * 68)
    print("PRUNE DEAD LINKS -- " + ("APPLIED" if applied else "DRY RUN (no changes written)"))
    print("=" * 68)
    print(f"data_files.csv    rows        : {len(a['file_rows']):>7}")
    print(f"  dead file rows to remove    : {n_dead_files:>7}")
    print()
    print(f"data_pictures.csv rows        : {len(a['pic_rows']):>7}")
    print(f"  Case A  drop whole picture  : {len(a['case_a_upns']):>7}")
    print(f"  Case B  trim Files list     : {len(a['caseb_dead_by_upn']):>7}")
    print(f"  Case A pictures w/ a JPG    : {len(a['dead_pics_with_jpg']):>7}  <-- eyeball before apply")
    print()
    print(f"species.csv counter impact (NOT auto-applied):")
    print(f"  species losing pictures     : {len(a['counter_impact']):>7}")
    print(f"  total collection pics lost  : {sum(a['counter_impact'].values()):>7}")
    print(f"  -> run Audit Species Counters / recount after apply")
    print()
    print(f"review CSVs written to: {report_dir}")
    for name in ("prune_dead_files.csv", "prune_dead_pictures.csv",
                 "prune_dead_pictures_with_jpg.csv", "prune_counter_impact.csv"):
        print("   ", name)


# ----------------------------------------------------------------------
# apply
# ----------------------------------------------------------------------
def apply(a):
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_dir = os.path.join(a["active"], "backup")
    os.makedirs(backup_dir, exist_ok=True)

    # backup both master CSVs
    for src in (a["files_csv"], a["pics_csv"]):
        base = os.path.basename(src)
        dst = os.path.join(backup_dir, f"{stamp}_pre-prune_{base}")
        with open(src, "rb") as r, open(dst, "wb") as w:
            w.write(r.read())
        print("  backed up ->", dst)

    # rewrite data_files.csv (drop dead rows)
    kept_files = 0
    tmp = a["files_csv"] + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=a["file_header"], delimiter=";",
                           extrasaction="ignore")
        w.writeheader()
        for row, upn, fn, alive in a["file_rows"]:
            if alive:
                w.writerow(row); kept_files += 1
    os.replace(tmp, a["files_csv"])

    # rewrite data_pictures.csv (drop Case A, trim Case B Files list)
    kept_pics = 0
    tmp = a["pics_csv"] + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=a["pic_header"], delimiter=";",
                           extrasaction="ignore")
        w.writeheader()
        for row in a["pic_rows"]:
            upn = (row.get("UniquePictureName") or "").strip().lower()
            if upn in a["case_a_upns"]:
                continue
            if upn in a["caseb_dead_by_upn"]:
                dead = a["caseb_dead_by_upn"][upn]
                files = [f for f in parse_files_list(row.get("Files"))
                         if f.lower() not in dead]
                row = dict(row)
                row["Files"] = str(files)
            w.writerow(row); kept_pics += 1
    os.replace(tmp, a["pics_csv"])

    print(f"\n  data_files.csv    kept {kept_files} rows")
    print(f"  data_pictures.csv kept {kept_pics} rows")
    print("\nDONE. Now run Audit Species Counters / recount to reconcile species.csv.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--master", default="E:/Dropbox/naturedb",
                    help="master root (contains metadata/active/)")
    ap.add_argument("--report-dir", default=None,
                    help="where to write review CSVs (default: ./prune_report)")
    ap.add_argument("--apply", action="store_true",
                    help="actually rewrite the master CSVs (backup taken first)")
    args = ap.parse_args()

    report_dir = args.report_dir or os.path.join(os.getcwd(), "prune_report")
    print("Scanning master:", args.master)
    a = analyze(args.master)
    n_dead = write_reports(a, report_dir)
    print_summary(a, n_dead, report_dir, applied=False)

    if args.apply:
        print("\n--apply given: rewriting master CSVs ...")
        apply(a)
    else:
        print("\n(dry run) re-run with --apply to remove these rows.")


if __name__ == "__main__":
    main()
