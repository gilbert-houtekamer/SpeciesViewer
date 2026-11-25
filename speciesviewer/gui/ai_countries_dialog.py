"""File ▸ Settings ▸ AI species lists — per country, what the AI names there.

ONE JOB: show and change, per country, which groups the AI chooses names from
(eBird's birds always; iNaturalist's mammals, reptiles, dragonflies, plants, …
as you tick them: engine.inat_lists), and fetch those lists now — on a worker
thread, so the window stays usable. A row for "All other countries" holds the
choice for every country without its own row (mammals unless changed).

INVARIANT: only the [ai] groups lines of the config and app-data
`species_lists/` + `ebird/` are written. Removing a country removes its row
only: the lists on disk stay (a country pack or a later trip can use them).
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QCompleter,
                             QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout,
                             QHeaderView, QLabel, QMenu, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout)

from gui import theme
from gui.widgets import wrapped

INTRO = (
    "For each country, choose what the AI names in your pictures there (the AI… "
    "button in culling). Birds always come from eBird's list for the country. "
    "Other groups come from iNaturalist: the species people photographed in that "
    "country. The fewer groups, the fewer wrong names: tick only what you photograph.\n"
    "The lists are kept on this computer, so the AI also works without internet "
    "once they are fetched. Plants and fungi are named from the whole picture, "
    "when no animal is found in it.")
OTHERS = "All other countries"

# Running fetches, held here and not by the dialog (Qt must not destroy a
# QThread that still runs when the Settings window closes).
_running: list = []


def _lang() -> str:
    try:
        from engine.countries import gui_language
        return gui_language()
    except Exception:
        return "en"


def country_label(region: str) -> str:
    from engine.countries import country_name
    return f"{country_name(region, _lang())} ({region})" if region else OTHERS


def groups_text(groups) -> str:
    """"Birds, Mammals, Plants" — what the AI names in words."""
    from engine import inat_lists
    return ", ".join(["Birds"] + [inat_lists.GROUPS[g][0] for g in groups
                                  if g in inat_lists.GROUPS])


def lists_text(region: str, groups) -> str:
    """What this computer has for the country, in words (no internet)."""
    from engine import ebird, inat_lists
    if not region:
        return "Fetched per country when the AI first needs them"
    have, missing, oldest = 0, [], None
    for g in groups:
        s = inat_lists.saved(region, g)
        if s is None:
            missing.append(inat_lists.GROUPS[g][0].lower())
            continue
        have += s[0]
        oldest = s[1] if oldest is None else min(oldest, s[1])
    birds = ebird.saved(region)
    parts = []
    if birds:
        parts.append(f"{birds[0]} birds")
        oldest = birds[1] if oldest is None else min(oldest, birds[1])
    elif not ebird.api_key():
        parts.append("no birds (needs the eBird key, Settings ▸ Global)")
    else:
        missing.insert(0, "birds")
    if groups and have:
        parts.append(f"{have:,} other species")
    text = " · ".join(parts)
    if oldest:
        text += f" · fetched {time.strftime('%d %b %Y', time.localtime(oldest))}"
    if missing:
        text = (text + " · " if text else "") + "not fetched yet: " + ", ".join(missing)
    return text


class _Fetch(QThread):
    step = pyqtSignal(str)
    done = pyqtSignal(str)            # what happened, in words

    def __init__(self, todo: dict, force: bool):
        """todo = {region: groups}; force = fetch again even when saved."""
        super().__init__()
        self.todo, self.force, self.stop = todo, force, False
        _running.append(self)
        self.finished.connect(lambda: _running.remove(self))

    def run(self):
        from engine import ebird, inat_lists
        failed = []
        t0 = time.time()
        try:
            for region, groups in self.todo.items():
                name = country_label(region)
                self.step.emit(f"{name}: birds (eBird)…")
                ebird.forget()
                if ebird.species(region) is None and ebird.api_key():
                    failed.append(f"{name} birds")
                for g in groups:
                    if self.stop:
                        self.done.emit("Stopped. Fetch again to get the rest.")
                        return
                    label = inat_lists.GROUPS[g][0].lower()
                    self.step.emit(f"{name}: {label}…")
                    got = inat_lists.fetch(
                        region, g, force=self.force,
                        progress=lambda s, n=name, l=label: self.step.emit(
                            f"{n}: {l} — {s.split(': ', 1)[-1]}"))
                    if got is None:
                        failed.append(f"{name} {label}")
        except Exception as e:
            failed.append(str(e))
        finally:
            inat_lists.forget()
            ebird.forget()
        self.done.emit(("Done" if not failed else
                        "Not fetched (no internet?): " + ", ".join(failed))
                       + f" — {time.time() - t0:.0f} s.")


class GroupsDialog(QDialog):
    """Tick the groups the AI names in one country."""

    def __init__(self, region: str, chosen, parent=None):
        super().__init__(parent)
        from engine import inat_lists
        self.setWindowTitle(f"What the AI names — {country_label(region)}")
        lay = QVBoxLayout(self)
        birds = QCheckBox("Birds — always (eBird's list for the country)")
        birds.setChecked(True)
        birds.setEnabled(False)
        lay.addWidget(birds)
        grid = QGridLayout()
        self._boxes: dict[str, QCheckBox] = {}
        for i, (g, (label, _inc, _out)) in enumerate(inat_lists.GROUPS.items()):
            s = inat_lists.saved(region, g) if region else None
            cb = QCheckBox(label + (f"  ({s[0]:,} species)" if s else ""))
            cb.setChecked(g in chosen)
            self._boxes[g] = cb
            grid.addWidget(cb, i // 2, i % 2)
        lay.addLayout(grid)
        note = wrapped("Plants and fungi are named from the whole picture, only when "
                       "no animal is found in it. A list not on this computer yet is "
                       "fetched when you press OK.", self)
        note.setStyleSheet(f"color: {theme.INK_FAINT};")
        lay.addWidget(note)
        row = QHBoxLayout()
        for text, on in (("All", True), ("None", False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, on=on: [cb.setChecked(on)
                                                       for cb in self._boxes.values()])
            row.addWidget(b)
        row.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)

    def chosen(self) -> list[str]:
        return [g for g, cb in self._boxes.items() if cb.isChecked()]


class AddCountryDialog(QDialog):
    """Pick a country by typing part of its name."""

    def __init__(self, taken, parent=None):
        super().__init__(parent)
        from engine.countries import all_countries
        self.setWindowTitle("Add a country")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Country (type part of its name):"))
        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for iso, name in all_countries(_lang()):
            if iso not in taken:
                self._combo.addItem(f"{name} ({iso})", iso)
        c = self._combo.completer()
        c.setFilterMode(Qt.MatchFlag.MatchContains)
        c.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._combo.setCurrentIndex(-1)
        lay.addWidget(self._combo)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(380, self.sizeHint().height())

    def region(self) -> str:
        i = self._combo.findText(self._combo.currentText())
        return self._combo.itemData(i) if i >= 0 else ""


class AiCountriesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI species lists")
        lay = QVBoxLayout(self)
        lay.addWidget(wrapped(INTRO, self))
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Country", "The AI names", "Lists on this computer"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().hide()
        self._table.setWordWrap(True)
        h = self._table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.cellDoubleClicked.connect(lambda *_: self._choose())
        self._table.itemSelectionChanged.connect(self._enable)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self._table, 1)
        self._line = wrapped("", self)
        self._line.setStyleSheet(f"color: {theme.INK_FAINT};")
        lay.addWidget(self._line)
        row = QHBoxLayout()
        self._add = QPushButton("+ Add a country…")
        self._add.clicked.connect(self._add_country)
        self._pick = QPushButton("Choose groups…")
        self._pick.clicked.connect(self._choose)
        self._fetch = QPushButton("Fetch lists now")
        self._fetch.setToolTip("Fetch this country's lists again from eBird and iNaturalist "
                               "(about a minute; a year-old list is refreshed by itself).")
        self._fetch.clicked.connect(lambda: self._start({self._region(): self._groups()}, True))
        self._stop = QPushButton("Stop")
        self._stop.clicked.connect(self.stop_work)
        self._stop.hide()
        self._remove = QPushButton("Remove country")
        self._remove.setToolTip("The country goes back to the choice of all other "
                                "countries. Its lists stay on this computer.")
        self._remove.clicked.connect(self._remove_country)
        for b in (self._add, self._pick, self._fetch, self._stop, self._remove):
            row.addWidget(b)
        row.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        lay.addLayout(row)
        self._job = None
        self._fill()
        self.resize(900, 460)

    # ── the table ──────────────────────────────────────────────────────────
    def _fill(self, select: str | None = None):
        from engine import inat_lists
        if select is None:
            select = self._region() if self._table.selectedItems() else None
        current = select
        rows = inat_lists.countries_set()
        order = sorted((r for r in rows if r), key=country_label) + [""]
        self._table.setRowCount(len(order))
        for i, region in enumerate(order):
            for c, text in enumerate((country_label(region), groups_text(rows[region]),
                                      lists_text(region, rows[region]))):
                it = QTableWidgetItem(text)
                it.setData(Qt.ItemDataRole.UserRole, region)
                it.setToolTip(text)
                self._table.setItem(i, c, it)
            if region == current:
                self._table.selectRow(i)
        self._table.resizeRowsToContents()
        if not self._table.selectedItems():
            self._table.selectRow(0)
        self._enable()

    def _region(self) -> str:
        items = self._table.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else ""

    def _groups(self) -> list[str]:
        from engine import inat_lists
        return inat_lists.countries_set().get(self._region(), [])

    def _enable(self):
        busy = self._job is not None
        has = bool(self._table.selectedItems())
        country = has and bool(self._region())
        self._pick.setEnabled(has)
        self._fetch.setEnabled(country and not busy)
        self._remove.setEnabled(country)
        self._add.setEnabled(True)

    def _menu(self, pos):
        it = self._table.itemAt(pos)
        if it is None:
            return
        self._table.selectRow(it.row())
        m = QMenu(self)
        m.addAction("Choose groups…", self._choose)
        if self._region():
            a = m.addAction("Fetch lists now",
                            lambda: self._start({self._region(): self._groups()}, True))
            a.setEnabled(self._job is None)
            m.addAction("Remove country", self._remove_country)
        m.exec(self._table.viewport().mapToGlobal(pos))

    # ── actions ────────────────────────────────────────────────────────────
    def _choose(self, region: str | None = None):
        from engine import inat_lists
        region = self._region() if region is None else region
        if not self._table.selectedItems() and region is None:
            return
        chosen = inat_lists.countries_set().get(region, inat_lists.chosen_groups(""))
        dlg = GroupsDialog(region, chosen, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        inat_lists.set_groups(region, dlg.chosen())
        self._fill(select=region)
        missing = [g for g in dlg.chosen() if inat_lists.saved(region, g) is None]
        if region and missing and self._job is None:
            self._start({region: missing}, False)
        return True

    def _add_country(self):
        from engine import inat_lists
        dlg = AddCountryDialog(set(inat_lists.countries_set()), self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.region():
            return
        self._choose(dlg.region())

    def _remove_country(self):
        from engine import inat_lists
        region = self._region()
        if region:
            inat_lists.remove_country(region)
            self._fill(select="")
            self._line.setText(f"{country_label(region)} now follows {OTHERS.lower()}; "
                               "its lists stay on this computer.")

    # ── fetching ───────────────────────────────────────────────────────────
    def _start(self, todo: dict, force: bool):
        if self._job is not None or not any(todo):
            return
        self._job = _Fetch(todo, force)
        self._job.step.connect(self._line.setText)
        self._line.setStyleSheet(f"color: {theme.WARN}; font-weight: bold;")   # busy: visible
        self._line.setText("Fetching the lists…")
        self._job.done.connect(self._on_done)
        self._stop.show()
        self._enable()
        self._job.start()

    def _on_done(self, text: str):
        self._job = None
        self._stop.hide()
        self._line.setStyleSheet(f"color: {theme.INK_FAINT};")
        self._line.setText(text)
        self._fill()

    def stop_work(self):
        if self._job is not None:
            self._job.stop = True
            self._line.setText("Stopping after this list…")

    def done(self, r):
        self.stop_work()
        super().done(r)


def show(parent) -> None:
    """The AI species lists tab of the Settings window."""
    from settings.settings_window import SettingsWindow
    SettingsWindow(parent, tab="AI species lists").exec()
