"""Albums ▸ "Find Trips in the Master…" — a read-only report.

Answers the question the Create Virtual Trip dialog cannot: *what date range?*
Creating a virtual trip means typing a from/to date you have to remember, so the
Tucson trip got found by hand-searching filename prefixes and the range that was
typed was padded two days either side of the pictures that actually exist.

This lists what the master already knows, from two sources in ONE table — which
is the point: it removes the "from an existing trip, or retroactive from master
dates?" question, because the answer is just which row you are looking at.

  KNOWN  a real trip out of the master's trip_admin.csv — dates recorded.
  FOUND  a run of consecutive shooting days discovered in the master.

REPORT ONLY. Nothing here writes. Feeding a row into Create Virtual Trip is the
next step; for now a row can be copied to the clipboard so the dates can be
pasted into Create by hand.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QMenu, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

logger = logging.getLogger("speciesviewer")

(SRC_COL, RANGE_COL, DAYS_COL, PICS_COL, RATE_COL, PREFIX_COL, PLACE_COL,
 NAME_COL, USED_COL, MAKE_COL) = range(10)
HEADERS = ["Source", "Date range", "Days", "Pictures", "Per day",
           "You called it", "Where", "Suggested name", "Already an album",
           ""]


class TripFinderDialog(QDialog):
    """Read-only candidate table. Sortable; right-click copies a row's details."""

    def __init__(self, parent, rows: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("Find Trips in the Master")
        self.resize(1040, 620)
        self._rows = rows

        layout = QVBoxLayout(self)

        known = sum(1 for r in rows if r["source"] == "known")
        found = len(rows) - known
        used = sum(1 for r in rows if r.get("existing"))
        intro = QLabel(
            f"<p><b>{len(rows)} candidate trips</b> — {known} known trips (dates from "
            f"the master's trip list) and {found} found by looking for runs of "
            f"consecutive shooting days. {used} already have an album.</p>"
            "<p>This is a <b>report</b> — nothing is created or changed. "
            "Right-click a row to copy its dates or its suggested name, so you can "
            "paste them into <i>Create Album</i>.</p>"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._table = QTableWidget(0, len(HEADERS))
        self._table.setHorizontalHeaderLabels(HEADERS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)          # fill first, then enable
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._table)

        self._fill(rows)
        self._table.setSortingEnabled(True)

        note = QLabel(
            "<p style='color:#666'>A trip with <b>0 pictures</b> is one whose photos are "
            "not in the master yet (a trip still being worked on). "
            "Low <b>per day</b> usually means time at home rather than a trip — those "
            "rank last but are kept, because a close-to-home collection is still a "
            "perfectly good album.</p>"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setToolTip("Re-scan the master — picks up albums made since "
                               "this report was opened")
        refresh_btn.clicked.connect(self._reload)
        buttons.addWidget(refresh_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    # ── table ──────────────────────────────────────────────────────────────
    def _fill(self, rows: list[dict]) -> None:
        from engine.trip_finder import suggested_name

        self._table.setRowCount(len(rows))
        grey = QBrush(QColor("#888888"))
        for i, r in enumerate(rows):
            used = bool(r.get("existing"))
            cells = [
                ("trip" if r["source"] == "known" else "found"),
                f"{r['start']}  →  {r['end']}",
                r["days"], r["pictures"], f"{r['per_day']:.0f}",
                r.get("prefix") or "", r["place"], suggested_name(r),
                r.get("existing") or "",
            ]
            for col, value in enumerate(cells):
                item = QTableWidgetItem()
                # Numbers must sort as numbers, not as text ("9" > "148").
                if col in (DAYS_COL, PICS_COL):
                    item.setData(Qt.ItemDataRole.DisplayRole, int(value))
                elif col == RATE_COL:
                    item.setData(Qt.ItemDataRole.DisplayRole, int(float(value)))
                else:
                    item.setText(str(value))
                if col in (DAYS_COL, PICS_COL, RATE_COL):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                # A row that is already a virtual trip is shown, not hidden — a
                # hidden row makes the list lie, and rebuilding one is legitimate.
                if used:
                    item.setForeground(grey)
                if col == SRC_COL and r["source"] == "known":
                    f = QFont()
                    f.setBold(True)
                    item.setFont(f)
                self._table.setItem(i, col, item)
            self._table.item(i, SRC_COL).setData(Qt.ItemDataRole.UserRole, i)

            # One button per row — the whole point of the report is to get from
            # "which dates?" to a created album without typing anything.
            btn = QPushButton("Make an album…")
            btn.setToolTip(
                f"Open Create Album with {r['start']} → {r['end']} "
                f"already filled in" + (f"\n\nAlready built as '{r['existing']}' — "
                                        "making another will not touch it."
                                        if r.get("existing") else ""))
            # No lambda-over-loop-variable: bind the row by default argument, or
            # every button would create the last row's album.
            btn.clicked.connect(lambda _checked, row=r: self._make_album(row))
            if r["pictures"] == 0:
                # Nothing to extract — the trip's pictures are not in the master yet.
                btn.setEnabled(False)
                btn.setToolTip("These pictures are not in the master yet, so an "
                               "album would come out empty.")
            self._table.setCellWidget(i, MAKE_COL, btn)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(PLACE_COL, QHeaderView.ResizeMode.Stretch)
        for col in (SRC_COL, RANGE_COL, DAYS_COL, PICS_COL, RATE_COL, PREFIX_COL,
                    NAME_COL, USED_COL, MAKE_COL):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

    # ── make an album ──────────────────────────────────────────────────────
    def _make_album(self, row: dict) -> None:
        """Hand this candidate to Create Virtual Trip, prefilled.

        Deliberately NOT a one-click build. It opens the existing, tested Create
        dialog with the dates and a suggested folder name filled in, because a
        row cannot know the things only you can supply — the title, the
        description, the trip image. What it removes is the part you cannot
        remember: the date range.

        Nothing is written here. Creating the folder and pressing Create are
        still explicit, and an album references the master's pictures rather
        than copying them, so an album you don't like is just a folder to delete.
        """
        from engine.trip_finder import suggested_name

        name = suggested_name(row)
        if row.get("existing"):
            answer = QMessageBox.question(
                self, "Already an album",
                f"This date range is already covered by <b>{row['existing']}</b>.\n\n"
                f"Create another album for {row['start']} → {row['end']} anyway?\n\n"
                "The existing one is not touched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return

        where = row.get("place", "")
        note = (f"From the report: {row['pictures']} pictures over {row['days']} days"
                + (f" — {where}" if where and not where.startswith("(no place") else ""))
        preset = {
            "folder": name,
            "fromdate": row["start"],
            "todate": row["end"],
            "title_nl": row.get("title") or (row.get("prefix") or "").strip(),
            "note": note,
        }
        logger.info(f"Find Trips: creating an album from {row['start']} → "
                    f"{row['end']} ({row['pictures']} pictures), suggested name '{name}'.")
        parent = self.parent()
        try:
            from gui.virttrip_process import VirtualTripCreationDialog
            from gui.menuspec import build_menu_spec
            spec = (build_menu_spec(parent).get("Albums", {})
                    .get("Create Album"))
        except Exception as e:                       # keep the report usable
            logger.warning(f"Find Trips: could not open Create Album: {e}")
            QMessageBox.warning(self, "Make an album",
                                f"Could not open Create Album:\n\n{e}")
            return
        if spec is None:
            QMessageBox.warning(self, "Make an album",
                                "Create Album is not available from here.")
            return
        # The report STAYS OPEN behind the modal Create dialog — it is a list you
        # work through, and closing it after one album meant re-running the scan
        # to make the next. Re-read afterwards so the "already an album" column
        # reflects what was just created.
        VirtualTripCreationDialog(parent, spec, preset=preset).exec()
        self._reload()

    def _reload(self) -> None:
        """Re-scan and repaint, so the report reflects albums made since it opened."""
        from engine.trip_finder import find_candidates
        from utils.config import _config

        master = (_config.get("master", "masterbiotadatabase", fallback="")
                  .replace('"', "").replace("'", ""))
        if not master:
            return
        virttrips = (_config.get("virttrip", "virttriprootdirectory", fallback="")
                     .replace('"', "").replace("'", ""))
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            rows = find_candidates(master, virttrips)
        except Exception as e:
            logger.warning(f"Find Trips: refresh failed: {e}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._rows = rows
        self._table.setSortingEnabled(False)
        self._fill(rows)
        self._table.setSortingEnabled(True)

    # ── copying ────────────────────────────────────────────────────────────
    def _selected_row(self) -> dict | None:
        items = self._table.selectedItems()
        if not items:
            return None
        # The model index survives sorting; the row's position does not.
        src = self._table.item(items[0].row(), SRC_COL)
        idx = src.data(Qt.ItemDataRole.UserRole) if src else None
        return self._rows[idx] if idx is not None else None

    def _copy(self, text: str, what: str) -> None:
        QApplication.clipboard().setText(text)
        logger.info(f"Find Trips: copied {what} — {text}")

    def _copy_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "Find Trips", "Select a row first.")
            return
        from engine.trip_finder import suggested_name
        self._copy(f"{row['start']} {row['end']} {suggested_name(row)}", "row")

    def _on_context_menu(self, pos) -> None:
        row = self._selected_row()
        if row is None:
            item = self._table.itemAt(pos)
            if item is None:
                return
            self._table.selectRow(item.row())
            row = self._selected_row()
            if row is None:
                return
        from engine.trip_finder import suggested_name

        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: white; color: black; }")
        act_from = QAction(f"Copy start date  ({row['start']})", self)
        act_to = QAction(f"Copy end date  ({row['end']})", self)
        act_both = QAction(f"Copy date range  ({row['start']} → {row['end']})", self)
        act_name = QAction(f"Copy suggested name  ({suggested_name(row)})", self)
        for a in (act_from, act_to, act_both, act_name):
            menu.addAction(a)
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is act_from:
            self._copy(row["start"], "start date")
        elif chosen is act_to:
            self._copy(row["end"], "end date")
        elif chosen is act_both:
            self._copy(f"{row['start']} {row['end']}", "date range")
        elif chosen is act_name:
            self._copy(suggested_name(row), "suggested name")


def show(main_window, config) -> None:
    """Menu entry point: scan the master, then show the report."""
    from engine.trip_finder import find_candidates

    master = (config.get("master", "masterbiotadatabase", fallback="")
              .replace('"', "").replace("'", ""))
    if not master:
        QMessageBox.warning(main_window, "Find Trips in the Master",
                            "No master database is configured.")
        return
    virttrips = (config.get("virttrip", "virttriprootdirectory", fallback="")
                 .replace('"', "").replace("'", ""))

    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        rows = find_candidates(master, virttrips)
    except Exception as e:
        logger.warning(f"Find Trips in the Master failed: {e}")
        QMessageBox.warning(main_window, "Find Trips in the Master",
                            f"Could not scan the master:\n\n{e}")
        return
    finally:
        QApplication.restoreOverrideCursor()

    if not rows:
        QMessageBox.information(main_window, "Find Trips in the Master",
                                "No candidate trips found in the master.")
        return
    logger.info(f"Find Trips in the Master: {len(rows)} candidate trip(s).")
    # show(), not exec(): the report is something to keep open and work beside —
    # exec() made it modal, which froze the main window and meant it could not
    # stay on screen while anything else was done. The reference is parked on the
    # main window because a modeless QDialog with no Python reference is garbage
    # collected the moment this function returns, and the window vanishes.
    dlg = TripFinderDialog(main_window, rows)
    dlg.setModal(False)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    main_window._trip_finder_dlg = dlg
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
