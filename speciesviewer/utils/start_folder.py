"""Where a file or folder dialog opens.

ONE job: turn a few hopeful candidates into a folder that exists and makes
sense to the user. Invariant: never the program's own folder. An empty start
makes Qt open the current directory, which is wherever SpeciesViewer was
launched from — d:\\code\\SpeciesViewer for him, a program folder for anyone
else. Nothing there is ever what the user is looking for.

Order: the caller's candidates (each walked up to the nearest folder that
exists), then the open trip's picture folder, then the CurrentPictures root,
then the drive root (C:\\) — on a Mac the home folder.
"""

import sys
from pathlib import Path

# speciesviewer/utils/start_folder.py → the checkout or install folder.
_PROGRAM_DIR = Path(__file__).resolve().parents[2]


def _existing(candidate) -> Path | None:
    """The nearest existing folder at or above `candidate`, or None. A file
    gives its folder. A relative or empty path is refused: it would resolve
    against the current directory, the very thing this module avoids."""
    if not candidate:
        return None
    try:
        p = Path(str(candidate).strip())
    except (TypeError, ValueError):
        return None
    if not str(p) or not p.is_absolute():
        return None
    while not p.is_dir():
        if p.parent == p:
            return None
        p = p.parent
    return p


def _inside_program(p: Path) -> bool:
    try:
        return p.resolve().is_relative_to(_PROGRAM_DIR)
    except OSError:
        return False


def _fallbacks() -> list:
    """Trip picture folder, CurrentPictures root, drive root — read late so a
    trip switched since start-up counts."""
    out = []
    try:
        from utils.config import _config, get_currentpictures_root
        day = _config.get("project", "picturedirectory", fallback="").strip()
        if day:
            out.append(Path(day).parent)   # the day's parent = the trip's pictures
        out.append(get_currentpictures_root())
    except Exception:
        pass
    home = Path.home()
    out.append(home.anchor if sys.platform == "win32" else home)
    return out


def start_folder(*candidates) -> str:
    """The folder a dialog should open in. Never "" and never the program folder."""
    for c in list(candidates) + _fallbacks():
        p = _existing(c)
        if p is not None and not _inside_program(p):
            return str(p)
    return str(Path.home())


def _removable_roots() -> list:
    """Drive roots of inserted removable media (SD-cards, USB sticks). Windows
    only — elsewhere a card is a folder under /Volumes or /media and the
    ordinary fallbacks do. Empty slots are left out: a card reader with no
    card reports DRIVE_REMOVABLE but its root is not a folder."""
    if sys.platform != "win32":
        return []
    import ctypes
    import string
    roots = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            root = f"{letter}:\\"
            if mask & (1 << i) and ctypes.windll.kernel32.GetDriveTypeW(root) == 2 \
                    and Path(root).is_dir():
                roots.append(Path(root))
    except Exception:
        return []
    return roots


def _drive(p) -> str:
    """The drive a path lives on (H: for anything on the card in H:)."""
    return Path(str(p)).anchor.upper()


def card_folder(*candidates, not_on=None) -> str:
    """Where a "choose the card" dialog opens: the caller's candidates (the card
    used last), else an inserted card — one with a DCIM folder first — that is
    NOT on the drive of `not_on` (the keeper card sits in a reader too), else
    the usual start_folder fallbacks."""
    for c in candidates:
        p = _existing(c)
        if p is not None and not _inside_program(p):
            return str(p)
    skip = _drive(not_on) if not_on else None
    cards = [r for r in _removable_roots() if _drive(r) != skip]
    cards.sort(key=lambda r: not (r / "DCIM").is_dir())
    if cards:
        return str(cards[0])
    return start_folder()


def start_file(folder_candidates, filename: str) -> str:
    """For a Save dialog: the start folder with a suggested file name."""
    if not isinstance(folder_candidates, (list, tuple)):
        folder_candidates = [folder_candidates]
    return str(Path(start_folder(*folder_candidates)) / filename)
