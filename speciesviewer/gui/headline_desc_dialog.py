"""Shared Headline / Description editor.

Headline (a short title for a landscape / context photo) and Description (the
website 'story') are per-picture text fields. The large viewer edits them in its
side panel; everywhere else — grid cards, the large image, filmstrip cells —
reaches them through this small modal, opened from a right-click
"Edit headline / description…".

The dialog is pure: it takes the current values in and returns the edited pair,
or None when nothing changed / the user cancelled. The caller writes via
``item.set_headline`` / ``item.set_description`` (so the item's own mirroring —
Description → master DB in the album paths — still runs) and then refreshes its
own card. ``apply_headline_description`` does that write, honouring the item's
edit flags.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QTextEdit, QDialogButtonBox,
)


def edit_headline_description(parent, *, title: str = "", headline: str = "",
                              description: str = "",
                              can_edit_headline: bool = True,
                              can_edit_description: bool = True):
    """Modal editor for one picture's Headline + Description.

    Returns ``(headline, description)`` when the user saved a change, else None
    (cancelled, or nothing changed). Fields whose edit flag is False are shown
    read-only so the context is still visible."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Edit headline / description")
    dlg.setMinimumWidth(460)
    lay = QVBoxLayout(dlg)

    if title:
        t = QLabel(f"<b>{title}</b>")
        t.setWordWrap(True)
        lay.addWidget(t)

    lay.addWidget(QLabel(
        "Headline — title for a landscape / context photo (optional)"))
    hl = QLineEdit(headline)
    hl.setPlaceholderText("e.g. Sunrise over the marsh")
    hl.setReadOnly(not can_edit_headline)
    lay.addWidget(hl)

    lay.addWidget(QLabel("Description — story for the website (optional)"))
    de = QTextEdit()
    de.setPlainText(description)
    de.setAcceptRichText(False)
    de.setReadOnly(not can_edit_description)
    de.setMinimumHeight(120)
    lay.addWidget(de)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)

    # Focus the first editable field so you can type at once.
    (hl if can_edit_headline else de).setFocus()

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    new_h = hl.text() if can_edit_headline else headline
    new_d = de.toPlainText() if can_edit_description else description
    if new_h == headline and new_d == description:
        return None
    return (new_h, new_d)


def apply_headline_description(item, values) -> bool:
    """Write the ``(headline, description)`` pair to ``item``, honouring its edit
    flags and only writing a field that actually changed. Returns True when
    anything was written (so the caller knows to refresh)."""
    if values is None:
        return False
    new_h, new_d = values
    wrote = False
    if getattr(item, "can_edit_headline", False) and new_h != item.headline:
        item.set_headline(new_h)
        wrote = True
    if getattr(item, "can_edit_description", False) and new_d != item.description:
        item.set_description(new_d)
        wrote = True
    return wrote
