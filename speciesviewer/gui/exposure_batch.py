"""Suggest ONE exposure value for a whole selection.

The batch half of exposure editing. The Editor's slider is the other half —
it shows a live re-render for the focused picture — and each surface used to have
only one of the two: the culling grid had this dialog and no slider, the Editor
had a slider and no way to reach a selection, and the Trip Species Viewer had
neither. This module is the dialog, extracted from the culling grid so every
surface can offer the same thing rather than each growing its own.

Collection-agnostic on purpose: it needs only `item_for_key`, plus the item's
`can_set_exposure` / `set_exposure`, all of which PreviewCollection,
SidecarCollection and RatingEditorCollection already provide.

NOT batched into one exiftool call, deliberately. Each picture's suggestion is a
surgical edit to its own .dop/.xmp (engine/sidecarlib.suggest_exposure), so this
writes one picture at a time behind a progress dialog — the same reason the batch
rating dialog does.
"""

import logging

from PyQt6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout,
                             QLabel, QProgressDialog, QVBoxLayout)

logger = logging.getLogger("speciesviewer")


def ask_exposure(parent, n: int, initial: float = 0.0):
    """The dialog alone: returns the chosen EV, or None if cancelled."""
    from gui.exposure_widget import ExposureWidget
    dlg = QDialog(parent)
    dlg.setWindowTitle(f"Set exposure — {n} picture{'s' if n != 1 else ''}")
    layout = QVBoxLayout(dlg)
    layout.addWidget(QLabel(
        f"Suggest this exposure for {n} picture{'s' if n != 1 else ''}:"))

    row = QHBoxLayout()
    row.addWidget(QLabel("Exposure:"))
    widget = ExposureWidget(initial)
    row.addWidget(widget)
    row.addStretch()
    layout.addLayout(row)

    btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                            QDialogButtonBox.StandardButton.Cancel)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    layout.addWidget(btns)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return widget.value_ev


def write_exposure(parent, collection, keys, value: float, *, on_written=None) -> int:
    """Write `value` to every key that can take it, behind a progress dialog.

    `on_written(key)` is called for each key afterwards so the caller can refresh
    whatever it owns — the grid card, an open Editor. Returns how many were
    written; anything that cannot take an exposure (no RAW, read-only, master
    reference card) is skipped and counted in the log, never written blindly.
    """
    key_list = list(keys)
    if not key_list:
        return 0
    progress = QProgressDialog("Updating…", None, 0, len(key_list) + 1, parent)
    progress.setWindowTitle("Updating pictures")
    progress.setMinimumDuration(0)
    progress.show()

    written = skipped = 0
    for i, key in enumerate(key_list):
        progress.setValue(i)
        progress.setLabelText(
            f"Writing exposure suggestion {i + 1} of {len(key_list)}…")
        QApplication.processEvents()
        try:
            item = collection.item_for_key(key)
        except (ValueError, KeyError, IndexError):
            skipped += 1
            continue
        if not getattr(item, "can_set_exposure", False):
            skipped += 1
            continue
        try:
            item.set_exposure(value)
            written += 1
        except Exception as e:                      # noqa: BLE001 — report, continue
            logger.error(f"Set exposure failed for {key}: {e}")
            skipped += 1

    if on_written:
        for key in key_list:
            on_written(key)
    progress.setValue(len(key_list) + 1)
    if skipped:
        logger.info(f"Set exposure: {skipped} of {len(key_list)} picture(s) "
                    f"skipped (no RAW / read-only).")
    return written


def shared_exposure(collection, keys) -> float:
    """The exposure the pictures already carry when they all agree, else 0.0 —
    what the dialog should open on, so pressing Enter changes nothing. Stops
    reading at the first disagreement."""
    value = None
    for k in keys:
        try:
            ev = float(collection.item_for_key(k).exposure or 0.0)
        except (ValueError, KeyError, AttributeError, TypeError):
            return 0.0
        if value is None:
            value = ev
        elif ev != value:
            return 0.0
    return value or 0.0


def open_batch_exposure_dialog(parent, collection, keys, *, initial: float | None = None,
                               on_written=None) -> int:
    """Ask for one exposure and write it to `keys`. Returns how many were written.
    With no `initial`, the dialog opens on the value the pictures share."""
    if not keys or collection is None:
        return 0
    if initial is None:
        initial = shared_exposure(collection, keys)
    value = ask_exposure(parent, len(keys), initial)
    if value is None:
        return 0
    return write_exposure(parent, collection, keys, value, on_written=on_written)
