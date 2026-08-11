"""The yellow panel for the Days lane — every registered Day, and what you can
do to it.

ONE JOB: show the whole Days list as rows you act on, so working a Day no longer
means using a menu that acts on an invisible "active day" while the panel below
still shows some trip.

The difference from Trip and Album is deliberate and is the only one: those show
the days of ONE selected thing, this shows every row there is. The left-column
list holds the same collection — it is what you scan, this is where you act — and
the two mirror each other's selection.

Column contract (double-click a cell = do that thing to that row):

    0 Day             the date; the folder is on the item and in the tooltip
    1 Pictures        count; opens Review & rate
    2 Add to Master   🔘 ready / ✅ already done / ⚪ folder gone
    3 Remove          ✖ stop tracking (pictures never touched)
    4 About this day  free text, the ONLY editable cell in the panel
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QMenu)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor

from gui import theme
from gui.overview_table import (make_overview_table, action_cell, text_cell,
                                row_folder, select_row_for_folder, order_like_panel)
from engine import day_note
from engine.unfiled import count_pictures
from utils import days_registry
from utils.open_path import open_path

TAB_TITLE = "Days"

DAY_COL, PICT_COL, MASTER_COL, REMOVE_COL, NOTE_COL = range(5)
_FOLDER_ROLE = Qt.ItemDataRole.UserRole + 1


def populate_days_overview(self):
    """(Re)build the Days tab in the yellow panel. `self` is the MainWindow."""
    self.remove_existing_days_tab()

    rows = days_registry.load_days()
    # Same order as the left list — see order_like_panel.
    by_folder = {r.get("folder", ""): r for r in rows}
    rows = [by_folder[f] for f in order_like_panel(self, by_folder.keys())]

    table = make_overview_table(
        ["Day", "Pictures", "Add to\nMaster", "Remove", "About this day"],
        rows=len(rows),
        fit_columns=(PICT_COL, MASTER_COL, REMOVE_COL))
    self.days_table = table

    total_pictures = 0
    for row, entry in enumerate(rows):
        folder = entry.get("folder", "")
        name = entry.get("date") or Path(folder).name
        exists = os.path.isdir(folder)
        done = entry.get("status") == days_registry.STATUS_DONE
        pictures = count_pictures(folder) if exists else 0
        total_pictures += pictures

        day_item = text_cell(name, folder if exists else f"{folder}\n(folder not found)")
        day_item.setData(_FOLDER_ROLE, folder)
        if not exists:
            day_item.setForeground(Qt.GlobalColor.gray)
        table.setItem(row, DAY_COL, day_item)

        table.setItem(row, PICT_COL, action_cell(
            str(pictures) if exists else "—",
            "Double-click to review & rate this day's pictures",
            inert=not exists))

        # Same sphere vocabulary as the Trip panel: ⚪ nothing to do here,
        # 🔘 ready to run, ✅ done. A Day is marked done by the commit itself
        # (add_to_master), so this cell reports rather than guesses.
        if not exists:
            table.setItem(row, MASTER_COL, action_cell(
                "⚪", "The folder is gone — nothing to add.", inert=True))
        elif done:
            table.setItem(row, MASTER_COL, action_cell(
                "✅", "Already added to the Master. Double-click to add again "
                      "(new or re-rated pictures)."))
        else:
            table.setItem(row, MASTER_COL, action_cell(
                "🔘", "Double-click to add this day to the Master"))

        table.setItem(row, REMOVE_COL, action_cell(
            "✖", "Stop tracking this day. The pictures on disk are not touched."))

        note = day_note.read_note(folder)
        table.setItem(row, NOTE_COL, text_cell(
            note.splitlines()[0] if note else "",
            note or "Double-click to describe what this day was about",
            editable=exists))

    # ---- header row ------------------------------------------------------
    overview_widget = QWidget()
    layout = QVBoxLayout()
    header_row = QHBoxLayout()
    header_label = QLabel("Days")
    header_label.setStyleSheet("font-weight: bold; font-size: 16px;")
    header_row.addWidget(header_label)
    count_label = QLabel(
        f"{len(rows)} day(s), {total_pictures} picture(s)" if rows
        else "No days yet — register one, or copy a day off a card.")
    count_label.setStyleSheet(f"color: {theme.INK_FAINT};")
    header_row.addWidget(count_label)
    header_row.addStretch()

    from gui import days_process
    register_btn = QPushButton("Register existing day…")
    register_btn.setToolTip("Track a YYYY-MM-DD folder that is already on disk "
                            "(nothing is copied or moved).")
    register_btn.clicked.connect(lambda: days_process.register_existing_day(self))
    header_row.addWidget(register_btn)

    copy_btn = QPushButton("Copy day from card…")
    copy_btn.setToolTip("Copy pictures off a card into date folders and track "
                        "them as Days.")
    copy_btn.clicked.connect(lambda: days_process.copy_day_from_card(self))
    header_row.addWidget(copy_btn)

    refresh_btn = QPushButton("Refresh")
    refresh_btn.setToolTip("Re-read the list and re-count the pictures on disk.")
    refresh_btn.clicked.connect(lambda: populate_days_overview(self))
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

    # Come back to the day that was already active, so switching modes does not
    # silently move the selection the Days menu acts on.
    select_row_for_folder(table, getattr(self, "active_day_folder", ""),
                          role=_FOLDER_ROLE)


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------

def _repopulate_later(self):
    """Rebuild the tab once the current signal has returned.

    Rebuilding replaces the table we are being called FROM, so doing it inline
    tears down the sender mid-emit. Same deferral the GPS refresh uses.
    """
    from PyQt6.QtCore import QTimer
    QTimer.singleShot(0, lambda: populate_days_overview(self))


def _folder_at(self, row):
    return row_folder(self.days_table, row, role=_FOLDER_ROLE)


def _make_active(self, folder):
    """Point the Days lane at this day — the menu actions and DaysPanel both
    read main_window.active_day_folder, so one setter keeps all three in step."""
    if not folder:
        return
    self.active_day_folder = folder
    days_registry.set_active_day(folder)
    panel = getattr(self, "days_panel", None)
    if panel is not None:
        panel.rebuild()


def _on_selection_changed(self):
    rows = {i.row() for i in self.days_table.selectedIndexes()}
    if len(rows) == 1:
        _make_active(self, _folder_at(self, rows.pop()))


def _on_double_click(self, row, column):
    from gui import days_process
    folder = _folder_at(self, row)
    if not folder:
        return
    _make_active(self, folder)

    if column == PICT_COL:
        days_process.review_day(self)
    elif column == MASTER_COL:
        days_process.add_day_to_master(self)
    elif column == REMOVE_COL:
        # No repaint here: remove_day_from_list goes through days_process's
        # _refresh_panel, which already repaints both surfaces (deferred).
        days_process.remove_day_from_list(self)
    # NOTE_COL falls through to Qt's own edit-on-double-click for that item.


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
    # write the note again on every keystroke's worth of signal.
    table = self.days_table
    table.blockSignals(True)
    item.setToolTip(day_note.read_note(folder)
                    or "Double-click to describe what this day was about")
    table.blockSignals(False)


def _on_context_menu(self, pos):
    table = self.days_table
    item = table.itemAt(pos)
    if item is None:
        return
    row = item.row()
    folder = _folder_at(self, row)
    if not folder:
        return
    _make_active(self, folder)

    menu = QMenu()
    review_action = menu.addAction("Review && rate…")
    master_action = menu.addAction("Add to Master")
    describe_action = menu.addAction("Describe this day…")
    menu.addSeparator()
    open_action = menu.addAction("Open folder")
    menu.addSeparator()
    remove_action = menu.addAction("Remove from list")

    chosen = menu.exec(QCursor.pos())
    if chosen is None:
        return
    from gui import days_process
    if chosen == review_action:
        days_process.review_day(self)
    elif chosen == master_action:
        days_process.add_day_to_master(self)
    elif chosen == describe_action:
        table.editItem(table.item(row, NOTE_COL))
    elif chosen == open_action:
        if os.path.isdir(folder):
            open_path(folder)
    elif chosen == remove_action:
        days_process.remove_day_from_list(self)   # repaints both surfaces itself
