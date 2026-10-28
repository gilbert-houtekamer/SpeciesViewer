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

Default invariant — **a JPG is never trashed.** A UPN that contains any `.jpg`/
`.jpeg` is not deletable (the JPG may be referenced elsewhere, e.g. a blog).
Deletes therefore target redundant RAW-only pictures and stray non-JPG files.

The one carve-out is `delete_jpg()`, which exists because a JPG can be in the
master that is *not a photograph of the species at all* — a banner strip cropped
for a web header (2000x270), swept in with the rest of a trip. Nothing else can
remove one: it is a JPG, so the invariant protects it, and it therefore keeps
representing its species. The carve-out is opt-in (`allow_jpg=True`), never the
default, and it still trashes rather than deletes, so the bytes remain
recoverable. The reason the invariant exists — "the JPG may be referenced
elsewhere" — is met head-on rather than dodged: `apply_delete_jpg` re-derives
every reference (the species' JPGmap, the picture's BestJPG, the species and
ancestor best-picks) as part of the same durable write.

Order of operations: **DB-first, then trash** for delete (a crash in between
leaves harmless on-disk orphans rather than a CSV that dangles at missing
files); **copy-in first, then DB** for add. Both keep all bytes recoverable.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from utils import no_window
import time
from datetime import date, datetime
from pathlib import Path

from utils.paths import to_fs_path
from utils.exiftool_path import exe as exiftool_exe

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
    win_path = to_fs_path(win) if win else None   # host-independent (Win-authored ok on macOS)
    if win_path and win_path.exists():
        return win_path
    # Return the relative-derived path even if missing, so callers can log it.
    if rel and fname:
        return master_root / rel / fname
    return win_path


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


def _safe_upn(upn: str) -> str:
    """A picture's trash folder name: its UPN, made filename-safe."""
    return "".join(c if c not in '<>:"/\\|?*' else "_" for c in upn) or "upn"


def _move_to_trash(master_root: Path, src: Path, day: str, upn: str) -> Path:
    """Move src into a SHALLOW <master>/.trash/<day>/<upn>/<filename> (grouped by
    deleted picture, not a deep mirror of the biota tree), deduplicating name
    collisions. The manifest keeps the absolute original path for restore.
    shutil.move is cross-device-safe."""
    dest_dir = trash_root(master_root) / day / _safe_upn(upn)
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


def delete_picture(db, master_root, upn: str, *, allow_jpg: bool = False) -> dict:
    """Delete a whole picture (UPN). Trashes its files, drops the DB rows, decrements
    species counts. Refuses a JPG-bearing UPN unless `allow_jpg` — see delete_jpg().
    Returns a summary dict (trashed paths, batch id)."""
    master_root = Path(master_root)
    picture, files = _collect_files(db, upn)
    if not allow_jpg and any(_is_jpg(fr) for fr in files.values()):
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
    logger.info(f"Deleted file {fname} from master picture {upn} (batch {batch}).")
    return {"upn": upn, "fname": fname, "trashed": trashed, "batch": batch}


def delete_jpg(db, master_root, upn: str, fname: str) -> dict:
    """Trash ONE JPG — the deliberate carve-out from the never-trash-a-JPG rule.

    Its `-small` thumbnail goes with it: a thumbnail whose source no longer exists is
    an orphan, and it is the file the index page would still try to render.

    If the JPG turns out to be the picture's ONLY file, this becomes a whole-picture
    delete (a banner export often has no RAW behind it — it is a second crop of a
    frame that is already in the master under its own name).

    Everything the JPG was referenced by is re-derived inside the DB write, not here.
    """
    master_root = Path(master_root)
    picture, files = _collect_files(db, upn)
    fr = files.get(fname)
    if fr is None:
        raise MasterMutateError(f"File {fname!r} not in UPN {upn!r}.")
    if not _is_jpg(fr):
        raise MasterMutateError(
            f"{fname!r} is not a JPG — use delete_file() for RAW/XMP/DOP.")

    # the thumbnail derived from THIS jpg (…-small.jpg), if the master tracks one
    stem = Path(fname).stem
    small = [f for f in files
             if f.lower().startswith(stem.lower() + "-small") and _is_jpg(files[f])]
    victims = [fname] + small

    remaining = [f for f in files if f not in victims]
    if not remaining:
        return delete_picture(db, master_root, upn, allow_jpg=True)

    day = date.today().isoformat()
    batch = f"{day}_{int(time.time()*1000)}"
    snapshot = {"upn": upn, "picture": picture,
                "files": [files[f] for f in victims], "single_file": fname}
    if not db.apply_delete_jpg(upn, victims):
        raise MasterMutateError(f"DB delete_jpg failed for {fname!r}.")

    trashed, manifest_rows = [], []
    for v in victims:
        p = _abs_path(master_root, picture, files[v])
        if p and p.exists():
            dest = _move_to_trash(master_root, p, day, upn)
            trashed.append(str(dest))
            try:
                rel = str(p.relative_to(master_root))
            except ValueError:
                rel = p.name
            manifest_rows.append({"ts": f"{time.time():.0f}", "op": "delete_jpg",
                                  "upn": upn, "rel_path": rel, "orig_abspath": str(p),
                                  "trashed_path": str(dest), "batch": batch})
        else:
            logger.warning(f"delete_jpg: not on disk, only DB row removed: {v}")
    _append_manifest(master_root, manifest_rows)
    _write_snapshot(master_root, batch, snapshot)
    logger.info(f"Trashed JPG {fname} from master picture {upn} "
                f"({len(trashed)} file(s), batch {batch}).")
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
    # "Already tracked" means A ROW EXISTS, not "the picture's Files list mentions it".
    # Files is a cached opinion and it can drift: a half-applied journal entry updated
    # it with a DxO render whose file row was dropped as stale, so the scan skipped the
    # very file that was missing and the picture went on naming a row that did not
    # exist. The files table is the truth about what is tracked.
    existing = {f.lower() for f in db.files_for_upns([upn])}
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
                f"picture {upn}: {added}")
    return {"upn": upn, "added": added, "jpg": len(jpgs), "meta": len(metas)}


def untracked_renders_for_upns(db, master_root, upns) -> dict[str, list[str]]:
    """Read-only counterpart of add_upn_files, for a whole day/album at once.

    {upn: [render filenames]} for every UPN that has a JPG render sitting in its
    species folder — belonging to it (name starts with the UPN) and NOT a '-small'
    web thumbnail — with no master file row yet. These are exactly the files
    add_upn_files would register; the album grid uses this to badge a card whose
    render would silently miss the blog, and the bulk "Add new renders" action uses
    it to know which UPNs to ingest. Folder listings are cached so pictures sharing a
    species folder are scanned once."""
    master_root = Path(master_root)
    listing_cache: dict[Path, list[str]] = {}
    out: dict[str, list[str]] = {}
    for upn in upns:
        picture = db.picture(upn)
        if picture is None:
            continue
        rel = _rel_dir(picture)
        if not rel:
            continue
        folder = master_root / rel
        names = listing_cache.get(folder)
        if names is None:
            try:
                names = [p.name for p in folder.iterdir() if p.is_file()]
            except OSError:
                names = []
            listing_cache[folder] = names
        existing = {f.lower() for f in db.files_for_upns([upn])}
        hits = [
            name for name in names
            if name.rsplit(".", 1)[-1].lower() in _JPG_EXTS
            and not name.lower().endswith("-small.jpg")
            and _belongs_to_upn(name, upn)
            and name.lower() not in existing
        ]
        if hits:
            out[upn] = sorted(hits)
    return out


def resync_jpg_sizes(db, master_root, upn: str) -> list[dict]:
    """Re-read the pixel size of every TRACKED JPG of this picture, and correct the
    master where the file on disk has changed underneath it.

    add_upn_files only ever ADDS files it has never seen. Re-export a JPG from PhotoLab
    over its own name — which is the whole point of replacing in place, and what the
    crop upgrade relies on — and the master keeps the OLD ImageSize forever: it has a
    row for that filename, so nothing looks at the file again. The picture then lies
    about itself to everything downstream (the "use this JPG" picker, BestJPG, and any
    future decision that compares sizes).

    Cheap: PIL reads only the JPEG header, no decode. Returns what changed."""
    from PIL import Image
    master_root = Path(master_root)
    picture = db.picture(upn)
    if picture is None:
        raise MasterMutateError(f"Picture {upn!r} not found in master.")
    files = db.files_for_upns([upn])
    changed = []
    for fname, fr in files.items():
        if not _is_jpg(fr):
            continue
        p = _abs_path(master_root, picture, fr)
        if not p or not p.exists():
            continue
        try:
            with Image.open(p) as im:
                w, h = im.size
        except Exception as e:
            logger.warning(f"resync_jpg_sizes: cannot read {p}: {e}")
            continue
        new_size = f"{w}x{h}"
        old_size = (fr.get("ImageSize") or "").strip()
        if _norm_size(old_size) == _norm_size(new_size):
            continue
        if db.apply_jpg_replaced(upn, fname, new_size):
            changed.append({"fname": fname, "old": old_size or "(none)", "new": new_size})
            logger.info(f"resync: {fname} was {old_size or '(none)'} in the master, "
                        f"is {new_size} on disk — corrected.")
    return changed


def _norm_size(s: str) -> tuple:
    """'2000x1334' / '2000 1334' → (2000, 1334); unparseable → ()."""
    import re
    nums = re.findall(r"\d+", s or "")
    return tuple(int(n) for n in nums[:2])


def _exif_batch(paths: list[Path]) -> dict:
    """ONE exiftool call for many files. Returns {normcased abspath: meta}.

    Via an argfile, not argv: a species folder holds hundreds of files and a
    command line of that many long paths does not fit in a Windows argv at all."""
    import os
    import tempfile
    if not paths:
        return {}
    af = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as fh:
            af = fh.name
            for p in paths:
                fh.write(str(p) + "\n")
        out = no_window.run(
            [exiftool_exe(), "-j", "-q", "-charset", "filename=utf8", "-charset", "utf8",
             "-ImageSize", "-XMP:Rating", "-XMP:Label", "-XMP:Subject",
             "-EXIF:DateTimeOriginal", "-@", af],
            capture_output=True, text=True, encoding="utf-8", timeout=600)
        rows = json.loads(out.stdout) if (out.returncode == 0 and out.stdout.strip()) else []
    except Exception as e:
        logger.warning(f"exiftool batch read failed: {e}")
        return {}
    finally:
        if af:
            try:
                os.unlink(af)
            except OSError:
                pass
    return {os.path.normcase(os.path.normpath(r.get("SourceFile", ""))): r
            for r in rows}


def _row_from_meta(path: Path, upn: str, meta: dict) -> dict:
    from engine.csv_columns import DATA_FILE_FIELDS
    row = {c: "" for c in DATA_FILE_FIELDS}
    row["File:FileName"] = path.name
    row["WindowsExtension"] = path.suffix.lstrip(".").lower()
    row["WindowsFileName"] = str(path)
    row["File:Directory"] = str(path.parent)
    row["UniquePictureName"] = upn
    row["ImageSize"] = str(meta.get("ImageSize", "") or "")
    row["XMP:Rating"] = str(meta.get("Rating", "") or "")
    row["XMP:Label"] = str(meta.get("Label", "") or "")
    row["EXIF:DateTimeOriginal"] = str(meta.get("DateTimeOriginal", "") or "")
    return row


def refresh_species(db, master_root, pictures: list[dict]) -> dict:
    """Ingest new files and correct changed JPG sizes for a WHOLE SPECIES at once.

    The per-picture version of this (add_upn_files + resync_jpg_sizes in a loop) listed
    the species folder once PER PICTURE and spawned exiftool once PER NEW FILE. On a
    species with 400 pictures that is 400 identical directory listings and a process per
    file. Here it is one listing, one files-table query, and one exiftool call, whatever
    the species costs.

    Returns {"added": [...], "resized": [{fname, old, new}], "folder": Path|None}."""
    import os
    master_root = Path(master_root)
    pictures = [p for p in pictures if p.get("UniquePictureName")]
    if not pictures:
        return {"added": [], "resized": [], "folder": None}

    rel = next((_rel_dir(p) for p in pictures if _rel_dir(p)), "")
    if not rel:
        return {"added": [], "resized": [], "folder": None}
    folder = master_root / rel
    if not folder.exists():
        raise MasterMutateError(f"Species folder not found: {folder}")

    upns = [p["UniquePictureName"] for p in pictures]
    by_upn = {p["UniquePictureName"]: p for p in pictures}
    tracked = db.files_for_upns(upns)                      # ONE query
    tracked_names = {f.lower() for f in tracked}
    # Longest UPN first: 'dsc_0620' must not claim a file of 'dsc_06201'. (_belongs_to_upn
    # already demands a separator, so this is belt and braces — but the cost is nil.)
    upns_by_len = sorted(upns, key=len, reverse=True)

    on_disk = [p for p in folder.iterdir() if p.is_file()]  # ONE listing
    new_files: list[tuple[str, Path]] = []
    for p in on_disk:
        if p.name.lower() in tracked_names:
            continue
        for upn in upns_by_len:
            if _belongs_to_upn(p.name, upn):
                new_files.append((upn, p))
                break

    added: list[str] = []
    if new_files:
        metas = _exif_batch([p for _u, p in new_files])     # ONE exiftool call
        for upn, p in new_files:
            meta = metas.get(os.path.normcase(os.path.normpath(str(p))), {})
            if db.apply_add_file(upn, _row_from_meta(p, upn, meta)):
                added.append(p.name)

    # Sizes of TRACKED jpgs that changed under us (a PhotoLab re-export over the same
    # name). PIL reads the JPEG header only — local, no decode, no process.
    resized: list[dict] = []
    for fname, fr in tracked.items():
        if not _is_jpg(fr):
            continue
        upn = fr.get("UniquePictureName", "")
        pic = by_upn.get(upn)
        if pic is None:
            continue
        p = _abs_path(master_root, pic, fr)
        if not p or not p.exists():
            continue
        try:
            from PIL import Image
            with Image.open(p) as im:
                w, h = im.size
        except Exception as e:
            logger.warning(f"refresh_species: cannot read {p}: {e}")
            continue
        new_size = f"{w}x{h}"
        old_size = (fr.get("ImageSize") or "").strip()
        if _norm_size(old_size) == _norm_size(new_size):
            continue
        if db.apply_jpg_replaced(upn, fname, new_size):
            resized.append({"fname": fname, "old": old_size or "(none)", "new": new_size})
            logger.info(f"resync: {fname} was {old_size or '(none)'} in the master, "
                        f"is {new_size} on disk — corrected.")

    logger.info(f"refresh_species: {folder.name} — {len(on_disk)} file(s) on disk, "
                f"{len(added)} ingested, {len(resized)} size(s) corrected "
                f"(1 listing, 1 exiftool call).")
    return {"added": added, "resized": resized, "folder": folder}


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
        out = no_window.run(
            [exiftool_exe(), "-j", "-charset", "filename=utf8", "-charset", "utf8",
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


# ── move to another species ─────────────────────────────────────────────────────

def move_picture_to_species(db, master_root, upn: str, target: dict) -> dict:
    """Reassign a no-JPG master picture to `target` species: relocate its files
    into the target species folder, rewrite the species keyword on the XMP
    sidecar, update the DB picture + file rows, and snapshot the pre-move state
    for undo. Refuses JPG-bearing UPNs — the website best-pick JPG move isn't
    implemented (mirrors the delete JPG-protection invariant).

    `target` = {"sci","nl","en","target_path"} from the master species table
    (db.species_row / db.all_species_rows). Species-count rollups are reconciled
    by a subsequent Recount (like delete/add). Returns a summary dict.
    """
    master_root = Path(master_root)
    picture, files = _collect_files(db, upn)
    if any(_is_jpg(fr) for fr in files.values()):
        raise MasterMutateError(
            f"{upn!r} contains a JPG — JPG-bearing pictures can't be moved "
            f"(the website best-pick move isn't implemented).")

    target_sci = (target.get("sci") or "").strip()
    target_rel = (target.get("target_path") or "").strip()
    if not target_sci:
        raise MasterMutateError("No target species given.")
    if not target_rel:
        raise MasterMutateError(
            f"Target species {target_sci!r} has no folder (TargetPath) in the "
            f"master yet — file a picture there via the pipeline first.")

    def _norm(p: str) -> str:
        return (p or "").strip().rstrip("/\\").replace("\\", "/").lower()

    src_rel = _rel_dir(picture)
    if _norm(src_rel) == _norm(target_rel):
        raise MasterMutateError(f"This picture is already filed under {target_sci}.")

    dest_dir = master_root / target_rel
    dest_dir.mkdir(parents=True, exist_ok=True)

    day = date.today().isoformat()
    batch = f"{day}_{int(time.time()*1000)}"
    snapshot = {"op": "move_species", "upn": upn, "picture": picture,
                "files": list(files.values()), "src_rel": src_rel,
                "target_rel": target_rel}

    from engine.exif_writer import exiftool_write
    from engine.filepicture import format_subject_for_xmp
    correct_for_xmp = format_subject_for_xmp(target_sci)

    # Plan the per-file source→dest moves up front WITHOUT touching disk, so the
    # DB write can go first (below). NB the files table has no TargetPath column —
    # the species/folder lives on the picture row; files carry only path fields.
    plan = []            # (fname, src, dest)
    file_updates = {}
    for fname, fr in files.items():
        src = _abs_path(master_root, picture, fr)
        dest = dest_dir / fname
        if src and src.exists() and dest.exists() and _norm(str(dest)) != _norm(str(src)):
            # Cross-species same-name collision is unlikely (names carry the UPN)
            # but never overwrite: suffix the incoming file if the name is taken.
            stem, suf = dest.stem, dest.suffix
            n = 1
            while dest.exists():
                dest = dest.with_name(f"{stem}__{n}{suf}")
                n += 1
        plan.append((fname, src, dest))
        file_updates[fname] = {
            "File:FileName": dest.name,
            "File:Directory": str(dest_dir),
            "WindowsFileName": str(dest),
        }

    picture_fields = {
        "WetSoortNaam": target_sci,
        "NLSoortNaam": target.get("nl") or "",
        "EngSoortNaam": target.get("en") or "",
        "subject": target_sci,
        "TargetPath": target_rel,
        "BestJPGPathName": "",   # no JPG on a moveable picture
    }

    # DB-FIRST: apply the reassignment before moving any bytes. If the DB write
    # fails (e.g. a bad column), it self-cleans its jsonl intent and raises here —
    # and no files have been touched, so nothing is left inconsistent.
    _write_snapshot(master_root, batch, snapshot)
    db.apply_move_species(upn, picture_fields=picture_fields, file_updates=file_updates)

    # Then relocate the files + rewrite the sidecar keyword. A failure here is
    # rare (disk/permission); the snapshot + partial manifest keep it recoverable.
    moved, manifest_rows = [], []
    for fname, src, dest in plan:
        if src and src.exists():
            shutil.move(str(src), str(dest))
            moved.append(str(dest))
            manifest_rows.append({"ts": f"{time.time():.0f}", "op": "move_species",
                                  "upn": upn, "rel_path": f"{target_rel}/{dest.name}",
                                  "orig_abspath": str(src), "trashed_path": str(dest),
                                  "batch": batch})
        else:
            logger.warning(f"move_picture_to_species: {fname} not on disk; DB path "
                           f"updated anyway.")
        # Keep the sidecar keyword in step (the DB WetSoortNaam stays the
        # authority; this just avoids a stale subject if the file is re-read).
        if dest.exists() and dest.suffix.lower() == ".xmp":
            try:
                exiftool_write(dest, [f"-XMP:Subject={correct_for_xmp}"])
            except Exception as e:
                logger.warning(f"move: subject rewrite failed for {dest.name}: {e}")

    _append_manifest(master_root, manifest_rows)
    logger.info(f"Moved master picture {upn} → {target_sci} "
                f"({len(moved)} files relocated, batch {batch}).")
    return {"upn": upn, "target": target_sci, "moved": moved, "batch": batch}


def undo_move(db, master_root, batch: str) -> dict:
    """Reverse a move batch: relocate the files back to their original folder and
    restore the picture/file species fields from the snapshot. Best-effort like
    undo_delete — a Recount reconciles species counts. (Not wired to a GUI button
    yet; the snapshot + manifest make a move recoverable.)"""
    import csv
    master_root = Path(master_root)
    sp = _snapshot_path(master_root, batch)
    if not sp.exists():
        raise MasterMutateError(f"No snapshot for batch {batch}.")
    snap = json.loads(sp.read_text(encoding="utf-8"))
    if snap.get("op") != "move_species":
        raise MasterMutateError(f"Batch {batch} is not a move.")
    upn = snap["upn"]
    src_rel = snap.get("src_rel", "")
    picture = snap.get("picture", {})
    files = snap.get("files", [])

    restored = []
    mp = _manifest_path(master_root)
    if mp.exists():
        with open(mp, newline="", encoding="utf-8") as fh:
            rows = [r for r in csv.DictReader(fh, delimiter=";")
                    if r.get("batch") == batch and r.get("op") == "move_species"]
        for r in rows:
            cur = Path(r["trashed_path"])    # current (target) location
            old = Path(r["orig_abspath"])    # original home
            if cur.exists():
                old.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(cur), str(old))
                restored.append(str(old))

    picture_fields = {
        "WetSoortNaam": picture.get("WetSoortNaam", ""),
        "NLSoortNaam": picture.get("NLSoortNaam", ""),
        "EngSoortNaam": picture.get("EngSoortNaam", ""),
        "subject": picture.get("subject", ""),
        "TargetPath": picture.get("TargetPath", src_rel),
        "BestJPGPathName": picture.get("BestJPGPathName", ""),
    }
    file_updates = {}
    for fr in files:
        fname = fr.get("File:FileName", "")
        if fname:
            file_updates[fname] = {
                "File:Directory": fr.get("File:Directory", str(master_root / src_rel)),
                "WindowsFileName": fr.get("WindowsFileName", ""),
            }
    db.apply_move_species(upn, picture_fields=picture_fields, file_updates=file_updates)
    logger.info(f"Undid move batch {batch}: {len(restored)} files restored to {src_rel}.")
    return {"batch": batch, "restored": restored}


# ── undo / snapshots ───────────────────────────────────────────────────────────

def _snapshot_path(master_root: Path, batch: str) -> Path:
    return trash_root(master_root) / "snapshots" / f"{batch}.json"


def _write_snapshot(master_root: Path, batch: str, snapshot: dict) -> None:
    sp = _snapshot_path(master_root, batch)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")


# Manifest ops that undo_delete() can reverse. "move_species" also lands in the
# same manifest and is undone by undo_move(), so it must never be offered here.
_DELETE_OPS = ("delete_upn", "delete_file", "delete_jpg")


def restorable_batches(master_root) -> list[dict]:
    """Every delete that can STILL be put back, newest first.

    Reads the trash manifest — the record of what is actually recoverable —
    rather than the snapshots folder, which also holds move batches and says
    nothing about whether the files are still in the trash.

    "Still" is the load-bearing word, and it is answered by one test: at least
    one of the batch's trashed files is present. Empty trash removes them, and
    an undo has already moved them back, so neither an emptied nor an
    already-undone batch appears here. No extra bookkeeping, and nothing to go
    stale — which also means the list is correct after a restore without being
    told a restore happened.

    Known gap: a delete whose files were already missing from disk (DB rows
    only) writes no manifest rows at all, so it cannot be found here. It also
    has nothing to restore but the rows, which is the case least worth listing.
    """
    import csv
    master_root = Path(master_root)
    mp = _manifest_path(master_root)
    if not mp.exists():
        return []
    try:
        with open(mp, newline="", encoding="utf-8") as fh:
            rows = [r for r in csv.DictReader(fh, delimiter=";")
                    if r.get("op") in _DELETE_OPS and r.get("batch")]
    except OSError:
        return []

    batches: dict[str, list[dict]] = {}
    for r in rows:
        batches.setdefault(r["batch"], []).append(r)

    out = []
    # Batch ids are "<YYYY-MM-DD>_<epoch-ms>", so newest-first is a plain
    # reverse sort — the millisecond field is fixed width within a date.
    for batch in sorted(batches, reverse=True):
        brows = batches[batch]
        live = [r for r in brows
                if r.get("trashed_path") and Path(r["trashed_path"]).exists()]
        if not live:
            continue                      # emptied, or already restored
        if not _snapshot_path(master_root, batch).exists():
            continue                      # no snapshot, nothing to re-insert
        size = 0
        for r in live:
            try:
                size += Path(r["trashed_path"]).stat().st_size
            except OSError:
                pass
        try:
            when = datetime.fromtimestamp(int(brows[0]["ts"]))
        except (KeyError, ValueError, OSError):
            when = None
        out.append({
            "batch": batch,
            "ops":   sorted({r["op"] for r in brows}),
            "upns":  sorted({r.get("upn", "") for r in brows if r.get("upn")}),
            "files": [Path(r["orig_abspath"]).name for r in live],
            "paths": [Path(r["trashed_path"]) for r in live],
            "bytes": size,
            "when":  when,
        })
    return out


def last_delete_batch(master_root) -> dict | None:
    """The most recent delete that can still be put back, or None."""
    found = restorable_batches(master_root)
    return found[0] if found else None


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


def trash_has_content(master_root) -> bool:
    """Cheap: is there any recoverable day-folder? (Menu grey-out runs this
    every time the Master menu opens — no walking, no manifest read.)"""
    root = trash_root(Path(master_root))
    try:
        return root.exists() and any(
            d.is_dir() and d.name != "snapshots" for d in root.iterdir())
    except OSError:
        return False


def trash_summary(master_root) -> dict:
    """What an Empty-trash would destroy, in the units you think in:
    {"files", "bytes", "pictures", "species", "first_day", "last_day"}.
    Pictures = the <day>/<picture>/ folders that still hold files (a restore
    leaves them empty). Species = distinct species folders those pictures came
    from, read from the manifest; a picture the manifest does not know (an old
    or damaged manifest) counts toward pictures, not species."""
    import csv
    root = trash_root(Path(master_root))
    out = {"files": 0, "bytes": 0, "pictures": 0, "species": 0,
           "first_day": "", "last_day": ""}
    if not root.exists():
        return out
    upns, days = set(), []
    for day_dir in root.iterdir():
        if not day_dir.is_dir() or day_dir.name == "snapshots":
            continue
        day_has_files = False
        for upn_dir in day_dir.iterdir():
            files = [f for f in upn_dir.rglob("*") if f.is_file()] if upn_dir.is_dir() else []
            if not files:
                continue
            day_has_files = True
            upns.add(upn_dir.name)
            out["files"] += len(files)
            out["bytes"] += sum(f.stat().st_size for f in files)
        if day_has_files:
            days.append(day_dir.name)
    out["pictures"] = len(upns)
    if days:
        days.sort()
        out["first_day"], out["last_day"] = days[0], days[-1]
    species = set()
    mp = _manifest_path(Path(master_root))
    if mp.exists():
        with open(mp, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                if _safe_upn(r.get("upn", "")) in upns and r.get("rel_path"):
                    species.add(Path(r["rel_path"]).parent.as_posix().casefold())
    out["species"] = len(species)
    return out


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
