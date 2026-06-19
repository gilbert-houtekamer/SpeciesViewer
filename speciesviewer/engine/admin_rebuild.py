# admin_rebuild.py: DRY-RUN "what would change" for rebuilding the master admin
# from the current tree. NON-DESTRUCTIVE.
#
# A clean rebuild would regenerate data_files.csv / data_pictures.csv from what
# is actually on disk: paths read from where each file really sits, and species
# taken from the folder each file lives in. This module does not rebuild — it
# only *compares* the current admin against disk reality so you can see exactly
# what a rebuild would change before replacing anything:
#   - REMOVE  : admin row whose file is nowhere on disk (a dead/ghost line).
#   - REPOINT : admin row whose file exists, but at a different path (old tree).
#   - ADD     : a file on disk that the admin doesn't list at all.
#   - SPECIES : a picture whose folder says a different species than the admin.
# Reports are written to <master>/admin/status/rebuild_*.csv.

import os
import csv
import logging
from engine.handling import deriveUniquePictureName

logger = logging.getLogger("speciesviewer")

_IMG_EXTS = {".orf", ".nef", ".cr2", ".cr3", ".arw", ".rw2", ".raf", ".pef",
             ".dng", ".jpg", ".jpeg", ".heic", ".png", ".tif", ".tiff"}


def _norm(p):
    return p.replace("\\", "/").lower()


def _index_disk(master_repo):
    """Walk the canonical picture tree (<master>/biota) once. Returns
      paths   : set of normalised absolute paths present,
      byname  : {lowercased filename: normalised path} (last wins; names are
                effectively unique here),
      species : {UPN: parent-folder-name} for image files (the folder a picture
                sits in = its species).
    Only biota/ — NOT metadata/review/.../frozen copies or root leftovers, which
    are not the canonical location a rebuild should point at."""
    paths, byname, species = set(), {}, {}
    biota = os.path.join(master_repo, "biota")
    for dirpath, _dirs, files in os.walk(biota):
        folder = os.path.basename(dirpath).lower()
        for f in files:
            full = os.path.join(dirpath, f)
            paths.add(_norm(full))
            byname[f.lower()] = _norm(full)
            ext = os.path.splitext(f)[1].lower()
            if ext in _IMG_EXTS and not f.lower().endswith("-small.jpg"):
                # First image wins per UPN (any file in the group shares folder).
                species.setdefault(deriveUniquePictureName(f), folder)
    return paths, byname, species


def plan_rebuild(master_repo, out_dir=None):
    files_csv = os.path.join(master_repo, "metadata", "active", "data_files.csv")
    pics_csv = os.path.join(master_repo, "metadata", "active", "data_pictures.csv")
    if not os.path.exists(files_csv) or not os.path.exists(pics_csv):
        return {"ok": False, "error": "master data_files.csv / data_pictures.csv not found"}

    disk_paths, disk_byname, disk_species = _index_disk(master_repo)

    remove, repoint = [], []          # (filename, recorded_path[, real_path])
    admin_names = set()
    with open(files_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            fn = (row.get("File:FileName") or "").strip()
            if not fn or fn.lower().endswith("-small.jpg"):
                continue
            admin_names.add(fn.lower())
            wfn = (row.get("WindowsFileName") or "").strip()
            if not wfn:
                d = (row.get("File:Directory") or "").strip()
                wfn = os.path.join(d, fn) if d else ""
            if not wfn:
                continue
            if _norm(wfn) in disk_paths:
                continue
            real = disk_byname.get(fn.lower())
            if real:
                repoint.append((fn, wfn, real))
            else:
                remove.append((fn, wfn))

    # Files on disk the admin doesn't list (would be ADDed). Image files only.
    add = [name for name in disk_byname
           if name not in admin_names
           and os.path.splitext(name)[1].lower() in _IMG_EXTS
           and not name.endswith("-small.jpg")]

    # Species differences: admin's recorded species vs the folder the file is in.
    species_change = []      # (upn, recorded_species, folder_species)
    with open(pics_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            upn = (row.get("UniquePictureName") or "").strip()
            rec = (row.get("WetSoortNaam") or "").strip().lower()
            if not upn or not rec:
                continue
            folder = disk_species.get(upn)
            if folder is not None and folder != rec:
                species_change.append((upn, rec, folder))

    out_dir = out_dir or os.path.join(master_repo, "admin", "status")
    os.makedirs(out_dir, exist_ok=True)

    def _write(name, header, rows):
        with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(header)
            w.writerows(rows)

    _write("rebuild_remove.csv", ["File:FileName", "recorded_path"], remove)
    _write("rebuild_repoint.csv", ["File:FileName", "recorded_path", "real_path"], repoint)
    _write("rebuild_add.csv", ["File:FileName"], [[n] for n in sorted(add)])
    _write("rebuild_species_change.csv",
           ["UPN", "admin_species", "folder_species"], species_change)

    return {"ok": True, "out_dir": out_dir,
            "disk_files": len(disk_paths), "admin_files": len(admin_names),
            "remove": len(remove), "repoint": len(repoint), "add": len(add),
            "species_change": len(species_change)}
