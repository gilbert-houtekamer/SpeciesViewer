"""Back up a day's WORK to a spare card.

ONE job: put everything a day's culling produced into a single ZIP, and answer
whether that ZIP is still current. It never touches RAWs and never deletes.

The cards keep the pictures — that is settled, and this is not a picture backup.
What is at risk is the *work*: culling colours, species identifications,
locations, crops and blog text, which exist only on the laptop. On a real day
(2026-08-14, 202 pictures) the RAWs are 4.1 GB and the sidecars 1.6 MB — 2500:1,
so taking the metadata is free and there is no reason to make it optional.

Invariant: the ZIP is the *current* state of the day, never a history. Each day
has exactly one ZIP, rewritten via .tmp + rename so an interrupted write cannot
destroy the previous good one. Nothing irreplaceable rides on an older copy,
because the cards still hold every RAW.

The manifest inside each ZIP is what makes the whole thing work:

  * "has this day changed?" is answered by comparing a directory scan against
    the manifest — which catches DELETIONS, the entire point of culling, and
    which a newest-mtime check silently misses.
  * it records every RAW filename that existed, so a restore can tell a picture
    you culled (RAW present, no sidecar) from a mismatch it should refuse.
  * it records the OBSERVED filename prefix, read off the files rather than out
    of a setting — see below.

**The prefix.** Files are named `2026 Goeree - N8140001.ORF`; `target_name`
builds that as f"{prefix} - {src_name}". Today that prefix lives only in
sv_gui_cache.json, a per-machine GUI cache that never leaves the laptop, so on a
fresh machine it is gone. Re-read the cards with a slightly different prefix and
every sidecar orphans — which looks exactly like "I deleted everything". The
manifest's copy is taken from the filenames actually present, so it stays true
even for a day read with a different prefix (a second body, or a correction
mid-trip); trip.ini's copy is convenience, this one is truth.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("speciesviewer")

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1

# The metadata work: ratings, colour labels, species keywords, GPS, crops.
WORK_EXTS = {".xmp", ".dop"}
# Derived renders. They dominate the ZIP's size and are still cheap enough to
# always include — a trip's worth fits several times over on a 16 GB card.
JPG_EXTS = {".jpg", ".jpeg"}
# Loose per-day files worth carrying. blog.ini is written into the day folder
# (sv4WriteBlog reads it from the project path, which IS the day).
EXTRA_NAMES = {"blog.ini"}
# Only to record WHICH pictures existed — RAWs are never put in the ZIP.
RAW_EXTS = {".cr3", ".cr2", ".nef", ".arw", ".raf", ".dng", ".orf", ".rw2"}

PREFIX_SEP = " - "      # target_name's separator; see the module note


def observed_prefix(names) -> str:
    """The filename prefix these files were read with, or "" if they carry none.

    Taken from the files themselves, never from a setting — that is the whole
    point. Names WITHOUT the separator are ignored rather than fatal: a day
    legitimately holds loose files that never carried a prefix (blog.ini), and
    letting one of those zero the answer would be silly. Returns "" when no name
    carries a prefix, and when the ones that do disagree — a day whose files
    disagree has no single prefix, and a guess would be worse than an admission.
    """
    prefixes = set()
    for n in names:
        head, sep, _rest = n.partition(PREFIX_SEP)
        if sep:
            prefixes.add(head)
    return prefixes.pop() if len(prefixes) == 1 else ""


def scan_day(day_dir) -> dict:
    """What the day holds right now. Read-only; no side effects."""
    day_dir = Path(day_dir)
    work, jpgs, extras, raws = [], [], [], []
    try:
        entries = sorted(day_dir.iterdir())
    except OSError:
        entries = []
    for p in entries:
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in WORK_EXTS:
            work.append(p)
        elif ext in JPG_EXTS:
            jpgs.append(p)
        elif ext in RAW_EXTS:
            raws.append(p.name)
        elif p.name.lower() in EXTRA_NAMES:
            extras.append(p)
    payload = work + jpgs + extras
    return {
        "day": day_dir.name,
        "trip": day_dir.parent.name,
        "payload": payload,
        "raws": raws,
        "prefix": observed_prefix([p.name for p in payload] + raws),
    }


def _file_stat(p: Path) -> dict:
    st = p.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def build_manifest(scan: dict) -> dict:
    files = {}
    for p in scan["payload"]:
        try:
            files[p.name] = _file_stat(p)
        except OSError:
            pass
    return {
        "version": MANIFEST_VERSION,
        "day": scan["day"],
        "trip": scan["trip"],
        "prefix": scan["prefix"],
        "created": datetime.now().isoformat(timespec="seconds"),
        "raws": scan["raws"],
        "files": files,
    }


def zip_name(scan: dict) -> str:
    """'2026 Goeree - 2026-08-14.zip'.

    The name stays trip-qualified even though the file now lives in a per-trip
    folder: a ZIP that gets copied out of its folder should still say what it is.
    """
    trip = scan["trip"].strip()
    return f"{trip}{PREFIX_SEP}{scan['day']}.zip" if trip else f"{scan['day']}.zip"


def trip_dir(dest_dir, scan: dict) -> Path:
    """<dest>/<trip>/ — one folder per trip on the card.

    A card collects several trips over time, and a flat root of forty ZIPs is
    not something you can read. With a folder per trip the card lists TRIPS, and
    a restore points at one folder instead of filtering names.
    """
    trip = scan["trip"].strip()
    return Path(dest_dir) / trip if trip else Path(dest_dir)


def migrate_flat_zip(dest_dir, scan: dict) -> Path | None:
    """Move a pre-existing root-level ZIP into its trip folder, once.

    Backups written before the per-trip layout sit in the card root. Re-zipping
    them would cost minutes and hundreds of megabytes for no new information, so
    they are MOVED — a rename on the same card, instant. Returns the new path
    when something moved, else None.

    Only ever moves a file this module itself would have written (the exact
    zip_name), never anything else on the card, and never overwrites a ZIP that
    is already in the trip folder.
    """
    dest_dir = Path(dest_dir)
    name = zip_name(scan)
    old = dest_dir / name
    new = trip_dir(dest_dir, scan) / name
    if old == new or not old.is_file() or new.exists():
        return None
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        os.replace(old, new)
        logger.info(f"Backup: moved {name} into {new.parent.name}/ "
                    f"(one-time tidy-up, nothing re-written).")
        return new
    except OSError:
        return None


def read_manifest(zip_path) -> dict | None:
    """The manifest out of an existing ZIP, or None if it has none/is unreadable.
    Cheap — one small entry read through the central directory."""
    zip_path = Path(zip_path)
    if not zip_path.exists():
        return None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None


def _friendly(iso: str) -> str:
    """'19 Aug 19:58' from a manifest timestamp — this ends up in a table cell
    and a tooltip, where an ISO string is just noise."""
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %H:%M")
    except (TypeError, ValueError):
        return iso or "at an unknown time"


def day_status(day_dir, dest_dir) -> dict:
    """Is this day's backup current? Read-only, and the basis for both the
    "skip unchanged" sweep and the overview column's glyph.

    state is 'never' | 'stale' | 'current' | 'empty'.
    """
    scan = scan_day(day_dir)
    if not scan["payload"]:
        return {"state": "empty", "reason": "nothing to back up", "scan": scan,
                "zip": None, "changed": 0}
    migrate_flat_zip(dest_dir, scan)      # one-time lift out of the card root
    zp = trip_dir(dest_dir, scan) / zip_name(scan)
    old = read_manifest(zp)
    if old is None:
        return {"state": "never", "reason": "no backup yet", "scan": scan,
                "zip": zp, "changed": len(scan["payload"])}
    now = build_manifest(scan)
    changed = 0
    for name, meta in now["files"].items():
        prev = old.get("files", {}).get(name)
        if prev is None or prev.get("size") != meta["size"] \
                or prev.get("mtime_ns") != meta["mtime_ns"]:
            changed += 1
    # Deletions are the point of culling, so a file that has GONE counts as a
    # change just as loudly as one that appeared.
    removed = set(old.get("files", {})) - set(now["files"])
    raws_changed = list(old.get("raws", [])) != list(now["raws"])
    if changed or removed or raws_changed:
        bits = []
        if changed:
            bits.append(f"{changed} changed")
        if removed:
            bits.append(f"{len(removed)} removed")
        if raws_changed:
            bits.append("picture list differs")
        return {"state": "stale", "reason": ", ".join(bits), "scan": scan,
                "zip": zp, "changed": changed + len(removed)}
    return {"state": "current", "reason": _friendly(old.get("created", "")),
            "scan": scan, "zip": zp, "changed": 0}


def backup_day(day_dir, dest_dir, logger_=None) -> dict:
    """Write one day's work to <dest_dir>/<trip> - <day>.zip.

    Text is deflated (XMP is XML, ~5:1); JPGs are stored, because they do not
    shrink and compressing them only costs time. The ZIP is built as .tmp and
    renamed, so an interrupted write leaves the previous good ZIP intact.
    """
    log = logger_ or logger
    day_dir = Path(day_dir)
    dest_dir = Path(dest_dir)
    scan = scan_day(day_dir)
    if not scan["payload"]:
        return {"day": scan["day"], "written": False, "files": 0, "bytes": 0,
                "reason": "nothing to back up", "path": None}

    migrate_flat_zip(dest_dir, scan)
    out_dir = trip_dir(dest_dir, scan)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / zip_name(scan)
    tmp = final.with_suffix(".tmp")

    added, failed, total = [], [], 0
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in scan["payload"]:
                stored = p.suffix.lower() in JPG_EXTS
                try:
                    zf.write(p, arcname=p.name,
                             compress_type=(zipfile.ZIP_STORED if stored
                                            else zipfile.ZIP_DEFLATED))
                    added.append(p)
                    total += p.stat().st_size
                except OSError as e:
                    log.warning(f"Backup: could not add {p.name}: {e}")
                    failed.append(p.name)
            if not added:
                # Nothing got in. Replacing a good ZIP with an empty one would
                # trade a real backup for a manifest that lies about it.
                raise OSError(f"no file could be added to {final.name}"
                              + (f" ({failed[0]} …)" if failed else ""))
            # Manifest LAST, describing only what actually went in. This is the
            # load-bearing rule of the module: the manifest is the truth about
            # the ZIP's contents, so a file that failed is simply absent from it
            # — which makes the day read as stale next run and retry itself. A
            # manifest written up front would claim files the ZIP does not hold.
            partial = dict(scan, payload=added)
            zf.writestr(MANIFEST_NAME,
                        json.dumps(build_manifest(partial), ensure_ascii=False,
                                   indent=1))
        os.replace(tmp, final)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)     # never leave a half ZIP behind
        except OSError:
            pass
        raise

    if failed:
        log.warning(f"⚠️ Backed up {scan['day']} WITHOUT {len(failed)} file(s) "
                    f"that could not be read: {', '.join(failed[:5])}"
                    f"{' …' if len(failed) > 5 else ''}. The day stays marked "
                    f"'not backed up' so the next run retries them.")
    log.info(f"Backed up {scan['day']}: {len(added)} file(s), "
             f"{total/1024/1024:.1f} MB → {final.name}")
    return {"day": scan["day"], "written": True, "files": len(added),
            "bytes": total, "zip_bytes": final.stat().st_size,
            "prefix": scan["prefix"], "path": str(final),
            "failed": len(failed), "failed_names": failed, "reason": ""}


def backup_days(day_dirs, dest_dir, skip_unchanged=True,
                progress=None, should_cancel=None, logger_=None) -> dict:
    """Back up several days. Returns a summary; never raises for one bad day.

    progress(done, total, day_name) is called before each day.
    """
    log = logger_ or logger
    day_dirs = [Path(d) for d in day_dirs]
    out = {"days": len(day_dirs), "written": 0, "skipped": 0, "empty": 0,
           "failed": 0, "files": 0, "bytes": 0, "results": [], "errors": []}
    for i, d in enumerate(day_dirs, 1):
        if should_cancel and should_cancel():
            log.warning("⏹ Backup cancelled.")
            break
        if progress:
            progress(i, len(day_dirs), d.name)
        status = day_status(d, dest_dir)
        if status["state"] == "empty":
            out["empty"] += 1
            continue
        if skip_unchanged and status["state"] == "current":
            out["skipped"] += 1
            out["results"].append({"day": d.name, "written": False,
                                   "reason": "unchanged"})
            continue
        try:
            res = backup_day(d, dest_dir, logger_=log)
        except OSError as e:
            log.warning(f"❌ Backup failed for {d.name}: {e}")
            out["failed"] += 1
            out["errors"].append(f"{d.name}: {e}")
            continue
        out["written"] += 1
        out["files"] += res["files"]
        out["bytes"] += res["bytes"]
        if res.get("failed"):
            out["errors"].append(
                f"{d.name}: {res['failed']} file(s) unreadable, left out of the backup")
        out["results"].append(res)
    return out


# ── Settings snapshot ─────────────────────────────────────────────────────────

SETTINGS_SUBDIR = "settings"
# sv_gui_cache.json ONLY. svconfig.ini is deliberately excluded: it carries
# Sftppwd under [websettings], and a website password does not belong on a card
# that travels in a camera bag. The GUI cache holds no credentials — it is the
# card prefix, the last source/destination and a few toggles.
SETTINGS_FILES = ("sv_gui_cache.json",)


def copy_settings_snapshot(dest_dir, source_dir=None, logger_=None) -> list[str]:
    """Drop a copy of the GUI cache into the backup folder.

    Not needed by the restore designed here — the manifest already carries the
    prefix, which is the one irreplaceable thing in it. This is for the restore
    nobody expects to do: having the last source path, destination and toggles
    to hand turns a reconstruction into a comparison.

    The filename carries the MACHINE, not the trip: the GUI cache is per-machine
    state, so backing up two trips from one laptop should not make two copies of
    the same thing, while backing up from the laptop and from BigBoy should not
    let one silently overwrite the other.
    """
    log = logger_ or logger
    dest = Path(dest_dir) / SETTINGS_SUBDIR
    src_root = Path(source_dir) if source_dir else Path.cwd()
    try:
        from utils.config import MACHINE_NAME
        machine = "".join(c for c in MACHINE_NAME if c.isalnum() or c in "-_") or "machine"
    except Exception:
        machine = "machine"
    copied = []
    for name in SETTINGS_FILES:
        src = src_root / name
        if not src.is_file():
            continue
        stem, dot, ext = name.partition(".")
        target = f"{stem}-{machine}{dot}{ext}"
        try:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / target)
            copied.append(target)
        except OSError as e:
            log.warning(f"Backup: could not copy {name}: {e}")
    return copied
