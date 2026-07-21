"""Delete an album — the LOCAL half, plus a scan of what deletion would remove.

An album is safe to delete in a way a trip is not: it owns only metadata and
generated web files. The master's photographs are REFERENCED, never copied in,
so nothing irreplaceable lives here — the 18 MB of a fully published album is
HTML, small web JPGs and per-day CSVs. That is why deletion can be fully backed
up (a zip of the whole folder) and therefore reversible.

Two scopes:
  * LOCAL  — the album folder under the albums root. Always. Backed up first.
  * REMOTE — the published /trips/<slug>/ tree on the server. Optional, and done
             by the GUI layer with an open SFTP connection (kept out of here so
             this stays testable without a network). rmtree already invalidates
             the upload cache as it walks, and the album's own FTPcache.json goes
             with the folder — so no stale-cache trap survives a delete.

The MASTER IS NEVER TOUCHED. This module has no path into it.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("speciesviewer")


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def scan_album(album_root, config_web_root: str = "") -> dict:
    """What deleting this album would remove. Reads only; changes nothing.

    config_web_root is the album's published web path from config
    ([virttrip] virttripbiotawebroot, e.g. "/trips/grancanaria2023") — passed in
    because it is the SLUG, which can differ from the folder name, and the remote
    tree lives at the slug, not the folder.
    """
    root = Path(album_root)
    info = {
        "root": str(root),
        "name": root.name,
        "exists": root.is_dir(),
        "size_bytes": 0,
        "has_local_web": False,
        "web_root": (config_web_root or "").strip(),
        "picture_count": 0,
    }
    if not root.is_dir():
        return info
    info["size_bytes"] = _dir_size(root)
    info["has_local_web"] = (root / "web").is_dir()
    # A rough "how much work would be lost" number: per-day picture CSVs.
    review = root / "metadata" / "review"
    if review.is_dir():
        info["picture_count"] = sum(
            1 for _ in review.glob("*/data_pictures.csv"))
    return info


def backup_album(album_root, trash_dir=None) -> Path | None:
    """Zip the album folder into the albums-root .trash before deleting it.

    Returns the zip path, or None if there was nothing to back up. Keeps the
    delete reversible: unzip the archive back into the albums root and the album
    is whole again (it only ever referenced the master, which is untouched).
    """
    root = Path(album_root)
    if not root.is_dir():
        return None
    if trash_dir is None:
        trash_dir = root.parent / ".trash"
    trash_dir = Path(trash_dir)
    trash_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = trash_dir / f"{root.name}-{stamp}"
    # make_archive appends .zip; base_dir=root.name zips the folder itself, so the
    # archive unpacks straight back into the albums root.
    archive = shutil.make_archive(str(base), "zip",
                                  root_dir=str(root.parent), base_dir=root.name)
    logger.info(f"Album backed up to {archive}")
    return Path(archive)


def _rmtree_retry(path, attempts: int = 5) -> bool:
    """rmtree with backoff. Dropbox holds file handles for a moment while it
    syncs a deletion, so a first rmtree of a Dropbox folder often hits a
    transient WinError 32 that is gone a fraction of a second later. Returns True
    if the folder is gone."""
    import time

    path = Path(path)
    for i in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except OSError as e:
            if i == attempts - 1:
                logger.warning(f"could not remove {path.name} after {attempts} "
                               f"tries: {e}")
                return not path.exists()
            time.sleep(0.4 * (i + 1))          # 0.4, 0.8, 1.2, 1.6s
    return not path.exists()


def sweep_deleting(albums_root) -> int:
    """Remove leftover '.<name>.deleting' staging folders in the albums root.

    A staging folder is left when the recursive delete was blocked by a transient
    lock (Dropbox syncing) after the album had already been renamed away — the
    album is gone, only the junk lingers. Swept at the start of every delete so it
    never accumulates. The pattern (leading dot AND trailing .deleting) cannot
    match a real album folder.
    """
    root = Path(albums_root)
    if not root.is_dir():
        return 0
    swept = 0
    for p in root.glob(".*.deleting"):
        if p.is_dir() and _rmtree_retry(p):
            logger.info(f"swept leftover delete-staging: {p.name}")
            swept += 1
    return swept


def delete_local(album_root) -> str:
    """Remove the album folder. Returns "" on success, else an error message.

    RENAME-FIRST, then delete the renamed copy. On Windows a directory that holds
    an open file cannot be renamed, so this claims the whole folder atomically or
    fails having touched NOTHING — where a plain shutil.rmtree deletes depth-first
    and, meeting a lock partway, leaves a HALF-GONE album (a stray Explorer or
    PhotoLab window on one sub-folder did exactly that). Once the rename succeeds
    the album is gone under its real name even if the recursive delete then
    stumbles on a late lock; the leftovers sit in a hidden staging folder.
    """
    import os

    root = Path(album_root)
    if not root.is_dir():
        return ""
    # Clear any leftover staging from a prior interrupted delete before starting.
    sweep_deleting(root.parent)
    staging = root.with_name(f".{root.name}.deleting")
    if staging.exists():
        _rmtree_retry(staging)
    try:
        os.rename(root, staging)
    except OSError as e:
        return (f"{root.name} is in use, so nothing was removed. Close any "
                f"Explorer or DxO PhotoLab window showing the album or its "
                f"sub-folders, then delete again. [{e}]")
    # Retry the recursive delete — the album is already gone under its real name,
    # this just clears the renamed staging past a transient Dropbox lock. If it
    # STILL loses the race the staging lingers, but the next delete sweeps it.
    _rmtree_retry(staging)
    return "" if not root.exists() else f"{root.name} could not be fully removed."


def remote_web_target(config_web_root: str, remote_prefix: str) -> str:
    """The absolute server path of the published album, or "" when unknown.

    remote_prefix is Sftp.remotePrefix(); config_web_root is the /trips/<slug>
    from config. Kept here (not in the GUI) so the one place that builds this
    path is also the place that documents the slug-vs-folder distinction.
    """
    web = (config_web_root or "").strip()
    if not web:
        return ""
    return remote_prefix.rstrip("/") + "/" + web.strip("/")
