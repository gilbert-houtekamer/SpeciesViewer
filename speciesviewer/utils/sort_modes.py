"""Shared sort modes for the Days/Trips/Albums lists (ch.15 follow-up).

One control, reused by all three lists (widgets/TripInfoPanel.py,
widgets/DaysPanel.py, widgets/VirtTripInfoPanel.py) via gui/mainwindow.py —
see the memory/backlog thread for why it's one shared control rather than
three independent ones.
"""
from pathlib import Path

from utils.gui_config import usage_key

SORT_ALPHA_ASC = "alpha_asc"
SORT_ALPHA_DESC = "alpha_desc"
SORT_RECENT = "recent"
SORT_FREQUENT = "frequent"

ALL_MODES = (SORT_ALPHA_ASC, SORT_ALPHA_DESC, SORT_RECENT, SORT_FREQUENT)
DEFAULT_MODE = SORT_ALPHA_ASC


def _alpha_key(path):
    return Path(path).name.lower()


def sort_items(paths, mode, usage_map):
    """Return `paths` (an iterable of path-like items) reordered per `mode`.

    Recent/Frequent fall back to alphabetical for anything with no recorded
    usage yet (a brand-new feature has no history to sort by — those items
    land at the end, alphabetically among themselves, rather than in
    arbitrary disk order)."""
    items = list(paths)
    if mode == SORT_ALPHA_DESC:
        return sorted(items, key=_alpha_key, reverse=True)
    if mode == SORT_RECENT:
        return sorted(
            items,
            key=lambda p: (-usage_map.get(usage_key(p), {}).get("last_opened", 0.0),
                            _alpha_key(p)),
        )
    if mode == SORT_FREQUENT:
        return sorted(
            items,
            key=lambda p: (-usage_map.get(usage_key(p), {}).get("open_count", 0),
                            _alpha_key(p)),
        )
    return sorted(items, key=_alpha_key)  # SORT_ALPHA_ASC, and any unknown mode
