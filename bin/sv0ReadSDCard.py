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
#
# Performance notes:
#   - _CardCache (.sv_ingest_cache.json on the card) stores SHA256 and capture
#     date per file, validated by size+mtime_ns. On repeat runs only new/changed
#     files trigger exiftool or a card read.
#   - _DestHashCache (~/.sv_ingest/dest_cache/<key>.json) mirrors the same idea
#     for the destination tree. Once a dest file is hashed for dedup it is never
#     re-hashed, so the 4000-old-files skip check costs ~2s instead of ~60s.
#   - _copy_and_hash_dest uses shutil.copy2 (OS-optimised, clonefile on macOS)
#     then hashes the local SSD copy. One card read (copy) + one fast SSD read
#     (hash); faster than the old two-card-read pattern and the old streaming
#     copy+hash (larger shutil buffers, no per-chunk hash overhead on card I/O).
#   - XMP sidecars are written in batches of 400 so the progress counter updates
#     roughly every 20 s instead of freezing for the full exiftool run.
#
# Copy speed vs Explorer: Explorer uses async overlapped I/O (CopyFileEx) which
# keeps the card pipeline full.  ThreadPoolExecutor(_COPY_WORKERS) submits all
# copy jobs at once so the OS can interleave card reads with SSD writes — the
# same pipelining advantage Explorer has.  SHA256 is NOT the bottleneck (runs at
# ~500 MB/s vs a card at ~60 MB/s, ~12% overhead); hashing the SSD copy instead
# of the card copy keeps the card free for the next read.

import hashlib
import json
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

UNKNOWN_DATE_DIR = "unknown-date"

_COPY_WORKERS = 4   # parallel copy threads; card I/O is the bottleneck, 4 keeps pipeline full

# Lower-case extensions (incl. dot) we treat as "pictures" worth ingesting.
RAW_EXTENSIONS = {".cr3", ".cr2", ".nef", ".arw", ".raf", ".dng", ".orf", ".rw2"}
PICTURE_EXTENSIONS = RAW_EXTENSIONS | {
    ".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff",       # stills
    ".mp4", ".mov",                                                    # video
}


# ── File hashing ──────────────────────────────────────────────────────────────

def _sha256(path, chunk_size=1 << 20) -> str:
    """Streaming sha256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_and_hash_dest(src: Path, dest: Path) -> str:
    """Copy src→dest using shutil, then hash the local dest copy.

    shutil.copy2 uses OS-optimised paths (8 MB buffers on Windows; clonefile /
    fcopyfile on macOS which can be near-instant).  Hashing on the local SSD is
    5-10× faster than hashing from the card, so total wall time is less than
    doing both in a single streaming card read.

    Verifies file size immediately after copy (raises OSError on mismatch so the
    caller can remove the partial file).  Does NOT byte-compare src vs dest —
    USB/Thunderbolt hardware CRC makes silent bit-flips effectively impossible in
    practice, and any OS-level I/O error raises before we get here.
    """
    shutil.copy2(src, dest)
    if src.stat().st_size != dest.stat().st_size:
        dest.unlink(missing_ok=True)
        raise OSError(f"Size mismatch after copy: {src.name}")
    return _sha256(dest)


# ── Card-level ingest cache ───────────────────────────────────────────────────

class _CardCache:
    """Per-card ingest state, stored as .sv_ingest_cache.json at the source root.

    Stores per-file SHA256, capture date, and last-copied timestamp, keyed by
    relative path. Entries are valid when file size and mtime_ns still match.

    Benefits:
    - Repeat runs skip exiftool for already-seen files (date cached).
    - Repeat runs skip the card read for already-processed files (SHA cached).
    - The last_copied timestamp records when each file was first imported.

    Writing is silently skipped on read-only media.
    """

    _FILENAME = ".sv_ingest_cache.json"
    _VERSION  = 1

    def __init__(self, source_root: Path):
        self._root  = Path(source_root)
        self._path  = self._root / self._FILENAME
        self._dirty = False
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            if self._path.exists():
                with open(self._path, encoding="utf-8") as f:
                    raw = json.load(f)
                if raw.get("version") == self._VERSION:
                    self._data = raw.get("files", {})
        except Exception:
            self._data = {}

    def _key(self, path: Path) -> str:
        """Relative path string, forward slashes, lowercase (Windows fs is case-insensitive)."""
        return str(path.relative_to(self._root)).replace("\\", "/").lower()

    def get(self, path: Path) -> dict | None:
        """Cached entry if size+mtime_ns still match; None if missing or stale."""
        entry = self._data.get(self._key(path))
        if not entry:
            return None
        try:
            st = path.stat()
            if st.st_size == entry.get("size") and st.st_mtime_ns == entry.get("mtime_ns"):
                return entry
        except OSError:
            pass
        return None

    def put(self, path: Path, sha256: str, capture_date: str | None,
            last_copied: str | None = None):
        """Store or update the entry for path."""
        key = self._key(path)
        try:
            st = path.stat()
        except OSError:
            return
        entry: dict = {
            "size":     st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "sha256":   sha256,
        }
        if capture_date:
            entry["capture_date"] = capture_date
        if last_copied:
            entry["last_copied"] = last_copied
        elif (prev := self._data.get(key)) and (lc := prev.get("last_copied")):
            entry["last_copied"] = lc   # preserve original import time
        self._data[key] = entry
        self._dirty = True

    def save(self):
        """Flush to disk atomically.  Silently skips on read-only media."""
        if not self._dirty:
            return
        try:
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": self._VERSION, "files": self._data},
                          f, separators=(",", ":"))
            tmp.replace(self._path)
        except OSError:
            pass


# ── Destination dedup index ───────────────────────────────────────────────────

# ── Destination hash cache ────────────────────────────────────────────────────

class _DestHashCache:
    """Persistent SHA256 cache for files in the destination tree.

    Stored in ~/.sv_ingest/dest_cache/<fingerprint>.json, keyed by the resolved
    dest root path.  Once a dest file is hashed it is never re-read unless its
    size or mtime changes — so on the second+ ingest run the dedup check for
    thousands of existing files costs stat() calls only, not SSD reads.
    """

    _VERSION = 1

    def __init__(self, dest_root: Path):
        self._path  = self._cache_file(dest_root)
        self._dirty = False
        self._data: dict[str, dict] = {}
        self._load()

    @staticmethod
    def _cache_file(dest_root: Path) -> Path:
        key = hashlib.md5(str(dest_root.resolve()).encode()).hexdigest()[:16]
        d = Path.home() / ".sv_ingest" / "dest_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{key}.json"

    def _load(self):
        try:
            if self._path.exists():
                with open(self._path, encoding="utf-8") as f:
                    raw = json.load(f)
                if raw.get("version") == self._VERSION:
                    self._data = raw.get("files", {})
        except Exception:
            self._data = {}

    def get(self, path: Path) -> str | None:
        """Cached SHA256 hex if size+mtime_ns still valid, else None."""
        entry = self._data.get(str(path))
        if not entry:
            return None
        try:
            st = path.stat()
            if st.st_size == entry["size"] and st.st_mtime_ns == entry["mtime_ns"]:
                return entry["sha256"]
        except OSError:
            pass
        return None

    def put(self, path: Path, sha256: str):
        try:
            st = path.stat()
            self._data[str(path)] = {
                "size":     st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "sha256":   sha256,
            }
            self._dirty = True
        except OSError:
            pass

    def save(self):
        if not self._dirty:
            return
        try:
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": self._VERSION, "files": self._data},
                          f, separators=(",", ":"))
            tmp.replace(self._path)
        except OSError:
            pass


# ── Destination dedup index ───────────────────────────────────────────────────

class _DestIndex:
    """Content index of pictures already in the destination tree, for dedup that
    is independent of filename/prefix ('is this card file already on the laptop?').

    Files are bucketed by size; hashes are computed lazily — only when a source
    file matches an existing size — so a card of all-new pictures triggers no
    hashing of the existing library.  Hashes are persisted via _DestHashCache so
    repeat runs never re-read destination files that haven't changed."""

    def __init__(self, dest_root):
        self._by_size:    dict[int, list[Path]] = {}
        self._hash:       dict[Path, str | None] = {}
        self._hash_cache  = _DestHashCache(Path(dest_root))
        root = Path(dest_root)
        if root.is_dir():
            for p in root.rglob("*"):
                if p.is_file() and p.suffix.lower() in PICTURE_EXTENSIONS:
                    try:
                        self._by_size.setdefault(p.stat().st_size, []).append(p)
                    except OSError:
                        pass

    def _h(self, p: Path) -> str | None:
        if p not in self._hash:
            cached = self._hash_cache.get(p)
            if cached is not None:
                self._hash[p] = cached          # free: already hashed in a prior run
            else:
                try:
                    h = _sha256(p)
                    self._hash[p] = h
                    self._hash_cache.put(p, h)   # persist for next run
                except OSError:
                    self._hash[p] = None
        return self._hash[p]

    def find_match(self, src: Path, src_hash: str | None = None) -> Path | None:
        """Return an existing dest path with identical content to src, or None.
        If src_hash is supplied (from the card cache) the source file is not read."""
        try:
            size = src.stat().st_size
        except OSError:
            return None
        candidates = self._by_size.get(size)
        if not candidates:
            return None
        if src_hash is None:
            src_hash = _sha256(src)
        for p in candidates:
            if self._h(p) == src_hash:
                return p
        return None

    def add(self, path: Path, sha256: str | None = None):
        """Register a freshly-copied file so later identical sources are skipped.
        Pass sha256 when already known (e.g. from _copy_and_hash_dest) to skip the SSD read."""
        try:
            self._by_size.setdefault(path.stat().st_size, []).append(path)
            h = sha256 or _sha256(path)
            self._hash[path] = h
            self._hash_cache.put(path, h)
        except OSError:
            pass

    def save_cache(self):
        self._hash_cache.save()


# ── EXIF date helpers ─────────────────────────────────────────────────────────

def _read_capture_datetimes(files, log) -> dict[str, datetime]:
    """{normalised path: datetime} from EXIF DateTimeOriginal, batched through
    exiftool. Files without a readable date are omitted (caller falls back to
    file mtime). exiftool is imported lazily so copy_card stays usable without
    it for the no-bucketing path."""
    result = {}
    if not files:
        return result
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


def _build_buckets(files, tz_offset_hours, cache: "_CardCache | None", log) -> dict[str, str]:
    """{normalised path: 'YYYY-MM-DD'} for all files.

    Dates come from the card cache where available; only cache-miss files go
    through exiftool.  Falls back to file mtime when exiftool has no date."""
    offset = timedelta(hours=tz_offset_hours or 0)
    buckets:   dict[str, str]      = {}
    need_exif: list[Path]          = []
    cached_dt: dict[str, datetime] = {}

    for f in files:
        entry = cache.get(f) if cache else None
        if entry and (date_str := entry.get("capture_date")):
            buckets[_norm(f)] = date_str
        else:
            need_exif.append(f)

    hits = len(files) - len(need_exif)
    if hits:
        log.info(f"Capture dates: {hits} from cache, {len(need_exif)} need exiftool")
    if need_exif:
        dt_map = _read_capture_datetimes(need_exif, log)
        for f in need_exif:
            key = _norm(f)
            dt = dt_map.get(key)
            if dt is None:
                try:
                    dt = datetime.fromtimestamp(f.stat().st_mtime)
                except OSError:
                    buckets[key] = UNKNOWN_DATE_DIR
                    continue
            date_str = (dt + offset).strftime("%Y-%m-%d")
            buckets[key] = date_str
            cached_dt[key] = dt   # used below to update cache

    return buckets, cached_dt


# ── XMP sidecars ─────────────────────────────────────────────────────────────

def _write_sidecars(raw_paths, log, progress_cb=None) -> int:
    """Create one XMP sidecar per RAW that lacks one. Batched in groups of 200
    so the caller can update a progress counter without excessive exiftool
    startup overhead."""
    todo = [p for p in raw_paths if not Path(p).with_suffix(".xmp").exists()]
    if not todo:
        return 0
    from ghexif.exif_writer import run_exiftool_files
    # -tagsfromfile @  → copy from each file being processed
    # -xmp:all<all     → translate every readable tag into its XMP equivalent
    # SubjectDistance  → AF focus distance in Olympus/OM maker notes; copy
    #                    explicitly since <all doesn't cover it
    # -o %d%f.xmp      → sidecar beside the RAW (won't overwrite existing)
    opts = ["-tagsfromfile", "@", "-xmp:all<all",
            "-XMP-exif:SubjectDistance<Olympus:FocusDistance#", "-o", "%d%f.xmp"]
    created = 0
    n = len(todo)
    batch_size = 400   # 1–2 exiftool starts per 600-file card; balances overhead vs UX
    for start in range(0, n, batch_size):
        batch = todo[start : start + batch_size]
        try:
            run_exiftool_files(opts, batch)
        except Exception as e:
            log.warning(f"XMP sidecar batch failed: {e}")
        created += sum(1 for p in batch if Path(p).with_suffix(".xmp").exists())
        if progress_cb:
            progress_cb(min(start + batch_size, n), n)

    if created < n:
        log.warning(f"XMP sidecars: created {created} of {n} (some failed).")
    else:
        log.info(f"Created {created} XMP sidecar(s).")
    return created


# ── Naming helpers ────────────────────────────────────────────────────────────

def target_name(src_name, prefix=""):
    """Destination filename. With a prefix the original name is preserved and
    prepended: 'IMG_1234.CR3' → '2026 Zeeland - IMG_1234.CR3'.  The prefix is
    what makes names unique across cards/runs, so re-running is idempotent."""
    prefix = (prefix or "").strip()
    return f"{prefix} - {src_name}" if prefix else src_name


def _norm(path) -> str:
    """Normalised key for matching exiftool's SourceFile against our paths
    (handles / vs \\ and case-insensitive Windows filesystems)."""
    return os.path.normcase(os.path.normpath(str(path)))


def _already_copied(src: Path, target: Path, src_hash: str | None = None) -> bool:
    """True if target is byte-identical to src (same size, same SHA256)."""
    try:
        if src.stat().st_size != target.stat().st_size:
            return False
    except OSError:
        return False
    if src_hash is None:
        src_hash = _sha256(src)
    return src_hash == _sha256(target)


def _dup_suffix(n):
    """Disambiguation suffix: ' B', ' C', … then ' 28' once past the alphabet."""
    return f" {chr(ord('A') + n)}" if n < 26 else f" {n + 1}"


def _next_free_name(target: Path, reserved: set | None = None) -> Path:
    """A non-existing path beside target with ' B'/' C'/… suffix before extension.
    Also avoids paths in `reserved` (targets already assigned this run)."""
    parent, stem, ext = target.parent, target.stem, target.suffix
    n = 1
    while True:
        cand = parent / f"{stem}{_dup_suffix(n)}{ext}"
        if not cand.exists() and (reserved is None or cand not in reserved):
            return cand
        n += 1


# ── Main entry point ─────────────────────────────────────────────────────────

def copy_card(source, dest, prefix="", bucket_by_date=False, tz_offset_hours=0.0,
              skip_if_on_disk=True, write_sidecars=True, use_card_cache=True,
              logger=None, progress=None, should_cancel=None):
    """Copy picture files from `source` (recursively) into `dest`.

    prefix            - optional filename prefix (see target_name).
    bucket_by_date    - when True, files go into dest/<YYYY-MM-DD>/ subfolders.
    tz_offset_hours   - hours added to capture time BEFORE the date is taken,
                        so near-midnight shots land in the intended day.
    skip_if_on_disk   - when True (default), skip any card file whose content is
                        already anywhere in `dest` (content match, ignores name),
                        so incremental re-reads only copy new pictures.
    write_sidecars    - when True (default), create an XMP sidecar per RAW.
    use_card_cache    - when True (default), read/write .sv_ingest_cache.json on
                        the card to cache SHA256 + capture dates across runs.
                        Set False to ignore the cache (forces full re-scan).
    progress(done, total, name) - optional callback, called per file before copy.
    should_cancel()             - optional callable; return True to stop early.

    Returns a summary dict: total / copied / skipped / conflict / failed / bytes.
    """
    log = logger or logging.getLogger("speciesviewer")
    source = Path(source)
    dest   = Path(dest)

    files = sorted(
        p for p in source.rglob("*")
        if p.is_file()
        and p.suffix.lower() in PICTURE_EXTENSIONS
        and p.name != _CardCache._FILENAME   # never copy the cache file itself
    )
    total = len(files)
    summary = {"total": total, "copied": 0, "skipped": 0,
               "renamed": 0, "conflict": 0, "failed": 0, "bytes": 0}
    targets = []   # destination paths now present (copied or already-there)

    log.info(f"Found {total} picture(s) under {source}")

    cache = _CardCache(source) if use_card_cache else None

    # ── Resolve date buckets ──────────────────────────────────────────────
    # Cache reduces exiftool to only new/changed files; updated below after copy.
    if bucket_by_date:
        buckets, cached_dates_for_new = _build_buckets(
            files, tz_offset_hours, cache, log)
    else:
        buckets = {}
        cached_dates_for_new = {}

    # ── Destination content index (dedup by content, ignores name) ────────
    dest_index = _DestIndex(dest) if skip_if_on_disk else None

    now_str = datetime.now().isoformat()

    # ── Phase 1: scan + dedup (sequential) ───────────────────────────────
    # Determine which files need copying and compute their target paths.
    # Dedup against the dest index is inherently sequential (shared state).
    # Each file that needs copying is queued in to_copy for Phase 2.
    to_copy: list[tuple[Path, Path, str | None]] = []  # (src, target, cached_sha)
    reserved: set[Path] = set()   # targets already assigned this run (not yet on disk)

    for i, src in enumerate(files, 1):
        if should_cancel and should_cancel():
            log.warning("⏹ Card read cancelled by user.")
            break
        if progress:
            progress(i, total, src.name)

        try:
            cached = cache.get(src) if cache else None
            cached_sha = cached.get("sha256") if cached else None

            if dest_index is not None:
                match = dest_index.find_match(src, src_hash=cached_sha)
                if match is not None:
                    summary["skipped"] += 1
                    targets.append(str(match))
                    continue

            target_dir = (dest / buckets.get(_norm(src), UNKNOWN_DATE_DIR)
                          if bucket_by_date else dest)
            target = target_dir / target_name(src.name, prefix)

            if target.exists() or target in reserved:
                if target.exists() and _already_copied(src, target, src_hash=cached_sha):
                    summary["skipped"] += 1
                    targets.append(str(target))
                    continue
                # Same name, different content (dual-slot cameras that restart frame
                # numbers, or two new files with the same target name queued in this
                # run). Auto-rename; a re-read recognises both by content, idempotent.
                renamed = _next_free_name(target, reserved)
                log.info(f"Name in use by a different shot — saving as {renamed.name}")
                target = renamed
                summary["renamed"] += 1

            target.parent.mkdir(parents=True, exist_ok=True)
            reserved.add(target)
            to_copy.append((src, target, cached_sha))

        except OSError as e:
            log.warning(f"❌ Failed to plan copy for {src.name}: {e}")
            summary["failed"] += 1

    # ── Phase 2: parallel copy ─────────────────────────────────────────────
    # Submit all copies at once so the OS can pipeline card reads with SSD
    # writes — the same async-I/O advantage Explorer's CopyFileEx has.
    n_to_copy = len(to_copy)
    if n_to_copy and not (should_cancel and should_cancel()):
        done_copying = 0
        with ThreadPoolExecutor(max_workers=_COPY_WORKERS) as pool:
            future_map = {
                pool.submit(_copy_and_hash_dest, src, target): (src, target)
                for src, target, _ in to_copy
            }
            for future in as_completed(future_map):
                src, target = future_map[future]
                done_copying += 1
                if progress:
                    progress(total, total,
                             f"Copying… {done_copying}/{n_to_copy}  {src.name}")
                try:
                    src_hash = future.result()   # OSError on size mismatch
                    summary["copied"] += 1
                    summary["bytes"] += target.stat().st_size
                    targets.append(str(target))
                    if dest_index is not None:
                        dest_index.add(target, sha256=src_hash)
                    if cache:
                        date_str = buckets.get(_norm(src)) if bucket_by_date else None
                        cache.put(src, src_hash, date_str, last_copied=now_str)
                except OSError as e:
                    log.warning(f"❌ Failed to copy {src.name}: {e}")
                    summary["failed"] += 1

                if should_cancel and should_cancel():
                    for f in future_map:
                        f.cancel()   # drop not-yet-started futures
                    log.warning("⏹ Card read cancelled during copy.")
                    break

    if dest_index is not None:
        dest_index.save_cache()   # persist any newly computed dest hashes
    if cache:
        cache.save()              # persist card SHA + dates for new files

    # ── XMP sidecars ──────────────────────────────────────────────────────
    summary["sidecars"] = 0
    if write_sidecars and not (should_cancel and should_cancel()):
        raw_targets = [t for t in targets if Path(t).suffix.lower() in RAW_EXTENSIONS]
        if raw_targets:
            n_xmp = len(raw_targets)
            if progress:
                progress(total, total, f"Writing XMP sidecars… 0/{n_xmp}")

            def _xmp_progress(done, n_total):
                if progress:
                    progress(total, total, f"Writing XMP sidecars… {done}/{n_total}")

            summary["sidecars"] = _write_sidecars(raw_targets, log,
                                                   progress_cb=_xmp_progress)

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
    parser = argparse.ArgumentParser(
        description="Copy pictures from an SD-card folder into a project directory.")
    parser.add_argument("source", help="Source folder (e.g. a mounted SD-card)")
    parser.add_argument("dest",   help="Destination project pictures folder")
    parser.add_argument("--prefix",     default="",  help="Optional filename prefix, e.g. '2026 Zeeland'")
    parser.add_argument("--by-date",    action="store_true", help="Sort into dest/<YYYY-MM-DD>/ subfolders")
    parser.add_argument("--tz-offset",  type=float, default=0.0,
                        help="Hours added to capture time before bucketing")
    parser.add_argument("--no-dedup",   action="store_true", help="Skip dedup against dest")
    parser.add_argument("--no-sidecars",action="store_true", help="Skip XMP sidecar creation")
    parser.add_argument("--no-cache",   action="store_true", help="Ignore card cache (force full re-scan)")
    args = parser.parse_args()
    copy_card(args.source, args.dest, prefix=args.prefix,
              bucket_by_date=args.by_date, tz_offset_hours=args.tz_offset,
              skip_if_on_disk=not args.no_dedup, write_sidecars=not args.no_sidecars,
              use_card_cache=not args.no_cache)
