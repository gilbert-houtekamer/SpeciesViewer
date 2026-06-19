# admin_audit.py: find "ghost" records in the master admin — rows in
# data_files.csv that reference a file which is not at the recorded path.
#
# Two kinds of ghost, both = corrupt admin:
#   - "elsewhere": a file with that name exists somewhere else under the master
#     (the recorded path is stale/wrong — fixable by repointing);
#   - "dangling": no file with that name exists anywhere in the master (the
#     record points at nothing — a candidate to remove).
# The picture files themselves may still live on a backup/NAS; this is about the
# admin being out of sync with what's on disk.

import os
import csv
import logging

logger = logging.getLogger("speciesviewer")


def _norm(path):
    return path.replace("\\", "/").lower()


def _index_master(master_repo):
    """(set of normalized absolute paths, set of lowercased filenames) actually
    present under the master."""
    paths, names = set(), set()
    for dirpath, _dirs, files in os.walk(master_repo):
        for f in files:
            paths.add(_norm(os.path.join(dirpath, f)))
            names.add(f.lower())
    return paths, names


def _recorded_path(row):
    wfn = (row.get("WindowsFileName") or "").strip()
    if wfn:
        return wfn
    d = (row.get("File:Directory") or "").strip()
    fn = (row.get("File:FileName") or "").strip()
    return os.path.join(d, fn) if d and fn else ""


def find_ghost_files(master_repo, out_csv=None):
    """Audit <master>/metadata/active/data_files.csv. Returns a summary dict and
    (optionally) writes the ghost rows to out_csv for review/cleanup."""
    csvp = os.path.join(master_repo, "metadata", "active", "data_files.csv")
    if not os.path.exists(csvp):
        return {"ok": False, "error": f"master data_files.csv not found: {csvp}"}

    present_paths, present_names = _index_master(master_repo)
    total = 0
    ghosts = []          # (filename, recorded_path, category)
    dangling = elsewhere = 0
    with open(csvp, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            path = _recorded_path(row)
            fn = (row.get("File:FileName") or "").strip()
            if not path or not fn:
                continue
            # "-small.jpg" web thumbnails come and go (dropped when the site no
            # longer references them) — not a corruption signal, so skip them.
            if fn.lower().endswith("-small.jpg"):
                continue
            total += 1
            if _norm(path) in present_paths:
                continue
            if fn.lower() in present_names:
                cat = "elsewhere"
                elsewhere += 1
            else:
                cat = "dangling"
                dangling += 1
            ghosts.append((fn, path, cat))

    if out_csv:
        os.makedirs(os.path.dirname(out_csv), exist_ok=True)
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["File:FileName", "recorded_path", "category"])
            w.writerows(ghosts)

    return {"ok": True, "total": total, "ghosts": len(ghosts),
            "elsewhere": elsewhere, "dangling": dangling, "report": out_csv}
