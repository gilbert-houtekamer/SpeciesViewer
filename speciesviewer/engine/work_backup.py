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
from utils.raw_extensions import RAW_OR_HEIC

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
EXTRA_NAMES = {"blog.ini", ".sv_sidecar_cache.json"}

# Files that ride along in the ZIP but must NOT make a day look stale. The
# sidecar cache is derived from the XMPs beside it and is rewritten whenever the
# culling viewer re-reads them, so treating it as work would have a day going
# stale for a change nobody made. Backed up because rebuilding it costs an
# exiftool pass over every sidecar on the restoring machine; ignored for
# staleness because it says nothing about whether the WORK moved.
DERIVED_NAMES = {".sv_sidecar_cache.json"}
# Only to record WHICH pictures existed — these are never put in the ZIP.
# HEIC belongs here: a phone-shot day is a real day (638 of them in 2026 Kreta),
# and leaving the format out meant its pictures were missing from the manifest,
# so a restore could not tell a culled one from a kept one.
SOURCE_EXTS = RAW_OR_HEIC  # canonical table — see utils/raw_extensions.py

# Separators a prefix can be joined with. target_name writes " - " today, but
# trips on disk use both "2026 Goeree - N8140001.ORF" and
# "2026 North Greece-IMG_4527.HEIC", so neither can be assumed.
PREFIX_SEPS = " -"


def observed_prefix(names) -> str:
    """The filename prefix these pictures were read with, or "" if none.

    Read off the files, never out of a setting — that is the whole point, and it
    is why no separator can be assumed: real trips on disk use " - "
    (2026 Goeree) and "-" (2026 North Greece) alike, and the prefix is not always
    the folder name either (2026 Zomer's pictures are "2026 juni Waldenlaan - …").

    **Majority, not unanimity.** Real day folders contain hand-named files
    sitting beside the machine-named ones — "short 2026 Kreta-N3130792.ORF.dop",
    "2026 Kreta Lois olijfgaard 1.jpg" — and any rule that needs every name to
    agree either returns nothing or, worse, returns a truncated prefix that
    still looks plausible: the common start of those two Kreta names cut at its
    last separator gives "2026", which is wrong and would pass a naive check.

    So: every separator position in every name proposes a candidate, each
    candidate is scored by how many names actually begin with it followed by a
    separator, and the winner is the best-supported — longest breaking a tie,
    which is what promotes "2026 Kreta" over the "2026" it contains. A candidate
    must be backed by more than half the names, so a day with no convention
    ("IMG_1234.HEIC") or two rival ones yields "" rather than a guess.

    Underscore is deliberately not a separator: "IMG_1234.HEIC" would otherwise
    hand back "IMG".
    """
    names = [n for n in names if n]
    if not names:
        return ""
    candidates = set()
    for n in names:
        for i, ch in enumerate(n):
            if ch in PREFIX_SEPS and i:
                head = n[:i].rstrip(PREFIX_SEPS)
                if head:
                    candidates.add(head)
    if not candidates:
        return ""
    best, best_score = "", (0, 0)
    for cand in candidates:
        support = sum(1 for n in names
                      if len(n) > len(cand) and n.startswith(cand)
                      and n[len(cand)] in PREFIX_SEPS)
        score = (support, len(cand))
        if score > best_score:
            best, best_score = cand, score
    return best if best_score[0] * 2 > len(names) else ""


def scan_day(day_dir) -> dict:
    """What the day holds right now. Read-only; no side effects.

    Uses os.scandir() rather than Path.iterdir(): on Windows a DirEntry's
    is_file()/stat() reuse the attributes the directory enumeration already
    returned (no extra per-file syscall), where the same calls on a bare Path
    handed back by iterdir() each re-stat the file. For a day with thousands
    of RAWs this used to mean thousands of avoidable round-trips — real cost
    on a synced/network drive, which is exactly what a trip's picture folder
    often is. The payload files' stats are kept in `_stat_cache` so
    build_manifest() can reuse them too — see its docstring."""
    day_dir = Path(day_dir)
    work, jpgs, extras, raws = [], [], [], []
    stat_cache: dict[str, dict] = {}
    try:
        with os.scandir(day_dir) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        entries = []
    for entry in entries:
        if not entry.is_file():
            continue
        ext = os.path.splitext(entry.name)[1].lower()
        if ext in WORK_EXTS:
            bucket = work
        elif ext in JPG_EXTS:
            bucket = jpgs
        elif ext in SOURCE_EXTS:
            raws.append(entry.name)   # name only — never stat'd, never in the ZIP
            continue
        elif entry.name.lower() in EXTRA_NAMES:
            bucket = extras
        else:
            continue
        p = Path(entry.path)
        bucket.append(p)
        try:
            st = entry.stat()
            stat_cache[entry.name] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
        except OSError:
            pass
    payload = work + jpgs + extras
    return {
        "day": day_dir.name,
        "trip": day_dir.parent.name,
        "payload": payload,
        "raws": raws,
        # Derived from the PICTURES only — a loose blog.ini carries no prefix
        # and would drag the common start down to nothing.
        "prefix": observed_prefix([p.name for p in work + jpgs] + raws),
        "_stat_cache": stat_cache,
    }


def _file_stat(p: Path) -> dict:
    st = p.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def build_manifest(scan: dict, previous: dict | None = None,
                   stat_cache: dict | None = None) -> dict:
    """`previous` is the manifest being replaced, used only to carry forward
    raws_initial — how many pictures this day EVER held. Today's picture list
    shrinks as culling proceeds, so without a high-water mark there is nothing
    left to say how many were thrown away, and a restore cannot tell you how
    many of the card's pictures it is about to leave behind.

    `stat_cache` (scan_day()'s `_stat_cache`) lets a read-only caller — the
    overview's backup-status check, which runs on every startup for every
    day — skip a second stat() per payload file. The actual ZIP write
    (backup_day()) never passes it: the manifest it writes must describe
    what was ACTUALLY zipped, stat'd fresh at that moment, not what scan_day()
    saw earlier."""
    files = {}
    for p in scan["payload"]:
        cached = stat_cache.get(p.name) if stat_cache else None
        if cached is not None:
            files[p.name] = cached
            continue
        try:
            files[p.name] = _file_stat(p)
        except OSError:
            pass
    prev = previous or {}
    initial = max(int(prev.get("raws_initial") or 0),
                  len(prev.get("raws") or []),
                  len(scan["raws"]))
    return {
        "version": MANIFEST_VERSION,
        "day": scan["day"],
        "trip": scan["trip"],
        "prefix": scan["prefix"],
        "created": datetime.now().isoformat(timespec="seconds"),
        "raws": scan["raws"],
        # High-water mark, never decreasing: raws_initial - len(raws) is how
        # many pictures the culling removed, cumulatively.
        "raws_initial": initial,
        "files": files,
    }


def zip_name(scan: dict) -> str:
    """'2026 Goeree - 2026-08-14.zip'.

    The name stays trip-qualified even though the file now lives in a per-trip
    folder: a ZIP that gets copied out of its folder should still say what it is.
    """
    trip = scan["trip"].strip()
    return f"{trip} - {scan['day']}.zip" if trip else f"{scan['day']}.zip"


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
    now = build_manifest(scan, stat_cache=scan.get("_stat_cache"))
    changed = 0
    for name, meta in now["files"].items():
        if name.lower() in DERIVED_NAMES:
            continue
        prev = old.get("files", {}).get(name)
        if prev is None or prev.get("size") != meta["size"] \
                or prev.get("mtime_ns") != meta["mtime_ns"]:
            changed += 1
    # Deletions are the point of culling, so a file that has GONE counts as a
    # change just as loudly as one that appeared.
    removed = ({n for n in old.get("files", {}) if n.lower() not in DERIVED_NAMES}
               - set(now["files"]))
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
    prev_manifest = read_manifest(out_dir / zip_name(scan))   # for raws_initial
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
                        json.dumps(build_manifest(partial, previous=prev_manifest),
                                   ensure_ascii=False, indent=1))
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


# ── Trip identity ─────────────────────────────────────────────────────────────

TRACKLOG_SUBDIR = "tracklogs"
# Tolerance when comparing mtimes across filesystems — see same_file().
FS_TIME_SLOP = 2
TRACKLOG_EXTS = {".gpx", ".log", ".nmea", ".txt", ".fit"}


def trip_matches_days(trip_root, day_dirs) -> tuple[bool, str]:
    """Is `trip_root` really the trip these day folders belong to?

    A guard, not a convenience. The identity ZIP carries the lifer baseline, and
    writing one trip's baseline into another trip's backup folder would be found
    only at restore, when it is too late to notice. In the GUI the two always
    agree — the overview shows the active trip's days — so a mismatch means
    something upstream is already wrong, which is exactly when a silent write is
    worst. Compare the trip's OWN picture root (trip.ini's, via the resolver)
    against where the days actually live.
    """
    if not trip_root or not day_dirs:
        return False, "no trip selected"
    try:
        from utils.trip_identity import resolve_trip_picture_root
        want = resolve_trip_picture_root(str(trip_root), kind="trip") or ""
    except Exception as e:
        return False, f"could not resolve the trip's picture root ({e})"
    if not want:
        return False, "the trip has no picture root"
    got = str(Path(day_dirs[0]).parent)
    if os.path.normcase(os.path.normpath(want)) != os.path.normcase(os.path.normpath(got)):
        return False, (f"these days are in {Path(got).name}, but the open trip's "
                       f"pictures are in {Path(want).name}")
    return True, ""


def ensure_trip_tz_offset(trip_root, logger_=None) -> str:
    """Backfill trip.ini's tz_offset from this machine's last card read.

    Like the prefix it is only recorded when a card is READ, so trips that
    already exist carry none — and unlike the prefix it cannot be re-derived
    from the files, because a picture on disk does not say what offset put it in
    that folder. This machine's last value is the only surviving evidence, so it
    is copied across once and thereafter the trip owns it.

    Only fills a BLANK value; an offset already recorded is never changed.
    """
    log = logger_ or logger
    if not trip_root:
        return ""
    try:
        from utils.trip_config import read_trip_ini, set_trip_flag
        current = ((read_trip_ini(str(trip_root)) or {})
                   .get("tz_offset", "") or "").strip()
        if current:
            return current
        from utils.gui_config import get_cache_gui_section
        tz = (get_cache_gui_section("sdcard_ingest", "tz_offset") or "").strip()
    except Exception:
        return ""
    if not tz:
        return ""
    try:
        if set_trip_flag(str(trip_root), "tz_offset", tz):
            log.info(f"Recorded this trip's timezone offset ({tz}h) from this "
                     f"machine's last card read — it now travels with the backup.")
            return tz
    except Exception as e:
        log.warning(f"Backup: could not record the timezone offset: {e}")
    return ""


def ensure_trip_prefix(trip_root, day_dirs, logger_=None) -> str:
    """Backfill trip.ini's picture_prefix from the filenames already on disk.

    The prefix is recorded when a card is READ, so a trip whose cards were all
    read before that existed — which is every trip that exists today — would
    never get one, and the trips most likely to need restoring are exactly the
    finished ones. The day folders already hold the answer: files are named
    "<prefix> - <camera name>", so it can simply be observed.

    Only fills a BLANK value; it never corrects a prefix already recorded,
    because that is a decision worth making by hand.

    Days are allowed to DISAGREE, and the most common one wins. A trip can hold
    several prefixes on purpose — sub-trips get their own so they are findable
    in Explorer, and a body that wraps its counter at 9999 (the D500 over a
    whole trip, the Olympus within a day) needs a second one to keep names
    unique. What trip.ini holds is therefore the DEFAULT the Read dialog offers,
    not a rule the trip obeys; the day-by-day truth lives in each backup's
    manifest, which records what its own files actually show.
    """
    log = logger_ or logger
    if not trip_root:
        return ""
    try:
        from utils.trip_config import read_trip_ini, set_trip_flag
        current = ((read_trip_ini(str(trip_root)) or {})
                   .get("picture_prefix", "") or "").strip()
    except Exception:
        return ""
    if current:
        return current
    from collections import Counter
    seen = Counter(p for p in (scan_day(d)["prefix"] for d in day_dirs) if p)
    if not seen:
        return ""
    prefix, n = seen.most_common(1)[0]
    if len(seen) > 1:
        others = ", ".join(f"{k} ({v})" for k, v in seen.most_common()[1:])
        log.info(f"Backup: this trip uses more than one filename prefix — "
                 f"recording the commonest, '{prefix}' ({n} day(s)); also seen: "
                 f"{others}. Each day's backup records its own.")
    try:
        if set_trip_flag(str(trip_root), "picture_prefix", prefix):
            log.info(f"Recorded this trip's filename prefix '{prefix}' from the "
                     f"pictures on disk — it now travels with the trip backup.")
            return prefix
    except Exception as e:
        log.warning(f"Backup: could not record the filename prefix: {e}")
    return ""


def backup_trip_identity(trip_root, dest_trip_dir, master_root="",
                         master_web_root="", logger_=None) -> dict:
    """The trip's own ZIP beside the day ZIPs — trip.ini, the species
    collection, the LIFER BASELINE and the translation cache. Kilobytes.

    Reuses `svr6ExportTrip`'s engine (engine.trip_transfer.export_trip) rather
    than assembling a second opinion about what a trip's identity is; that
    module already owns the question, down to importing the result on the other
    side.

    Day ZIPs alone are NOT a restore. They carry every culling decision and no
    lifer verdicts, and the baseline is the one file that must never be
    regenerated from a partial master — so a backup without this is a backup
    that quietly loses the answer to "was this a new species for me?".

    Cheap enough to redo on every run, so it is never out of step with the days
    beside it. Best-effort: a trip without a trip.ini is reported, not raised.
    """
    log = logger_ or logger
    try:
        from engine import trip_transfer
        info = trip_transfer.export_trip(
            str(trip_root), str(dest_trip_dir),
            master_root=master_root, master_web_root=master_web_root)
    except Exception as e:
        log.warning(f"Backup: could not write the trip identity ZIP: {e}")
        return {"written": False, "reason": str(e), "path": None}
    log.info(f"Backed up trip identity → {Path(info['zip_path']).name} "
             f"({info['size_kb']:.0f} KB)")
    return {"written": True, "path": info["zip_path"],
            "kb": info["size_kb"], "included": info.get("included", []),
            "missing": info.get("missing", []), "reason": ""}


def same_file(a, b) -> bool:
    """Same size, and same mtime to within FS_TIME_SLOP seconds.

    NOT exact mtime equality: the card is exFAT, which stores mtime at 2-second
    resolution, while NTFS/APFS keep sub-second. A file copied either way comes
    back rounded, so an exact test called 29 of 30 unchanged tracklogs "changed"
    and re-copied 20 MB that was already there. Two seconds is the FAT rule and
    is far below the interval at which a tracklog is actually rewritten.
    """
    return a.st_size == b.st_size and abs(a.st_mtime - b.st_mtime) <= FS_TIME_SLOP


def backup_tracklogs(gpslogs_root, dest_trip_dir, logger_=None) -> dict:
    """Copy the GPS tracklogs in beside the trip ZIP.

    They live in one folder on the laptop and nowhere else, and every picture
    that has not been geotagged yet depends on them — lose them mid-trip and
    those days can never get their positions. A megabyte or so, so the whole
    folder is copied rather than worked out which log belongs to which day.

    Only files that changed are re-copied, on size + mtime, so a nightly run
    over an unchanged folder does nothing.

    `reason` says why nothing was copied. It used to return a silent zero when
    no GPS log folder was configured, and the dialog only ever reported a
    non-zero count — so on a laptop without a [gpslogs] section the tracklogs
    were left behind and the backup still said it had succeeded.
    """
    log = logger_ or logger
    out = {"copied": 0, "skipped": 0, "bytes": 0, "reason": "", "found": 0}
    if not gpslogs_root:
        out["reason"] = ("no GPS log folder on this machine — logs live in "
                         "<CurrentPictures>/gpslogs, so set [currentpictures] "
                         "rootdirectory (or [gpslogs] rootdirectory to override)")
        return out
    src_root = Path(gpslogs_root)
    if not src_root.is_dir():
        out["reason"] = f"the GPS log folder {src_root} does not exist"
        return out
    dest = Path(dest_trip_dir) / TRACKLOG_SUBDIR
    for src in sorted(src_root.rglob("*")):
        if not src.is_file() or src.suffix.lower() not in TRACKLOG_EXTS:
            continue
        target = dest / src.relative_to(src_root)
        try:
            st = src.stat()
            if target.exists() and same_file(target.stat(), st):
                out["skipped"] += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            out["copied"] += 1
            out["bytes"] += st.st_size
        except OSError as e:
            log.warning(f"Backup: could not copy tracklog {src.name}: {e}")
    out["found"] = out["copied"] + out["skipped"]
    if out["copied"]:
        log.info(f"Backed up {out['copied']} tracklog(s), "
                 f"{out['bytes']/1024:.0f} KB.")
    elif not out["found"]:
        out["reason"] = f"no tracklog files found in {src_root}"
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
    if source_dir:
        src_root = Path(source_dir)
    else:
        from utils.config import STATE_DIR
        src_root = STATE_DIR
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
