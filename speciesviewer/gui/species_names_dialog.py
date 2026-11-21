"""Other Species Names… — see, add and remove the names that point at a species.

ONE job: show the master's names table (speciesnames.csv) one row per name, with
the species it means and that species' English and Dutch name; filter it; remove
a wrong name; add a local one. These names are only used to FIND the species
(a guide's local name, an old scientific name, an older Dutch name) — they are
never shown in a report or on the website.

INVARIANTS
- Removing marks the row (engine.species_aliases.remove_aliases), never deletes
  it: the table is merged as a union with trips and the laptop, and a deleted
  row would come back. Removed rows are shown greyed, and adding one back
  un-marks it.
- Writes go to the master's table and the active trip's copy, as the species
  step's "Local name…" does.
- Selection without Ctrl/Shift: right-click ▸ Add to selection.
"""

import logging
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMenu, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from engine import species_aliases as sa
from gui import theme as _th
from gui.widgets import wrapped
from utils.config import _config

logger = logging.getLogger("speciesviewer")

COLUMNS = ["Other name", "Language", "Means", "English", "Dutch", "Kind", "From", "Removed"]
_LANG = {"local": "local", "sci": "scientific", "en": "English", "nl": "Dutch"}


def table_paths():
    """(master table, active trip table) — either may be ""."""
    def p(section, key):
        root = _config.get(section, key, fallback="").strip().strip('"')
        return sa.alias_path(root) if root and os.path.isdir(root) else ""
    return p("master", "masterbiotadatabase"), p("trip", "tripbiotadatabase")


def matches(row: dict, names: dict, text: str, mine_only: bool) -> bool:
    """Does a table row pass the filter? `names` = sci -> (English, Dutch).
    Pure, for the tests."""
    if mine_only and (row.get("Kind") or "") != "local name":
        return False
    if not text:
        return True
    en, nl = names.get((row.get("PointsTo") or "").strip().lower(), ("", ""))
    hay = " ".join([row.get("Name") or "", row.get("PointsTo") or "", en, nl,
                    row.get("Kind") or "", row.get("Source") or ""]).lower()
    return all(w in hay for w in text.lower().split())


class SpeciesNamesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Other Species Names")
        self.resize(1200, 720)
        self._master, self._trip = table_paths()
        self._sc = None
        self._rows: list[dict] = []
        self._names: dict = {}

        lay = QVBoxLayout(self)
        lay.addWidget(wrapped(
            "Other names that lead to a species: local names a guide used, and the "
            "old scientific and Dutch names the taxonomy update left behind. They are "
            "only used to find the species when you type a name — never shown on a "
            "website. Right-click a row to remove a wrong name; a removed name stays "
            "listed, greyed, so it cannot come back from a trip or the laptop."))

        top = QHBoxLayout()
        top.addWidget(QLabel("Filter:"))
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("any part of a name, species, kind or trip")
        self._filter.textChanged.connect(self._fill)
        top.addWidget(self._filter, 1)
        self._mine = QCheckBox("Only names you added")
        self._mine.setChecked(True)
        self._mine.toggled.connect(self._fill)
        top.addWidget(self._mine)
        self._show_removed = QCheckBox("Show removed")
        self._show_removed.setChecked(True)
        self._show_removed.toggled.connect(self._fill)
        top.addWidget(self._show_removed)
        lay.addLayout(top)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self._table, 1)
        # Keep references: a QShortcut without one is collected.
        self._del_key = QShortcut(QKeySequence(Qt.Key.Key_Delete), self._table,
                                  activated=lambda: self._remove_selected())

        self._count = QLabel("")
        lay.addWidget(self._count)

        bottom = QHBoxLayout()
        add = QPushButton("Add a name…")
        add.clicked.connect(self._add)
        bottom.addWidget(add)
        self._remove_btn = QPushButton("Remove…")
        self._remove_btn.clicked.connect(lambda: self._remove_selected())
        bottom.addWidget(self._remove_btn)
        bottom.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        bottom.addWidget(close)
        lay.addLayout(bottom)

        self._load()

    # ── data ─────────────────────────────────────────────────────────────
    def _load(self):
        from gui.culling_species import load_species_classification
        self._sc = load_species_classification()
        self._names = {}
        if self._sc is not None:
            for sci, row in self._sc.sc_dict.items():
                self._names[sci.strip().lower()] = (row.get("EnglishName") or "",
                                                    row.get("DutchName") or "")
        _h, self._rows = sa.read_aliases(self._master or self._trip)
        self._fill()

    def _fill(self, *_):
        text = self._filter.text().strip()
        mine, removed = self._mine.isChecked(), self._show_removed.isChecked()
        shown = [r for r in self._rows if matches(r, self._names, text, mine)
                 and (removed or not sa.is_removed(r))]
        t = self._table
        t.setSortingEnabled(False)
        t.setRowCount(len(shown))
        faint = QColor(_th.INK_FAINT)
        for i, r in enumerate(shown):
            sci = (r.get("PointsTo") or "").strip().lower()
            en, nl = self._names.get(sci, ("", ""))
            vals = [r.get("Name") or "", _LANG.get(r.get("Language") or "", r.get("Language") or ""),
                    sci, en, nl, r.get("Kind") or "", r.get("Source") or "",
                    r.get("Removed") or ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.ItemDataRole.UserRole, sa._key(r))
                if sa.is_removed(r):
                    it.setForeground(faint)
                t.setItem(i, c, it)
        t.setSortingEnabled(True)
        t.resizeColumnsToContents()
        live = sum(1 for r in self._rows if not sa.is_removed(r))
        self._count.setText(f"{len(shown):,} shown · {live:,} names in use"
                            + (f" · {len(self._rows) - live:,} removed"
                               if len(self._rows) > live else ""))

    def _selected_keys(self):
        rows = {i.row() for i in self._table.selectedIndexes()}
        return [self._table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in sorted(rows)]

    # ── actions ──────────────────────────────────────────────────────────
    def _menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        m = QMenu(self)
        sel = self._table.selectionModel()
        idx = self._table.model().index(row, 0)
        if sel.isRowSelected(row, idx.parent()):
            a = QAction("Take out of the selection", m)
            a.triggered.connect(lambda: sel.select(
                idx, sel.SelectionFlag.Deselect | sel.SelectionFlag.Rows))
        else:
            a = QAction("Add to selection", m)
            a.triggered.connect(lambda: sel.select(
                idx, sel.SelectionFlag.Select | sel.SelectionFlag.Rows))
        m.addAction(a)
        m.addSeparator()
        # Remove acts on the selection; with nothing selected, on this row.
        here = self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        rem = QAction("Remove…\tDel", m)
        rem.triggered.connect(lambda: self._remove_selected(
            None if self._selected_keys() else [here]))
        m.addAction(rem)
        m.exec(self._table.viewport().mapToGlobal(pos))

    def _remove_selected(self, keys=None):
        keys = [k for k in (keys or self._selected_keys())
                if not any(sa._key(r) == k and sa.is_removed(r) for r in self._rows)]
        if not keys:
            return
        if not self._master:
            QMessageBox.warning(self, "Remove", "The master is not reachable, so the "
                                "names table cannot be changed. Reconnect it and try again.")
            return
        listing = "\n".join(f"   {n}  →  {to}" for n, _l, to in keys[:15])
        more = f"\n   … and {len(keys) - 15} more" if len(keys) > 15 else ""
        if QMessageBox.question(
                self, "Remove",
                f"Remove {len(keys)} name(s)? Typing them will no longer find the "
                f"species:\n\n{listing}{more}\n\nThe species themselves are not touched. "
                f"The names stay listed as removed, and can be added again.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            sa.remove_aliases([self._master, self._trip], keys)
        except Exception as e:
            QMessageBox.critical(self, "Remove", f"The names table could not be saved:\n{e}")
            return
        self._load()

    def _add(self):
        if self._sc is None or not self._master:
            QMessageBox.warning(self, "Add a name", "The master's species list is not "
                                "reachable. Reconnect the master and try again.")
            return
        name, ok = QInputDialog.getText(self, "Add a name",
                                        "The other name (for example a guide's local name):")
        name = (name or "").strip()
        if not ok or not name:
            return
        from gui.culling_species import LocalNameDialog
        dlg = LocalNameDialog(self._sc, name, self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.sci:
            return
        problem = sa.alias_problem(self._sc, name, dlg.sci)
        if problem:
            QMessageBox.warning(self, "Add a name", problem)
            return
        trip = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(self._trip)))) \
            if self._trip else "by hand"
        try:
            sa.add_alias([self._master, self._trip], name, dlg.sci, source=trip)
        except Exception as e:
            QMessageBox.critical(self, "Add a name", f"The names table could not be saved:\n{e}")
            return
        self._filter.setText(name.lower())
        self._load()


def open_species_names(parent):
    dlg = SpeciesNamesDialog(parent)
    parent._species_names_dialog = dlg          # keep a ref (PyQt GC)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
