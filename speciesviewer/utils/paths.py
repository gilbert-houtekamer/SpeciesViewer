# utils/paths.py — cross-platform path handling (Windows + macOS).
#
# The rule (see path_portability_plan.md):
#   * Anything STORED in a CSV/.ini or sent over the wire uses ONE canonical form:
#     forward slashes, relative to a known root  (to_stored / relativize_path).
#   * Anything READ back is interpreted host-INDEPENDENTLY and turned into an
#     OS-native path only at the filesystem boundary                (to_fs_path).
#   * Never call Path()/os.path.* directly on a stored value, and never hardcode
#     '\\' or a drive letter in source.
#
# Why host-independent parsing matters: a Windows-authored value like
# "D:\\CurrentPictures\\x\\y.jpg" is a *single filename with backslashes* to macOS
# (Path(...).drive == "", .name == the whole string). Detecting the drive with
# PureWindowsPath makes the same value parse correctly on both OSes.

import os
from pathlib import Path, PureWindowsPath


def is_windows_absolute(stored: str) -> bool:
    """True if `stored` is an absolute Windows path — a drive letter ('D:\\…' or
    'D:/…') or a UNC share ('\\\\server\\share\\…'). Uses PureWindowsPath so the
    answer is the same on Windows and macOS (the host-native Path would report
    no drive on macOS and mis-classify the value as relative)."""
    if not stored:
        return False
    return bool(PureWindowsPath(stored).drive)


def to_stored(p) -> str:
    """Canonical STORED form for CSV/.ini/wire: forward slashes, no backslashes."""
    return str(p).replace("\\", "/")


def display_path(p) -> str:
    """Native-separator form for logs/UI — avoids the mixed '\\…/…\\' look."""
    return os.path.normpath(str(p))


def to_fs_path(stored: str, root: str | None = None) -> Path:
    """Interpret a STORED path and return an OS-native Path for THIS machine.

    - An absolute Windows path (drive letter / UNC) is returned as-is; it only
      resolves on the machine that actually has that drive (a legacy row — the
      data-probe flags these so they can be relativized).
    - Otherwise the value is treated as relative to `root` (leading '/' or '\\'
      stripped, backslashes normalised), joined and returned as an OS Path. With
      no root, the normalised relative Path is returned."""
    if not stored:
        return Path(stored)
    if is_windows_absolute(stored):
        return Path(stored)
    rel = str(stored).replace("\\", "/").lstrip("/")
    return (Path(root) / rel) if root else Path(rel)
