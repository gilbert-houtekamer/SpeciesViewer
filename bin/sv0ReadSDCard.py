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
RAW_EXTENSIONS = {".cr3", ".cr2", ".nef", ".arw", ".raf", ".dng", ".orf", ".rw2"}
PICTURE_EXTENSIONS = RAW_EXTENSIONS | {
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


def _write_sidecars(raw_paths, log):
    """Create one XMP sidecar per RAW (stem-named, beside the RAW) carrying the
    RAW's full metadata translated into XMP namespaces — capture date, GPS,
    make/model, lens, ISO, shutter, aperture, focal length, dimensions, plus any
    Rating/Subject/Label. This is the durable, RAW-immutable carrier the culling
    grid reads and edits; Read & Summarize later absorbs it into the CSV.

    One batched exiftool process for all files. Skips RAWs that already have a
    sidecar (idempotent). Returns the number of sidecars created."""
    todo = [p for p in raw_paths if not Path(p).with_suffix(".xmp").exists()]
    if not todo:
        return 0
    import subprocess
    # -tagsfromfile @  → copy from each file being processed
    # -xmp:all<all     → translate every readable tag into its XMP equivalent
    # SubjectDistance  → AF focus distance lives in Olympus/OM maker notes (no XMP
    #                    equivalent, so <all drops it); copy it explicitly into the
    #                    standard XMP-exif:SubjectDistance slot. Missing on other
    #                    brands → exiftool silently skips that one copy.
    # -o %d%f.xmp      → write a sidecar next to each RAW (won't overwrite existing)
    cmd = ["exiftool", "-charset", "filename=utf8", "-charset", "utf8",
           "-tagsfromfile", "@", "-xmp:all<all",
           "-XMP-exif:SubjectDistance<Olympus:FocusDistance#",
           "-o", "%d%f.xmp"] + [str(p) for p in todo]
    try:
        subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    except Exception as e:
        log.warning(f"XMP sidecar creation failed: {e}")
    created = sum(1 for p in todo if Path(p).with_suffix(".xmp").exists())
    if created < len(todo):
        log.warning(f"XMP sidecars: created {created} of {len(todo)} (some failed).")
    else:
        log.info(f"Created {created} XMP sidecar(s).")
    return created


class _DestIndex:
    """Content index of pictures already in the destination tree, for dedup that
    is independent of filename/prefix ('is this card file already on the laptop?').

    Files are bucketed by size; hashes are computed lazily — only when a source
    file matches an existing size — so a card of all-new pictures triggers no
    hashing of the existing library, keeping the check fast on big projects."""

    def __init__(self, dest_root):
        self._by_size = {}
        self._hash = {}
        root = Path(dest_root)
        if root.is_dir():
            for p in root.rglob("*"):
                if p.is_file() and p.suffix.lower() in PICTURE_EXTENSIONS:
                    try:
                        self._by_size.setdefault(p.stat().st_size, []).append(p)
                    except OSError:
                        pass

    def _h(self, p):
        if p not in self._hash:
            try:
                self._hash[p] = _sha256(p)
            except OSError:
                self._hash[p] = None
        return self._hash[p]

    def find_match(self, src):
        """Return an existing dest path with identical content to `src`, or None."""
        try:
            size = src.stat().st_size
        except OSError:
            return None
        candidates = self._by_size.get(size)
        if not candidates:
            return None
        src_hash = _sha256(src)
        for p in candidates:
            if self._h(p) == src_hash:
                return p
        return None

    def add(self, path):
        """Register a freshly-copied file so later identical sources are skipped."""
        try:
            self._by_size.setdefault(path.stat().st_size, []).append(path)
            self._hash[path] = _sha256(path)
        except OSError:
            pass


def _dup_suffix(n):
    """Disambiguation suffix for the n-th name collision: ' B', ' C', … then ' 28'
    once past the alphabet. (The original, unsuffixed file is the implicit 'A'.)"""
    return f" {chr(ord('A') + n)}" if n < 26 else f" {n + 1}"


def _next_free_name(target):
    """A non-existing path beside `target`, inserting a ' B'/' C'/… suffix before
    the extension. Used when a different file already owns the wanted name (e.g. a
    dual-slot camera that restarts frame numbers per card)."""
    parent, stem, ext = target.parent, target.stem, target.suffix
    n = 1
    while True:
        cand = parent / f"{stem}{_dup_suffix(n)}{ext}"
        if not cand.exists():
            return cand
        n += 1


def copy_card(source, dest, prefix="", bucket_by_date=False, tz_offset_hours=0.0,
              skip_if_on_disk=True, write_sidecars=True,
              logger=None, progress=None, should_cancel=None):
    """Copy picture files from `source` (recursively) into `dest`.

    prefix            - optional filename prefix (see target_name).
    bucket_by_date    - when True, files go into dest/<YYYY-MM-DD>/ subfolders.
    tz_offset_hours   - hours added to capture time BEFORE the date is taken,
                        so near-midnight shots land in the intended day.
    skip_if_on_disk   - when True (default), skip any card file whose content is
                        already anywhere in `dest` (content match, ignores name),
                        so incremental re-reads only copy new pictures.
    write_sidecars    - when True (default), create an XMP sidecar per RAW (see
                        _write_sidecars) carrying its metadata for the culling grid.
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
               "renamed": 0, "conflict": 0, "failed": 0, "bytes": 0}
    targets = []  # destination paths now present (copied or already-there) — for previewing

    log.info(f"Found {total} picture(s) under {source}")

    # Resolve date buckets up front (one batched exiftool call) so the copy loop
    # just looks up where each file goes.
    buckets = _bucket_dates(files, tz_offset_hours, log) if bucket_by_date else {}

    # Content index of what's already on the laptop — drives dedup independent of
    # the prefix, so re-reading a card only copies pictures not yet imported.
    dest_index = _DestIndex(dest) if skip_if_on_disk else None

    for i, src in enumerate(files, 1):
        if should_cancel and should_cancel():
            log.warning("⏹ Card read cancelled by user.")
            break
        if progress:
            progress(i, total, src.name)

        try:
            # Already on the laptop (by content, any name)? Skip — this is the
            # incremental-ingest case. Deleting a copy and re-reading brings it
            # back, which is also how a wrong-prefix run is corrected.
            if dest_index is not None:
                match = dest_index.find_match(src)
                if match is not None:
                    summary["skipped"] += 1
                    targets.append(str(match))
                    continue

            target_dir = dest / buckets.get(_norm(src), UNKNOWN_DATE_DIR) if bucket_by_date else dest
            target = target_dir / target_name(src.name, prefix)
            if target.exists():
                if _already_copied(src, target):
                    # Identical file already there (only reachable with dedup off;
                    # with dedup on, the content index already skipped it above).
                    summary["skipped"] += 1
                    targets.append(str(target))
                    continue
                # Same name, different content — typically a dual-slot camera that
                # restarts frame numbers per card. Auto-rename (… B/C/…) and copy;
                # a re-read recognises it by content and skips, so it's idempotent.
                renamed = _next_free_name(target)
                log.info(f"Name in use by a different shot — saving as {renamed.name}")
                target = renamed
                summary["renamed"] += 1

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
            if dest_index is not None:
                dest_index.add(target)
        except OSError as e:
            log.warning(f"❌ Failed to copy {src.name}: {e}")
            summary["failed"] += 1

    # XMP sidecars for every RAW now in dest (copied or already-present) that
    # lacks one — the durable culling carrier. Done after the copy loop in one
    # batched exiftool process.
    summary["sidecars"] = 0
    if write_sidecars and not (should_cancel and should_cancel()):
        raw_targets = [t for t in targets if Path(t).suffix.lower() in RAW_EXTENSIONS]
        if raw_targets:
            if progress:
                progress(total, total, "Writing XMP sidecars…")
            summary["sidecars"] = _write_sidecars(raw_targets, log)

    log.info(
        f"Card read summary — copied {summary['copied']} (of which {summary['renamed']} renamed), "
        f"skipped {summary['skipped']}, failed {summary['failed']} of {total}; "
        f"{summary['sidecars']} sidecar(s)."
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
    parser.add_argument("--no-dedup", action="store_true", help="Do not skip files already present on disk by content")
    parser.add_argument("--no-sidecars", action="store_true", help="Do not create XMP sidecars for RAWs")
    args = parser.parse_args()
    copy_card(args.source, args.dest, prefix=args.prefix,
              bucket_by_date=args.by_date, tz_offset_hours=args.tz_offset,
              skip_if_on_disk=not args.no_dedup, write_sidecars=not args.no_sidecars)
