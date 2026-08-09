"""Copying species names out of the app as PLAIN text.

One job: wherever a species' Dutch / English / scientific name is shown, let the
user lift it out — individually, or all three at once — with nothing but the
characters. No font, no size, no colour, no rich-text payload, so it pastes
clean into a page layout for a printed photobook.

Invariant: everything here puts `text/plain` on the clipboard and nothing else.

Two entry points, because names appear on two kinds of surface:
  • `make_copyable(label, text, ...)` — a QLabel that IS a name (the species
    panel's title, subtitle and names table). Also becomes mouse-selectable.
  • `add_name_copy_actions(menu, node)` — a widget that CARRIES a name but owns
    its clicks (the Biota Browser's taxon cards, where a left click navigates).
    Fills a right-click menu instead.

`node` is a biota node / master row dict: nl / en / sci (the resolver in
utils.name_profile also knows the pipeline's NLSoortNaam… spellings).
"""

from PyQt6.QtWidgets import QApplication, QLabel, QMenu
from PyQt6.QtCore import Qt

_LANGS = (("Dutch", "nl"), ("English", "en"), ("Scientific", "sci"))


def copy_plain(text: str) -> None:
    """Put text on the clipboard as plain text."""
    QApplication.clipboard().setText((text or "").strip())


def node_names(node) -> list:
    """The node's (language label, name) pairs — NL, EN, Sci — blanks dropped."""
    node = node or {}
    out = []
    for label, field in _LANGS:
        v = (node.get(field) or "").strip()
        if v:
            out.append((label, v))
    return out


def all_three_entries(names) -> list:
    """The two 'copy the whole set' menu entries for a list of (label, name)
    pairs. Empty when there is only one name — nothing to combine."""
    vals = [v for _l, v in names]
    if len(vals) < 2:
        return []
    return [("Copy all names", " · ".join(vals)),
            ("Copy all names, one per line", "\n".join(vals))]


def _short(text: str) -> str:
    return text if len(text) <= 40 else text[:38] + "…"


def make_copyable(label: QLabel, text: str, extras=None) -> QLabel:
    """Make a name label yield its text: drag-select it with the mouse, or
    right-click for 'Copy'. `extras` adds further (menu label, text) entries —
    used for 'copy all three names at once'."""
    text = (text or "").strip()
    if not text:
        return label
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setCursor(Qt.CursorShape.IBeamCursor)
    label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def _menu(pos, _lbl=label, _text=text, _extras=tuple(extras or ())):
        m = QMenu(_lbl)
        m.addAction(f'Copy "{_short(_text)}"', lambda t=_text: copy_plain(t))
        for lab, val in _extras:
            val = (val or "").strip()
            if val and val != _text:
                m.addAction(lab, lambda t=val: copy_plain(t))
        m.exec(_lbl.mapToGlobal(pos))

    label.customContextMenuRequested.connect(_menu)
    label.setToolTip("Right-click to copy this name as plain text")
    return label


def add_name_copy_actions(menu: QMenu, node) -> int:
    """Append this node's name-copy actions to an open menu: one per name, then
    the two combined entries. Returns how many names were offered (0 = the node
    has no name at all, so the caller can skip showing a menu)."""
    names = node_names(node)
    if not names:
        return 0
    for lang, val in names:
        menu.addAction(f'Copy {lang}: "{_short(val)}"',
                       lambda t=val: copy_plain(t))
    combined = all_three_entries(names)
    if combined:
        menu.addSeparator()
        for lab, val in combined:
            menu.addAction(lab, lambda t=val: copy_plain(t))
    return len(names)
