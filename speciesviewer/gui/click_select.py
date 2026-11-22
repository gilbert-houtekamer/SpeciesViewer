"""Standard Windows click selection in the picture grids — as an EXTRA.

ONE JOB: turn a Ctrl+click or Shift+click on a card into the grid's new
selection. Pure logic, no Qt: the host passes the display order, its current
selection and which keys were held, and applies what comes back.

INVARIANT: a click WITHOUT Ctrl/Shift never changes the selection. It only
moves the cursor, as it always did — so a selection built without holding a
key (right-click ▸ Add to selection, `[` `]`, Ctrl+A) survives a plain click.
Holding a key is never the only way to select (backlog 1.19).

  Ctrl+click        add the picture to the selection, or take it out
  Shift+click       select the range from the anchor to this picture
                    (replaces the selection, like `[` … `]`)
  Ctrl+Shift+click  add that range to the selection

The anchor is the picture last clicked without Shift. If the cursor moved
since (arrow keys), the cursor is the anchor instead — as in Explorer.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClickResult:
    selected: set                       # the new selection
    brackets: tuple | None = None       # new ([, ]) pair, or None = leave as is


class ClickSelector:
    """Keeps the Shift-click anchor for one grid."""

    def __init__(self):
        self._anchor: str | None = None
        self._cursor_then: str | None = None   # cursor right after our last click

    def _anchor_for(self, cursor):
        # The cursor moved (keys) since our last click → it is the anchor now.
        if self._anchor is None or cursor != self._cursor_then:
            return cursor
        return self._anchor

    def click(self, order, selected, cursor, key, *, ctrl: bool, shift: bool):
        """Return a ClickResult, or None when the selection stays untouched.

        order    — keys in display order
        selected — the current selection (not modified)
        cursor   — the picture that had the cursor BEFORE this click
        key      — the clicked picture (it gets the cursor afterwards)
        """
        anchor = self._anchor_for(cursor)
        if not shift:
            self._anchor = key
        else:
            self._anchor = anchor
        self._cursor_then = key

        if not ctrl and not shift:
            return None
        if not shift:                                   # Ctrl+click: toggle
            new = set(selected)
            if key in new:
                new.discard(key)
            else:
                new.add(key)
            return ClickResult(new)

        span = _range(order, anchor, key)
        if ctrl:                                        # Ctrl+Shift: add range
            return ClickResult(set(selected) | span)
        left = anchor if anchor in span else key
        return ClickResult(span, brackets=(left, key))


def _range(order, anchor, key) -> set:
    """Inclusive range anchor..key in display order; just {key} when the anchor
    is unknown or no longer shown (filtered out)."""
    order = list(order)
    if key not in order:
        return set()
    if anchor is None or anchor not in order:
        return {key}
    a, b = order.index(anchor), order.index(key)
    if a > b:
        a, b = b, a
    return set(order[a:b + 1])
