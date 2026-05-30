#!/usr/bin/env python3

# Read pictures from an SD-card (or any folder) into the project pictures
# directory — the front-stage "ingest" step that precedes the per-day sv1-sv7
# pipeline.
#
# M0 scope (deliberately minimal):
#   - copy flat into the destination: NO rename, NO date-bucketing (those come later)
#   - verified copy (sha256 of source vs destination); a failed verify removes the
#     partial copy and is reported as a failure
#   - skip files already copied (same name + identical content) so re-runs are safe
#   - never modify the card (no delete / format)
#
# Only picture / raw / video files are copied; everything else on the card is
# ignored. Paths use pathlib so the same code works on Windows and macOS.

import hashlib
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

UNKNOWN_DATE_DIR = "unknown-date"

# Lower-case extensions (incl. dot) we treat as "pictures" worth ingesting.
PICTURE_EXTENSIONS = {
    ".cr3", ".cr2", ".nef", ".arw", ".raf", ".dng", ".orf", ".rw2",  # raw
    ".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff",       # stills
    ".mp4", ".mov",                                                    # video
}


def _sha256(path, chunk_size=1 << 20):
    """Streaming sha256 of a file, so we never load a multi-GB raw into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _already_copied(src, target):
    """True if target is byte-identical to src (same size, same hash)."""
    try:
        if src.stat().st_size != target.stat().st_size:
            return False
    except OSError:
        return False
    return _sha256(src) == _sha256(target)


def target_name(src_name, prefix=""):
    """Destination filename for a source picture. With a prefix, the original
    name is preserved and prefixed: 'IMG_1234.CR3' -> '2026 Zeeland - IMG_1234.CR3'.
    The prefix is what makes names unique across cards/runs, so re-running is
    still idempotent (same input -> same target name)."""
    prefix = (prefix or "").strip()
    return f"{prefix} - {src_name}" if prefix else src_name


def _norm(path):
    """Normalised key for matching exiftool's SourceFile against our paths
    (handles / vs \\ and case-insensitive Windows filesystems)."""
    return os.path.normcase(os.path.normpath(str(path)))


def _read_capture_datetimes(files, log):
    """{normalised path: datetime} from EXIF DateTimeOriginal, batched through
    exiftool. Files without a readable date are simply omitted (caller falls
    back to file mtime). exiftool is imported lazily so copy_card stays usable
    (and unit-testable) without it for the no-bucketing path."""
    result = {}
    try:
        import exiftool
        with exiftool.ExifToolHelper(
            common_args=["-G", "-charset", "utf8", "-charset", "filename=utf8"],
            encoding="utf-8", check_execute=False,
        ) as et:
            for meta in et.get_metadata([str(p) for p in files], params=["-DateTimeOriginal"]):
                src = meta.get("SourceFile")
                dt_str = meta.get("EXIF:DateTimeOriginal", "")
                if src and dt_str:
                    try:
                        result[_norm(src)] = datetime.strptime(dt_str[:19], "%Y:%m:%d %H:%M:%S")
                    except ValueError:
                        pass
    except Exception as e:
        log.warning(f"Could not read capture dates via exiftool ({e}); using file mtime instead.")
    return result


def _bucket_dates(files, tz_offset_hours, log):
    """{normalised path: 'YYYY-MM-DD'} for each file, applying tz_offset_hours
    BEFORE taking the date so a near-midnight shot lands in the right day.
    Capture time comes from EXIF; missing EXIF falls back to file mtime."""
    dt_map = _read_capture_datetimes(files, log)
    offset = timedelta(hours=tz_offset_hours or 0)
    buckets = {}
    for f in files:
        key = _norm(f)
        dt = dt_map.get(key)
        if dt is None:
            try:
                dt = datetime.fromtimestamp(f.stat().st_mtime)
            except OSError:
                buckets[key] = UNKNOWN_DATE_DIR
                continue
        buckets[key] = (dt + offset).strftime("%Y-%m-%d")
    return buckets


def copy_card(source, dest, prefix="", bucket_by_date=False, tz_offset_hours=0.0,
              logger=None, progress=None, should_cancel=None):
    """Copy picture files from `source` (recursively) into `dest`.

    prefix            - optional filename prefix (see target_name).
    bucket_by_date    - when True, files go into dest/<YYYY-MM-DD>/ subfolders.
    tz_offset_hours   - hours added to capture time BEFORE the date is taken,
                        so near-midnight shots land in the intended day.
    progress(done, total, name) - optional callback, called per file before copy.
    should_cancel()             - optional callable; return True to stop early.

    Returns a summary dict: total / copied / skipped / conflict / failed / bytes.
    """
    log = logger or logging.getLogger("speciesviewer")
    source = Path(source)
    dest = Path(dest)

    files = sorted(
        p for p in source.rglob("*")
        if p.is_file() and p.suffix.lower() in PICTURE_EXTENSIONS
    )
    total = len(files)
    summary = {"total": total, "copied": 0, "skipped": 0,
               "conflict": 0, "failed": 0, "bytes": 0}
    targets = []  # destination paths now present (copied or already-there) — for previewing

    log.info(f"Found {total} picture(s) under {source}")

    # Resolve date buckets up front (one batched exiftool call) so the copy loop
    # just looks up where each file goes.
    buckets = _bucket_dates(files, tz_offset_hours, log) if bucket_by_date else {}

    for i, src in enumerate(files, 1):
        if should_cancel and should_cancel():
            log.warning("⏹ Card read cancelled by user.")
            break
        if progress:
            progress(i, total, src.name)

        target_dir = dest / buckets.get(_norm(src), UNKNOWN_DATE_DIR) if bucket_by_date else dest
        target = target_dir / target_name(src.name, prefix)
        try:
            if target.exists():
                if _already_copied(src, target):
                    summary["skipped"] += 1
                    targets.append(str(target))
                    continue
                # Same name, different content — flat copy has no place to put
                # this without overwriting. Leave the existing file alone and
                # flag it; renaming (M1) is what ultimately resolves this.
                log.warning(f"⚠️ Name collision, not overwriting: {target.name} "
                            f"(already exists with different content)")
                summary["conflict"] += 1
                continue

            src_hash = _sha256(src)
            target.parent.mkdir(parents=True, exist_ok=True)  # create date subdir on demand
            shutil.copy2(src, target)  # copy2 keeps mtime — fallback date source when EXIF is absent
            if _sha256(target) != src_hash:
                target.unlink(missing_ok=True)
                log.warning(f"❌ Verification failed, removed partial copy: {src.name}")
                summary["failed"] += 1
                continue

            summary["copied"] += 1
            summary["bytes"] += target.stat().st_size
            targets.append(str(target))
        except OSError as e:
            log.warning(f"❌ Failed to copy {src.name}: {e}")
            summary["failed"] += 1

    log.info(
        f"Card read summary — copied {summary['copied']}, skipped {summary['skipped']}, "
        f"conflicts {summary['conflict']}, failed {summary['failed']} of {total}."
    )
    summary["targets"] = targets
    return summary


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Copy pictures from an SD-card folder into a project directory.")
    parser.add_argument("source", help="Source folder (e.g. a mounted SD-card)")
    parser.add_argument("dest", help="Destination project pictures folder")
    parser.add_argument("--prefix", default="", help="Optional filename prefix, e.g. '2026 Zeeland'")
    parser.add_argument("--by-date", action="store_true", help="Sort into dest/<YYYY-MM-DD>/ subfolders")
    parser.add_argument("--tz-offset", type=float, default=0.0, help="Hours added to capture time before bucketing")
    args = parser.parse_args()
    copy_card(args.source, args.dest, prefix=args.prefix,
              bucket_by_date=args.by_date, tz_offset_hours=args.tz_offset)
