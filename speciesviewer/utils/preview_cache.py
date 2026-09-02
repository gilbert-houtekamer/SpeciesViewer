"""Preview-cache layout, LRU stamping and hygiene.

The preview cache is derived data: one extracted/decoded JPG per source picture,
at `preview_cache/<sha1(source folder)>/<stem>.jpg`. It is a pure optimization —
anything dropped from here costs exactly one re-extract, never data. That is what
makes it safe to prune on a timer.

It only ever GREW. No TTL, and no sweep when the source pictures were deleted, so
it kept previews for culled, deleted and retired trip RAWs indefinitely. Two
mechanisms fix that, and they cover different holes:

  * **LRU (90 days).** The cache file's MTIME is its last-USED stamp: `touch()` is
    called on every hit, at most once a day per file (day granularity is plenty
    for a 90-day TTL and stops a grid scroll from becoming a utime storm). So a
    preview you still look at never ages out. Same net, and the same reasoning, as
    the EXIF cache's last-seen sweep — see `engine.exifcache.CACHE_TTL_DAYS`.

  * **Orphan sweep.** The folder hash is ONE-WAY, so nothing could ask "does this
    entry's source still exist?". Each cache folder now carries a `source.txt`
    naming the folder it was built for, which is what makes the question
    answerable at all: source folder gone → the whole cache folder is dead; source
    file gone → that one preview is dead. This catches pictures deleted OUTSIDE
    the app (Explorer, PhotoLab) too, which a delete-time hook never can.

Folders written before the marker existed answer no questions, so they simply age
out under the LRU — an honest migration rather than a guess about their source.

`drop_for_files()` is the third, precise path: the places that delete pictures
themselves (trip delete, colour/map delete) drop those previews immediately rather
than waiting for a sweep to notice.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from pathlib import Path

logger = logging.getLogger("speciesviewer")

# Deliberately the same 90 days as engine.exifcache.CACHE_TTL_DAYS: comfortably
# longer than a trip stays open, and the only cost of a wrong eviction is one
# re-extract. Kept as its own constant because the two caches are pruned by
# different code on different triggers — a shared import would tie them together
# for no reason beyond a coincidence of value.
PREVIEW_TTL_DAYS = 90

SOURCE_MARKER = "source.txt"

# Cache folders whose marker we have already written this session — keeps the hot
# path (one call per card) from re-stat'ing the marker on every thumbnail.
_marked: set[str] = set()


def cache_root() -> Path:
    from utils.cache_paths import cache_root as _root
    return _root("preview")


def cache_dir_for(src_folder) -> Path:
    """Cache directory for `src_folder` — a short SHA-1 of the normalised folder
    path, so identical stems from different trip days never collide."""
    folder_hash = hashlib.sha1(
        os.path.normcase(str(src_folder)).encode()
    ).hexdigest()[:16]
    return cache_root() / folder_hash


def ensure_dir(cache_dir: Path, src_folder) -> None:
    """Create the cache folder and record WHICH source folder it belongs to.

    The marker is only written when `cache_dir` is genuinely the hash of
    `src_folder` — a caller that passes an explicit cache root (its own folder for
    a mixed set of sources) gets no marker rather than a false one. An entry that
    cannot prove its source is pruned by age alone, which is the safe answer."""
    cache_dir = Path(cache_dir)
    # The mkdir stays unconditional (it is one cheap syscall, and it is what the
    # cache did before): Clear Preview Cache can rmtree the whole tree while a
    # viewer is open, and the next extract must simply recreate its folder rather
    # than write into a directory that is no longer there. Only the MARKER check is
    # memoised — that is the part that would otherwise cost a stat per thumbnail.
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(str(cache_dir))
    if key in _marked:
        return
    _marked.add(key)
    marker = cache_dir / SOURCE_MARKER
    if marker.exists() or cache_dir != cache_dir_for(src_folder):
        return
    try:
        marker.write_text(str(src_folder), encoding="utf-8")
    except OSError as e:
        logger.debug(f"preview cache: could not mark {cache_dir}: {e}")


def forget() -> None:
    """Drop the memo of which folders carry a marker. Called after the cache tree
    is deleted wholesale, so the folders that get recreated are marked again
    instead of silently reverting to unmarked (age-only) for the rest of the
    session."""
    _marked.clear()


def touch(path: Path) -> None:
    """Stamp a cache hit — this is what makes the mtime a LAST-USED time rather
    than a written-at time, and so what makes the LRU an LRU. Skipped when the
    file was already used today (a full grid would otherwise utime every card)."""
    try:
        if time.time() - path.stat().st_mtime < 86400:
            return
        os.utime(path, None)
    except OSError:
        pass


def drop_for_files(paths) -> int:
    """Drop the cached previews of source pictures WE are deleting. Returns the
    number removed. A JPG has no cache entry (it is its own preview) and a stem
    with no entry is simply a miss, so this is safe to call with any file list."""
    import glob as _glob
    removed = 0
    for p in paths:
        p = Path(p)
        d = cache_dir_for(p.parent)
        # the full-size preview <stem>.jpg AND any scaled thumbnails
        # <stem>@<width>.jpg written beside it (see gui/thumb_cache.py)
        targets = [d / (p.stem + ".jpg")]
        targets += [Path(x) for x in _glob.glob(str(d / (_glob.escape(p.stem) + "@*.jpg")))]
        for cached in targets:
            try:
                if cached.is_file():
                    cached.unlink()
                    removed += 1
            except OSError:
                pass
    if removed:
        logger.info(f"preview cache: dropped {removed} preview(s) for deleted pictures.")
    return removed


def _source_folder(cache_dir: Path):
    """The source folder this cache dir was built for, or None when unmarked."""
    try:
        txt = (cache_dir / SOURCE_MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(txt) if txt else None


def _drive_mounted(folder: Path) -> bool:
    """Is the volume this folder lives on actually present? `D:\\` on Windows, `/`
    or the mount point on macOS. A missing folder on a mounted drive was deleted;
    a missing folder on an absent drive tells us nothing."""
    anchor = folder.anchor
    if not anchor:
        return True                     # relative path — no volume to check
    return os.path.exists(anchor)


def _source_stems(folder: Path) -> set[str]:
    """Lower-cased stems of every file in the source folder — ONE listing, not a
    glob per cached preview (a day folder holds thousands of files)."""
    try:
        return {p.stem.lower() for p in folder.iterdir()}
    except OSError:
        return set()


def scan(ttl_days: int = PREVIEW_TTL_DAYS) -> dict:
    """What a prune WOULD remove — a dry run, so the count can be shown before
    anything is deleted.

    Returns {"files": [Path], "bytes": int, "expired": int, "orphaned": int,
             "dead_dirs": [Path]}. A file can be both expired and orphaned; it is
    counted under `orphaned`, which is the more specific reason."""
    root = cache_root()
    out = {"files": [], "bytes": 0, "expired": 0, "orphaned": 0, "dead_dirs": []}
    if not root.is_dir():
        return out
    cutoff = time.time() - ttl_days * 86400
    for cache_dir in root.iterdir():
        if not cache_dir.is_dir():
            continue
        src = _source_folder(cache_dir)
        # GUARDRAIL: an unplugged drive looks exactly like a deleted trip — the
        # source folder is "gone" either way. Deleting the cache for pictures that
        # are merely OFFLINE costs no data, but it silently throws away a warm cache
        # that then has to be re-extracted picture by picture. If the drive itself
        # is not mounted we cannot conclude anything, so we conclude nothing.
        if src is not None and not _drive_mounted(src):
            continue
        # The whole folder is dead: its source is gone (a deleted/retired trip).
        if src is not None and not src.is_dir():
            dead = list(cache_dir.glob("*.jpg"))
            for f in dead:
                try:
                    out["bytes"] += f.stat().st_size
                except OSError:
                    continue
                out["files"].append(f)
                out["orphaned"] += 1
            if dead:
                out["dead_dirs"].append(cache_dir)
            continue
        stems = _source_stems(src) if src is not None else None
        for f in cache_dir.glob("*.jpg"):
            try:
                st = f.stat()
            except OSError:
                continue
            orphan = stems is not None and f.stem.lower() not in stems
            if not orphan and st.st_mtime >= cutoff:
                continue
            out["files"].append(f)
            out["bytes"] += st.st_size
            if orphan:
                out["orphaned"] += 1
            else:
                out["expired"] += 1
    return out


def stats(ttl_days: int = PREVIEW_TTL_DAYS) -> tuple[int, int]:
    """(count, bytes) a prune would reclaim — for the Manage Caches button."""
    rep = scan(ttl_days)
    return len(rep["files"]), rep["bytes"]


def prune(ttl_days: int = PREVIEW_TTL_DAYS) -> tuple[int, int]:
    """Delete the stale entries. Returns (files_removed, bytes_reclaimed)."""
    rep = scan(ttl_days)
    removed = freed = 0
    for f in rep["files"]:
        try:
            size = f.stat().st_size
            f.unlink()
            removed += 1
            freed += size
        except OSError:
            pass
    for d in rep["dead_dirs"]:
        try:
            if not any(p.suffix.lower() == ".jpg" for p in d.iterdir()):
                shutil.rmtree(d, ignore_errors=True)   # marker + empty folder
                _marked.discard(os.path.normcase(str(d)))
        except OSError:
            pass
    if removed:
        logger.info(
            f"preview cache: pruned {removed:,} entries ({freed / 1e6:.0f} MB) — "
            f"{rep['orphaned']:,} whose pictures are gone, {rep['expired']:,} unused "
            f"> {ttl_days} days.")
    return removed, freed
