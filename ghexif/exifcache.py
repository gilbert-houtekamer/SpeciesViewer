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

logger = logging.getLogger("speciesviewer")

CACHE_VERSION = 1
CACHE_BACKUPS = 3

# Bytes never change → name+size key (enables cross-folder / cross-copy reuse).
IMMUTABLE_EXTENSIONS = {"orf", "nef", "cr2", "cr3", "arw", "rw2", "raf",
                        "dng", "heic"}
# exiftool does NOT emit rows for these in a recursive scan (verified: 1482 .dop
# and the .ini files produced 0 rows on Kreta). Never feed them — an explicit
# file list would otherwise create spurious rows the dir-scan never produced.
DENY_EXTENSIONS = {"dop", "ini", "txt", "ico", "html", "lrcat", "lrdata"}
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
    """<master>/metadata/cache/exif_cache.json — the admin area, alongside the
    other metadata/ subtrees (active, review). Used by day/trip Read Directory;
    stays small."""
    return os.path.join(master_repository(config), "metadata", "cache",
                        "exif_cache.json")


def master_cache_path(config):
    """Separate cache for the master-wide EXIF scan (Master menu > Rescan).
    Kept distinct from exif_cache.json so warming the full master (~159k files,
    ~89 MB) never taxes the small day/trip Read Directory cache."""
    return os.path.join(master_repository(config), "metadata", "cache",
                        "exif_cache_master.json")


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


def partition(root, cache, fingerprint):
    """Walk `root` once; return (hits, misses, stats).
        hits   – cached exif dicts, path keys re-injected for the on-disk file
        misses – absolute paths that must go to exiftool
        stats  – dict of per-bucket hit/miss counts for logging
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
        for name in files:
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


def rescan(root, cache_path, chunk=2000):
    """Warm/refresh a cache file by reading `root` with exiftool: read only the
    files not already cached (or changed), in chunks to bound memory, then save
    once. No CSV, no XMP writes — a pure cache-population pass for the Master
    menu's Rescan. Progress is logged so the GUI spinner has running feedback.
    First full-master pass is slow (~1 h / 159k files); later passes re-read
    only what changed."""
    import exiftool
    from ghexif.selected_fields import SELECTED_FIELDS
    fp = field_fingerprint(SELECTED_FIELDS)
    cache = load_cache(cache_path)
    hits, misses, stats = partition(root, cache, fp)
    logger.info(f"EXIF cache rescan of {root}: "
                f"{len(hits)} already cached, {len(misses)} to read.")
    if misses:
        # No logger= here: pyexiftool would dump every -stay_open command into
        # the GUI log; our own per-chunk progress lines below are the feedback.
        with exiftool.ExifToolHelper(
                common_args=["-G", "-charset", "utf8", "-charset", "filename=utf8"],
                encoding="utf-8", check_execute=False) as et:
            for i in range(0, len(misses), chunk):
                fresh = et.get_metadata(misses[i:i + chunk], params=SELECTED_FIELDS)
                update(cache, fresh, fp)
                logger.info(f"EXIF cache rescan: read "
                            f"{min(i + chunk, len(misses))}/{len(misses)} files")
        save_cache(cache_path, cache)
    logger.info(f"EXIF cache rescan complete: {len(cache['immutable'])} immutable "
                f"+ {len(cache['mutable'])} mutable entries in "
                f"{os.path.basename(cache_path)}.")
    return stats
