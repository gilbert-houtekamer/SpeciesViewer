"""Central, override-aware resolution of the per-machine cache locations.

Every derived/throwaway store the app keeps on the local disk (decoded full-res
JPEGs, extracted preview thumbnails, the EXIF read cache) resolves its directory
through here so that:

  * there is ONE definition of the per-OS app-data root, and
  * the user can relocate any individual cache to another disk via the Manage
    Caches dialog (stored as a `[caches]` override in the ini).

Defaults live under the per-OS app-data dir (SSD, never Dropbox):
    Windows  %LOCALAPPDATA%/SpeciesViewer/<name>
    macOS    ~/Library/Application Support/SpeciesViewer/<name>
    Linux    $XDG_DATA_HOME or ~/.local/share/SpeciesViewer/<name>

NB: the EXIF cache used to live under <master>/metadata/cache (i.e. on Dropbox)
so it was shared across machines. It now defaults to the local app-data dir like
the others — per-machine, warmed once. The master SQLite DB has its own location
logic in db.master_db (it is not a throwaway cache and is managed separately).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Logical cache kinds → default sub-directory name under app_data_dir().
_DEFAULT_SUBDIR = {
    "fullres": "fullres_cache",
    "preview": "preview_cache",
    "exif": "exif_cache",
    # The XMP sidecar cache made the SAME move the EXIF cache did, and for the same
    # reason (see the module note above): it used to be written beside the pictures,
    # which for the master means inside Dropbox. A sync client opens each file the
    # moment we write it, so the next atomic replace onto it failed with Access
    # Denied — the save was lost and that folder then re-read every XMP through
    # exiftool on every open. A per-machine throwaway cache does not belong in
    # synced storage.
    "sidecar": "sidecar_cache",
}

# Logical cache kinds → [caches] ini key holding a user override (absolute path).
_CONFIG_KEY = {
    "fullres": "fullres_dir",
    "preview": "preview_dir",
    "exif": "exif_dir",
    "sidecar": "sidecar_dir",
}


def app_data_dir() -> Path:
    """Per-OS user app-data directory (SSD, NOT in Dropbox)."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SpeciesViewer"
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "SpeciesViewer"


def default_cache_root(kind: str) -> Path:
    return app_data_dir() / _DEFAULT_SUBDIR[kind]


def _override(kind: str) -> str:
    from utils.config import _config
    return _config.get("caches", _CONFIG_KEY[kind], fallback="").strip()


def cache_root(kind: str) -> Path:
    """Current directory for `kind`: the user override if set, else the default."""
    ov = _override(kind)
    return Path(ov) if ov else default_cache_root(kind)


def is_overridden(kind: str) -> bool:
    return bool(_override(kind))


def set_cache_root(kind: str, path: str | None) -> None:
    """Persist (or clear, when path is falsy / equals the default) the override."""
    from utils.config import _config, persist_config
    if not _config.has_section("caches"):
        _config.add_section("caches")
    if path and Path(path).resolve() != default_cache_root(kind).resolve():
        _config.set("caches", _CONFIG_KEY[kind], str(path))
    else:
        _config.remove_option("caches", _CONFIG_KEY[kind])
    persist_config()


def relocate_cache(kind: str, new_root, move_existing: bool = True) -> int:
    """Point `kind` at `new_root`, optionally moving the existing contents there.

    Returns the number of top-level entries moved. Cross-device moves are handled
    by shutil.move. Existing entries of the same name in the destination are
    overwritten so a relocate is idempotent.
    """
    new_root = Path(new_root)
    old_root = cache_root(kind)
    moved = 0
    if (move_existing and old_root.exists()
            and old_root.resolve() != new_root.resolve()):
        new_root.mkdir(parents=True, exist_ok=True)
        for entry in list(old_root.iterdir()):
            dest = new_root / entry.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest, ignore_errors=True)
                else:
                    try:
                        dest.unlink()
                    except OSError:
                        pass
            shutil.move(str(entry), str(dest))
            moved += 1
    set_cache_root(kind, str(new_root))
    return moved


def display_path(p) -> str:
    """Human-readable path that shows the platform app-data token when `p` lives
    under the default app-data root (e.g. %LOCALAPPDATA%\\SpeciesViewer\\...)."""
    p = Path(p)
    root = app_data_dir()
    try:
        rel = p.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return str(p)
    if sys.platform.startswith("win"):
        token = "%LOCALAPPDATA%"
        head = str(Path(token) / "SpeciesViewer" / rel)
    elif sys.platform == "darwin":
        head = str(Path("~/Library/Application Support/SpeciesViewer") / rel)
    else:
        head = str(Path("$XDG_DATA_HOME/SpeciesViewer") / rel)
    return head
