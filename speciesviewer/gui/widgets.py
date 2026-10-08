"""Widgets that behave correctly by default.

ONE job: make the right thing the easy thing, for the cases where Qt's default
is a trap rather than a preference.

So far that is two cases: wrapped labels, and a combo list whose type-ahead
lands the match at the top.
"""

from PyQt6.QtCore import QObject, QEvent, QTimer
from PyQt6.QtWidgets import QAbstractItemView, QComboBox, QLabel


def wrapped(text: str, parent=None) -> QLabel:
    """A QLabel that wraps — use this for any explanatory paragraph.

    ⚠ WHY THIS EXISTS. A QLabel without word wrap can only draw its text as one
    line, so when the layout asks it "what is the narrowest you can be?" it has
    to answer with the full width of that line. A window's minimum width is the
    widest answer among its rows, and a layout minimum silently overrules
    `resize()` — so one long paragraph sets the width of the whole dialog and no
    size request can take it back. "Empty a card" asked for 820 px and opened at
    3310, wider than a laptop screen.

    The trap is specifically the INLINE form:

        layout.addWidget(QLabel("a long explanation…"))     # can never be fixed

    because no reference is kept, there is nothing to call `setWordWrap(True)`
    on afterwards. Every one of the seven cases found in this codebase was that
    shape, and in each of them the labels that DID wrap were the ones held in a
    variable. The bug is not carelessness about wrapping; it is that the concise
    way to add a label is the broken one. Hence a helper that is just as short:

        layout.addWidget(wrapped("a long explanation…"))

    Short labels — a field caption, a column heading — do not need this and read
    better without it; wrapping "Card to empty:" would only let it fold in half.
    """
    label = QLabel(text, parent) if parent is not None else QLabel(text)
    label.setWordWrap(True)
    return label


class _TypeAheadToTop(QObject):
    """Event filter on a combo's popup list: after a typed letter, scroll the
    match to the TOP. Lives on the view (parented to it), so it dies with it."""

    def eventFilter(self, view, event):
        if event.type() == QEvent.Type.KeyPress and event.text().strip():
            # Qt's own keyboardSearch runs after this filter returns; scroll once
            # it has moved the current item.
            QTimer.singleShot(0, lambda: view.scrollTo(
                view.currentIndex(), QAbstractItemView.ScrollHint.PositionAtTop))
        return False


def type_ahead_to_top(combo: QComboBox) -> QComboBox:
    """Make typing a letter in the open drop-down put the matching item at the
    TOP of the list, not wherever it first becomes visible.

    Qt scrolls a type-ahead match only just into view: jumping back up the list
    it lands on the top line, but jumping DOWN it lands on the bottom line, so
    the names after it — the ones you are looking for — need scrolling. Arrow
    keys and the mouse wheel are untouched (a letter only; arrows have no text).
    """
    view = combo.view()
    # Keep a Python reference: Qt's parent keeps only the C++ object alive, and
    # once the wrapper is garbage-collected the filter is a bare QObject whose
    # eventFilter override is gone — it silently stops working.
    combo._type_ahead_filter = _TypeAheadToTop(view)
    view.installEventFilter(combo._type_ahead_filter)
    return combo
