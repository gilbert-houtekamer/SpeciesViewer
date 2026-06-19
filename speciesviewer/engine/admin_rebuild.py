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
import re
import csv
import ast
import shutil
import datetime
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
    for dirpath, dirs, files in os.walk(biota):
        # Skip hidden helper dirs (.preview_cache thumbnails, etc.) — they are
        # not canonical pictures and would masquerade as adds / repoint targets.
        dirs[:] = [d for d in dirs if not d.startswith(".")]
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

    # Species differences + the set of picture UPNs the admin already knows
    # (used to tell an "attach" render from a genuinely new picture).
    species_change = []      # (upn, recorded_species, folder_species)
    admin_upns = set()
    with open(pics_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            upn = (row.get("UniquePictureName") or "").strip()
            if upn:
                admin_upns.add(upn)
            rec = (row.get("WetSoortNaam") or "").strip().lower()
            if not upn or not rec:
                continue
            folder = disk_species.get(upn)
            if folder is not None and folder != rec:
                species_change.append((upn, rec, folder))

    # Files on disk the admin doesn't list. Classify each (report only — never
    # auto-applied) so a handful can be eyeballed:
    #   attach : its picture (UPN) is already in the admin -> a DxO render or
    #            extra file of an existing picture, not a new picture;
    #   new?   : UPN unknown but the name carries a camera frame number;
    #   junk?  : UPN unknown and no frame number (banners, stray exports).
    add = []                 # (filename, klass, upn, path)
    for name in sorted(disk_byname):
        if (name in admin_names
                or os.path.splitext(name)[1].lower() not in _IMG_EXTS
                or name.endswith("-small.jpg")):
            continue
        upn = deriveUniquePictureName(name)
        if upn in admin_upns:
            klass = "attach"
        elif re.search(r"\d{3,}", os.path.splitext(name)[0]):
            klass = "new?"
        else:
            klass = "junk?"
        add.append((name, klass, upn, disk_byname[name]))

    out_dir = out_dir or os.path.join(master_repo, "admin", "status")
    os.makedirs(out_dir, exist_ok=True)

    def _write(name, header, rows):
        with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(header)
            w.writerows(rows)

    _write("rebuild_remove.csv", ["File:FileName", "recorded_path"], remove)
    _write("rebuild_repoint.csv", ["File:FileName", "recorded_path", "real_path"], repoint)
    _write("rebuild_add.csv", ["File:FileName", "class", "UPN", "path"], add)
    _write("rebuild_species_change.csv",
           ["UPN", "admin_species", "folder_species"], species_change)

    add_breakdown = {}
    for _n, klass, _u, _p in add:
        add_breakdown[klass] = add_breakdown.get(klass, 0) + 1

    return {"ok": True, "out_dir": out_dir,
            "disk_files": len(disk_paths), "admin_files": len(admin_names),
            "remove": len(remove), "repoint": len(repoint), "add": len(add),
            "add_breakdown": add_breakdown, "species_change": len(species_change)}


# ── Execute the heal (DESTRUCTIVE — rewrites the master admin CSVs) ──────────────

def _read_report(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh, delimiter=";")
        next(reader, None)                      # header
        return [r for r in reader if r]


def _atomic_write_csv(path, header, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(header)
        w.writerows(rows)
    os.replace(tmp, path)


def execute_heal(master_repo, do_remove=True, do_repoint=True, do_species=True):
    """Apply the reviewed rebuild_remove.csv / rebuild_repoint.csv /
    rebuild_species_change.csv to the master admin. Backs up data_files.csv +
    data_pictures.csv first. DESTRUCTIVE.
      - remove : drop those data_files rows; drop the file from each picture's
                 Files list; drop a picture whose Files becomes empty.
      - repoint: set WindowsFileName + File:Directory to the real biota path.
      - species: a picture whose folder says a different species than the admin
                 is re-classified to the folder (ground truth): copy Wet/Eng/NL
                 names + TargetPath from an existing row of that species, and
                 rewrite the old species path inside BestJPGPathName.
    data_pictures path fields are otherwise left for Build Website to recompute.
    rebuild_add.csv is NOT applied (see find/refine; mostly renders + junk).
    Returns a counts dict."""
    status = os.path.join(master_repo, "admin", "status")
    remove_rows = _read_report(os.path.join(status, "rebuild_remove.csv")) if do_remove else []
    repoint_rows = _read_report(os.path.join(status, "rebuild_repoint.csv")) if do_repoint else []
    species_rows = _read_report(os.path.join(status, "rebuild_species_change.csv")) if do_species else []
    if not remove_rows and not repoint_rows and not species_rows:
        return {"ok": False, "error": "No rebuild_*.csv to apply — run the dry-run first."}

    remove_key = {(r[0].lower(), _norm(r[1])) for r in remove_rows if len(r) >= 2}
    repoint_map = {(r[0].lower(), _norm(r[1])): r[2] for r in repoint_rows if len(r) >= 3}
    removed_filenames = {r[0] for r in remove_rows if r}
    # UPN -> target species (folder is ground truth); species names are lower-case.
    species_target = {r[0]: r[2] for r in species_rows if len(r) >= 3}
    species_needed = {s.lower() for s in species_target.values()}

    files_csv = os.path.join(master_repo, "metadata", "active", "data_files.csv")
    pics_csv = os.path.join(master_repo, "metadata", "active", "data_pictures.csv")
    if not (os.path.exists(files_csv) and os.path.exists(pics_csv)):
        return {"ok": False, "error": "master CSVs not found"}

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(status, "backup")
    os.makedirs(backup_dir, exist_ok=True)
    shutil.copy2(files_csv, os.path.join(backup_dir, f"data_files.{ts}.csv"))
    shutil.copy2(pics_csv, os.path.join(backup_dir, f"data_pictures.{ts}.csv"))

    # --- data_files.csv: remove + repoint ---
    removed = repointed = 0
    with open(files_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh, delimiter=";")
        header = next(reader)
        idx = {h: i for i, h in enumerate(header)}
        fi, wi, di = idx.get("File:FileName"), idx.get("WindowsFileName"), idx.get("File:Directory")
        out_files = []
        for row in reader:
            fn = row[fi].strip() if fi is not None and fi < len(row) else ""
            rec = (row[wi].strip() if wi is not None and wi < len(row) and row[wi].strip()
                   else (os.path.join(row[di].strip(), fn) if di is not None and di < len(row) and row[di].strip() else ""))
            key = (fn.lower(), _norm(rec))
            if key in remove_key:
                removed += 1
                continue
            new = repoint_map.get(key)
            if new:
                if wi is not None and wi < len(row):
                    row[wi] = new
                if di is not None and di < len(row):
                    row[di] = os.path.dirname(new)
                repointed += 1
            out_files.append(row)
    _atomic_write_csv(files_csv, header, out_files)

    # --- data_pictures.csv: clean Files lists, drop empty pictures, reclassify ---
    pics_dropped = files_cleaned = species_changed = 0
    with open(pics_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh, delimiter=";")
        pheader = next(reader)
        pidx = {h: i for i, h in enumerate(pheader)}
        filesi = pidx.get("Files")
        ui = pidx.get("UniquePictureName")
        wet_i, eng_i, nl_i = pidx.get("WetSoortNaam"), pidx.get("EngSoortNaam"), pidx.get("NLSoortNaam")
        tp_i, bjp_i = pidx.get("TargetPath"), pidx.get("BestJPGPathName")
        out_pics = []
        templates = {}        # species(lower) -> {wet, eng, nl, targetpath}
        species_pending = []  # rows awaiting reclassify (template may appear later)
        for row in reader:
            if removed_filenames and filesi is not None and filesi < len(row):
                raw = row[filesi].strip()
                if raw.startswith("["):
                    try:
                        flist = ast.literal_eval(raw)
                        newf = [f for f in flist if f not in removed_filenames]
                        if len(newf) != len(flist):
                            files_cleaned += 1
                            row[filesi] = str(newf)
                        if not newf:
                            pics_dropped += 1
                            continue
                    except Exception:
                        pass
            # Collect a template row for any species we need to reclassify into.
            if species_needed and wet_i is not None and wet_i < len(row):
                wet = row[wet_i].strip().lower()
                if wet in species_needed and wet not in templates:
                    templates[wet] = {
                        "wet": row[wet_i], "eng": row[eng_i] if eng_i is not None else "",
                        "nl": row[nl_i] if nl_i is not None else "",
                        "tp": row[tp_i] if tp_i is not None else ""}
            if species_target and ui is not None and ui < len(row) and row[ui] in species_target:
                species_pending.append(row)
            out_pics.append(row)

        for row in species_pending:
            t = templates.get(species_target[row[ui]].lower())
            if not t:
                continue          # no existing row of that species to copy from
            old_tp = row[tp_i] if tp_i is not None and tp_i < len(row) else ""
            if wet_i is not None and wet_i < len(row):
                row[wet_i] = t["wet"]
            if eng_i is not None and eng_i < len(row):
                row[eng_i] = t["eng"]
            if nl_i is not None and nl_i < len(row):
                row[nl_i] = t["nl"]
            if bjp_i is not None and bjp_i < len(row) and old_tp and t["tp"]:
                row[bjp_i] = row[bjp_i].replace(old_tp, t["tp"])
            if tp_i is not None and tp_i < len(row):
                row[tp_i] = t["tp"]
            species_changed += 1
    _atomic_write_csv(pics_csv, pheader, out_pics)

    return {"ok": True, "removed_files": removed, "repointed": repointed,
            "kept_files": len(out_files), "pictures_dropped": pics_dropped,
            "files_cleaned": files_cleaned, "species_changed": species_changed,
            "backup_dir": backup_dir}


# ── Orphan sidecars (XMP/DOP whose image was deleted) ───────────────────────────

def find_orphan_sidecars(master_repo, out_csv=None):
    """Walk <master>/biota, group files by unique-picture name, and find groups
    that have a sidecar (.xmp/.dop) but NO image left — the RAW/JPG was deleted,
    leaving a useless sidecar. Returns counts + writes the orphan paths."""
    biota = os.path.join(master_repo, "biota")
    groups = {}      # upn -> {"image": bool, "sidecars": [path]}
    for dirpath, _dirs, files in os.walk(biota):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            # .dop carries a double extension (X.jpg.dop / X.NEF.dop); strip it
            # before deriving so the sidecar groups with its image, not alone.
            base = os.path.splitext(f)[0] if ext == ".dop" else f
            g = groups.setdefault(deriveUniquePictureName(base),
                                  {"image": False, "sidecars": []})
            if ext in _IMG_EXTS and not f.lower().endswith("-small.jpg"):
                g["image"] = True
            elif ext in (".xmp", ".dop"):
                g["sidecars"].append(os.path.join(dirpath, f))

    orphans = []     # (path, ext)
    for g in groups.values():
        if g["sidecars"] and not g["image"]:
            for p in g["sidecars"]:
                orphans.append((p, os.path.splitext(p)[1].lower().lstrip(".")))

    if out_csv:
        os.makedirs(os.path.dirname(out_csv), exist_ok=True)
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["path", "ext"])
            w.writerows(orphans)

    by_ext = {}
    for _p, e in orphans:
        by_ext[e] = by_ext.get(e, 0) + 1
    return {"ok": True, "orphan_files": len(orphans), "by_ext": by_ext,
            "paths": [p for p, _e in orphans], "report": out_csv}


def clean_orphan_sidecars(master_repo):
    """Move orphan sidecars (no image left in the group) to a recoverable trash
    folder under admin/status/orphan_trash/<timestamp>/. Their now-dangling admin
    rows are swept by the next Heal Admin run. Returns counts + trash path."""
    info = find_orphan_sidecars(master_repo)
    paths = info.get("paths", [])
    if not paths:
        return {"ok": True, "moved": 0, "trash": None}
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    trash = os.path.join(master_repo, "admin", "status", "orphan_trash", ts)
    os.makedirs(trash, exist_ok=True)
    moved = 0
    for p in paths:
        try:
            shutil.move(p, os.path.join(trash, os.path.basename(p)))
            moved += 1
        except Exception as e:
            logger.warning(f"clean_orphan_sidecars: {p}: {e}")
    return {"ok": True, "moved": moved, "trash": trash}
