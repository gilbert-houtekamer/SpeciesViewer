"""Master add/delete operator (the engine's missing 'delete operator').

The master collection was built append-only. This module is the single place
that performs a *reversible* master mutation, coordinating three layers:

  1. **Files on disk** — deleted files are MOVED to an in-repo trash
     (`<master>/.trash/<date>/`, mirroring their relative path) with a manifest
     and a JSON snapshot of the removed DB rows, so a delete can be undone and
     no bytes are ever lost. Added files are copied into the species folder.
  2. **The master SQLite DB** — via the Phase-2 durable write path
     (`MasterDB.apply_delete_upn / apply_delete_file / apply_add_file`): a
     `wal.jsonl` intent, the SQLite mutation, then a debounced CSV checkpoint.
  3. **species.csv counts** — maintained inside those DB ops for delete (which,
     thanks to the no-JPG invariant, only shifts plain picture counts). Add's
     species-level rollups are left to the Recount backstop.

Hard invariant — **a JPG is never trashed.** A UPN that contains any `.jpg`/
`.jpeg` is not deletable (the JPG may be referenced elsewhere, e.g. a blog).
Deletes therefore target redundant RAW-only pictures and stray non-JPG files.

Order of operations: **DB-first, then trash** for delete (a crash in between
leaves harmless on-disk orphans rather than a CSV that dangles at missing
files); **copy-in first, then DB** for add. Both keep all bytes recoverable.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path

logger = logging.getLogger("speciesviewer")

_JPG_EXTS = {"jpg", "jpeg"}


class MasterMutateError(Exception):
    """Refused or failed master mutation (e.g. JPG-protection violation)."""


# ── path resolution (master files are stored relative to the master root) ──────

def _rel_dir(picture: dict) -> str:
    return (picture.get("TargetPath") or picture.get("BestJPGPathName") or "").strip()


def _abs_path(master_root: Path, picture: dict, file_row: dict) -> Path | None:
    """Resolve a master file's on-disk path. Master CSV paths are RELATIVE
    (rebuilt from master_root + the picture's species folder); fall back to the
    file row's WindowsFileName for older absolute rows."""
    fname = file_row.get("File:FileName", "")
    rel = _rel_dir(picture)
    if rel and fname:
        p = master_root / rel / fname
        if p.exists():
            return p
    win = file_row.get("WindowsFileName", "")
    if win and Path(win).exists():
        return Path(win)
    # Return the relative-derived path even if missing, so callers can log it.
    if rel and fname:
        return master_root / rel / fname
    return Path(win) if win else None


def _is_jpg(file_row: dict) -> bool:
    ext = (file_row.get("WindowsExtension") or "").lower()
    if ext in _JPG_EXTS:
        return True
    return Path(file_row.get("File:FileName", "")).suffix.lstrip(".").lower() in _JPG_EXTS


# ── trash ──────────────────────────────────────────────────────────────────────

def trash_root(master_root) -> Path:
    return Path(master_root) / ".trash"


def _manifest_path(master_root: Path) -> Path:
    return trash_root(master_root) / "trash_manifest.csv"


def _append_manifest(master_root: Path, rows: list[dict]) -> None:
    import csv
    mp = _manifest_path(master_root)
    mp.parent.mkdir(parents=True, exist_ok=True)
    new = not mp.exists()
    fields = ["ts", "op", "upn", "rel_path", "orig_abspath", "trashed_path", "batch"]
    with open(mp, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _move_to_trash(master_root: Path, src: Path, day: str, upn: str) -> Path:
    """Move src into a SHALLOW <master>/.trash/<day>/<upn>/<filename> (grouped by
    deleted picture, not a deep mirror of the biota tree), deduplicating name
    collisions. The manifest keeps the absolute original path for restore.
    shutil.move is cross-device-safe."""
    safe_upn = "".join(c if c not in '<>:"/\\|?*' else "_" for c in upn) or "upn"
    dest_dir = trash_root(master_root) / day / safe_upn
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        stem, suf = dest.stem, dest.suffix
        n = 1
        while dest.exists():
            dest = dest.with_name(f"{stem}__{n}{suf}")
            n += 1
    shutil.move(str(src), str(dest))
    return dest


def _belongs_to_upn(filename: str, upn: str) -> bool:
    """A file belongs to a UPN when its name starts with the UPN stem followed by
    a separator (so all of dsc_0620.nef / dsc_0620.jpg / dsc_0620_DxO.jpg /
    dsc_0620-small.jpg / dsc_0620.nef.dop / dsc_0620.xmp match, but dsc_06201
    does not). Case-insensitive — the UPN is already lower-case."""
    name = filename.lower()
    u = upn.lower()
    if not name.startswith(u):
        return False
    rest = name[len(u):]
    return rest == "" or rest[0] in "._- "


# ── delete ─────────────────────────────────────────────────────────────────────

def _collect_files(db, upn: str) -> tuple[dict, dict]:
    """Return (picture_row, {fname: file_row}) for a UPN from the DB."""
    picture = db.picture(upn)
    if picture is None:
        raise MasterMutateError(f"Picture {upn!r} not found in master.")
    files = db.files_for_upns([upn])
    return picture, files


def delete_picture(db, master_root, upn: str) -> dict:
    """Delete a whole no-JPG picture (UPN). Trashes its RAW/XMP/DOP files, drops
    the DB rows, decrements species counts. Refuses if the UPN has any JPG.
    Returns a summary dict (trashed paths, batch id)."""
    master_root = Path(master_root)
    picture, files = _collect_files(db, upn)
    if any(_is_jpg(fr) for fr in files.values()):
        raise MasterMutateError(
            f"{upn!r} contains a JPG — JPG-bearing pictures are protected and "
            f"cannot be deleted.")

    # Snapshot for undo BEFORE we mutate the DB.
    snapshot = {"upn": upn, "picture": picture, "files": list(files.values())}
    day = date.today().isoformat()
    batch = f"{day}_{int(time.time()*1000)}"

    # DB-first (durable), then trash the files.
    fnames = db.apply_delete_upn(upn)
    if fnames is None:
        raise MasterMutateError(f"{upn!r} vanished before delete.")

    trashed, manifest_rows = [], []
    for fname, fr in files.items():
        p = _abs_path(master_root, picture, fr)
        if p and p.exists():
            dest = _move_to_trash(master_root, p, day, upn)
            trashed.append(str(dest))
            try:
                rel = str(p.relative_to(master_root))
            except ValueError:
                rel = p.name
            manifest_rows.append({"ts": f"{time.time():.0f}", "op": "delete_upn",
                                  "upn": upn, "rel_path": rel, "orig_abspath": str(p),
                                  "trashed_path": str(dest), "batch": batch})
        else:
            logger.warning(f"delete_picture: file not on disk, only DB row removed: {fname}")
    _append_manifest(master_root, manifest_rows)
    _write_snapshot(master_root, batch, snapshot)
    logger.info(f"Deleted master picture {upn} ({len(trashed)} files trashed, batch {batch}).")
    return {"upn": upn, "trashed": trashed, "batch": batch}


def delete_file(db, master_root, upn: str, fname: str) -> dict:
    """Delete one non-JPG file from a UPN. If it is the last file, this becomes a
    whole-picture delete. Refuses JPG files."""
    master_root = Path(master_root)
    picture, files = _collect_files(db, upn)
    fr = files.get(fname)
    if fr is None:
        raise MasterMutateError(f"File {fname!r} not in UPN {upn!r}.")
    if _is_jpg(fr):
        raise MasterMutateError(f"{fname!r} is a JPG — JPGs are protected.")
    remaining = [f for f in files if f != fname]
    if not remaining:
        return delete_picture(db, master_root, upn)

    day = date.today().isoformat()
    batch = f"{day}_{int(time.time()*1000)}"
    snapshot = {"upn": upn, "picture": picture, "files": [fr], "single_file": fname}
    if not db.apply_delete_file(upn, fname):
        raise MasterMutateError(f"DB delete_file failed for {fname!r}.")

    p = _abs_path(master_root, picture, fr)
    trashed = []
    if p and p.exists():
        dest = _move_to_trash(master_root, p, day, upn)
        trashed.append(str(dest))
        try:
            rel = str(p.relative_to(master_root))
        except ValueError:
            rel = p.name
        _append_manifest(master_root, [{"ts": f"{time.time():.0f}", "op": "delete_file",
                                        "upn": upn, "rel_path": rel, "orig_abspath": str(p),
                                        "trashed_path": str(dest), "batch": batch}])
    _write_snapshot(master_root, batch, snapshot)
    logger.info(f"Deleted file {fname} from master UPN {upn} (batch {batch}).")
    return {"upn": upn, "fname": fname, "trashed": trashed, "batch": batch}


# ── add (attach a crop JPG to an existing UPN) ────────────────────────────────

def add_upn_files(db, master_root, upn: str) -> dict:
    """Ingest every file in the UPN's species folder that belongs to this picture
    (name starts with the UPN) and isn't tracked yet — the typical case being a
    DxO render dropped beside the RAW (`..._DxO.jpg` plus a `.dop`). Picks up
    JPGs AND metafiles; best-JPG is re-distilled per added JPG. Returns a summary
    with separate JPG / metafile counts."""
    master_root = Path(master_root)
    picture = db.picture(upn)
    if picture is None:
        raise MasterMutateError(f"Picture {upn!r} not found in master.")
    rel = _rel_dir(picture)
    if not rel:
        raise MasterMutateError(f"{upn!r} has no species folder (TargetPath).")
    folder = master_root / rel
    if not folder.exists():
        raise MasterMutateError(f"Species folder not found: {folder}")
    existing = {f.lower() for f in (picture.get("Files") or [])}
    added: list[str] = []
    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        if not _belongs_to_upn(p.name, upn):
            continue
        if p.name.lower() in existing:
            continue
        row = _read_exif_row(p, master_root, rel, upn)
        if db.apply_add_file(upn, row):
            added.append(p.name)
    jpgs = [n for n in added if n.rsplit(".", 1)[-1].lower() in _JPG_EXTS]
    metas = [n for n in added if n not in jpgs]
    logger.info(f"Added {len(jpgs)} JPG(s) + {len(metas)} metafile(s) to master "
                f"UPN {upn}: {added}")
    return {"upn": upn, "added": added, "jpg": len(jpgs), "meta": len(metas)}


def _read_exif_row(path: Path, master_root: Path, rel: str, upn: str) -> dict:
    """Build a data_files row for a freshly added file. Reads a handful of EXIF
    fields with exiftool; the pipeline / a Recount fills the rest. Only the
    picture-distillation fields (name, ext, size) need to be right here."""
    from engine.csv_columns import DATA_FILE_FIELDS
    row = {c: "" for c in DATA_FILE_FIELDS}
    row["File:FileName"] = path.name
    row["WindowsExtension"] = path.suffix.lstrip(".").lower()
    row["WindowsFileName"] = str(path)
    row["File:Directory"] = str(path.parent)
    row["UniquePictureName"] = upn
    try:
        out = subprocess.run(
            ["exiftool", "-j", "-charset", "filename=utf8", "-charset", "utf8",
             "-ImageSize", "-XMP:Rating", "-XMP:Label", "-XMP:Subject",
             "-EXIF:DateTimeOriginal", str(path)],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        if out.returncode == 0 and out.stdout:
            meta = json.loads(out.stdout)[0]
            row["ImageSize"] = str(meta.get("ImageSize", "") or "")
            row["XMP:Rating"] = str(meta.get("Rating", "") or "")
            row["XMP:Label"] = str(meta.get("Label", "") or "")
            row["EXIF:DateTimeOriginal"] = str(meta.get("DateTimeOriginal", "") or "")
    except Exception as e:
        logger.warning(f"add_file: exiftool read failed for {path.name}: {e}")
    return row


# ── undo / snapshots ───────────────────────────────────────────────────────────

def _snapshot_path(master_root: Path, batch: str) -> Path:
    return trash_root(master_root) / "snapshots" / f"{batch}.json"


def _write_snapshot(master_root: Path, batch: str, snapshot: dict) -> None:
    sp = _snapshot_path(master_root, batch)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")


def undo_delete(db, master_root, batch: str) -> dict:
    """Reverse a delete batch: move the trashed files back and re-insert the
    snapshotted picture/file rows into the DB. (Species counts are then best
    repaired with a Recount; the common single-delete case is exact.)"""
    master_root = Path(master_root)
    sp = _snapshot_path(master_root, batch)
    if not sp.exists():
        raise MasterMutateError(f"No snapshot for batch {batch}.")
    snap = json.loads(sp.read_text(encoding="utf-8"))

    # Move files back from the manifest rows for this batch.
    import csv
    mp = _manifest_path(master_root)
    restored = []
    if mp.exists():
        with open(mp, newline="", encoding="utf-8") as fh:
            rows = [r for r in csv.DictReader(fh, delimiter=";") if r.get("batch") == batch]
        for r in rows:
            tp = Path(r["trashed_path"])
            orig = Path(r["orig_abspath"])
            if tp.exists():
                orig.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(tp), str(orig))
                restored.append(str(orig))

    db.apply_restore(snap["picture"], snap["files"])
    logger.info(f"Undid delete batch {batch}: {len(restored)} files restored. "
                f"Run 'Recount master species' to refresh rollups.")
    return {"batch": batch, "restored": restored}


# ── purge ──────────────────────────────────────────────────────────────────────

def trash_size(master_root) -> tuple[int, int]:
    """(total_bytes, file_count) of RECOVERABLE trashed files — the per-day
    folders only, excluding the manifest + snapshots audit trail."""
    root = trash_root(Path(master_root))
    total = count = 0
    if root.exists():
        for day_dir in root.iterdir():
            if not day_dir.is_dir() or day_dir.name == "snapshots":
                continue
            for p in day_dir.rglob("*"):
                if p.is_file():
                    total += p.stat().st_size
                    count += 1
    return total, count


def purge_trash(master_root, older_than_days: int = 0) -> int:
    """Permanently remove trashed day-folders older than `older_than_days`
    (0 = everything). Returns the number of day-folders removed.

    A full empty (older_than_days == 0) removes the ENTIRE `.trash` tree —
    day-folders, snapshots, and the manifest — so nothing stale lingers (and
    nothing keeps syncing in Dropbox). A partial purge also prunes the manifest
    rows + snapshots belonging to the removed days, so the audit trail never
    points at files that are gone."""
    root = trash_root(Path(master_root))
    if not root.exists():
        return 0
    if older_than_days == 0:
        n = sum(1 for d in root.iterdir() if d.is_dir() and d.name != "snapshots")
        shutil.rmtree(root, ignore_errors=True)
        return n
    cutoff = time.time() - older_than_days * 86400
    removed_days = []
    for day_dir in root.iterdir():
        if not day_dir.is_dir() or day_dir.name == "snapshots":
            continue
        if day_dir.stat().st_mtime <= cutoff:
            shutil.rmtree(day_dir, ignore_errors=True)
            removed_days.append(day_dir.name)
    if removed_days:
        _prune_manifest(root, removed_days)
    return len(removed_days)


def _prune_manifest(root: Path, removed_days: list[str]) -> None:
    """Drop manifest rows (and their snapshots) whose batch belongs to a purged
    day. Batch ids are '<YYYY-MM-DD>_<ms>', so a removed day prefixes them."""
    import csv
    mp = root / "trash_manifest.csv"
    if not mp.exists():
        return
    days = set(removed_days)
    with open(mp, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        fields = reader.fieldnames or []
        kept, dropped_batches = [], set()
        for r in reader:
            batch = r.get("batch", "")
            if batch.split("_", 1)[0] in days:
                dropped_batches.add(batch)
            else:
                kept.append(r)
    if not fields:
        return
    with open(mp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        w.writeheader()
        for r in kept:
            w.writerow(r)
    for batch in dropped_batches:
        snap = root / "snapshots" / f"{batch}.json"
        if snap.exists():
            snap.unlink()
