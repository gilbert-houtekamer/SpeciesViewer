"""Absolute paths for the tiny per-machine files one process writes and another
process reads.

ONE JOB: give every process the same absolute path for such a file, whatever
directory it was started from.

The batch steps run as separate processes from the GUI that launches them, so
they cannot return a value; a step writes the URL it just published to a small
file and the GUI reads it back to offer "open in browser". That handoff used a
bare relative filename, which resolves against the *current working directory* —
so it only worked while both processes happened to start in the repo root, and
it dropped throwaway files into the source tree.

These are runtime state, not caches: they are never relocatable through Manage
Caches, so they deliberately do not go through cache_paths.cache_root().
"""

from __future__ import annotations

from pathlib import Path

from utils.masters import ACTIVE_DIR

# Shared by writer and reader — a literal typed twice is a silent breakage
# (the writer publishes, the reader finds nothing and quietly opens the site root).
LAST_BLOG_URL = "last_blog_url.txt"
LAST_WEBSITE_URL = "last_website_url.txt"


def runtime_path(name: str) -> Path:
    """Absolute path to the per-machine runtime file `name`, creating its
    directory. Lives beside the caches under the per-OS app-data dir
    (%LOCALAPPDATA%/SpeciesViewer on Windows, ~/Library/Application Support
    on macOS) — local, never Dropbox, never the source tree. Per master
    (utils/masters.py): svconfig.ini carries that master's paths."""
    d = ACTIVE_DIR / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


# The batch handoff: the GUI writes it (utils/batch_config.generate_svconfig_ini)
# just before a step runs, and reads it straight back. It carries the SFTP
# password, which is one more reason it must not land in the source tree.
SVCONFIG = "svconfig.ini"


def svconfig_path() -> Path:
    """Absolute path of svconfig.ini — the one name writer, readers and the
    batch scripts' --config default all use."""
    return runtime_path(SVCONFIG)
