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

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from engine.handling import deriveUniquePictureName

logger = logging.getLogger("speciesviewer")

TRASH_DIRNAME = ".trash"
# Mirrored under this subfolder in the backup so a restore can tell "kept"
# from "already deleted" apart at a glance.
TRASH_SUBDIR = "_trash"
# Where a colour-delete actually puts .trash depends on HOW the day was
# culled (see gui.image_viewer._color_delete_trash_root): a day culled before
# it belonged to a full trip gets .trash INSIDE the day folder itself; once
# it's part of a trip (the normal case here), deletes go to
# <pictures folder>/.trash/<day> (engine/trip_trash.py) — before 2026-10-07 they
# went to <trip_root>/.trash/<day>. A day can carry any of these, so scan ALL
# rather than guessing one.


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
                            exclude_blue: bool = False, trip_root=None
                            ) -> tuple[list[tuple[Path, Path]], dict]:
    """[(source_path, path_relative_to_the_backup_day_folder), ...] for one day,
    plus a stats dict ({"folder_total": N, "excluded_blue": N}) describing the
    plain day folder BEFORE trash is added — the same counts a persisted
    backup-status record needs (see backup_status()), at no extra cost since
    the Blue lookup already runs to build the file list itself.

    Everything in the day folder is included — RAW/HEIC, .xmp/.dop sidecars,
    JPGs, blog.ini, all of it — matching a plain folder copy. `exclude_blue`
    drops every file belonging to a Blue-labelled UPN (the discard-candidate
    colour); Purple is untouched, matching Blue's narrower "not yet deleted,
    but going" meaning in this backup's context. `include_trash` adds the
    day's already-deleted pictures alongside the kept ones, checked at BOTH
    places a colour-delete can have put them — see TRASH_DIRNAME above; pass
    `trip_root` (the trip repo, not the picture root) to also cover the
    in-trip location."""
    day_dir = Path(day_dir)
    blue = _day_blue_stems(day_dir) if exclude_blue else set()

    try:
        all_main = sorted(p for p in day_dir.iterdir() if p.is_file())
    except OSError:
        all_main = []
    main_entries = ([p for p in all_main if deriveUniquePictureName(p.name) not in blue]
                     if blue else all_main)
    stats = {"folder_total": len(all_main), "excluded_blue": len(all_main) - len(main_entries)}

    def _files_of(folder: Path) -> list[Path]:
        try:
            entries = sorted(p for p in folder.iterdir() if p.is_file())
        except OSError:
            return []
        if not blue:
            return entries
        return [p for p in entries if deriveUniquePictureName(p.name) not in blue]

    out: list[tuple[Path, Path]] = [(p, Path(p.name)) for p in main_entries]
    if include_trash:
        from engine.trip_trash import day_trash_dir, legacy_day_trash_dir
        trash_dirs = [day_dir / TRASH_DIRNAME, day_trash_dir(day_dir)]
        if trip_root:
            trash_dirs.append(legacy_day_trash_dir(trip_root, day_dir.name))
        seen_upns: set[str] = set()
        for trash_day in trash_dirs:
            if not trash_day.is_dir():
                continue
            try:
                upn_dirs = sorted(p for p in trash_day.iterdir() if p.is_dir())
            except OSError:
                upn_dirs = []
            for upn_dir in upn_dirs:
                if upn_dir.name in seen_upns:
                    continue  # same UPN trashed in both places — count it once
                seen_upns.add(upn_dir.name)
                out.extend((p, Path(TRASH_SUBDIR) / upn_dir.name / p.name)
                           for p in _files_of(upn_dir))
    return out, stats


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
                 exclude_blue: bool = False, trip_root=None,
                 progress=None, should_cancel=None) -> dict:
    """Copy one day's picture files into <dest_root>/<trip_name>/<day>/.

    Skips a file already at the destination with matching size+mtime.
    progress(done, total, filename), if given, is called before each file;
    should_cancel(), if given, is checked between files — whatever copied
    before a cancel is simply left there (nothing to undo; it's a copy, the
    source is never touched). `trip_root`, if given, lets `include_trash`
    also find deletes made after the day joined a full trip — see
    scan_day_for_raw_backup.
    """
    day_dir = Path(day_dir)
    dest_day = raw_backup_dest(dest_root, trip_name, day_dir.name)
    pairs, stats = scan_day_for_raw_backup(
        day_dir, include_trash=include_trash, exclude_blue=exclude_blue,
        trip_root=trip_root)
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
            "errors": errors, "dest": str(dest_day),
            "folder_total": stats["folder_total"], "excluded_blue": stats["excluded_blue"],
            "include_trash": include_trash, "exclude_blue": exclude_blue}


# ── Remembered status, across sessions ──────────────────────────────────────
#
# The picker table and the trip overview both used to show "copied/total" —
# a plain file count at the destination vs. the day folder — which never
# lands on an exact match once Blue-skip or .trash is in play, even on a day
# that is genuinely fully handled. That's the confusion this replaces: each
# "Copy selected" run now records what it actually did, per day, in
# admin/raw_backup_state.json (in the TRIP repo, not the backup destination —
# so status reads back even when the destination card/drive isn't attached).
# backup_status() below turns that record into one of four words, using only
# the day's current plain file count (which callers already have) — no
# destination access, no XMP reads, same spirit as the Work Backup's own
# "folder vs manifest" staleness rule (deletions count as a change too).

STATE_FILENAME = "raw_backup_state.json"


def _state_path(trip_root) -> Path:
    return Path(trip_root) / "admin" / STATE_FILENAME


def load_backup_state(trip_root) -> dict:
    """day name -> record of that day's last RAW-backup run. Empty if none has
    ever run, or the file can't be read — callers fall back to 'not_copied'
    (or, at the picker, a legacy destination check) either way."""
    p = _state_path(trip_root)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_backup_state(trip_root, state: dict):
    p = _state_path(trip_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def record_backup_run(trip_root, result: dict) -> dict:
    """Fold one day's copy_day_raw() result into the persisted state and save
    it. Called once per day, right after that day's copy finishes, so a
    cancelled run still keeps whatever days it did complete."""
    state = load_backup_state(trip_root)
    state[result["day"]] = {
        "ran_at": datetime.now().isoformat(timespec="seconds"),
        "folder_total": result["folder_total"],
        "include_trash": result["include_trash"],
        "exclude_blue": result["exclude_blue"],
        "copied": result["copied"],
        "skipped": result["skipped"],
        "excluded_blue": result["excluded_blue"],
        "failed": result["failed"],
    }
    save_backup_state(trip_root, state)
    return state[result["day"]]


def backup_status(day_name: str, current_total: int, state: dict) -> str:
    """'not_copied' | 'compact' | 'full' | 'incomplete' — from the persisted
    record alone. 'compact' = the last run excluded both Blue and .trash
    (keepers only); including either makes it 'full'. 'incomplete' = the
    day's plain file count has changed since that run (grown OR shrunk —
    culling is mostly deleting, so a shrink counts as a change same as a
    grow) — UNLESS the shrink is exactly the Blue count that run already
    knew about and left out on purpose: a compact backup's Blue-marked
    pictures getting colour-deleted afterwards (moved out of the day folder)
    is the expected, harmless case, not something newly missing, so it's
    still 'compact' rather than a false 'incomplete'."""
    rec = state.get(day_name)
    if rec is None:
        return "not_copied"
    compact = bool(rec.get("exclude_blue")) and not rec.get("include_trash")
    recorded_total = rec.get("folder_total")
    if current_total != recorded_total:
        if compact and current_total == recorded_total - rec.get("excluded_blue", 0):
            return "compact"
        return "incomplete"
    return "compact" if compact else "full"


def backup_status_verified(day_name: str, current_total: int, state: dict,
                           dest_root: str, trip_name: str) -> str:
    """backup_status(), plus one thing a pure state lookup can't catch: files
    deleted from the destination AFTER a backup ran (a card reformatted, a day
    folder dragged to Trash on the drive itself). The recorded state has no
    way to know that happened — it would otherwise keep reporting 'compact'/
    'full' forever, which is exactly the wrong failure mode for a backup tool.

    Only checked when the destination ROOT is currently reachable, so a day
    whose card just isn't plugged in right now still shows its remembered
    status for free, same as backup_status() alone — this is one extra
    is_dir()-and-peek per TRACKED day, not a full listing, and only paid at
    all when the drive is already known to be present."""
    status = backup_status(day_name, current_total, state)
    if status in ("compact", "full") and dest_root and Path(dest_root).is_dir():
        dest_day = raw_backup_dest(dest_root, trip_name, day_name)
        try:
            has_files = dest_day.is_dir() and any(dest_day.iterdir())
        except OSError:
            has_files = False
        if not has_files:
            return "missing"
    return status


def write_backup_report(trip_root, trip_name: str, dest: str, results: list[dict]) -> Path:
    """Write metadata/reports/RAW backup report.txt — modeled on Count
    Species' 'Species overview.txt': plain text, regenerated fresh each run.
    Covers only the day(s) THIS run touched (matches what backup_status()
    can cheaply know) rather than re-scanning the whole trip."""
    reports_dir = Path(trip_root) / "metadata" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / "RAW backup report.txt"
    lines = [
        f"RAW backup report for {trip_name}",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Backup location: {dest}",
        "",
        "compact = kept pictures only (Blue and already-deleted skipped)",
        "full    = everything, including Blue and/or already-deleted pictures",
        "",
    ]
    tot_copied = tot_skipped = tot_blue = tot_failed = 0
    for r in results:
        mode = "compact" if r.get("exclude_blue") and not r.get("include_trash") else "full"
        lines.append(
            f"{r['day']}: {mode} — {r['copied']} copied, {r['skipped']} already there, "
            f"{r.get('excluded_blue', 0)} excluded (Blue), {r['failed']} failed")
        tot_copied += r["copied"]
        tot_skipped += r["skipped"]
        tot_blue += r.get("excluded_blue", 0)
        tot_failed += r["failed"]
    lines.append("")
    lines.append(f"Totals: {tot_copied} copied, {tot_skipped} already there, "
                 f"{tot_blue} excluded (Blue), {tot_failed} failed.")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path
