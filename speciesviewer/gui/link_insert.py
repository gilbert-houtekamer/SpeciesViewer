"""Insert / edit a hyperlink in a plain-text box that holds website HTML.

ONE job: Ctrl+K (⌘K on a Mac — the Word / Google Docs / Outlook key) and a
right-click "Insert link…" open a small Text + Link dialog and write

    <a href="LINK">TEXT</a>          (or, "Open in a new tab" ticked:)
    <a href="LINK" target="_blank" rel="noopener">TEXT</a>

into the box — the same markup the website generator writes, and that the blog
text already accepted when typed by hand.

  * Text selected first → it becomes the link text; you only paste the address.
  * Cursor inside an existing <a href="…">…</a> → that link opens for editing,
    and OK replaces it (Word's behaviour). An empty Link removes the link and
    keeps its text.
  * A web address on the clipboard pre-fills Link.
  * "Open in a new tab" is ticked by default for a web address (http…), off for
    one of our own pages (Blog Pictures ….html), which belongs in the same tab.

    from gui.link_insert import add_link_editing
    add_link_editing(self.message_nl)
"""

from __future__ import annotations

import html
import re

from PyQt6.QtCore import QObject, Qt
from PyQt6.QtGui import QCursor, QKeySequence, QShortcut, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                             QLineEdit, QTextEdit, QPlainTextEdit)

SHORTCUT = "Ctrl+K"   # Qt maps Ctrl to ⌘ on macOS

# One anchor, as written by hand or by us: its attributes, then its words.
_ANCHOR = re.compile(r'<a\s+([^>]*)>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_HREF = re.compile(r'href\s*=\s*"([^"]*)"', re.IGNORECASE)
_BLANK = re.compile(r'target\s*=\s*"_blank"', re.IGNORECASE)


def _looks_like_url(text: str) -> bool:
    t = text.strip()
    return bool(t) and "\n" not in t and " " not in t and (
        t.startswith(("http://", "https://", "mailto:", "www.")) or t.endswith(".html"))


def is_external(href: str) -> bool:
    return href.strip().lower().startswith(("http://", "https://", "www."))


def anchor_at(text: str, pos: int):
    """(start, end, href, label, new_tab) of the <a>…</a> containing `pos`, else None."""
    for m in _ANCHOR.finditer(text):
        if m.start() <= pos <= m.end():
            h = _HREF.search(m.group(1))
            return (m.start(), m.end(), html.unescape(h.group(1)) if h else "",
                    m.group(2), bool(_BLANK.search(m.group(1))))
    return None


def make_anchor(href: str, label: str, new_tab: bool = False) -> str:
    # Only the attribute needs escaping: a '"' would end href="…". The label is
    # the user's own text in an HTML box and stays exactly as typed.
    # rel="noopener": the opened page gets no handle back on ours (standard
    # companion of target="_blank").
    extra = ' target="_blank" rel="noopener"' if new_tab else ""
    return f'<a href="{href.strip().replace(chr(34), "%22")}"{extra}>{label}</a>'


class _LinkDialog(QDialog):
    def __init__(self, parent, label: str, href: str, editing: bool, new_tab: bool):
        super().__init__(parent)
        self.setWindowTitle("Edit link" if editing else "Insert link")
        self.setMinimumWidth(520)
        form = QFormLayout(self)
        self.text = QLineEdit(label)
        self.text.setPlaceholderText("Words shown on the page")
        self.href = QLineEdit(href)
        self.href.setPlaceholderText("https://…  or  a page name, e.g. Blog Pictures 2026-08-29-….html")
        form.addRow("Text:", self.text)
        form.addRow("Link:", self.href)
        self.new_tab = QCheckBox("Open in a new tab")
        self.new_tab.setChecked(new_tab)
        self.new_tab.setToolTip("The visitor keeps this page open; the link opens beside it.\n"
                                "On by default for a web address, off for one of our own pages.")
        # Follow the address while the user hasn't decided: typing an http…
        # address ticks it, a page name unticks it. A click on the box wins.
        self._tab_touched = editing
        self.new_tab.clicked.connect(lambda: setattr(self, "_tab_touched", True))
        self.href.textEdited.connect(
            lambda t: None if self._tab_touched else self.new_tab.setChecked(is_external(t)))
        form.addRow("", self.new_tab)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        # Focus where the typing is still to be done.
        (self.href if label and not href else self.text).setFocus()


def edit_link(edit) -> bool:
    """Open the dialog for `edit` (QTextEdit / QPlainTextEdit). True if changed."""
    if edit.isReadOnly():
        return False
    cur = edit.textCursor()
    full = edit.toPlainText()
    found = None if cur.hasSelection() else anchor_at(full, cur.position())
    if found:
        start, end, href, label, new_tab = found
    else:
        start, end = cur.selectionStart(), cur.selectionEnd()
        label = cur.selectedText().replace(" ", "\n")   # Qt's paragraph separator
        clip = QApplication.clipboard().text()
        href = clip.strip() if _looks_like_url(clip) else ""
        new_tab = is_external(href)

    dlg = _LinkDialog(edit, label, href, editing=bool(found), new_tab=new_tab)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    new_label = dlg.text.text()
    new_href = dlg.href.text().strip()
    if not new_href:
        if not found:
            return False                   # nothing to link to
        replacement = new_label            # empty Link = remove the link, keep the words
    else:
        replacement = make_anchor(new_href, new_label or new_href, dlg.new_tab.isChecked())

    cur.setPosition(start)
    cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    # Plain format: never inherit a neighbour's (e.g. a rendered link's blue).
    cur.insertText(replacement, QTextCharFormat())   # one undo step (Ctrl+Z restores)
    edit.setTextCursor(cur)
    edit.setFocus()
    return True


class _LinkEditing(QObject):
    """Owns the shortcut + context-menu hook for one text box. Kept referenced
    on the box (see add_link_editing) — a garbage-collected wrapper would leave
    a dead hook behind."""

    def __init__(self, edit):
        super().__init__(edit)
        self._edit = edit
        sc = QShortcut(QKeySequence(SHORTCUT), edit)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(lambda: edit_link(edit))
        self._shortcut = sc
        edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        edit.customContextMenuRequested.connect(self._menu)

    def _menu(self, pos):
        edit = self._edit
        # A right-click doesn't move Qt's text cursor; without a selection, put
        # it where you clicked so "Edit link…" means the link under the mouse.
        # (`pos` is in viewport coordinates for a scroll-area text box.)
        if not edit.textCursor().hasSelection():
            edit.setTextCursor(edit.cursorForPosition(pos))
        menu = edit.createStandardContextMenu()
        menu.addSeparator()
        inside = anchor_at(edit.toPlainText(), edit.textCursor().position())
        act = menu.addAction("Edit link…" if inside and not edit.textCursor().hasSelection()
                             else "Insert link…")
        act.setShortcut(QKeySequence(SHORTCUT))
        act.setEnabled(not edit.isReadOnly())
        act.triggered.connect(lambda: edit_link(edit))
        menu.exec(QCursor.pos())
        menu.deleteLater()


def add_link_editing(edit: QTextEdit | QPlainTextEdit):
    """Give a text box Ctrl+K + right-click "Insert link…". Returns the box."""
    edit._link_editing = _LinkEditing(edit)
    return edit
