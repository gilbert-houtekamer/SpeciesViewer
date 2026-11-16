"""Day folders under a trip's picture root.

ONE JOB: recognise YYYY-MM-DD day folders and move between a trip's picture
root and its days.

INVARIANT: a "day" is a folder whose name is exactly YYYY-MM-DD; folders whose
name starts with "." (tool caches such as .preview_cache) are never a trip root.

Shared by the Create Trip, Import Trip and Read SD-cards dialogs.
"""
import os
import re
from pathlib import Path

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def strip_to_project_root(path: str) -> str:
    """Walk a project picture path up past any trailing day folder (YYYY-MM-DD)
    and/or tool-cache folder (.preview_cache etc.) so the SD-card target defaults
    to the project root — never a specific day or a cache subdir."""
    if not path:
        return path
    p = Path(path)
    while p.name and (p.name.startswith(".") or DAY_RE.match(p.name)):
        if p.parent == p:
            break
        p = p.parent
    return str(p)


def first_day_under(root: str) -> str:
    """The first YYYY-MM-DD day folder directly under `root`, or '' if none.
    WP8: [project] picturedirectory must stay a DAY (day-navigation relies on
    parent()==trip root), so when the user selects a trip ROOT we map it to a
    day here rather than storing the root itself."""
    try:
        days = sorted(d for d in os.listdir(root)
                      if DAY_RE.match(d) and os.path.isdir(os.path.join(root, d)))
    except OSError:
        return ""
    return os.path.join(root, days[0]) if days else ""
