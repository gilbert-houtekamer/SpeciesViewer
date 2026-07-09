# Global EXIF read cache for Read Directory (bin/sv2ReadDirExif.py).
#
# Why: exiftool's cost is per-FILE overhead (~15-35 ms), not bytes — measured
# 2026-05-23, size-independent. A full-trip scan (e.g. Kreta: ~3000 readable
# files) is ~50-70 s, and the folder is re-scanned many times during editing.
# Caching exiftool's per-file output makes a re-scan re-read only the handful of
# files that actually changed.
#
# Two buckets, keyed differently on purpose:
#   immutable (RAW + HEIC) — bytes never change in this workflow, so key by
#     filename+size. A byte-identical copy in another folder (trip vs master,
#     day folder vs whole-trip scan) reuses the same entry.
#   mutable (xmp, jpg, png, mov, aae, ...) — edited in place, so key by
#     path+size+mtime. An edit re-reads; an untouched file is a hit.
#
# What is cached is exiftool's RAW per-file dict (minus location/time-specific
# keys), NOT the processed CSV row — the row-building in sv2ReadDirExif stays the
# single source of truth and runs fresh every time. On a hit the current file's
# path keys are injected before processing.
#
# NOTE: stored as one JSON file under the master repo. If the global cache ever
# grows large enough that the per-run load/save dominates a small day scan,
# migrate the storage backend (load_cache/save_cache + the two dict lookups) to
# sqlite — the partition/update API here is written to make that swap local.

import configparser
import hashlib
import json
import os
import shutil
import tempfile
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("speciesviewer")

CACHE_VERSION = 1
CACHE_BACKUPS = 3

# exiftool common args used by every read path (sv2ReadDirExif, rescan, and the
# spawned parallel workers). Kept in ONE place so the parallel workers read
# byte-identically to the primary helper. -G group names + UTF-8 for both file
# contents and filenames (see sv2ReadDirExif for the full rationale).
COMMON_ARGS = ["-G", "-charset", "utf8", "-charset", "filename=utf8"]

# Bytes never change → name+size key (enables cross-folder / cross-copy reuse).
# NOTE: dng is deliberately NOT here — DxO/PureRaw DeepPRIME DNGs are bulky and
# recreatable from the RAW (PhotoLab regenerates them for free), so we don't
# read or store them in the master (DENY below). They're cleaned locally by the
# normal trip-delete (recreatable, RAW is in the master).
IMMUTABLE_EXTENSIONS = {"orf", "nef", "cr2", "cr3", "arw", "rw2", "raf", "heic"}
# exiftool does NOT emit rows for these in a recursive scan (verified: 1482 .dop
# and the .ini files produced 0 rows on Kreta). Never feed them — an explicit
# file list would otherwise create spurious rows the dir-scan never produced.
# dng: excluded by policy (bulky, recreatable). dop is read separately as a
# copy-only sidecar (see sv2's .dop pass) so the DxO recipe travels with the RAW.
DENY_EXTENSIONS = {"dop", "dng", "ini", "txt", "ico", "html", "lrcat", "lrdata"}
# Everything else readable (xmp, jpg, jpeg, png, mov, aae, ...) is treated as
# mutable and keyed by path+size+mtime.

# Location/time-specific keys: never trusted from cache, re-derived per file.
_VOLATILE_EXIF_KEYS = {
    "SourceFile", "File:Directory", "File:FileName",
    "File:FileModifyDate", "File:FileInodeChangeDate", "File:FileAccessDate",
    "File:FileSize", "File:FilePermissions",
}


def field_fingerprint(selected_fields):
    """Stamp of the field list an entry was captured under. A change here
    (e.g. a new field added to SELECTED_FIELDS) makes every entry a miss, so
    the next run re-reads — handles schema evolution without serving a value
    that was never captured."""
    blob = " ".join(selected_fields).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:8]


def master_repository(config):
    """Root of the MASTER BiotaDatabase. During a trip run this is the shared
    master ([master] MasterBiotaDatabase); on the master itself that key is
    absent/empty, so fall back to this repo. The global cache lives here so
    master and every trip share ONE cache and a file read once is never re-read
    for its trip copies. Reads config only — no Project/Trip side effects."""
    try:
        master = config.get("master", "MasterBiotaDatabase")
    except (configparser.NoSectionError, configparser.NoOptionError):
        master = ""
    master = master.replace('"', "").replace("'", "").strip()
    if not master:
        master = config.get("repository", "BiotaDatabase")
    return master.replace('"', "").replace("'", "").strip()


def exif_cache_path(config):
    """exif_cache.json in the local EXIF cache dir (per-machine SSD by default;
    relocatable via Manage Caches). Used by day/trip Read Directory; stays small.

    Historically lived under <master>/metadata/cache (Dropbox, shared across
    machines); it is now per-machine so master scans no longer churn Dropbox.
    `config` is kept in the signature for callers but no longer used here."""
    from utils.cache_paths import cache_root
    return str(cache_root("exif") / "exif_cache.json")


def master_cache_path(config):
    """Separate cache for the master-wide EXIF scan (Master menu > Rescan).
    Kept distinct from exif_cache.json so warming the full master (~159k files,
    ~89 MB) never taxes the small day/trip Read Directory cache. Same local
    EXIF cache dir as exif_cache_path; `config` kept for signature compat."""
    from utils.cache_paths import cache_root
    return str(cache_root("exif") / "exif_cache_master.json")


def new_cache():
    return {"cache_version": CACHE_VERSION, "immutable": {}, "mutable": {}}


def load_cache(path):
    """Return the cache, falling back through .bak1..N on corruption."""
    candidates = [path] + [f"{path}.bak{i}" for i in range(1, CACHE_BACKUPS + 1)]
    for p in candidates:
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("cache_version") == CACHE_VERSION:
                data.setdefault("immutable", {})
                data.setdefault("mutable", {})
                if p != path:
                    logger.warning(f"EXIF cache: recovered from backup {p}")
                return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"EXIF cache: {p} unreadable ({e}); trying backup")
    return new_cache()


def _replace_with_retry(tmp, path, attempts=12, delay=0.25):
    """os.replace, retried on transient PermissionError. The cache lives under
    the master repo, which is typically a Dropbox/OneDrive folder; the sync
    client (and AV real-time scanners) briefly open the destination during
    sync, which surfaces as WinError 5 (Access denied) on an otherwise-valid
    replace. Retrying for a few seconds rides out the lock."""
    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def save_cache(path, cache):
    """Atomic write (temp + fsync + os.replace) and rotate .bak1..N so a crash
    never loses the prior good copy. Backups share the cache's volume, so the
    replace stays atomic. os.replace is retried because a Dropbox/AV lock on the
    destination is transient (see _replace_with_retry); backup rotation is
    best-effort — a locked .bak must not cost us the save of fresh data."""
    cache["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # rotate: .bak{N-1} -> .bak{N}, ..., current -> .bak1 (best-effort)
    try:
        for i in range(CACHE_BACKUPS, 1, -1):
            src = f"{path}.bak{i - 1}"
            if os.path.exists(src):
                shutil.copy2(src, f"{path}.bak{i}")
        if os.path.exists(path):
            shutil.copy2(path, f"{path}.bak1")
    except OSError as e:
        logger.warning(f"EXIF cache: backup rotation skipped ({e}).")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _imm_key(name, size):
    return f"{name.lower()}|{size}"


def _mut_key(path):
    return os.path.normcase(os.path.normpath(path))


def _inject_paths(exif, full, dirpath, name):
    """Return a copy of a cached exif dict with the path keys of the file
    actually on disk now — so a master file's entry serves its trip copy."""
    e = dict(exif)
    e["SourceFile"] = full.replace("\\", "/")
    e["File:Directory"] = dirpath.replace("\\", "/")
    e["File:FileName"] = name
    return e


def partition(root, cache, fingerprint, recurse=True):
    """Walk `root` once; return (hits, misses, stats).
        hits   – cached exif dicts, path keys re-injected for the on-disk file
        misses – absolute paths that must go to exiftool
        stats  – dict of per-bucket hit/miss counts for logging
    recurse=False reads only `root`'s own files (no sub-directories) — used by
    the Add-to-Master micro-trip so a folder with nested trips/exports counts
    just its own pictures.
    """
    imm, mut = cache["immutable"], cache["mutable"]
    hits, misses = [], []
    stats = {"imm_hit": 0, "imm_miss": 0, "mut_hit": 0, "mut_miss": 0,
             "skipped": 0}
    for dirpath, dirs, files in os.walk(root):
        # Match exiftool -r: it does NOT recurse into directories whose names
        # begin with '.' (e.g. the GUI's .preview_cache). Prune in place so we
        # don't read files the dir-scan never produced rows for.
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if not recurse:
            dirs[:] = []        # top-level only
        for name in files:
            # Hidden helper files (.sv_sidecar_cache.json, .sv_ingest_cache.json,
            # .DS_Store, …) are tool caches, never pictures — skip like dot-dirs.
            if name.startswith("."):
                stats["skipped"] += 1
                continue
            ext = os.path.splitext(name)[1][1:].lower()
            if ext in DENY_EXTENSIONS:
                stats["skipped"] += 1
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if ext in IMMUTABLE_EXTENSIONS:
                ent = imm.get(_imm_key(name, st.st_size))
                if ent and ent.get("fp") == fingerprint:
                    hits.append(_inject_paths(ent["exif"], full, dirpath, name))
                    stats["imm_hit"] += 1
                else:
                    misses.append(full)
                    stats["imm_miss"] += 1
            else:
                ent = mut.get(_mut_key(full))
                if (ent and ent.get("fp") == fingerprint
                        and ent.get("size") == st.st_size
                        and ent.get("mtime") == int(st.st_mtime)):
                    hits.append(_inject_paths(ent["exif"], full, dirpath, name))
                    stats["mut_hit"] += 1
                else:
                    misses.append(full)
                    stats["mut_miss"] += 1
    return hits, misses, stats


def update(cache, fresh, fingerprint):
    """Fold freshly-read exiftool dicts into the cache (immutable by name+size,
    mutable by path+size+mtime). Volatile path/time keys are stripped before
    storing; they're re-injected on the next hit."""
    imm, mut = cache["immutable"], cache["mutable"]
    for d in fresh:
        src = d.get("SourceFile") or ""
        name = d.get("File:FileName") or os.path.basename(src)
        ext = os.path.splitext(name)[1][1:].lower()
        if ext in DENY_EXTENSIONS or not src:
            continue
        try:
            st = os.stat(src)
        except OSError:
            continue
        slim = {k: v for k, v in d.items() if k not in _VOLATILE_EXIF_KEYS}
        if ext in IMMUTABLE_EXTENSIONS:
            imm[_imm_key(name, st.st_size)] = {"fp": fingerprint, "exif": slim}
        else:
            mut[_mut_key(src)] = {
                "fp": fingerprint, "size": st.st_size,
                "mtime": int(st.st_mtime), "exif": slim,
            }


def auto_worker_count(n_files, configured="auto", *, cap=16, per_worker=24):
    """How many parallel exiftool workers to use for `n_files`.

    Benchmarked 2026-07-09 (see project_exif_parallelism): a single -stay_open
    exiftool parses files serially, so N independent processes give near-linear
    speedup UP TO the machine's *physical* core count, then regress (per-worker
    process startup + contention overtake the gains). Findings:
      * the useful peak tracks PHYSICAL cores (SmallBoy ~6, BigBoy ~16), not the
        LOGICAL count os.cpu_count() returns (16 / 32) — hyperthreads add no
        parse throughput. cpu_count()//2 is the right approximation.
      * small folders must scale DOWN: on 132 files, 16+ workers spend more time
        launching exiftool than reading. `per_worker` enforces that floor.

    `configured` is the [performance] exif_workers config value: "auto" (derive
    from cores) or an explicit integer to pin a machine. `cap` bounds the top
    end regardless (past the physical-core count every machine regressed).
    """
    text = str(configured or "auto").strip().lower()
    if text in ("", "auto", "0"):
        base = max(2, (os.cpu_count() or 4) // 2)
    else:
        try:
            base = max(1, int(text))
        except ValueError:
            base = max(2, (os.cpu_count() or 4) // 2)
    by_files = max(1, n_files // per_worker)
    return max(1, min(base, cap, by_files))


def _read_chunk(paths, fields):
    """Read one chunk in its own short-lived exiftool subprocess. Used by the
    worker threads in read_metadata_parallel; the primary helper handles the
    first chunk on the calling thread, so no process is spawned needlessly."""
    import exiftool
    with exiftool.ExifToolHelper(
            common_args=COMMON_ARGS, encoding="utf-8", check_execute=False) as et:
        return et.get_metadata(paths, params=fields)


def read_metadata_parallel(paths, fields, workers, primary):
    """Yield exiftool metadata for `paths` in batches, split across `workers`
    parallel exiftool processes.

    A GENERATOR (not a list) so a huge scan (master rescan) can fold each batch
    into the cache and free it before the next arrives — memory stays bounded by
    the largest single batch, not the whole scan.

    `primary` is the caller's already-open ExifToolHelper; it reads the first
    chunk on THIS thread (so it is genuinely reused, never left idle), while the
    remaining chunks each get a short-lived helper on a worker thread. Each
    ExifToolHelper is its own subprocess, so the threads never share state and
    the GIL is released while they block on their pipes. Batch order is not
    preserved — callers build rows per-record, so order does not matter.
    """
    if not paths:
        return
    if workers <= 1 or len(paths) <= 1:
        yield primary.get_metadata(paths, params=fields)
        return
    n = len(paths)
    size = max(1, (n + workers - 1) // workers)
    chunks = [paths[i:i + size] for i in range(0, n, size)]
    # chunks[0] on this thread via `primary`; chunks[1:] on the pool.
    with ThreadPoolExecutor(max_workers=max(1, len(chunks) - 1)) as pool:
        futures = [pool.submit(_read_chunk, c, fields) for c in chunks[1:]]
        yield primary.get_metadata(chunks[0], params=fields)
        for fut in as_completed(futures):
            yield fut.result()


def _fmt_duration(seconds):
    """'5 min 25 s' / '48 s' — plain, for logs and the GUI status line."""
    seconds = int(round(seconds))
    m, s = divmod(seconds, 60)
    return f"{m} min {s} s" if m else f"{s} s"


def rescan(root, cache_path, chunk=2000, workers="auto"):
    """Warm/refresh a cache file by reading `root` with exiftool: read only the
    files not already cached (or changed), split across N parallel exiftool
    workers (auto_worker_count), in super-chunks to bound memory, then save once.
    No CSV, no XMP writes — a pure cache-population pass for the Master menu's
    Rescan. Progress is logged for the GUI spinner. A full ~160k-file master
    reads in a few minutes on a many-core machine (benchmarked ~500 files/s at
    16 workers, 2026-07-09); later passes re-read only what changed.

    Returns a stats dict that also carries the run's brag numbers
    (read/workers/elapsed/rate/summary) so the caller can show them."""
    import exiftool
    from engine.selected_fields import SELECTED_FIELDS
    start = time.time()
    fp = field_fingerprint(SELECTED_FIELDS)
    cache = load_cache(cache_path)
    hits, misses, stats = partition(root, cache, fp)
    total = len(misses)
    n_workers = auto_worker_count(total, workers) if misses else 0
    if misses:
        logger.info(f"EXIF cache rescan of {root}: {len(hits):,} already cached, "
                    f"{total:,} to read with {n_workers} parallel workers.")
        # Read in super-chunks so peak memory stays bounded to one group even on
        # a 159k-file master scan; each group is split across n_workers parallel
        # exiftool processes (read_metadata_parallel). No logger= on the helper:
        # pyexiftool would echo every -stay_open command into the GUI log; the
        # per-group progress lines below are the feedback.
        group_size = max(chunk, n_workers * 1000)
        with exiftool.ExifToolHelper(
                common_args=COMMON_ARGS,
                encoding="utf-8", check_execute=False) as et:
            for i in range(0, total, group_size):
                group = misses[i:i + group_size]
                gw = auto_worker_count(len(group), workers)
                for batch in read_metadata_parallel(group, SELECTED_FIELDS, gw, et):
                    update(cache, batch, fp)
                done = min(i + group_size, total)
                logger.info(f"EXIF cache rescan: read {done:,}/{total:,} files "
                            f"({n_workers} workers)")
        save_cache(cache_path, cache)
    else:
        logger.info(f"EXIF cache rescan of {root}: all {len(hits):,} files "
                    f"already cached — nothing to read.")

    elapsed = time.time() - start
    rate = total / elapsed if elapsed > 0 else 0.0
    if total:
        summary = (f"{total:,} files processed by {n_workers} workers in "
                   f"{_fmt_duration(elapsed)} ({rate:,.0f} files/s sustained)")
    else:
        summary = (f"all {len(hits):,} files already up to date "
                   f"({_fmt_duration(elapsed)})")
    logger.info(f"EXIF cache rescan complete: {summary}. Cache now "
                f"{len(cache['immutable']):,} immutable + "
                f"{len(cache['mutable']):,} mutable entries.")
    stats.update({
        "read": total, "workers": n_workers, "elapsed": elapsed,
        "rate": rate, "hits": len(hits),
        "immutable": len(cache["immutable"]), "mutable": len(cache["mutable"]),
        "summary": summary,
    })
    return stats
