"""Widgets that behave correctly by default.

ONE job: make the right thing the easy thing, for the cases where Qt's default
is a trap rather than a preference.

So far that is exactly one case, and it is worth the module on its own.
"""

from PyQt6.QtWidgets import QLabel


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
