"""Shared culling key bindings for the picture viewers (Species Image Preview
grid, Rating Editor grid, and the Large Image View).

One definition, three call sites — each viewer calls handle_cull_key() at the
top of its keyPressEvent and supplies small callbacks for its own cursor model.
Keep docs/viewer_keybindings.md in sync with the bindings below.

Bindings (applied to the focused/current picture, then advance to the next):
  Ctrl+1 … Ctrl+5     set rating 1–5      (aliases q w e r t for the same)
  Ctrl+0              clear rating
  1 … 5               set colour          (Red Yellow Green Blue Purple)
  0                   clear colour
  s                   open the species dialog
  a                   copy the species the selection already agrees on (no dialog),
                      falling back to the 's' dialog when that is ambiguous
  c                   open the rating / colour dialog
  Ctrl+C / Ctrl+V     copy / paste the species name (trip-wide buffer)
  Ctrl+Shift+C / +V   copy / paste GPS + place (trip-wide buffer)
  Space               next picture (no change)
Keys are ignored while a text field (description / species) has focus.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLineEdit, QTextEdit, QAbstractSpinBox

# Colour-label order matching the UI swatches (1→Red … 5→Purple).
CULL_COLORS = ["Red", "Yellow", "Green", "Blue", "Purple"]

# Star aliases for people who find Ctrl+digits awkward: q w e r t → 1..5, p → 0.
_STAR_ALIAS = {Qt.Key.Key_Q: 1, Qt.Key.Key_W: 2, Qt.Key.Key_E: 3,
               Qt.Key.Key_R: 4, Qt.Key.Key_T: 5, Qt.Key.Key_P: 0}
_DIGIT = {Qt.Key.Key_1: 1, Qt.Key.Key_2: 2, Qt.Key.Key_3: 3,
          Qt.Key.Key_4: 4, Qt.Key.Key_5: 5}


def text_input_focused() -> bool:
    """True when a text-entry widget has focus, so the bindings don't hijack
    typing in a description or species field."""
    w = QApplication.focusWidget()
    return isinstance(w, (QLineEdit, QTextEdit, QAbstractSpinBox))


def parse_cull_key(event):
    """Map a key event to ('rating', n) | ('color', name) | ('next', None) | None."""
    k = event.key()
    mods = event.modifiers()
    ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
    shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
    # Copy/paste. Tested before the bare-letter bindings below so Ctrl+C is a
    # copy and not the rating dialog, and before the Ctrl+digit ratings so the
    # shift variants can't be read as an unmodified key.
    if ctrl and k == Qt.Key.Key_C:
        return ("copy_gps" if shift else "copy_species", None)
    if ctrl and k == Qt.Key.Key_V:
        return ("paste_gps" if shift else "paste_species", None)
    if k == Qt.Key.Key_Space and not ctrl:
        return ("next", None)
    if not ctrl and k == Qt.Key.Key_S:
        return ("species", None)
    if not ctrl and k == Qt.Key.Key_A:
        return ("species_quick", None)
    if not ctrl and k == Qt.Key.Key_C:
        return ("rating_dialog", None)
    if ctrl and k in _DIGIT:
        return ("rating", _DIGIT[k])
    if ctrl and k == Qt.Key.Key_0:
        return ("rating", 0)
    if not ctrl and k in _STAR_ALIAS:
        return ("rating", _STAR_ALIAS[k])
    if not ctrl and k in _DIGIT:
        return ("color", CULL_COLORS[_DIGIT[k] - 1])
    if not ctrl and k == Qt.Key.Key_0:
        return ("color", "")
    return None


def handle_cull_key(event, *, order, current_key, item_for_key, set_cursor, refresh_key,
                    open_species=None, open_rating=None, quick_species=None,
                    clipboard=None, after_edit=None) -> bool:
    """Apply a culling key to the current picture and advance. Returns True if the
    event was a culling key (and is now consumed), False to let the caller handle it.

    order        - list of keys in display order
    current_key  - the focused/current key (may be None or stale)
    item_for_key - key -> PictureItem
    set_cursor   - key -> focus/scroll/navigate to that picture (the 'advance')
    refresh_key  - key -> repaint that picture's card/thumbnail after an edit
    open_species - callback for 's' (open the species dialog for the selection/current)
    open_rating  - callback for 'c' (open the rating/colour dialog likewise)
    quick_species- callback for 'a' (copy the selection's agreed species without a
                   dialog; it decides for itself when to fall back to open_species).
                   A viewer that doesn't supply one falls through to 's'.
    after_edit   - callback(key) fired only when a rating/colour was actually
                   written, after the repaint and the advance. A grid sorted by
                   Rate uses it to re-sort, so a keyboard edit reorders the grid
                   exactly as clicking a star does; a viewer in file order
                   simply leaves it unset.
    clipboard    - callback(what) for the four copy/paste keys, `what` being one
                   of copy_species / paste_species / copy_gps / paste_gps. A
                   viewer that doesn't supply one simply ignores them, rather
                   than letting Ctrl+C fall through to something unrelated.
    """
    if text_input_focused():
        return False
    parsed = parse_cull_key(event)
    if parsed is None:
        return False
    action, value = parsed
    if action == "species":
        if open_species:
            open_species()
        return True
    if action == "species_quick":
        # 'a' degrades to 's' rather than doing nothing: a viewer that hasn't
        # wired the quick path yet should still name the selection.
        cb = quick_species or open_species
        if cb:
            cb()
        return True
    if action == "rating_dialog":
        if open_rating:
            open_rating()
        return True
    if action in ("copy_species", "paste_species", "copy_gps", "paste_gps"):
        # Consumed either way: a viewer without a clipboard must still swallow
        # Ctrl+C rather than pass it to a parent that might do something else
        # with it entirely.
        if clipboard:
            clipboard(action)
        return True
    if not order:
        return True
    cur = current_key if current_key in order else order[0]

    def advance():
        set_cursor(order[min(order.index(cur) + 1, len(order) - 1)])

    if action == "next":
        advance()
        return True

    try:
        item = item_for_key(cur)
    except Exception:
        item = None
    applied = False
    if item is not None:
        try:
            if action == "rating" and getattr(item, "can_edit_rating", False):
                item.set_rating(value)
                applied = True
            elif action == "color" and getattr(item, "can_edit_color", False):
                item.set_color(value)
                applied = True
        except Exception:
            applied = False
    if applied:
        refresh_key(cur)
        advance()
        if after_edit:
            after_edit(cur)
    return True


def handle_grid_arrows(event, *, order, current_key, columns, set_cursor) -> bool:
    """Move the grid cursor: Left/Right by one, Up/Down by a full row
    (`columns`), Ctrl+Home/Ctrl+End to the first/last picture. Returns True if
    the key was handled.

    Ctrl+Home/End move the CURSOR, not just the scrollbar, so the grid stays
    keyboard-navigable from where you land — arrow keys continue from the first
    or last picture rather than from wherever the cursor was left behind."""
    if text_input_focused() or not order:
        return False
    k = event.key()
    ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
    if ctrl and k in (Qt.Key.Key_Home, Qt.Key.Key_End):
        set_cursor(order[0] if k == Qt.Key.Key_Home else order[-1])
        return True
    step = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1,
            Qt.Key.Key_Up: -max(1, columns), Qt.Key.Key_Down: max(1, columns)}.get(k)
    if step is None:
        return False
    cur = current_key if current_key in order else order[0]
    j = max(0, min(order.index(cur) + step, len(order) - 1))
    set_cursor(order[j])
    return True


def handle_scroll_ends(event, scroll_area) -> bool:
    """Ctrl+Home / Ctrl+End for a grid that has no cursor model — the Biota
    Browser's taxon cards, which are navigated by clicking, not by a focused
    item. Here the pair can only mean "scroll to the top / bottom".

    Split from handle_grid_arrows on purpose: a cursor grid must MOVE the cursor
    (see above), and quietly doing something different under the same keys in
    the two kinds of grid would be worse than having two functions."""
    if text_input_focused() or scroll_area is None:
        return False
    if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
        return False
    bar = scroll_area.verticalScrollBar()
    if event.key() == Qt.Key.Key_Home:
        bar.setValue(bar.minimum())
        return True
    if event.key() == Qt.Key.Key_End:
        bar.setValue(bar.maximum())
        return True
    return False
