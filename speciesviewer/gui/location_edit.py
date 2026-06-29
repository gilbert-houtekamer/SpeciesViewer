"""Manual Location editor dialog — type a place name into the XMP when
reverse-geocoding can't supply it (e.g. a nature reserve OSM/Nominatim hasn't
mapped as an enclosing polygon, so 'Get location' only finds the village/road).

Returns the edited location fields (a dict), or None if the user cancelled. The
caller diffs against the current values and writes only what changed; a blank
field means "leave unchanged" (this dialog never clears an existing value)."""

from __future__ import annotations

# Editable fields, in display order (matches the large-view LOCATION panel:
# City · Municipality · State · Country, with Location as the descriptive line).
_FIELDS = (("Location", "Location"), ("City", "City"),
           ("Municipality", "Municipality"), ("State", "State"),
           ("Country", "Country"))


def edit_location_dialog(parent, current, n):
    """Show the edit dialog pre-filled from `current` (a read_location dict) for
    `n` pictures. Returns {field: text} on OK, or None on Cancel."""
    from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit,
                                 QLabel, QDialogButtonBox)
    pics = f"{n} picture{'s' if n != 1 else ''}"
    dlg = QDialog(parent)
    dlg.setWindowTitle(f"Edit location — {pics}")
    dlg.setMinimumWidth(420)
    layout = QVBoxLayout(dlg)
    layout.addWidget(QLabel(f"Set the place name for {pics}.\n"
                            "Blank fields are left unchanged."))
    form = QFormLayout()
    edits = {}
    for key, label in _FIELDS:
        e = QLineEdit(current.get(key, "") or "")
        edits[key] = e
        form.addRow(label + ":", e)
    layout.addLayout(form)
    btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                            QDialogButtonBox.StandardButton.Cancel)
    # "Clear fields" empties every box so only what you re-type is written (blank
    # fields are left untouched) — handy to set just the Location for a GPS'd shot.
    clear_btn = btns.addButton("Clear fields", QDialogButtonBox.ButtonRole.ResetRole)
    clear_btn.clicked.connect(lambda: [e.clear() for e in edits.values()])
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    layout.addWidget(btns)
    edits["Location"].setFocus()
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return {key: edits[key].text().strip() for key, _ in _FIELDS}
