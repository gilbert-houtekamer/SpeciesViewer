"""The yellow panel for the Unfiled lane — every day off a card that nothing
claims yet, and the three ways one can leave.

ONE JOB: turn the inbox from a modal you open into the panel you are looking at
while you empty a stack of cards.

Rows come from engine.unfiled.scan_unfiled() on every build — a disk read, no
cache. This screen owns no state, so it cannot disagree with what is there.

Column contract (double-click a cell = do that thing to that row):

    0 Day             the date; the folder is on the item and in the tooltip
    1 Pictures        count; opens Review & cull
    2 Trip     📁     move into a trip's pictures folder
    3 Day      📅     track as a Day — WHERE IT LIES, nothing moves
    4 Geotag   🛰️     Geotag from tracklog… — independent of a card read
    5 Move     →      move to a folder you pick, out of SV's world
    6 About this day  free text, the ONLY editable cell in the panel

THE RULE: nothing here deletes pictures. There is no Discard, no .trash, no
remove-from-list — the inbox holds the only copy of pictures that are in no trip,
no Day and not in the master. A day disappears because the user moved or deleted
the directory themselves, and then it is simply not in the next scan.
"""

from __future__ import annotations

import os

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QMenu, QMessageBox, QDialog,
                             QFileDialog)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCursor

from gui import theme
from gui.overview_table import (make_overview_table, action_cell, text_cell,
                                row_folder, select_row_for_folder, order_like_panel)
from engine import unfiled, day_note
from utils import days_registry
from utils.open_path import open_path

TAB_TITLE = "Unfiled"

DAY_COL, PICT_COL, TRIP_COL, ASDAY_COL, TRACK_COL, MOVE_COL, NOTE_COL = range(7)
_FOLDER_ROLE = Qt.ItemDataRole.UserRole + 1


def populate_unfiled_overview(self):
    """(Re)build the Unfiled tab in the yellow panel. `self` is the MainWindow."""
    self.remove_existing_unfiled_tab()

    from gui.unfiled_view import _active_picture_dir
    base = unfiled.currentpictures_root(_active_picture_dir())
    rows = unfiled.scan_unfiled(base) if base else []
    # Same order as the left list — see order_like_panel.
    by_folder = {r["folder"]: r for r in rows}
    rows = [by_folder[f] for f in order_like_panel(self, by_folder.keys())]
    self._unfiled_rows = rows

    table = make_overview_table(
        ["Day", "Pictures", "Trip", "Day", "Geotag", "Move", "About this day"],
        rows=len(rows),
        fit_columns=(PICT_COL, TRIP_COL, ASDAY_COL, TRACK_COL, MOVE_COL))
    self.unfiled_table = table

    for row, entry in enumerate(rows):
        folder = entry["folder"]

        day_item = text_cell(entry["day"], folder)
        day_item.setData(_FOLDER_ROLE, folder)
        table.setItem(row, DAY_COL, day_item)

        table.setItem(row, PICT_COL, action_cell(
            str(entry["pictures"]),
            "Double-click to review & cull this day before you file it"))

        table.setItem(row, TRIP_COL, action_cell(
            "📁", "File under a trip — moves the folder into that trip's pictures folder"))

        table.setItem(row, ASDAY_COL, action_cell(
            "📅", "Track as a Day — the folder stays exactly where it is; it just "
                  "stops being unfiled and appears under Days"))

        table.setItem(row, TRACK_COL, action_cell(
            "🛰️", "Geotag from tracklog… — fills missing GPS/altitude for this "
                  "day from its GPS log, independent of a card read"))

        table.setItem(row, MOVE_COL, action_cell(
            "→", "Move elsewhere — pick a folder; the day leaves the inbox and "
                 "SpeciesViewer stops tracking it"))

        note = day_note.read_note(folder)
        table.setItem(row, NOTE_COL, text_cell(
            note.splitlines()[0] if note else "",
            note or "Double-click to describe what this day was about",
            editable=True))

    # ---- header row ------------------------------------------------------
    overview_widget = QWidget()
    layout = QVBoxLayout()
    header_row = QHBoxLayout()
    header_label = QLabel("Unfiled pictures")
    header_label.setStyleSheet("font-weight: bold; font-size: 16px;")
    header_row.addWidget(header_label)

    total = sum(r["pictures"] for r in rows)
    if rows:
        summary = f"{len(rows)} day(s), {total} picture(s) — claimed by nothing yet"
    elif base:
        summary = "Nothing unfiled — the inbox is empty."
    else:
        summary = ("No pictures folder configured yet — read a card into the "
                   "inbox once and it will be set.")
    count_label = QLabel(summary)
    count_label.setStyleSheet(f"color: {theme.INK_FAINT};")
    header_row.addWidget(count_label)
    header_row.addStretch()

    read_btn = QPushButton("Read a card…")
    read_btn.setToolTip("Copy another card into the unfiled inbox — no trip is "
                        "chosen or claimed.")
    read_btn.clicked.connect(lambda: _read_card(self))
    header_row.addWidget(read_btn)

    open_btn = QPushButton("Open inbox folder")
    open_btn.setToolTip(f"{base}\\<year>\\<year> unfiled" if base
                        else "Not configured yet")
    open_btn.setEnabled(bool(base and os.path.isdir(base)))
    open_btn.clicked.connect(lambda: open_path(base))
    header_row.addWidget(open_btn)

    refresh_btn = QPushButton("Refresh")
    refresh_btn.setToolTip("Re-read the inbox from disk.")
    refresh_btn.clicked.connect(lambda: populate_unfiled_overview(self))
    header_row.addWidget(refresh_btn)

    layout.addLayout(header_row)
    layout.addWidget(table)
    overview_widget.setLayout(layout)

    self.yellow_tabs.addTab(overview_widget, TAB_TITLE)
    # Do NOT force this tab: the left column decides which scope shows.
    self.sync_yellow_to_mode()

    table.cellDoubleClicked.connect(lambda r, c: _on_double_click(self, r, c))
    table.customContextMenuRequested.connect(lambda pos: _on_context_menu(self, pos))
    table.itemChanged.connect(lambda item: _on_item_changed(self, item))
    table.itemSelectionChanged.connect(lambda: _on_selection_changed(self))


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------

def _repopulate_later(self):
    """Rebuild once the current signal has returned — rebuilding replaces the
    table we are being called FROM."""
    QTimer.singleShot(0, lambda: populate_unfiled_overview(self))


def _folder_at(self, row):
    return row_folder(self.unfiled_table, row, role=_FOLDER_ROLE)


def _day_at(self, row):
    item = self.unfiled_table.item(row, DAY_COL)
    return item.text() if item is not None else ""


def _refresh_panel(self):
    panel = getattr(self, "unfiled_panel", None)
    if panel is not None:
        panel.rebuild()


def _on_selection_changed(self):
    rows = {i.row() for i in self.unfiled_table.selectedIndexes()}
    if len(rows) != 1:
        return
    panel = getattr(self, "unfiled_panel", None)
    if panel is not None:
        panel.select_folder(_folder_at(self, rows.pop()))


def _read_card(self):
    from gui import unfiled_view
    unfiled_view.read_card_into_unfiled(self)
    populate_unfiled_overview(self)
    _refresh_panel(self)


def _on_double_click(self, row, column):
    folder = _folder_at(self, row)
    if not folder:
        return
    if column == PICT_COL:
        _review(self, row, folder)
    elif column == TRIP_COL:
        _file_under_trip(self, row, folder)
    elif column == ASDAY_COL:
        _track_as_day(self, row, folder)
    elif column == TRACK_COL:
        _geotag_from_tracklog(self, row, folder)
    elif column == MOVE_COL:
        _move_elsewhere(self, row, folder)
    # NOTE_COL falls through to Qt's own edit-on-double-click for that item.


def _review(self, row, folder):
    """Cull an unfiled day in place. Everything the viewer writes lands in the
    .xmp beside each picture, so the work is not tied to this folder and survives
    the move into a trip. Culling BEFORE filing is the point: what you throw away
    never reaches the trip."""
    from gui.image_viewer import ImagePreviewDialog
    dlg = ImagePreviewDialog(self, image_dir=folder, trip_root_dir=folder, mode="all")
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    self._unfiled_cull_dlg = dlg   # keep a ref (PyQt GC)
    dlg.finished.connect(lambda *_: populate_unfiled_overview(self))
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


def _file_under_trip(self, row, folder):
    from gui.unfiled_view import _PickTripDialog
    day = _day_at(self, row)
    # Opened even when the list is empty: the dialog can make the trip now, so
    # "there are no trips" is a state it handles rather than a reason to refuse.
    trips = unfiled.trips_covering(day)
    dlg = _PickTripDialog(self, day, trips)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return
    trip = dlg.chosen()
    if not trip:
        return
    ok, msg = unfiled.move_day_to(folder, trip["picture_root"])
    if not ok:
        QMessageBox.warning(self, "Could not file this day", msg)
        self.logger.warning(f"Unfiled → {trip['trip_id']}: {msg}")
    else:
        self.logger.info(f"Filed {day} under {trip['trip_id']}: "
                         f"{trip['picture_root']}")
    _repopulate_later(self)
    _refresh_panel(self)


def _track_as_day(self, row, folder):
    """Claim the day for the Days lane WITHOUT moving it.

    A Day is any YYYY-MM-DD folder tracked by path — it has no storage of its
    own — so there is nothing to move and nowhere to move it to. The day stops
    being listed here because scan_unfiled subtracts registered days, and
    Remove from list in the Days panel puts it straight back."""
    day = _day_at(self, row)
    ok, msg = days_registry.add_day(folder)
    if not ok:
        QMessageBox.warning(self, "Could not track this day", msg)
        return
    self.active_day_folder = folder
    days_registry.set_active_day(folder)
    self.logger.info(f"{day} is now tracked as a Day (not moved): {folder}")
    _repopulate_later(self)
    _refresh_panel(self)
    panel = getattr(self, "days_panel", None)
    if panel is not None:
        panel.rebuild()
    from gui.days_overview import populate_days_overview
    QTimer.singleShot(0, lambda: populate_days_overview(self))


def _geotag_from_tracklog(self, row, folder):
    """Run the tracklog geotag review dialog against this already-unfiled
    day, independent of a card being present — the day's own auto-detected
    GPS log (if any) is pre-loaded. A modal dialog, so the refresh after it
    closes picks up any GPS/altitude it wrote."""
    from gui.tracklog_geotag_dialog import open_tracklog_geotag_for_folder
    open_tracklog_geotag_for_folder(self, folder)
    _repopulate_later(self)
    _refresh_panel(self)


def _move_elsewhere(self, row, folder):
    """Move the day to a folder the user picks — the family-outing case: these
    pictures are not going to a trip, not going to the master, they just need to
    live somewhere sensible. The move is all SpeciesViewer does; it keeps no
    record of where the day went."""
    day = _day_at(self, row)
    chosen = QFileDialog.getExistingDirectory(
        self, f"Move {day} into which folder?", os.path.dirname(folder))
    if not chosen:
        return
    ok, msg = unfiled.move_day_to(folder, chosen)
    if not ok:
        QMessageBox.warning(self, "Could not move this day", msg)
    else:
        self.logger.info(f"Moved {day} out of the inbox to {chosen}")
    _repopulate_later(self)
    _refresh_panel(self)


def _on_item_changed(self, item):
    """The note cell was typed into. Nothing else in this table is editable, so
    an itemChanged from any other column means a bug, not an edit."""
    if item is None or item.column() != NOTE_COL:
        return
    folder = _folder_at(self, item.row())
    if not folder:
        return
    day_note.write_note(folder, item.text())
    # Blocked: setToolTip is itself an item change, and re-entering here would
    # write the note again.
    table = self.unfiled_table
    table.blockSignals(True)
    item.setToolTip(day_note.read_note(folder)
                    or "Double-click to describe what this day was about")
    table.blockSignals(False)
    _refresh_panel(self)


def _on_context_menu(self, pos):
    table = self.unfiled_table
    item = table.itemAt(pos)
    if item is None:
        return
    row = item.row()
    folder = _folder_at(self, row)
    if not folder:
        return

    menu = QMenu()
    review_action = menu.addAction("Review && cull…")
    menu.addSeparator()
    trip_action = menu.addAction("File under a trip…")
    day_action = menu.addAction("Track as a Day (leave it here)")
    track_action = menu.addAction("Geotag from tracklog…")
    move_action = menu.addAction("Move elsewhere…")
    menu.addSeparator()
    describe_action = menu.addAction("Describe this day…")
    open_action = menu.addAction("Open folder")

    chosen = menu.exec(QCursor.pos())
    if chosen is None:
        return
    if chosen == review_action:
        _review(self, row, folder)
    elif chosen == trip_action:
        _file_under_trip(self, row, folder)
    elif chosen == day_action:
        _track_as_day(self, row, folder)
    elif chosen == track_action:
        _geotag_from_tracklog(self, row, folder)
    elif chosen == move_action:
        _move_elsewhere(self, row, folder)
    elif chosen == describe_action:
        table.editItem(table.item(row, NOTE_COL))
    elif chosen == open_action:
        if os.path.isdir(folder):
            open_path(folder)
