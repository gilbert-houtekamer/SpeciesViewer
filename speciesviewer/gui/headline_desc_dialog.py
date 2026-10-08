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
    QCheckBox,
)


def edit_headline_description(parent, *, title: str = "", headline: str = "",
                              description: str = "",
                              can_edit_headline: bool = True,
                              can_edit_description: bool = True,
                              n_selected: int = 1):
    """Modal editor for one picture's Headline + Description.

    Returns ``(headline, description, apply_to_all)`` when the user saved a
    change, else None (cancelled, or nothing changed). Fields whose edit flag is
    False are shown read-only so the context is still visible.

    `n_selected` > 1 adds an "apply to all N selected" tick, OFF by default: a
    headline is normally about the ONE picture it titles, but a burst of the same
    scene shares it. Opened from a selection, the editor still shows the clicked
    picture's own text — the tick only widens where that text is written."""
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
    from gui.link_insert import add_link_editing
    add_link_editing(de)   # Ctrl+K / right-click → <a href>
    de.setReadOnly(not can_edit_description)
    de.setMinimumHeight(120)
    lay.addWidget(de)

    all_box = None
    if n_selected > 1:
        all_box = QCheckBox(f"Apply to all {n_selected} selected pictures")
        all_box.setToolTip(
            "Off: only the picture you clicked gets this headline and story.\n"
            "On: every picture in the selection gets the same text.")
        lay.addWidget(all_box)

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
    apply_all = bool(all_box is not None and all_box.isChecked())
    # "Nothing changed" is only nothing to do when the write stays on THIS
    # picture. Ticking "apply to all" on a picture that already carries the text
    # is exactly how you copy it onto the rest of the selection — returning None
    # there made Save do nothing at all.
    if new_h == headline and new_d == description and not apply_all:
        return None
    return (new_h, new_d, apply_all)


def apply_headline_description(item, values, parent=None) -> bool:
    """Single-picture convenience over ``apply_headline_description_to``."""
    return bool(apply_headline_description_to([item], values, parent=parent))


def apply_headline_description_to(items, values, parent=None) -> list:
    """Write the dialog's result to `items`, honouring each item's edit flags and
    only writing a field that actually changed. Returns the items written (so the
    caller knows which cards to refresh).

    SCOPE: ``items[0]`` is the picture that was clicked and always gets the text;
    the rest of the selection only does when the user ticked "apply to all" in the
    dialog (the third element of `values`). Defaulting to the one picture keeps a
    mis-aimed headline from overwriting thirteen others.

    With `parent` given, pictures that gained a headline but have no subject are
    offered the landscape marker (see ``offer_landscape_marker``) — without it,
    the headline is stored but nothing shows it."""
    if values is None or not items:
        return []
    new_h, new_d = values[0], values[1]
    apply_all = bool(values[2]) if len(values) > 2 else False
    targets = list(items) if apply_all else list(items[:1])
    written = []
    for item in targets:
        wrote = False
        if getattr(item, "can_edit_headline", False) and new_h != item.headline:
            item.set_headline(new_h)
            wrote = True
        if getattr(item, "can_edit_description", False) and new_d != item.description:
            item.set_description(new_d)
            wrote = True
        if wrote:
            written.append(item)
    if written and parent is not None:
        offer_landscape_marker(parent, written)
    return written


def offer_landscape_marker(parent, items) -> list:
    """Offer to mark just-titled pictures as landscape / context photos.

    A Headline is what titles a BLOG EXTRA — a landscape or context photo, which
    carries the `landscape` keyword in XMP:Subject instead of a species. Give one
    to a picture with no subject and nothing happens visibly: the grid card keeps
    showing the filename, and the blog would not render it, because the marker
    (not the headline) is what makes it a blog extra. So offer the marker here.

    ASKED, not applied silently: the keyword decides whether a picture is a
    species record or a topic — and once there is more than one topic, WHICH
    topic. Today `landscape` is the only one, so a Yes/No is enough; a second
    topic turns this into a picker.

    Returns the items that were marked."""
    from PyQt6.QtWidgets import QMessageBox
    if not isinstance(items, (list, tuple, set)):
        items = [items]
    # Only a picture that HAS a headline, has NO subject yet (neither a species
    # nor the marker) and may be given one is a candidate.
    cands = [i for i in items
             if getattr(i, "headline", "")
             and getattr(i, "can_edit_species", False)
             and not getattr(i, "species", "")]
    if not cands:
        return []
    if len(cands) == 1:
        what = f"“{cands[0].headline}” has a headline but no species."
        ask = "Mark this picture as a landscape / context photo?"
    else:
        what = f"{len(cands)} pictures now have a headline but no species."
        ask = "Mark them as landscape / context photos?"
    if QMessageBox.question(
            parent, "Mark as landscape?",
            f"{what}\n\n"
            "A headline titles a landscape / context photo — a picture that "
            "appears in the day blog under that title instead of under a species "
            "name. That needs the “landscape” keyword.\n\n" + ask
            ) != QMessageBox.StandardButton.Yes:
        return []
    for item in cands:
        item.set_species("landscape")
    return cands
