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
# Copy speed: on Windows _os_copy calls CopyFileEx (the same kernel routine
# Explorer uses — internal async I/O, read-ahead, no Python read/write loop).
# ThreadPoolExecutor(_COPY_WORKERS) submits all copies at once so the card
# controller can pipeline requests across files.  On macOS shutil.copy2 already
# uses clonefile/fcopyfile.  SHA256 is hashed from the SSD dest (500 MB/s),
# not the card (~60 MB/s).

import errno
import hashlib
import json
import logging
import os
import queue
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

from utils.disk_space import check_free_space
from utils.raw_extensions import RAW, SELF_DESCRIBING, is_raw, is_video
from utils.exiftool_path import exe as exiftool_exe

UNKNOWN_DATE_DIR = "unknown-date"

_COPY_WORKERS = 4   # parallel copy threads; card I/O is the bottleneck, 4 keeps pipeline full
# XMP sidecar creation scales its exiftool workers per-machine via
# engine.exifcache.auto_worker_count (≈physical-core count, capped, floored by
# file count) instead of a hardcoded 4 — see project_exif_parallelism.

# ── Platform-specific fast copy ───────────────────────────────────────────────
# Windows: CopyFileEx (the same kernel call Explorer uses — internal async I/O,
#          read-ahead, no Python read/write loop overhead).
# macOS:   shutil.copy2 already calls clonefile/fcopyfile — near-instant on APFS,
#          and hardware-optimal over USB (no change needed there).
if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as _wt
    _kernel32   = ctypes.WinDLL("kernel32", use_last_error=True)
    _CopyFileExW = _kernel32.CopyFileExW
    _CopyFileExW.restype  = _wt.BOOL
    _CopyFileExW.argtypes = [_wt.LPCWSTR, _wt.LPCWSTR,
                              ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_void_p, _wt.DWORD]

    def _os_copy(src: Path, dest: Path):
        if not _CopyFileExW(str(src), str(dest), None, None, None, 0):
            raise OSError(ctypes.get_last_error(), os.strerror(ctypes.get_last_error()),
                          str(src))
else:
    def _os_copy(src: Path, dest: Path):
        shutil.copy2(src, dest)

# Lower-case extensions (incl. dot) we treat as "pictures" worth ingesting.
# .nrw is Nikon's Coolpix raw (P7800 and friends) — a format that IS shot here
# and already recognised elsewhere in the codebase (svu5SyncSidecars). Its
# absence meant a card holding NRWs copied the JPGs and left every raw behind,
# saying nothing. The pipeline itself needs no change: engine.exifcache filters
# by a DENY list, and nrw is not on it, so exiftool reads these normally.
RAW_EXTENSIONS = RAW  # canonical table — see utils/raw_extensions.py
# Everything that gets a .xmp sidecar written for it — RAW only.
#
# A HEIC does NOT, and this is deliberate: it was briefly given one (2026-09-23)
# on the reasoning that a sidecar is where annotations belong, and that was the
# wrong way round. PhotoLab expects a HEIC's properties INSIDE the file, exactly
# as for a JPG, Lightroom embeds them there too, and every field SpeciesViewer
# stores is a standard XMP field both of them can read AND write. A sidecar would
# therefore be a SECOND copy of facts the file already owns, and the reader that
# prefers it would shadow whatever PhotoLab had just written. So the HEIC is the
# one truth (see _SELF_DESCRIBING in gui/sidecar_provider.py and the HEIC set in
# utils/raw_extensions.py), and nothing here creates a sidecar for it.
#
# The sidecars written for HEICs during that one day are harmless — they carry
# only date and GPS, never an annotation — and are ignored from here on.
SIDECAR_EXTENSIONS = RAW  # canonical table — see utils/raw_extensions.py
PICTURE_EXTENSIONS = RAW_EXTENSIONS | {
    ".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff",       # stills
    ".mp4", ".mov",                                                    # video
}


# Directories the FILESYSTEM writes on a card, not the camera. Windows puts
# "System Volume Information" (IndexerVolumeGuid, WPSettings.dat) on every card
# it ever mounts; macOS adds .Spotlight-V100 / .fseventsd / .Trashes. Nothing in
# them is ever a picture, and counting their contents made a freshly formatted
# 112-picture card report "NOT copied: 1 (no extension), 1 .dat" — a warning
# that invites the reader to add .dat to the RAW table. They are PRUNED from the
# walk rather than filtered per file, so the walk never descends into them at
# all (on Windows that descent is also the one that tends to raise on
# permissions). Their own dot-names would not have been enough: the junk sits
# one level DOWN, in files whose names are perfectly ordinary.
HOUSEKEEPING_DIRS = {
    "system volume information", "$recycle.bin", "recycler",     # Windows
    ".spotlight-v100", ".fseventsd", ".trashes",                 # macOS
    ".temporaryitems", ".documentrevisions-v100",
}


THUMBNAIL_SUFFIX = "-small.jpg"
THUMBNAIL_LABEL = "-small.jpg (website thumbnails)"


def scan_source(source):
    """ONE walk of the source → (pictures, ignored) where `ignored` is
    {extension: count} for files this ingest will NOT take.

    The ignored tally exists because the silent version of this cost real data:
    a card of 3863 files reported "2413 pictures" and said nothing about the
    other 1450, of which 218 were Nikon .nrw raws that simply were not on the
    recognised list. A count that quietly excludes things reads as a complete
    count. This project has been bitten by exactly this before — see the note on
    engine.work_backup.SOURCE_EXTS, where a missing format left pictures out of
    a restore manifest.

    Sidecars are NOT counted as ignored: they travel with their picture rather
    than on their own (see _SidecarIndex). Dot-files are tool caches, never
    pictures, and are not worth reporting either.
    """
    pictures, ignored = [], {}
    # os.walk, not rglob, for the one thing rglob cannot do: prune a directory
    # before descending into it. It also hands back files and directories
    # already separated, so the per-file is_file() stat is gone — over USB, on a
    # 2000-file card, that stat is not free.
    for dirpath, dirnames, filenames in os.walk(source):
        # Dot-DIRECTORIES are pruned as well as the named ones. Dot-FILES were
        # always skipped, but the walk still descended into dot-folders, and the
        # files inside them have perfectly ordinary names. The case that matters
        # is `.trash`: culling MOVES a condemned stem to <day>/.trash/<stem>/
        # rather than unlinking it, so pointing a read at a folder you have
        # already culled — the very case _SidecarIndex exists for — copied every
        # deleted picture straight back in. Nothing this project puts behind a
        # dot is ever an ingest source.
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d.lower() not in HOUSEKEEPING_DIRS]
        for name in filenames:
            if name.startswith(".") or name == _CardCache._FILENAME:
                continue
            if name.lower().endswith(THUMBNAIL_SUFFIX):
                # SpeciesViewer's own website thumbnail (600x450, no capture date):
                # never a picture to read in. Taken along, it was filed by its
                # file date — the day a website build made it — and showed up as
                # days of its own (TaxoGreece: 93 on 2026-05-23, 28 on 2026-07-08).
                ignored[THUMBNAIL_LABEL] = ignored.get(THUMBNAIL_LABEL, 0) + 1
                continue
            ext = Path(name).suffix.lower()
            if ext in PICTURE_EXTENSIONS:
                pictures.append(Path(dirpath) / name)
            elif ext not in SIDECAR_SUFFIXES:
                ignored[ext or "(no extension)"] = ignored.get(ext or "(no extension)", 0) + 1
    return sorted(pictures), ignored


def describe_ignored(ignored) -> str:
    """'218 .nrw, 34 .jpg_original' — most numerous first, so the biggest
    surprise is the first thing read."""
    return ", ".join(f"{n} {ext}" for ext, n in
                     sorted(ignored.items(), key=lambda kv: (-kv[1], kv[0])))


def _warn_ignored(ignored, source, log):
    """Say what is being left behind, once, where it will be seen."""
    if not ignored:
        return
    log.warning(f"Not copied from {source}: {describe_ignored(ignored)} — these "
                f"file types are not recognised as pictures. If one of them is a "
                f"camera format you shoot, it needs adding to RAW in "
                f"utils/raw_extensions.py "
                f"before this card is formatted.")


# ── File hashing ──────────────────────────────────────────────────────────────

def _sha256(path, chunk_size=1 << 20) -> str:
    """Streaming sha256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_and_hash_dest(src: Path, dest: Path) -> str:
    """Copy src→dest via the OS native API, then hash the local dest copy.

    Hashing on the local SSD (500 MB/s) is 5-10× faster than hashing from
    the card (~60 MB/s), so total wall time is lower than a single streaming
    card read.  Size mismatch raises OSError; caller removes the partial file.
    """
    _os_copy(src, dest)
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
    _RESERVE  = ".sv_ingest_reserve"
    _RESERVE_BYTES = 1 << 20      # 1 MB — ample for several thousand entries
    _VERSION  = 1

    def __init__(self, source_root: Path, logger=None):
        self._root  = Path(source_root)
        self._path  = self._root / self._FILENAME
        self._log   = logger or logging.getLogger("speciesviewer")
        self._dirty = False
        self._data: dict[str, dict] = {}
        self._reads: list[dict] = []
        self._load()
        self._make_reserve()

    def _load(self):
        try:
            if self._path.exists():
                with open(self._path, encoding="utf-8") as f:
                    raw = json.load(f)
                if raw.get("version") == self._VERSION:
                    self._data = raw.get("files", {})
                    self._reads = raw.get("reads", [])
        except Exception:
            self._data = {}
            self._reads = []

    # ── read history ──────────────────────────────────────────────────────
    # Which trip this card was read into, and when. It lives ON THE CARD, with
    # the checksums, so it travels: the answer to "have I done this one?" is
    # carried by the object in your hand, not by a laptop that may be gone.
    #
    # Reading a card twice is already harmless — dedup is by content, so a
    # second pass copies nothing — but it was also silent, and on a restore with
    # a dozen cards going in in random order "have I done this one?" is the
    # question that actually gets asked. This is what lets it be answered.
    _KEEP_READS = 5

    def history(self) -> list[dict]:
        """Past reads of this card, newest first."""
        return list(reversed(getattr(self, "_reads", [])))

    def record_read(self, stamp: dict):
        """Append one read. Capped, so a card used all season stays small."""
        if not stamp:
            return
        self._reads = (getattr(self, "_reads", []) + [stamp])[-self._KEEP_READS:]
        self._dirty = True

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

    def remember_date(self, path: Path, capture_date: str):
        """Keep a capture date read from the EXIF, also for a picture that was
        not copied (a day you did not tick), so the next scan of this card
        groups it by its real day without reading it again. An existing entry
        for the same file keeps its hash and import time."""
        key = self._key(path)
        try:
            st = path.stat()
        except OSError:
            return
        prev = self.get(path) or {}
        self._data[key] = {**prev, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                           "capture_date": capture_date}
        self._dirty = True

    # ── Reserved space on the card ────────────────────────────────────────
    # A card that is full to the last megabyte cannot save its own cache: the
    # save writes a .tmp and renames, so it briefly needs room for TWO copies.
    # With 18 MB RAWs the space left over on a full card is effectively uniform
    # in [0, 18 MB), so roughly one card in eighteen ends up with under a
    # megabyte spare — across the ten-odd cards of a trip that is close to a
    # coin flip, which is why this is worth having at all.
    #
    # So a 1 MB dummy sits on the card and is spent ONLY once a save has
    # actually failed. That ordering is the whole point. The obvious
    # alternative — delete the dummy before every write, put it back after —
    # would have SpeciesViewer deleting a file on the card on every single
    # read, and the card holds the only original copy of the pictures. Here the
    # destructive step exists only on the one path where it is what saves you.
    #
    # The reserve is laid down at load time, i.e. at the start of a read,
    # because that is when the card is most likely to still have room: a card
    # read at lunch and again that evening gets its reserve while it is half
    # empty and spends it when it is full.

    @property
    def _reserve_path(self) -> Path:
        return self._root / self._RESERVE

    def _make_reserve(self) -> bool:
        """Lay down the reserve if it is not already there. Failure is a normal
        condition and stays silent: a write-protected card cannot have one, and
        neither can a card that is already too full. Nothing is lost either
        way — the reserve is an optimisation, never a prerequisite."""
        path = self._reserve_path
        try:
            if path.exists() and path.stat().st_size >= self._RESERVE_BYTES:
                return True
            with open(path, "wb") as f:
                f.write(bytes(self._RESERVE_BYTES))
            return True
        except OSError:
            return False

    def _spend_reserve(self) -> bool:
        """Delete the reserve so a retried save has room. True only when one was
        actually removed — which is also proof that the card is writable."""
        try:
            if self._reserve_path.exists():
                self._reserve_path.unlink()
                return True
        except OSError:
            pass
        return False

    def _write(self) -> bool:
        """One atomic attempt: .tmp then rename. False on any OS refusal."""
        try:
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": self._VERSION, "files": self._data,
                           "reads": getattr(self, "_reads", [])},
                          f, separators=(",", ":"))
            tmp.replace(self._path)
            return True
        except OSError:
            return False

    def save(self):
        """Flush to disk atomically. Silently skips on read-only media.

        On a card with no room left the first attempt fails; the reserve is then
        spent and the save retried once. A failure AFTER the reserve was spent
        IS reported — the card proved itself writable by giving the reserve up,
        so that is a real symptom rather than the read-only case this method's
        original silence was there to tolerate."""
        if not self._dirty:
            return
        if self._write():
            self._make_reserve()      # keep it stocked for the next read
            return
        if not self._spend_reserve():
            return                    # read-only card, or never had a reserve — quiet, as before
        if self._write():
            self._log.warning(
                f"⚠️ The card at {self._root} is full — spent the reserved megabyte "
                f"to save its ingest cache. It will be restored when the card has room.")
            self._make_reserve()      # normally fails while the card is still full; harmless
            return
        self._log.warning(
            f"⚠️ Could not save the ingest cache on {self._root}, even after freeing "
            f"the reserve — the next read of this card will re-scan it from scratch.")


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

def _read_capture_datetimes(files, log, progress_cb=None) -> dict[str, datetime]:
    """{normalised path: datetime} from the file's own capture time, split across
    N parallel exiftool workers (auto_worker_count — see project_exif_parallelism).
    The tag order is engine.capture_time's, so a movie is dated by its QuickTime
    tags rather than dropping through to mtime.
    Files without a readable date are omitted (caller falls back to file mtime).
    exiftool is imported lazily so copy_card stays usable without it for the
    no-bucketing path.

    Each worker keeps ONE exiftool process open and pulls 100-file batches off
    a shared queue (rather than one big chunk per worker), so progress lands
    roughly every 100 files instead of once per worker's whole share — and,
    unlike engine.exifcache.read_metadata_parallel
    (a generator built for cache rescans), no batch is read synchronously
    before the others start being drained, so reported throughput doesn't
    burst-catch-up: all workers run concurrently the whole time, so the rate
    is accurate from the first batch. progress_cb(done, total, rate) fires
    after each batch."""
    result = {}
    if not files:
        return result
    try:
        import exiftool
        from engine.exifcache import COMMON_ARGS, auto_worker_count
        from engine.capture_time import EXIFTOOL_ARGS as CAPTURE_ARGS, capture_datetime
        total = len(files)
        workers = auto_worker_count(total)
        # This pass is fast (a single tag, not a full copy) — one update per
        # 100 files is plenty, and never more than 100 files of silence even
        # on a big card or a slow machine.
        batch_size = 100
        paths = [str(p) for p in files]
        work: queue.Queue = queue.Queue()
        for i in range(0, total, batch_size):
            work.put(paths[i:i + batch_size])

        lock = threading.Lock()
        start = time.time()
        done = 0

        def _worker():
            nonlocal done
            with exiftool.ExifToolHelper(
                executable=exiftool_exe(), common_args=COMMON_ARGS, encoding="utf-8", check_execute=False,
            ) as et:
                while True:
                    try:
                        batch = work.get_nowait()
                    except queue.Empty:
                        return
                    for meta in et.get_metadata(batch, params=CAPTURE_ARGS):
                        src = meta.get("SourceFile")
                        # One rule for photos and movies (engine.capture_time):
                        # a movie has no EXIF:DateTimeOriginal, so without this
                        # every .mov/.mp4 fell through to file mtime and its day
                        # folder was decided by the file system rather than by
                        # the camera. Also rejects "0000:00:00 00:00:00", which
                        # would otherwise parse as nothing and land the file in
                        # UNKNOWN rather than on its real day.
                        dt = capture_datetime(meta)
                        if src and dt is not None:
                            with lock:
                                result[_norm(src)] = dt
                    with lock:
                        done += len(batch)
                        d = done
                    if progress_cb:
                        elapsed = time.time() - start
                        rate = d / elapsed if elapsed > 0 else 0.0
                        progress_cb(d, total, rate)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_worker) for _ in range(workers)]
            for f in futures:
                f.result()   # propagate any worker exception
    except Exception as e:
        log.warning(f"Could not read capture dates via exiftool ({e}); using file mtime instead.")
    return result


def _file_date_is_capture_date(f: Path) -> bool:
    """True for a camera original — a RAW or a video — whose file date the
    camera set when it wrote the file, so it is the day of the shot. Anything
    else (a JPG, a HEIC, a PNG) may have been exported later — a DxO JPG, a
    Create JPG, a phone picture copied on — and then its file date is the
    EXPORT day: on a re-read Brazil day (2026-09-09) five JPGs and a HEIC
    showed as four other days and were left behind when 09-09 was ticked.
    Those need their real capture date from the EXIF."""
    return is_raw(f) or is_video(f)


def _stem_key(f: Path) -> str:
    """'…/2026 Brazilie - IMG_6300' for both IMG_6300.HEIC and IMG_6300.jpg."""
    return _norm(f.with_suffix(""))


def _partner_dates(undated, buckets: dict[str, str], dated_keys) -> dict[str, str]:
    """A picture with no capture date in its EXIF takes the date of a same-name
    partner that has one: SpeciesViewer's own Create JPG writes no metadata, so
    IMG_6300.jpg (made from IMG_6300.HEIC on 6 Oct) would otherwise be dated by
    its file date and show as a day of its own. Only partners whose date is
    real (EXIF, a camera original, or remembered on the card) count."""
    by_stem: dict[str, str] = {}
    for key in dated_keys:
        if key in buckets:
            by_stem.setdefault(_stem_key(Path(key)), buckets[key])
    return {_norm(f): by_stem[_stem_key(f)] for f in undated
            if _stem_key(f) in by_stem}


def _build_buckets(files, tz_offset_hours, cache: "_CardCache | None", log,
                    include_dates=None) -> dict[str, str]:
    """{normalised path: 'YYYY-MM-DD'} for all files.

    Dates come from the card cache where available; only cache-miss files go
    through exiftool.  Falls back to file mtime when exiftool has no date.

    include_dates, when given, is the set of days the caller actually wants
    (see copy_card). A cache-miss file whose cheap file-mtime day is more than
    one calendar day from every wanted day cannot become a wanted day after a
    real EXIF read either — mtime is already the camera's own clock at write
    time (see scan_days), so the two dates only ever drift by clock skew or a
    near-midnight shot, never by more than about a day. Such a file is bucketed
    from mtime alone and skipped from the exiftool batch entirely: it was going
    to be filtered out downstream regardless, so reading its real date would
    only have confirmed the exclusion. A file within that margin still goes
    through exiftool, so a real boundary case is never resolved by mtime."""
    offset = timedelta(hours=tz_offset_hours or 0)
    buckets:   dict[str, str]      = {}
    need_exif: list[Path]          = []
    cached_dt: dict[str, datetime] = {}

    near_dates = None
    if include_dates:
        near_dates = set()
        for d in include_dates:
            try:
                day = date.fromisoformat(d)
            except ValueError:
                near_dates.add(d)          # e.g. UNKNOWN_DATE_DIR — no neighbours
                continue
            for delta in (-1, 0, 1):
                near_dates.add((day + timedelta(days=delta)).isoformat())

    skipped_far = 0
    for f in files:
        entry = cache.get(f) if cache else None
        if entry and (date_str := entry.get("capture_date")):
            buckets[_norm(f)] = date_str
            continue
        if near_dates is not None and _file_date_is_capture_date(f):
            try:
                mtime_day = (datetime.fromtimestamp(f.stat().st_mtime) + offset) \
                    .strftime("%Y-%m-%d")
            except OSError:
                mtime_day = None
            if mtime_day is not None and mtime_day not in near_dates:
                buckets[_norm(f)] = mtime_day
                skipped_far += 1
                continue
        need_exif.append(f)

    hits = len(files) - len(need_exif) - skipped_far
    log.info(f"{len(files)} picture(s) on the card: {hits} already have a known "
             f"date, {skipped_far} are clearly outside the selected day(s) "
             f"(from file date), {len(need_exif)} need to be read from EXIF.")
    if need_exif:
        from engine.exifcache import _fmt_duration

        def _report(done, total, rate):
            remaining = (total - done) / rate if rate > 0 else 0
            log.info(f"Reading capture dates: {done}/{total} "
                      f"({rate:,.0f} files/s, ~{_fmt_duration(remaining)} left)")

        dt_map = _read_capture_datetimes(need_exif, log, progress_cb=_report)
        undated: list[Path] = []
        for f in need_exif:
            key = _norm(f)
            dt = dt_map.get(key)
            if dt is None:
                undated.append(f)
                continue
            buckets[key] = (dt + offset).strftime("%Y-%m-%d")
            cached_dt[key] = dt   # a real date: remembered on the card
        # No capture date at all: a same-name partner's date, else the file date
        # (not remembered — it is a guess, and the partner may arrive later).
        dated_keys = [k for k in buckets if k not in {_norm(f) for f in undated}]
        partners = _partner_dates(undated, buckets, dated_keys)
        for f in undated:
            key = _norm(f)
            if key in partners:
                buckets[key] = partners[key]
                cached_dt[key] = None          # real enough: remember the day
                continue
            try:
                dt = datetime.fromtimestamp(f.stat().st_mtime)
            except OSError:
                buckets[key] = UNKNOWN_DATE_DIR
                continue
            buckets[key] = (dt + offset).strftime("%Y-%m-%d")

    return buckets, cached_dt


def pictures_on_disk_by_day(dest):
    """{day: picture count} already sitting in the destination's day folders.

    Answers "what did I already read?" across sessions, which neither the card
    nor the run summary can: the summary describes ONE run, and reopening the
    dialog forgets it. Reading a card over several sittings — three days now,
    three more after lunch — is normal, and without this the day table looks
    identical whether a day is safely on disk or has never been touched.

    Counted, not content-matched: one listing per day folder, no hashing. It is
    an indicator for the eye, and the copy itself still verifies every file by
    content before skipping it, so a wrong count here can never cause a picture
    to be missed.
    """
    root = Path(dest) if dest else None
    if not root or not root.is_dir():
        return {}
    out = {}
    try:
        children = list(root.iterdir())
    except OSError:
        return {}
    for day_dir in children:
        if not day_dir.is_dir():
            continue
        try:
            n = sum(1 for f in day_dir.iterdir()
                    if f.suffix.lower() in PICTURE_EXTENSIONS and f.is_file()
                    and not f.name.lower().endswith(THUMBNAIL_SUFFIX))
        except OSError:
            continue
        if n:
            out[day_dir.name] = n
    return out


def scan_days(source, tz_offset_hours=0.0, use_card_cache=True, logger=None):
    """Group the card's pictures by capture day WITHOUT copying anything — a
    preview for the day-picker. For a camera original (RAW, video) the file
    system mtime stands in for the capture date: cheap, already known from the
    directory walk, and right because the camera set it from its own clock when
    it wrote the file. Everything else (JPG, HEIC, PNG) may have been exported
    later, when its mtime is the export day — so those get their real EXIF
    capture date (see _file_date_is_capture_date). A card cache hit from a PRIOR
    scan/read (a real EXIF date, already confirmed) is preferred over both.

    The trade-off left: a RAW whose camera clock was off, or shot near midnight,
    could show under a slightly different day here than where Read finally puts
    it — cosmetic only, never a data-loss risk.

    Returns (days, total, ignored) where `days` is a sorted list of
    (date_str, count), `total` is the picture count, and `ignored` is
    {extension: count} for what this ingest will not take."""
    from collections import Counter
    log = logger or logging.getLogger("speciesviewer")
    source = Path(source)
    files, ignored = scan_source(source)
    total = len(files)
    # Before the copy, not after: this is the moment the answer can still change
    # what you do (point at a different folder, add the format, keep the card).
    _warn_ignored(ignored, source, log)
    if not total:
        log.info(f"No pictures found under {source}")
        return [], 0, ignored
    log.info(f"Found {total} picture(s) on the card.")

    cache = _CardCache(source, logger=log) if use_card_cache else None
    offset = timedelta(hours=tz_offset_hours or 0)
    buckets: dict[str, str] = {}
    need_exif: list[Path] = []
    for f in files:
        entry = cache.get(f) if cache else None
        date_str = entry.get("capture_date") if entry else None
        if not date_str and not _file_date_is_capture_date(f):
            need_exif.append(f)        # maybe exported later: file date lies
            continue
        if not date_str:
            try:
                dt = datetime.fromtimestamp(f.stat().st_mtime)
            except OSError:
                date_str = UNKNOWN_DATE_DIR
            else:
                date_str = (dt + offset).strftime("%Y-%m-%d")
        buckets[_norm(f)] = date_str
    if need_exif:
        log.info(f"Reading the capture date of {len(need_exif)} JPG/HEIC/other "
                 f"picture(s) — their file date may be the day they were exported.")
        dt_map = _read_capture_datetimes(need_exif, log)
        undated: list[Path] = []
        for f in need_exif:
            dt = dt_map.get(_norm(f))
            if dt is None:
                undated.append(f)
                continue
            buckets[_norm(f)] = (dt + offset).strftime("%Y-%m-%d")
        # No date in the EXIF: a same-name partner's date, else the file date.
        partners = _partner_dates(undated, buckets, list(buckets))
        for f in undated:
            if _norm(f) in partners:
                buckets[_norm(f)] = partners[_norm(f)]
                continue
            try:
                dt = datetime.fromtimestamp(f.stat().st_mtime)
            except OSError:
                buckets[_norm(f)] = UNKNOWN_DATE_DIR
                continue
            buckets[_norm(f)] = (dt + offset).strftime("%Y-%m-%d")

    counts = Counter(buckets.values())
    days = sorted(counts.items())
    log.info(f"Scanned {total} picture(s) across {len(days)} day(s) on the card "
             f"(RAW and video by file date, other pictures by capture date).")
    return days, total, ignored


# ── XMP sidecars ─────────────────────────────────────────────────────────────

# The exiftool recipe that creates a sidecar lives in engine.sidecarlib — ONE
# definition, shared with the species commit, which now authors a sidecar for a
# RAW that never got one (see FilePicture.updateSubjects). Re-exported under this
# module's name because the per-body camera checklist (tools/svr_camera_checklist.py)
# runs the REAL expressions ingest uses against a corpus file; a checklist that
# retyped them would certify a body on the strength of a copy.
from engine.sidecarlib import sidecar_opts   # noqa: F401  (re-export)


def _write_sidecars(raw_paths, log, progress_cb=None) -> int:
    """Create one XMP sidecar per RAW that lacks one.

    Splits the work across N parallel exiftool processes (N auto-scales per
    machine via auto_worker_count).  Each process is an independent subprocess
    so there is no shared state.  progress_cb is called from the calling thread
    (as_completed loop) so it is safe to update Qt widgets from there."""
    todo = [p for p in raw_paths if not Path(p).with_suffix(".xmp").exists()]
    if not todo:
        return 0
    from engine.exif_writer import run_exiftool_files
    from engine.exifcache import auto_worker_count
    # DateTimeOriginal is re-sourced WITH the camera's UTC offset appended, e.g.
    # "2026:08:14 10:34:01+01:00". `-xmp:all<all` alone drops it, because EXIF
    # keeps the offset in a separate tag (OffsetTimeOriginal, EXIF 2.31) — and
    # the tracklog geotag reads the SIDECAR, so it was structurally blind to the
    # one value that makes a picture's time absolute, and fell back to guessing
    # the offset by correlation (which put 24 Goeree pictures 8.6 km out).
    #
    # Two properties, both measured before adopting this:
    #   - Additive. Every existing reader takes date[:19] and still sees
    #     "2026:08:14 10:34:01". No master CSV row has ever carried a zone
    #     (75,050 checked), so nothing historic needs rewriting.
    #   - Degrades. On a pre-EXIF-2.31 camera with no offset tag the expression
    #     simply doesn't fire and the plain date written by -xmp:all<all stands.
    #     It can never leave a sidecar with no DateTimeOriginal at all.
    opts = sidecar_opts()

    n = len(todo)
    workers = auto_worker_count(n)
    chunk_size = max(1, (n + workers - 1) // workers)
    chunks = [todo[i:i + chunk_size] for i in range(0, n, chunk_size)]

    def _run_chunk(chunk):
        try:
            run_exiftool_files(opts, chunk)
        except Exception as e:
            log.warning(f"XMP sidecar chunk failed: {e}")
        return sum(1 for p in chunk if Path(p).with_suffix(".xmp").exists()), len(chunk)

    created = 0
    done_count = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(_run_chunk, chunk): chunk for chunk in chunks}
        for future in as_completed(future_map):
            try:
                c, cnt = future.result()
            except Exception as e:
                log.warning(f"XMP sidecar chunk failed: {e}")
                c, cnt = 0, len(future_map[future])
            created += c
            done_count += cnt
            if progress_cb:
                progress_cb(done_count, n)   # called from this thread, not worker

    if created < n:
        log.warning(f"XMP sidecars: created {created} of {n} (some failed).")
    else:
        log.info(f"Created {created} XMP sidecar(s).")
    return created


# ── Naming helpers ────────────────────────────────────────────────────────────

# The one naming convention: "<prefix> - <original camera name>". The separator
# is a space, a hyphen and a space — chosen over Lightroom's space-less style
# because it stays readable in a folder listing and in Explorer search.
PREFIX_JOIN = " - "


def target_name(src_name, prefix=""):
    """Destination filename: '<prefix> - <original>', e.g. 'IMG_1234.CR3' →
    '2026 Zeeland - IMG_1234.CR3'. The original name is always preserved; the
    prefix is what makes names unique across cards and runs, so re-running is
    idempotent.

    A name that ALREADY carries this prefix is left alone. Without that, a file
    which has been round-tripped through another program — Lightroom exports
    under its own naming, and re-importing one is easy to do by accident —
    comes back as "2026 Kreta - 2026 Kreta - IMG_1.jpg", and no later pass can
    tell that apart from a deliberate name. Cheap to prevent, awkward to undo.
    """
    prefix = (prefix or "").strip().rstrip(" -")
    if not prefix:
        return src_name
    if src_name.startswith(prefix) and src_name[len(prefix):len(prefix) + 1] in (" ", "-"):
        return src_name
    return f"{prefix}{PREFIX_JOIN}{src_name}"


SIDECAR_SUFFIXES = {".xmp", ".dop"}


class _SidecarIndex:
    """Finds the sidecars sitting beside a source picture, case-insensitively.

    Why this exists: a real SD card holds no sidecars, so the copy was only ever
    built to move pictures and GENERATE fresh sidecars at the destination. But
    the source field accepts any folder, and a folder you have already culled
    holds .xmp files carrying the species, stars and colours you set. Copying
    the pictures without them would leave every copy untagged while the work sat
    in the old folder — silently, and unrecoverably once the source was tidied
    up. So sidecars travel WITH their picture.

    The two shapes differ and neither is guessable from the other:
      .xmp REPLACES the picture's extension   (DSC_1.ORF  → DSC_1.xmp)
      .dop APPENDS to the whole filename      (DSC_1.ORF  → DSC_1.ORF.dop)
    Case varies by who wrote the file (cameras and PhotoLab disagree, and this
    project's own folders hold '.XMP'), so the index is keyed lowercase — off
    Windows a plain `.exists()` on the wrong case simply misses.

    One `iterdir` per directory, cached: a flat month folder is ~1300 pictures in
    ONE directory, and a per-file probe would walk it 1300 times.
    """

    def __init__(self):
        self._dirs: dict[str, dict[str, Path]] = {}

    def _names(self, directory: Path) -> dict:
        key = str(directory).lower()
        cached = self._dirs.get(key)
        if cached is None:
            cached = {}
            try:
                for p in directory.iterdir():
                    # Suffix FIRST: it is a free string test, while is_file() is a
                    # stat. On a real SD card nothing matches, so a card read pays
                    # one listing and zero stats instead of one per picture — over
                    # USB, on a 2000-file card, that ordering is the difference
                    # between free and a few seconds.
                    if p.suffix.lower() in SIDECAR_SUFFIXES and p.is_file():
                        cached[p.name.lower()] = p
            except OSError:
                pass                       # unreadable dir → no sidecars, not a crash
            self._dirs[key] = cached
        return cached

    def for_picture(self, src: Path) -> list:
        names = self._names(src.parent)
        found = [names.get(f"{src.stem}.xmp".lower()),
                 names.get(f"{src.name}.dop".lower())]
        return [p for p in found if p is not None]


def sidecar_target(picture_target: Path, sidecar: Path) -> Path:
    """Where a carried sidecar lands: beside its picture's FINAL name.

    The final name matters — the copy may auto-rename a picture whose target
    name is already taken by a different shot, and a sidecar left on the old
    stem is an orphan the moment that happens.

    The suffix is normalised to lowercase because `_write_sidecars` decides
    whether to generate one with `with_suffix('.xmp').exists()`. That check is
    case-sensitive off Windows, so a carried '.XMP' would be missed there and a
    blank generated sidecar would land beside it — two sidecars for one RAW,
    with the empty one winning on some readers.
    """
    if sidecar.suffix.lower() == ".dop":
        return picture_target.parent / (picture_target.name + ".dop")
    return picture_target.with_suffix(".xmp")


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


# ── Copying an explicit list ──────────────────────────────────────────────────

def copy_known_files(pairs, logger=None, progress=None, should_cancel=None,
                     refresh_exts=frozenset()) -> dict:
    """Copy an explicit list of (source, destination) pictures, sidecars included.

    `copy_card` is the right tool for a CARD: unknown contents, capture dates
    that have to be read, a destination that may already hold the same shot
    under a different name. None of that applies once the caller has decided,
    file by file, what is copied and where — which is what emptying a card does
    (see engine/card_empty.py). Routing that through copy_card would force an
    exiftool date pass and a whole-destination hash index to answer a question
    already answered, and `use_card_cache` would drop an ingest cache AND a 1 MB
    reserve into every one of the laptop's day folders on the way past.

    It lives HERE, beside copy_card, rather than in the caller, because the parts
    worth reusing are exactly the fiddly ones and they are private to this
    module: `_SidecarIndex` (the .xmp and .dop carrying the culling work must
    travel with their picture, and the two have different naming shapes) and
    `_copy_and_hash_dest` (verified copy).

    Idempotent: a destination that is already byte-identical is skipped, so a
    re-run after a cancel or a full card finishes the job. A destination name in
    use by DIFFERENT content is auto-renamed rather than overwritten — the same
    rule copy_card follows, for the same reason.

    `refresh_exts` names the extensions for which a differing destination is
    REPLACED instead of renamed. It exists for the keeper-card case, where the
    RAW is immutable but the sidecars and renders are not: species names get
    corrected and JPGs get made for days after a card is consolidated, so a
    keeper card that could never catch up would freeze on the day it was
    written. The usual "never overwrite a sidecar at the destination" rule is
    there because the destination may hold work done THERE — which is true of a
    picture folder and false of a keeper card, where nothing edits anything and
    the laptop is unambiguously the source. Empty by default, so no other caller
    is affected.

    Returns a copy_card-shaped summary so either can be reported the same way.
    """
    log = logger or logging.getLogger("speciesviewer")
    summary = {"total": len(pairs), "copied": 0, "skipped": 0, "renamed": 0,
               "refreshed": 0, "failed": 0, "bytes": 0, "sidecars": 0,
               "failed_names": [], "error": "", "targets": []}
    sidecar_index = _SidecarIndex()
    sidecar_plan: list[tuple[Path, Path]] = []
    reserved: set[Path] = set()

    for i, (src, dest) in enumerate(pairs, 1):
        if should_cancel and should_cancel():
            log.warning("⏹ Copy cancelled by user.")
            break
        src, dest = Path(src), Path(dest)
        if progress:
            progress(i, len(pairs), src.name)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() or dest in reserved:
                if dest.exists() and _already_copied(src, dest):
                    summary["skipped"] += 1
                    summary["targets"].append(str(dest))
                    _plan_carried_sidecars(sidecar_index, src, dest, sidecar_plan,
                                           refresh_exts)
                    continue
                if dest.suffix.lower() in refresh_exts:
                    dest.unlink(missing_ok=True)      # the laptop is the source
                    summary["refreshed"] += 1
                else:
                    dest = _next_free_name(dest, reserved)
                    summary["renamed"] += 1
            reserved.add(dest)
            _copy_and_hash_dest(src, dest)
            summary["copied"] += 1
            summary["bytes"] += dest.stat().st_size
            summary["targets"].append(str(dest))
            _plan_carried_sidecars(sidecar_index, src, dest, sidecar_plan,
                                   refresh_exts)
        except OSError as e:
            summary["failed"] += 1
            summary["failed_names"].append(src.name)
            log.error(f"Could not copy {src.name}: {e}")
            # ENOSPC on the target is not one file's problem, it is the end of
            # the run — carrying on would produce a failure line per remaining
            # picture and bury the one that matters.
            if getattr(e, "errno", None) == errno.ENOSPC:
                summary["error"] = (f"The target ran out of space after "
                                    f"{summary['copied']} picture(s).")
                log.error(f"❌ {summary['error']}")
                break

    for sc_src, sc_dest in sidecar_plan:
        try:
            sc_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sc_src, sc_dest)
            summary["sidecars"] += 1
        except OSError as e:
            log.warning(f"Could not copy sidecar {sc_src.name}: {e}")
    return summary


def _plan_carried_sidecars(index, src_picture: Path, picture_target: Path,
                           plan: list, refresh_exts=frozenset()) -> None:
    """Queue this picture's sidecars for copying. A sidecar already at the
    destination is left alone — it may hold work done there since, which is the
    derived-file rule this project learned the hard way — unless the caller has
    declared this destination one it owns outright (see refresh_exts)."""
    for sc in index.for_picture(src_picture):
        target_sc = sidecar_target(Path(picture_target), sc)
        if not target_sc.exists() or target_sc.suffix.lower() in refresh_exts:
            plan.append((sc, target_sc))


# ── Main entry point ─────────────────────────────────────────────────────────

def card_history(source) -> list[dict]:
    """What this card has already been read into, newest first — [] if unknown.

    Read-only and cheap: one small JSON off the card. Answers "have I done this
    one?" from the card itself, which is the only place that can answer it after
    a laptop is replaced.
    """
    try:
        return _CardCache(Path(source), logger=logging.getLogger("speciesviewer")).history()
    except Exception:
        return []


def copy_card(source, dest, prefix="", bucket_by_date=False, tz_offset_hours=0.0,
              skip_if_on_disk=True, write_sidecars=True, use_card_cache=True,
              include_dates=None, include_names=None, trip_stamp=None,
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
    include_dates     - optional set of 'YYYY-MM-DD' strings; when given, files
                        whose capture-day is not in the set are skipped entirely
                        (see scan_days). Independent of bucket_by_date.
    include_names     - optional {day: {filename, …}} allow-list, per day, of
                        TARGET names (i.e. already prefixed). A day present in
                        the mapping copies only those files; a day ABSENT from
                        it copies everything. That asymmetry is the point: it
                        lets a restore re-apply the culling for days the backup
                        knows, without silently dropping a day it has never seen.
    trip_stamp        - optional {"trip_id":…, "trip":…} recorded ON THE CARD
                        with this read, so the card can later say which trip it
                        was read into and when. See _CardCache.record_read.
    progress(done, total, name) - optional callback, called per file before copy.
    should_cancel()             - optional callable; return True to stop early.

    Returns a summary dict: total / copied / skipped / conflict / failed / bytes.
    """
    log = logger or logging.getLogger("speciesviewer")
    source = Path(source)
    dest   = Path(dest)

    files, ignored = scan_source(source)
    total = len(files)
    summary = {"total": total, "copied": 0, "skipped": 0, "deselected": 0,
               "renamed": 0, "conflict": 0, "failed": 0, "bytes": 0,
               "culled": 0,
               "failed_names": [], "planned_bytes": 0, "error": ""}
    targets = []   # destination paths now present (copied or already-there)

    log.info(f"Found {total} picture(s) under {source}")
    summary["ignored"] = ignored
    _warn_ignored(ignored, source, log)

    cache = _CardCache(source, logger=log) if use_card_cache else None
    summary["previous_reads"] = cache.history() if cache else []

    # ── Resolve date buckets ──────────────────────────────────────────────
    # Needed for the subfolder layout AND for an include_dates day filter.
    # Cache reduces exiftool to only new/changed files; updated below after copy.
    files_by_key = {_norm(f): f for f in files}
    if bucket_by_date or include_dates is not None or include_names:
        buckets, cached_dates_for_new = _build_buckets(
            files, tz_offset_hours, cache, log, include_dates=include_dates)
    else:
        buckets = {}
        cached_dates_for_new = {}
    if cache:
        # Every capture date just read from the EXIF, also for days you did not
        # tick — before, they were read, thrown away, and read again next scan.
        for key in cached_dates_for_new:
            src_path = files_by_key.get(key)
            if src_path is not None and buckets.get(key):
                cache.remember_date(src_path, buckets[key])

    # ── Destination content index (dedup by content, ignores name) ────────
    dest_index = _DestIndex(dest) if skip_if_on_disk else None

    now_str = datetime.now().isoformat()

    # ── Phase 1: scan + dedup (sequential) ───────────────────────────────
    # Determine which files need copying and compute their target paths.
    # Dedup against the dest index is inherently sequential (shared state).
    # Each file that needs copying is queued in to_copy for Phase 2.
    to_copy: list[tuple[Path, Path, str | None]] = []  # (src, target, cached_sha)
    reserved: set[Path] = set()   # targets already assigned this run (not yet on disk)

    # Sidecars that must travel with their picture (see _SidecarIndex). Planned
    # alongside the pictures — including for pictures ALREADY at the destination,
    # which is the repair case: a folder copied before this existed has the
    # pictures but none of the culling, and a re-run should complete it.
    sidecar_index = _SidecarIndex()
    sidecar_plan: list[tuple[Path, Path]] = []          # (src_sidecar, target)

    # Per-day tally for the dialog's day table. The run summary counts the whole
    # run, which cannot answer the per-day question actually being asked — "is
    # this day safely on disk?" — because a re-read reports 0 copied for a day
    # that is completely present.
    per_day: dict[str, dict] = {}

    def _tally(src_path: Path, key: str) -> None:
        day = buckets.get(_norm(src_path), UNKNOWN_DATE_DIR)
        slot = per_day.setdefault(day, {"copied": 0, "skipped": 0, "failed": 0})
        slot[key] += 1

    def _plan_sidecars(src_picture: Path, picture_target: Path) -> None:
        for sc in sidecar_index.for_picture(src_picture):
            target_sc = sidecar_target(Path(picture_target), sc)
            # NEVER overwrite a sidecar already at the destination. It may hold
            # work done there since — the derived-file rule this project learned
            # the hard way — and skipping instead makes a re-run idempotent.
            if target_sc.exists():
                continue
            sidecar_plan.append((sc, target_sc))

    for i, src in enumerate(files, 1):
        if should_cancel and should_cancel():
            log.warning("⏹ Card read cancelled by user.")
            break
        if progress:
            progress(i, total, src.name)

        # Day filter: skip files whose capture-day wasn't selected — before any
        # dedup/hash/copy work, so deselected days cost nothing.
        src_day = buckets.get(_norm(src), UNKNOWN_DATE_DIR)
        if include_dates is not None and src_day not in include_dates:
            summary["deselected"] += 1
            continue

        # Restore filter: this day's backup recorded which pictures survived the
        # culling, so the ones it does not name are the ones deleted during the
        # trip. Skipping them here beats copying and removing them afterwards —
        # gigabytes never move, and a culled picture never touches the disk. A
        # day the backup has never heard of is copied in full.
        if include_names:
            allowed = include_names.get(src_day)
            if allowed is not None and \
                    target_name(src.name, prefix).lower() not in allowed:
                summary["culled"] += 1
                continue

        try:
            cached = cache.get(src) if cache else None
            cached_sha = cached.get("sha256") if cached else None

            if dest_index is not None:
                match = dest_index.find_match(src, src_hash=cached_sha)
                if match is not None:
                    summary["skipped"] += 1
                    _tally(src, "skipped")
                    targets.append(str(match))
                    _plan_sidecars(src, match)
                    continue

            target_dir = (dest / buckets.get(_norm(src), UNKNOWN_DATE_DIR)
                          if bucket_by_date else dest)
            target = target_dir / target_name(src.name, prefix)

            if target.exists() or target in reserved:
                if target.exists() and _already_copied(src, target, src_hash=cached_sha):
                    summary["skipped"] += 1
                    _tally(src, "skipped")
                    targets.append(str(target))
                    _plan_sidecars(src, target)
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
            _plan_sidecars(src, target)

        except OSError as e:
            log.warning(f"❌ Failed to plan copy for {src.name}: {e}")
            summary["failed"] += 1
            summary["failed_names"].append(src.name)

    # ── Pre-flight: does what we are about to copy actually fit? ──────────
    # Phase 1 knows the exact file list, so the byte total is exact too. A
    # short disk stops the run here, with nothing copied — better than
    # discovering it 800 files in, when every remaining copy fails one by one.
    if to_copy and not (should_cancel and should_cancel()):
        planned_bytes = 0
        for src, _target, _sha in to_copy:
            try:
                planned_bytes += src.stat().st_size
            except OSError:
                pass
        for _sc_src, _sc_target in sidecar_plan:
            try:
                planned_bytes += _sc_src.stat().st_size
            except OSError:
                pass
        summary["planned_bytes"] = planned_bytes
        space_error = check_free_space(dest, planned_bytes,
                                       what=f"Copying {len(to_copy)} picture(s)")
        if space_error:
            log.error(f"❌ {space_error} Nothing was copied.")
            summary["error"] = space_error
            to_copy = []              # nothing gets copied; caches below still save
            sidecar_plan = []         # …and no sidecar lands without its picture

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
                    _tally(src, "copied")
                    summary["bytes"] += target.stat().st_size
                    targets.append(str(target))
                    if dest_index is not None:
                        dest_index.add(target, sha256=src_hash)
                    if cache:
                        date_str = buckets.get(_norm(src)) or None
                        cache.put(src, src_hash, date_str, last_copied=now_str)
                except OSError as e:
                    log.warning(f"❌ Failed to copy {src.name}: {e}")
                    summary["failed"] += 1
                    _tally(src, "failed")
                    summary["failed_names"].append(src.name)

                if should_cancel and should_cancel():
                    for f in future_map:
                        f.cancel()   # drop not-yet-started futures
                    log.warning("⏹ Card read cancelled during copy.")
                    break

    if dest_index is not None:
        dest_index.save_cache()   # persist any newly computed dest hashes
    if cache:
        # Stamp the card with what this read did, BEFORE saving: a card that
        # comes back in a fortnight can then say so itself.
        if trip_stamp and not (should_cancel and should_cancel()):
            cache.record_read({**trip_stamp,
                               "when": now_str,
                               "dest": str(dest),
                               "prefix": prefix,
                               "copied": summary["copied"],
                               "skipped": summary["skipped"],
                               "total": total})
        cache.save()              # persist card SHA + dates for new files

    # ── Phase 2b: carry the source's own sidecars ─────────────────────────
    # Deliberately BEFORE the generator below: _write_sidecars only creates an
    # .xmp for a RAW that has none, so a sidecar carried here is what survives,
    # and the picture keeps the species/stars/colours it arrived with. Do this
    # the other way round and every carried sidecar loses to a blank one.
    #
    # Sequential and unthreaded: these are a few KB each, next to multi-MB RAWs
    # already copied in parallel — a second thread pool would cost more to start
    # than the whole phase takes.
    summary["per_day"] = per_day
    summary["sidecars_copied"] = 0
    if sidecar_plan and not (should_cancel and should_cancel()):
        for i, (sc_src, sc_target) in enumerate(sidecar_plan, 1):
            if should_cancel and should_cancel():
                log.warning("⏹ Cancelled while copying sidecars.")
                break
            if progress:
                progress(total, total,
                         f"Copying sidecars… {i}/{len(sidecar_plan)}  {sc_src.name}")
            try:
                # The picture copy may have failed, in which case its sidecar has
                # nothing to belong to — an orphan sidecar reads as a picture that
                # went missing, which is worse than no sidecar at all.
                if not sc_target.parent.is_dir():
                    continue
                shutil.copy2(sc_src, sc_target)
                summary["sidecars_copied"] += 1
            except OSError as e:
                log.warning(f"❌ Failed to copy sidecar {sc_src.name}: {e}")
                summary["failed"] += 1
                summary["failed_names"].append(sc_src.name)
        if summary["sidecars_copied"]:
            log.info(f"Carried {summary['sidecars_copied']} existing sidecar(s) "
                     f"(.xmp/.dop) across with their pictures — the species, "
                     f"ratings and colours already set travel with the copy.")

    # ── XMP sidecars ──────────────────────────────────────────────────────
    summary["sidecars"] = 0
    summary["sidecars_missing"] = 0
    raw_targets = [t for t in targets if Path(t).suffix.lower() in SIDECAR_EXTENSIONS]
    if (write_sidecars and not summary["error"]
            and not (should_cancel and should_cancel())):
        if raw_targets:
            n_xmp = len(raw_targets)
            if progress:
                progress(total, total, f"Writing XMP sidecars… 0/{n_xmp}")

            def _xmp_progress(done, n_total):
                if progress:
                    progress(total, total, f"Writing XMP sidecars… {done}/{n_total}")

            summary["sidecars"] = _write_sidecars(raw_targets, log,
                                                   progress_cb=_xmp_progress)

            # Build per-folder sidecar cache so the culling viewer opens
            # without re-running exiftool.  Incremental: only new/changed XMPs
            # are re-read, so a card top-up on a second machine is fast.
            from engine.sidecar_cache import build_cache_for_folder
            xmp_by_folder: dict[Path, list[Path]] = {}
            for t in raw_targets:
                xmp = Path(t).with_suffix(".xmp")
                if xmp.exists():
                    xmp_by_folder.setdefault(xmp.parent, []).append(xmp)
            for folder, xmps in xmp_by_folder.items():
                if progress:
                    progress(total, total, f"Caching XMP metadata… {folder.name}")
                build_cache_for_folder(folder, xmps)

    # ── Metadata cache for pictures that ARE their own sidecar ────────────
    # A HEIC/PNG keeps its annotations inside the file, so there is no sidecar to
    # cache — cache the PICTURE's metadata instead, into the same per-folder cache
    # under the same mtime_ns rule (engine.sidecar_cache is path-agnostic, and its
    # mtime key catches an edit to the file correctly). Without this the culling
    # viewer re-read every HEIC through exiftool on first open: 279 of them on one
    # iPhone card, which is the whole reason this cache exists.
    #
    # Outside the sidecar gate above on purpose: nothing is written here, it is a
    # read, so it should not depend on the "write XMP sidecars" checkbox.
    own_targets = [t for t in targets
                   if Path(t).suffix.lower() in SELF_DESCRIBING]
    if own_targets and not summary["error"] and not (should_cancel and should_cancel()):
        from engine.sidecar_cache import build_cache_for_folder
        own_by_folder: dict[Path, list[Path]] = {}
        for t in own_targets:
            own_by_folder.setdefault(Path(t).parent, []).append(Path(t))
        for folder, files in own_by_folder.items():
            if progress:
                progress(total, total, f"Caching picture metadata… {folder.name}")
            try:
                build_cache_for_folder(folder, files)
            except Exception as e:
                # A cache is an optimisation; failing to build one must never
                # cost us the card read that has already copied every file.
                log.warning(f"Could not cache picture metadata for {folder.name}: {e}")

    # ── Did every RAW end up with a sidecar? ──────────────────────────────
    # This phase is the LAST one and it is gated three ways — the checkbox, an
    # earlier error, and cancel — so it is the phase most likely to be skipped,
    # and skipping it used to be invisible: "…; 0 sidecar(s)." at INFO reads
    # exactly like a clean run with nothing to do. On 2026-09-21 that left 678
    # RAWs of one Brazil day with no sidecar, which is not a cosmetic loss: a
    # species named on such a picture has no file to be written to, and was
    # silently dropped at the next Read & Summarize. Count what is actually on
    # disk and say so — the count is the invariant, not the intention.
    if raw_targets:
        summary["sidecars_missing"] = sum(
            1 for t in raw_targets if not Path(t).with_suffix(".xmp").exists()
            and not Path(t).with_suffix(".XMP").exists())

    line = (
        f"Card read summary — copied {summary['copied']} (of which {summary['renamed']} renamed), "
        f"skipped {summary['skipped']}, deselected {summary['deselected']}, "
        f"culled {summary['culled']}, "
        f"failed {summary['failed']} of {total}; {summary['sidecars']} sidecar(s)."
    )
    # A run that lost files must never end on a plain INFO line that reads the
    # same as a clean one — see the matching ⚠️ marker in the Read dialog.
    if summary["sidecars_missing"]:
        line += (f" ⚠️ {summary['sidecars_missing']} RAW(s) have NO .xmp sidecar — "
                 f"species, ratings and crops have nowhere to be stored for them. "
                 f"Re-read the card, or run tools/svr_sidecar_heal.py.")
    if summary["error"]:
        log.error(f"❌ {line} ABORTED: {summary['error']}")
    elif summary["failed"]:
        names = ", ".join(summary["failed_names"][:5])
        more = " …" if len(summary["failed_names"]) > 5 else ""
        log.warning(f"⚠️ {line} FAILED file(s): {names}{more}")
    elif summary["sidecars_missing"]:
        log.warning(f"⚠️ {line}")
    else:
        log.info(line)
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
