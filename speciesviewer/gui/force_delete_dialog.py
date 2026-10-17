"""Trips ▸ Advanced ▸ Delete Trip Completely… and Albums ▸ Delete Album…
(docs/plans/force_delete_plan.md).

ONE JOB: show every trip (or album) name and what exists for it, let the user
pick one and tick the parts, confirm once, then run engine.force_delete and
report. Two dialogs for the user — trips and albums are different things —
one class and one worker underneath.

Pictures and the master are never touched. The website parts are offered only
on a computer that owns the website (engine.force_delete.website_parts_allowed).
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog,
                             QHBoxLayout, QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout)

from engine import force_delete as fd
from gui import theme
from gui.wait_picture import run_waiting
from gui.widgets import wrapped
from utils.config import _config, get_trips_root, get_virttrips_root, set_setting

logger = logging.getLogger("speciesviewer")

_TEXT = {
    "trip": {
        "title": "Delete Trip Completely",
        "intro": ("For a trip that never became a real one — a test, a mistake, or a "
                  "copy that now lives on your other computer. The normal end of a "
                  "trip is To Master ▸ Retire. <b>Your pictures and the master are "
                  "never touched.</b>"),
        "column": "Trip",
    },
    "album": {
        "title": "Delete Album",
        "intro": ("An album is its settings, texts and blogs — its pictures are the "
                  "master's and stay there. <b>Your pictures and the master are "
                  "never touched.</b>"),
        "column": "Album",
    },
}


class DeleteDialog(QDialog):
    def __init__(self, main_window, kind: str):
        super().__init__(main_window)
        self.main_window = main_window
        self.kind = kind
        self.text = _TEXT[kind]
        self.setWindowTitle(self.text["title"])
        self.resize(820, 560)
        self._rows: list[dict] = []
        self._web_problem = ""

        lay = QVBoxLayout(self)
        lay.addWidget(wrapped(self.text["intro"], self))

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [self.text["column"], "On this computer", "On the trip list", "Published", "Size"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_pick)
        lay.addWidget(self.table, 1)

        self.cb_folder = QCheckBox()
        self.cb_list = QCheckBox("The row on the trip list")
        self.cb_pages = QCheckBox()
        self.cb_backup = QCheckBox()
        for cb in (self.cb_folder, self.cb_list, self.cb_pages, self.cb_backup):
            lay.addWidget(cb)
            cb.toggled.connect(self._refresh_button)
        self.cb_folder.toggled.connect(lambda on: self.cb_backup.setEnabled(on))
        self.note = wrapped("", self)
        self.note.setStyleSheet(f"color: {theme.INK_FAINT};")
        lay.addWidget(self.note)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.delete_btn = QPushButton("Delete…")
        self.delete_btn.clicked.connect(self._do_delete)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(self.delete_btn)
        buttons.addWidget(close)
        lay.addLayout(buttons)

        self._load()
        self._select_active()

    # ── the list ─────────────────────────────────────────────────────────────
    def _load(self):
        master = _config.get("master", "masterbiotadatabase", fallback="").strip()

        def look():                          # off the GUI thread: the website is asked
            listed, published, problem = fd.website_state(master)
            rows = fd.scan(self.kind, get_trips_root(), get_virttrips_root(),
                           master, listed, published or set())
            return listed, published, problem, rows
        listed, published, self._web_problem, self._rows = run_waiting(
            self.main_window, f"Looking up your {'trips' if self.kind == 'trip' else 'albums'} "
                              f"and what is on the website…", look)
        self._published_known = published is not None
        self.table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            here = r["folder"].name if r["folder"] else "— no folder"
            if r["other_master"]:
                here += "  (another master's)"
            pages = ("yes" if r["pages"] else "—") if self._published_known else "?"
            cells = [r["name"], here, "yes" if r["listed"] else "—", pages,
                     fd.human(r["size"]) if r["folder"] else ""]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if r["other_master"]:
                    item.setForeground(Qt.GlobalColor.gray)
                self.table.setItem(i, c, item)
        self.table.resizeColumnsToContents()
        self._on_pick()

    def _active_folder(self) -> str:
        section, key = (("trip", "tripbiotadatabase") if self.kind == "trip"
                        else ("virttrip", "virttripbiotadatabase"))
        return _config.get(section, key, fallback="").strip()

    def _select_active(self):
        """Start on the open trip/album, if it is in the list."""
        active = self._active_folder()
        for i, r in enumerate(self._rows):
            if active and r["folder"] and _same(r["folder"], active):
                self.table.selectRow(i)
                return

    def _picked(self) -> dict | None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        return self._rows[rows[0].row()] if rows else None

    def _on_pick(self):
        r = self._picked()
        boxes = (self.cb_folder, self.cb_list, self.cb_pages, self.cb_backup)
        if r is None:
            for cb in boxes:
                cb.setChecked(False)
                cb.setEnabled(False)
            self.cb_folder.setText("SpeciesViewer's files")
            self.cb_pages.setText("The published pages")
            self.cb_backup.setText("Keep a backup zip")
            self.note.setText(f"Pick {'a trip' if self.kind == 'trip' else 'an album'}.")
            self._refresh_button()
            return
        own = not r["other_master"]
        if r["folder"]:
            self.cb_folder.setText(f"SpeciesViewer's files — {r['folder']}  ({fd.human(r['size'])})")
            self.cb_backup.setText(f"Keep a backup zip in .trash  ({fd.human(r['size'])} before compression)")
        else:
            self.cb_folder.setText("SpeciesViewer's files — no folder")
            self.cb_backup.setText("Keep a backup zip")
        self.cb_folder.setEnabled(bool(r["folder"]) and own)
        self.cb_folder.setChecked(bool(r["folder"]) and own)
        self.cb_backup.setChecked(False)
        self.cb_backup.setEnabled(self.cb_folder.isChecked())

        self.cb_pages.setText(f"The published pages — /trips/{r['name']}/")
        web = (fd.website_parts_allowed() and not self._web_problem and own
               and not r["album_from_trip"])
        self.cb_list.setEnabled(web and r["listed"])
        self.cb_list.setChecked(web and r["listed"])
        self.cb_pages.setEnabled(web and r["pages"])
        self.cb_pages.setChecked(web and r["pages"])

        notes = []
        if r["other_master"]:
            notes.append(f"This {self.kind} belongs to another master ({r['owner']}). "
                         "Open that master to delete it.")
        if r["album_from_trip"]:
            notes.append(f"The album “{r['album_from_trip']}” was made from this trip: "
                         "the web address and the trip-list row are the album's now. "
                         "Delete the album for those.")
        if self._web_problem and (r["listed"] or r["pages"] or not r["folder"]
                                  or not self._published_known):
            notes.append(self._web_problem)
        if not r["folder"] and own and not self._web_problem:
            notes.append(f"There is no folder for this {self.kind} — only its row on "
                         "the trip list and its published pages are left. Removing "
                         "them removes it from the website.")
        if r["pictures"]:
            notes.append(f"Your pictures stay in {r['pictures']}.")
        if self.kind == "trip":
            notes.append("If this trip was added to the master, its pictures stay there.")
        self.note.setText("\n".join(notes))
        self._refresh_button()

    def _refresh_button(self, *_):
        self.delete_btn.setEnabled(any(cb.isEnabled() and cb.isChecked()
                                       for cb in (self.cb_folder, self.cb_list, self.cb_pages)))

    # ── delete ───────────────────────────────────────────────────────────────
    def _do_delete(self):
        r = self._picked()
        if r is None:
            return
        do_folder = self.cb_folder.isEnabled() and self.cb_folder.isChecked()
        do_list = self.cb_list.isEnabled() and self.cb_list.isChecked()
        do_pages = self.cb_pages.isEnabled() and self.cb_pages.isChecked()
        backup = do_folder and self.cb_backup.isChecked()
        items = []
        if do_folder:
            items.append(f"• the folder {r['folder']} ({fd.human(r['size'])})"
                         + (" — a backup zip is kept in .trash" if backup else " — no backup"))
        if do_list:
            items.append(f"• “{r['name']}” from the trip list")
        if do_pages:
            items.append(f"• the published pages /trips/{r['name']}/")
        title = self.text["title"]
        if QMessageBox.question(
                self, title, f"Delete for “{r['name']}”:\n\n" + "\n".join(items)
                + "\n\nYour pictures and the master are not touched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        was_active = do_folder and _same(r["folder"], self._active_folder())
        if was_active and self.kind == "trip":       # GUI: before the folder goes
            from gui.trip_retire import _deselect_active_trip
            _deselect_active_trip(self.main_window)
        if do_folder:
            _release_watches(r["folder"])

        def work():                              # off the GUI thread, in a wait box
            out = []
            # Website first, folder last: a website failure leaves the folder
            # to try again with.
            if do_list:
                try:
                    fd.remove_from_trip_list(r["name"], self.kind)
                    out.append(f"✅ Removed “{r['name']}” from the trip list.")
                except Exception as e:
                    out.append(f"❌ Trip list: {e}")
            if do_pages:
                try:
                    fd.remove_pages(r["name"])
                    out.append(f"✅ Removed the published pages /trips/{r['name']}/.")
                except Exception as e:
                    out.append(f"❌ Published pages: {e}")
            folder_err = fd.delete_folder(r["folder"], backup=backup) if do_folder else None
            return out, folder_err
        report, folder_err = run_waiting(self, f"Deleting “{r['name']}”…", work)
        if do_folder:
            report.append(self._after_folder(r, backup, was_active, folder_err))

        for line in report:
            (logger.warning if line.startswith("❌") else logger.info)(f"{title} — {line}")
        if r["pictures"]:
            report.append(f"\nYour pictures are still in {r['pictures']}.")
        QMessageBox.information(self, title, "\n".join(report))
        self._load()

    def _after_folder(self, r, backup, was_active, err) -> str:
        """The GUI follow-up once the folder delete (in the wait box) is done."""
        folder = r["folder"]
        if err:
            return f"❌ Folder: {err}"
        try:
            if self.kind == "trip":
                from gui.trip_retire import _forget_trip
                _forget_trip(self.main_window, str(folder), r["name"])
            else:
                if was_active:
                    set_setting("virttrip", "virttripbiotadatabase", "")
                    set_setting("virttrip", "virttripbiotawebroot", "")
                from gui.virttrip_overview import populate_virttrip_overview
                populate_virttrip_overview(self.main_window)
                self.main_window.virttrip_panel.rebuild()
        except Exception as e:
            logger.info(f"{self.text['title']}: cleanup skipped: {e}")
        return (f"✅ Deleted {folder} ({fd.human(r['size'])})"
                + (" — backup in .trash." if backup else "."))


def _release_watches(folder) -> int:
    """Make every open window stop watching paths inside `folder`.

    The culling viewer and the Trip Species Viewer watch a day's folders for
    PhotoLab edits, and on Windows a watched folder is held open — the delete
    then stops on WinError 32 for as long as SpeciesViewer runs (Greece and
    waarneming, 2026-10-17: gone at once after closing the app). Returns how
    many paths were let go."""
    from PyQt6.QtCore import QFileSystemWatcher
    try:
        root = Path(folder).resolve()
    except OSError:
        return 0
    released = 0
    for top in QApplication.topLevelWidgets():
        for w in top.findChildren(QFileSystemWatcher):
            inside = []
            for p in w.files() + w.directories():
                try:
                    if Path(p).resolve().is_relative_to(root):
                        inside.append(p)
                except OSError:
                    continue
            if inside:
                w.removePaths(inside)
                released += len(inside)
    if released:
        logger.info(f"Stopped watching {released} path(s) in {root.name} before the delete.")
    return released


def _same(a, b) -> bool:
    try:
        return bool(a) and bool(b) and Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


class DeleteTripDialog(DeleteDialog):
    def __init__(self, main_window):
        super().__init__(main_window, "trip")


class DeleteAlbumDialog(DeleteDialog):
    def __init__(self, main_window):
        super().__init__(main_window, "album")
