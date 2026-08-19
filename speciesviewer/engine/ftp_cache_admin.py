"""The upload caches: what each website has already sent to the web server.

Every repository (the master, each trip, each album) keeps its own
`admin/FTPcache.json` — a map of *remote target path* → (local file name, size
for JPGs / CRC32 for everything else). `sftplib.cachedUpload` consults it before
every put, which is why republishing a site re-sends only what actually changed
(typically ~3% of the files).

That makes it the one cache that decides whether a publish is a minute or an
hour, so it deserves to be visible: this module lists the caches per repository
with their entry count and size, and clears one — which costs nothing but a full
re-upload of that site on its next publish.

Read-only except for `clear()`, and deliberately free of Qt so the numbers can
be checked without a GUI.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("speciesviewer")

CACHE_NAME = "FTPcache.json"


def cache_path(repo_root: str) -> str:
    """Where a repository keeps its upload cache."""
    return os.path.join(repo_root, "admin", CACHE_NAME)


def scan_repo(repo_root: str, kind: str, name: str = "") -> dict:
    """One row: the repository, its cache file, and what is in it.

    `entries` is the number of remote paths already uploaded; it is None when the
    file exists but could not be read (truncated/corrupt JSON) — that is a real
    state to show, not a zero.
    """
    path = cache_path(repo_root)
    row = dict(name=name or os.path.basename(os.path.normpath(repo_root)),
               kind=kind, repo_root=repo_root, path=path,
               exists=False, entries=0, bytes=0, error="")
    try:
        row["bytes"] = os.path.getsize(path)
        row["exists"] = True
    except OSError:
        return row
    try:
        with open(path, "r", encoding="utf-8") as fh:
            row["entries"] = len(json.load(fh))
    except Exception as e:                      # corrupt / half-written cache
        row["entries"] = None
        row["error"] = str(e)
        logger.warning(f"upload cache unreadable: {path}: {e}")
    return row


def _is_repo(path: str) -> bool:
    """A repository is a directory named by a trip.ini or virttrip.ini — the same
    marker publish_index uses to decide which site a folder is."""
    return (os.path.isfile(os.path.join(path, "trip.ini"))
            or os.path.isfile(os.path.join(path, "virttrip.ini")))


def _enumerate(root: str, kind: str) -> list[dict]:
    """Every repository directly under `root`, whether or not it has a cache yet
    (no cache means the next publish uploads everything — worth seeing)."""
    rows = []
    if not root or not os.path.isdir(root):
        return rows
    for entry in sorted(os.listdir(root), key=str.lower):
        repo = os.path.join(root, entry)
        if os.path.isdir(repo) and _is_repo(repo):
            rows.append(scan_repo(repo, kind))
    return rows


def list_caches(master_root: str = "", trips_root: str = "",
                virttrips_root: str = "", extra: list[tuple] = ()) -> list[dict]:
    """All upload caches on this machine: master first, then trips, then albums.

    `extra` holds (repo_root, kind) pairs to include even when they sit outside
    the roots — the active trip/album on a machine that has no roots configured
    would otherwise be missing. Duplicates are dropped case-insensitively.
    """
    rows = []
    if master_root and os.path.isdir(master_root):
        rows.append(scan_repo(master_root, "master", os.path.basename(
            os.path.normpath(master_root))))
    rows += _enumerate(trips_root, "trip")
    rows += _enumerate(virttrips_root, "album")

    seen = {os.path.normcase(os.path.normpath(r["repo_root"])) for r in rows}
    for repo_root, kind in extra or ():
        key = os.path.normcase(os.path.normpath(repo_root or ""))
        if repo_root and key not in seen and os.path.isdir(repo_root):
            rows.append(scan_repo(repo_root, kind))
            seen.add(key)
    return rows


def totals(rows: list[dict]) -> tuple[int, int, int]:
    """(caches present, entries across them, bytes) — entries of an unreadable
    cache count as 0, its size still counts."""
    present = sum(1 for r in rows if r["exists"])
    entries = sum(r["entries"] or 0 for r in rows if r["exists"])
    size = sum(r["bytes"] for r in rows)
    return present, entries, size


def clear(repo_root: str) -> bool:
    """Forget what this site has uploaded. Returns False when there was nothing
    to forget. Purely a cache: the website itself is untouched, and the next
    publish simply re-sends every file."""
    path = cache_path(repo_root)
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    logger.info(f"Cleared upload cache: {path}")
    return True
