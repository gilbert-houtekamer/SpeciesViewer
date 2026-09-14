"""Copy a day's picture folder to a RAW-backup target (a spare SD card or an
external HDD): the plain Windows copy the user was doing by hand, made
selectable and repeatable.

ONE job: copy files. It never deletes and never touches anything outside the
day (and, opt-in, that day's own .trash). This is deliberately separate from
engine.work_backup, which ZIPs just the WORK — ratings, species, colours,
crops, blog text — and explicitly never touches a RAW ("the cards keep the
pictures"). This module is for the pictures themselves.

Invariant: additive and incremental. A file already at the destination with
matching size and mtime is skipped, so a repeat run over a day that already
has hundreds or thousands of pictures costs a stat() each, not a re-copy —
RAWs never change once written.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from engine.handling import deriveUniquePictureName

logger = logging.getLogger("speciesviewer")

TRASH_DIRNAME = ".trash"
# Trashed files land one level deeper than a live day (.trash/<day>/<UPN>/<file>
# — see gui.preview_provider.delete_keys) — mirrored under this subfolder in the
# backup so a restore can tell "kept" from "already deleted" apart at a glance.
TRASH_SUBDIR = "_trash"


def raw_backup_dest(dest_root, trip_name: str, day_name: str) -> Path:
    """<dest_root>/<trip_name>/<day_name> — the same per-trip layout
    engine.work_backup.trip_dir uses, so one root/card can hold both kinds of
    backup side by side without the two colliding."""
    trip_name = (trip_name or "").strip()
    base = Path(dest_root) / trip_name if trip_name else Path(dest_root)
    return base / day_name


def _day_blue_stems(day_dir: Path) -> set[str]:
    """UPN stems (deriveUniquePictureName) whose XMP:Label is 'Blue'.

    Cache-first via engine.sidecar_cache — exiftool only runs for sidecars
    this machine has not already read since they last changed (the same cache
    ingest and culling keep warm), so this costs nothing extra on a day
    that was just culled in the app."""
    from engine.sidecar_cache import SidecarCache, read_xmp_rows
    try:
        xmps = [p for p in day_dir.iterdir()
                if p.is_file() and p.suffix.lower() == ".xmp"]
    except OSError:
        return set()
    if not xmps:
        return set()
    cache = SidecarCache(day_dir)
    rows: dict[Path, dict] = {}
    stale = [p for p in xmps if cache.get(p) is None]
    for p in xmps:
        row = cache.get(p)
        if row is not None:
            rows[p] = row
    if stale:
        for row in read_xmp_rows(stale):
            src = row.get("SourceFile", "")
            if not src:
                continue
            p = Path(src)
            cache.put(p, row)
            rows[p] = row
        cache.save()
    return {deriveUniquePictureName(p.name) for p, row in rows.items()
            if str(row.get("Label", "") or "").strip().lower() == "blue"}


def scan_day_for_raw_backup(day_dir, *, include_trash: bool = False,
                            exclude_blue: bool = False) -> list[tuple[Path, Path]]:
    """[(source_path, path_relative_to_the_backup_day_folder), ...] for one day.

    Everything in the day folder is included — RAW/HEIC, .xmp/.dop sidecars,
    JPGs, blog.ini, all of it — matching a plain folder copy. `exclude_blue`
    drops every file belonging to a Blue-labelled UPN (the discard-candidate
    colour); Purple is untouched, matching Blue's narrower "not yet deleted,
    but going" meaning in this backup's context. `include_trash` adds the
    day's already-deleted pictures from .trash alongside the kept ones."""
    day_dir = Path(day_dir)
    blue = _day_blue_stems(day_dir) if exclude_blue else set()

    def _files_of(folder: Path) -> list[Path]:
        try:
            entries = sorted(p for p in folder.iterdir() if p.is_file())
        except OSError:
            return []
        if not blue:
            return entries
        return [p for p in entries if deriveUniquePictureName(p.name) not in blue]

    out: list[tuple[Path, Path]] = [(p, Path(p.name)) for p in _files_of(day_dir)]
    if include_trash:
        trash_day = day_dir.parent / TRASH_DIRNAME / day_dir.name
        if trash_day.is_dir():
            try:
                upn_dirs = sorted(p for p in trash_day.iterdir() if p.is_dir())
            except OSError:
                upn_dirs = []
            for upn_dir in upn_dirs:
                out.extend((p, Path(TRASH_SUBDIR) / upn_dir.name / p.name)
                           for p in _files_of(upn_dir))
    return out


def count_copied(day_dir, dest_root, trip_name: str) -> int:
    """How many files already sit at this day's RAW-backup destination.

    A plain directory-listing count, not a content check — see copy_day_raw
    for the real one (size + mtime per file). One iterdir(), not a stat per
    file, so it stays cheap regardless of how many pictures the day holds.
    Shared by the overview column and the backup dialog's day picker, so the
    two never show different numbers for the same day.
    """
    dest_day = raw_backup_dest(dest_root, trip_name, Path(day_dir).name)
    try:
        return sum(1 for p in dest_day.iterdir() if p.is_file()) if dest_day.is_dir() else 0
    except OSError:
        return 0


def copy_day_raw(day_dir, dest_root, trip_name: str, *, include_trash: bool = False,
                 exclude_blue: bool = False, progress=None, should_cancel=None) -> dict:
    """Copy one day's picture files into <dest_root>/<trip_name>/<day>/.

    Skips a file already at the destination with matching size+mtime.
    progress(done, total, filename), if given, is called before each file;
    should_cancel(), if given, is checked between files — whatever copied
    before a cancel is simply left there (nothing to undo; it's a copy, the
    source is never touched).
    """
    day_dir = Path(day_dir)
    dest_day = raw_backup_dest(dest_root, trip_name, day_dir.name)
    pairs = scan_day_for_raw_backup(
        day_dir, include_trash=include_trash, exclude_blue=exclude_blue)
    total = len(pairs)
    copied = skipped = failed = 0
    bytes_copied = 0
    errors: list[str] = []
    for i, (src, rel) in enumerate(pairs, 1):
        if should_cancel and should_cancel():
            logger.warning(f"⏹ RAW backup of {day_dir.name} cancelled "
                           f"after {copied} file(s).")
            break
        if progress:
            progress(i, total, src.name)
        target = dest_day / rel
        try:
            st = src.stat()
            if target.exists():
                tst = target.stat()
                if tst.st_size == st.st_size and int(tst.st_mtime) == int(st.st_mtime):
                    skipped += 1
                    continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            copied += 1
            bytes_copied += st.st_size
        except OSError as e:
            logger.warning(f"RAW backup: could not copy {src.name}: {e}")
            failed += 1
            errors.append(f"{src.name}: {e}")
    if copied:
        logger.info(f"RAW backup {day_dir.name}: {copied} file(s), "
                    f"{bytes_copied/1024/1024:.1f} MB → {dest_day}")
    return {"day": day_dir.name, "total": total, "copied": copied,
            "skipped": skipped, "failed": failed, "bytes": bytes_copied,
            "errors": errors, "dest": str(dest_day)}
