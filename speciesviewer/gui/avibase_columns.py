"""Say what each column of a species list holds, with the list on screen.

ONE JOB: turn a Birding Breaks list — the .xlsx the tour operator sends, with
title rows above the real header and a box per trip day, or a CSV massaged out
of one — into a column map the reader can follow. Nothing here parses species;
it only records which column is what, and from which row the species start.

INVARIANT: a column left as "not used" is never read. That is what keeps the
day boxes, the spacer columns and the title rows out of the checklist.
"""

import logging
import os

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QAbstractItemView,
)
from PyQt6.QtGui import QColor

from engine.avibaselib import (ROLE_KEYS, ROLE_LABELS, load_source_rows,
                              guess_columns, column_title, describe_roles)

logger = logging.getLogger("speciesviewer")

NOT_USED = "not used"
_MUTED = QColor("#8a8a8a")          # readable on the light and the dark theme

def fixed_layout_complaint(path):
    """Why the fixed AviBase layout cannot read this file, or None.

    The fixed reader splits on semicolons. Handed a spreadsheet or the
    tab-separated text the AviBase page produces, it does not fail — it returns
    a list in which every line became a section heading, and the checklist comes
    out with no species in it at all.
    """
    from engine.avibaselib import sniff_delimiter
    if os.path.splitext(path)[1].lower() in (".xlsx", ".xlsm"):
        return ("❌ A spreadsheet cannot be read with the fixed AviBase layout — "
                "choose “Columns I marked myself”.")
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            lines = f.read().splitlines()[:50]
    except OSError as err:
        return f"❌ Cannot read {os.path.basename(path)}: {err}"
    found = sniff_delimiter(lines)
    if found == ";":
        return None
    named = {"\t": "tabs", ",": "commas", "|": "bars"}.get(found, found)
    return (f"❌ {os.path.basename(path)} is separated by {named}, and the fixed "
            f"AviBase layout expects semicolons — it would produce a checklist of "
            f"headings with no species. Choose “Columns I marked myself” "
            f"and mark the columns.")


class ColumnMappingDialog(QDialog):
    PREVIEW_ROWS = 50

    def __init__(self, parent, path, roles=None, first_data_row=1, delimiter=None):
        super().__init__(parent)
        self.setWindowTitle("Which column holds what")
        self.resize(900, 560)

        self.path = path
        self.roles = dict(roles or {})
        self.first_data_row = max(1, int(first_data_row or 1))
        self.delimiter = delimiter
        self.is_spreadsheet = os.path.splitext(path)[1].lower() in (".xlsx", ".xlsm")
        self._rows = []
        self._combos = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{os.path.basename(path)}</b>"))
        self.hint = QLabel(
            "Mark what each column holds. A column left as “not used” is "
            "ignored, so the day boxes and the empty columns do no harm. Dutch "
            "names are optional — missing ones are filled in from the species "
            "database."
        )
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Species start on row"))
        self.row_spin = QSpinBox()
        self.row_spin.setMinimum(1)
        self.row_spin.setMaximum(self.PREVIEW_ROWS)
        controls.addWidget(self.row_spin)

        self.delim_dropdown = None
        if not self.is_spreadsheet:
            controls.addSpacing(20)
            controls.addWidget(QLabel("Separator"))
            self.delim_dropdown = QComboBox()
            for label, value in (("detect", None), ("semicolon  ;", ";"),
                                 ("comma  ,", ","), ("tab", "\t"), ("bar  |", "|")):
                self.delim_dropdown.addItem(label, value)
            controls.addWidget(self.delim_dropdown)
        controls.addStretch()
        layout.addLayout(controls)

        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        layout.addWidget(self.table)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.ok_btn = QPushButton("Use these columns")
        self.cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(self.ok_btn)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

        self.ok_btn.clicked.connect(self._on_accept)
        self.cancel_btn.clicked.connect(self.reject)
        self.row_spin.valueChanged.connect(self._on_first_row_changed)
        if self.delim_dropdown is not None:
            self.delim_dropdown.currentIndexChanged.connect(self._on_delimiter_changed)

        self._load()

    # ── loading and painting ────────────────────────────────────────────────
    def _load(self):
        try:
            self._rows, sniffed = load_source_rows(
                self.path, self.delimiter, limit=self.PREVIEW_ROWS)
        except Exception as err:
            self._rows = []
            self.status_label.setText(f"❌ Cannot read this file: {err}")
            self.table.clear()
            return

        if not self.is_spreadsheet and self.delimiter is None and sniffed:
            # Say what "detect" landed on, so a wrong guess is visible rather
            # than mysterious.
            shown = {"\t": "tab"}.get(sniffed, sniffed)
            self.status_label.setText(f"Separator detected: {shown}")

        if not self.roles:
            self.roles, self.first_data_row = guess_columns(
                self._rows, os.path.basename(self.path))

        self.row_spin.setMaximum(max(1, len(self._rows)))
        self.row_spin.blockSignals(True)
        self.row_spin.setValue(min(self.first_data_row, self.row_spin.maximum()))
        self.row_spin.blockSignals(False)
        self._build_table()

    def _column_count(self):
        return max((len(r) for r in self._rows), default=0)

    def _build_table(self):
        ncols = self._column_count()
        self.table.clear()
        self.table.setColumnCount(ncols)
        self.table.setRowCount(len(self._rows) + 1)
        self.table.setHorizontalHeaderLabels([column_title(c) for c in range(ncols)])
        self.table.setVerticalHeaderLabels(
            ["what it holds"] + [str(i + 1) for i in range(len(self._rows))])

        self._combos = []
        for col in range(ncols):
            combo = QComboBox()
            combo.addItem(NOT_USED, None)
            for key in ROLE_KEYS:
                combo.addItem(ROLE_LABELS[key], key)
            role = self.roles.get(col)
            if role in ROLE_KEYS:
                combo.setCurrentIndex(1 + ROLE_KEYS.index(role))
            combo.currentIndexChanged.connect(
                lambda _idx, c=col: self._on_role_changed(c))
            self.table.setCellWidget(0, col, combo)
            self._combos.append(combo)

        for r, row in enumerate(self._rows, start=1):
            for col in range(ncols):
                text = row[col] if col < len(row) else ""
                self.table.setItem(r, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        self._paint_skipped_rows()

    def _paint_skipped_rows(self):
        """Grey the rows above the first species row — the title rows the reader
        will step over. Seeing which rows are out is the whole point of the
        preview."""
        first = self.row_spin.value()
        for r in range(1, self.table.rowCount()):
            muted = r < first
            for col in range(self.table.columnCount()):
                item = self.table.item(r, col)
                if item is not None:
                    item.setForeground(_MUTED if muted else QColor())

    # ── reacting ────────────────────────────────────────────────────────────
    def _on_role_changed(self, col):
        role = self._combos[col].currentData()
        if role:
            # One column per job: taking a role hands it over, rather than
            # leaving two columns both claiming to be the English name.
            for other, combo in enumerate(self._combos):
                if other != col and combo.currentData() == role:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(0)
                    combo.blockSignals(False)
                    self.roles.pop(other, None)
            self.roles[col] = role
        else:
            self.roles.pop(col, None)

    def _on_first_row_changed(self, value):
        self.first_data_row = value
        self._paint_skipped_rows()

    def _on_delimiter_changed(self):
        self.delimiter = self.delim_dropdown.currentData()
        self._load()

    def _on_accept(self):
        if not any(role in ("en", "nl", "sc") for role in self.roles.values()):
            self.status_label.setText(
                "❌ Mark at least one name column — English, Dutch or "
                "scientific. Without a name there is nothing to look species up by."
            )
            return
        self.first_data_row = self.row_spin.value()
        self.accept()
