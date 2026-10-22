"""Master ▸ Masters — switch between masters, add, rename and remove one, move the
"main" flag (docs/plans/multiple_masters_plan.md, steps 3-4).

ONE JOB: the GUI over utils/masters.py. Every change that alters which master
is open goes through a restart: the whole app caches the open master's state,
so a live switch would leave half of it behind.
"""

from __future__ import annotations

import os
import subprocess
import sys

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QInputDialog, QLineEdit,
                             QMessageBox, QRadioButton, QVBoxLayout)

from gui.menu import MenuSpec
from settings.reposettings import RepoSettingsDialog
from gui.widgets import wrapped
from utils import masters


def restart_into(main_window, name: str) -> None:
    """Make `name` the active master and restart SpeciesViewer.

    The main window is closed FIRST — its closeEvent saves this master's
    settings and flushes open grids — and only then is the new process
    started, with SV_MASTER set to the new master (this process's own
    SV_MASTER names the OLD one and would otherwise be inherited)."""
    masters.set_active(name)
    main_window.close()
    from utils.single_instance import ENV_RESTARTING
    _start_detached({**os.environ, masters.ENV_VAR: name, ENV_RESTARTING: "1"})


def _start_detached(env: dict) -> None:
    """Start a new SpeciesViewer that outlives this one.

    On Windows it must leave this process's JOB: VS Code's debugger (F5) puts
    the app in a job that kills every child when the session ends — the new
    SpeciesViewer was started and killed at once. The debugger's job allows
    breaking away (JOB_OBJECT_LIMIT_BREAKAWAY_OK); a job that does not makes
    CreateProcess fail, and then we start without it. Output goes to DEVNULL:
    the log file has it, and a detached process has no console to write to."""
    if getattr(sys, "frozen", False):   # packaged build: the .exe IS the program
        cmd = [sys.executable, *sys.argv[1:]]
    else:
        cmd = [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]]
    kwargs = dict(env=env, cwd=os.getcwd(), stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform.startswith("win"):
        base = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            subprocess.Popen(cmd, creationflags=base | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except OSError:
            subprocess.Popen(cmd, creationflags=base, **kwargs)
    else:
        subprocess.Popen(cmd, start_new_session=True, **kwargs)


def _ask_restart(main_window, name: str, why: str) -> None:
    answer = QMessageBox.question(
        main_window, "Restart SpeciesViewer",
        f"{why}\n\nRestart SpeciesViewer now?")
    if answer == QMessageBox.StandardButton.Yes:
        restart_into(main_window, name)


def _switch(main_window, name: str) -> None:
    answer = QMessageBox.question(
        main_window, "Switch master",
        f"Switch to the master “{name}”?\n\n"
        "SpeciesViewer saves your work, closes and starts again "
        "with that master.")
    if answer == QMessageBox.StandardButton.Yes:
        restart_into(main_window, name)


class NewMasterDialog(QDialog):
    """Name + how it starts: empty (what a brand-new user sees) or with this
    computer's settings copied from the main master — and, as part of making
    it (Gilbert: creating a collection is not "configuration"), optionally a
    new, empty collection in an empty folder. Mistakes keep the dialog open."""

    def __init__(self, parent):
        from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QPushButton
        super().__init__(parent)
        self.setWindowTitle("New Master")
        layout = QVBoxLayout(self)
        layout.addWidget(wrapped(
            "A new master has its own settings, trips and albums. Your existing "
            "masters are not touched while you work in it.", self))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name, e.g. Birds or Plants")
        layout.addWidget(self.name)
        self.empty = QRadioButton("Start with SpeciesViewer's default settings")
        self.copy = QRadioButton("Copy this computer's settings from the main master "
                                 "(PhotoLab, window sizes — no data, no website)")
        self.empty.setChecked(True)
        layout.addWidget(self.empty)
        layout.addWidget(self.copy)
        self.with_collection = QCheckBox(
            "Also create a new, empty collection in this folder (it must be empty or new):")
        self.with_collection.setChecked(True)
        layout.addWidget(self.with_collection)
        row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText(r"e.g. D:\Nature\Plants")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        # The folder follows the name (<base>/<name>/nature) until it is
        # edited; with a folder named "nature" the master also gets its own
        # trips, albums and pictures folders beside it — masters do not share.
        self._folder_follows = True
        self.folder.textEdited.connect(self._folder_edited)
        self.name.textChanged.connect(self._suggest_folder)
        self.layout_note = wrapped("", self)
        layout.addWidget(self.layout_note)
        self.folder.textChanged.connect(self._show_layout)
        self.with_collection.toggled.connect(self.folder.setEnabled)
        self.with_collection.toggled.connect(browse.setEnabled)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._check_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(560, self.sizeHint().height())

    def _browse(self):
        from PyQt6.QtWidgets import QFileDialog
        from utils.start_folder import start_folder
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose an EMPTY folder for the new collection", start_folder())
        if chosen:
            self._folder_follows = False
            self.folder.setText(chosen)

    def _folder_edited(self, _text):
        self._folder_follows = False

    def _suggest_folder(self, name):
        from utils import default_folders
        if self._folder_follows:
            name = name.strip()
            self.folder.setText(str(default_folders.master_folder(name, main=False))
                                if name else "")

    def _show_layout(self, text):
        from pathlib import Path
        from engine.master_create import sibling_folders
        sib = sibling_folders(Path(text.strip())) if text.strip() else None
        self.layout_note.setText(
            ("Its own trips, albums and pictures folders go beside it: "
             + ", ".join(str(p) for p in sib.values()) + ".") if sib else "")

    def _check_and_accept(self):
        """Everything that could fail cheaply is checked BEFORE anything is
        made: a taken name or a full folder must not leave half a master."""
        from pathlib import Path
        from engine.master_create import CreateMasterError, check_folder
        try:
            masters.check_new_name(self.name.text())
            if self.with_collection.isChecked():
                if not self.folder.text().strip():
                    raise CreateMasterError("Choose a folder for the collection, "
                                            "or untick the option.")
                check_folder(Path(self.folder.text().strip()))
        except (masters.MasterError, CreateMasterError) as e:
            QMessageBox.warning(self, "New Master", str(e))
            return
        self.accept()


def _new_master(main_window) -> None:
    from pathlib import Path
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    from engine.master_create import make_collection, point_master_at, point_master_folders
    from utils.config import CONFIG_FILENAME
    dlg = NewMasterDialog(main_window)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return
    source = None
    if dlg.copy.isChecked():
        main = masters.main_master(masters.load())
        source = masters.folder_of(main) / "config" / CONFIG_FILENAME
    folder = Path(dlg.folder.text().strip()) if dlg.with_collection.isChecked() else None
    # Collection first, then the master: if the collection fails there is no
    # master pointing at half a folder.
    if folder:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            make_collection(folder)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            main_window.logger.warning(f"New Master: collection failed: {e}")
            QMessageBox.warning(main_window, "New Master",
                                f"The collection could not be created:\n{e}\n\n"
                                "No master was added.")
            return
        QApplication.restoreOverrideCursor()
    try:
        entry = masters.create(dlg.name.text(), copy_machine_settings_from=source)
        if folder:
            config_file = masters.folder_of(entry) / "config" / CONFIG_FILENAME
            point_master_at(config_file, folder)
            point_master_folders(config_file, folder)
    except (masters.MasterError, OSError) as e:
        QMessageBox.warning(main_window, "New Master", str(e))
        return
    main_window.logger.info(f"New master “{entry['name']}” → {masters.folder_of(entry)}"
                            + (f", collection in {folder}" if folder else ""))
    main_window.rebuild_menu()            # list it in Master ▸ Masters
    _ask_restart(main_window, entry["name"],
                 f"The master “{entry['name']}” is ready"
                 + (f", with its collection in {folder}." if folder else "."))


def _make_main(main_window) -> None:
    name = masters.ACTIVE["name"]
    old = masters.main_master(masters.load())["name"]
    answer = QMessageBox.question(
        main_window, "Make main master",
        f"Make “{name}” your main master?\n\n"
        f"“{old}” stays available as one of your masters. Nothing is moved or deleted.")
    if answer != QMessageBox.StandardButton.Yes:
        return
    masters.make_main(name)
    main_window.logger.info(f"Main master is now “{name}” (was “{old}”).")
    main_window.rebuild_menu()
    _ask_restart(main_window, name,
                 "The window shows the change after a restart.")


def _rename(main_window) -> None:
    names = [m["name"] for m in masters.load()["masters"]]
    old, ok = QInputDialog.getItem(main_window, "Rename Master",
                                   "Master to rename:", names,
                                   names.index(masters.ACTIVE["name"]), False)
    if not ok:
        return
    new, ok = QInputDialog.getText(main_window, "Rename Master",
                                   f"New name for “{old}”:", text=old)
    if not ok or new.strip() == old:
        return
    try:
        masters.rename(old, new)
    except masters.MasterError as e:
        QMessageBox.warning(main_window, "Rename Master", str(e))
        return
    main_window.logger.info(f"Master “{old}” renamed to “{new.strip()}”.")
    main_window.refresh_master_banner()
    main_window.rebuild_menu()


def _remove(main_window) -> None:
    data = masters.load()
    removable = [m["name"] for m in data["masters"]
                 if not m.get("main") and m["name"] not in (masters.ACTIVE["name"], data["active"])]
    if not removable:
        QMessageBox.information(
            main_window, "Remove Master",
            "There is no master to remove. The main master and the open master "
            "cannot be removed.")
        return
    name, ok = QInputDialog.getItem(main_window, "Remove Master",
                                    "Master to remove:", removable, 0, False)
    if not ok:
        return
    # What "also delete its data" would delete — read from THAT master's own
    # settings, shown before anything happens (docs/plans/force_delete_plan.md B).
    from engine import master_purge
    from engine.force_delete import human
    try:
        data_plan = master_purge.plan(name)
    except Exception as e:
        main_window.logger.info(f"Remove Master: could not list {name}'s data: {e}")
        data_plan = None
    # Fool-proof (Gilbert half-read an unticked checkbox, 2026-10-12): the
    # choice is IN the buttons — "settings only" or "with its data (size)" —
    # and deleting data is confirmed once more, with the size and "no backup".
    has_data = bool(data_plan and (data_plan["collection"] or data_plan["trips"]
                                   or data_plan["albums"]))
    text = (f"Remove the master “{name}” from SpeciesViewer.\n\n"
            "Settings only: its settings and remembered state are zipped to the "
            "trash; its website on this computer is deleted (it can be published "
            "again from the collection); its collection, trips and albums stay on disk.")
    master_pictures = 0
    if has_data:
        def _line(kind, f):
            n = data_plan["pictures_in"].get(str(f), 0)
            what = (f", {n} pictures" if kind == "collection" else f", copies of {n} pictures") if n else ""
            return f"• {kind} {f} ({human(data_plan['sizes'][str(f)])}{what})"
        lines = []
        if data_plan["collection"]:
            lines.append(_line("collection", data_plan["collection"]))
            master_pictures = data_plan["pictures_in"].get(str(data_plan["collection"]), 0)
        lines += [_line("trip", f) for f in data_plan["trips"]]
        lines += [_line("album", f) for f in data_plan["albums"]]
        kept = []
        if data_plan["other_trips"]:
            kept.append(f"{len(data_plan['other_trips'])} trip(s)/album(s) of other "
                        "masters in the same folders")
        kept += [f"{p} — {why}" for p, why in data_plan["skipped"]]
        # "Its data" made Gilbert think of his pictures and ratings: say what
        # these are — SpeciesViewer's own files — and what is never touched.
        text += ("\n\nWith SpeciesViewer's files: ALSO deleted, permanently, no "
                 "backup — the lists, blog texts and web pages SpeciesViewer made "
                 "for this master, and the pictures it copied into them:\n"
                 + "\n".join(lines)
                 + (f"\n\nNever touched: your pictures folder ({data_plan['pictures']}) "
                    "— the pictures and what is stored with them: ratings, species "
                    "names, landscape codes." if data_plan["pictures"] else
                    "\n\nNever touched: your pictures folder — the pictures and what "
                    "is stored with them: ratings, species names, landscape codes.")
                 + ("\nAlso kept: " + "; ".join(kept) + "." if kept else ""))
    box = QMessageBox(QMessageBox.Icon.Question, "Remove Master", text,
                      QMessageBox.StandardButton.NoButton, main_window)
    settings_btn = box.addButton("Remove settings only",
                                 QMessageBox.ButtonRole.AcceptRole)
    data_btn = (box.addButton(f"Remove with SpeciesViewer's files — {human(data_plan['size'])}",
                              QMessageBox.ButtonRole.DestructiveRole)
                if has_data else None)
    cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(cancel_btn)
    box.exec()
    clicked = box.clickedButton()
    if clicked not in (settings_btn, data_btn) or clicked is None:
        return
    with_data = clicked is data_btn
    only_copies = (f"\n\nThis master holds {master_pictures} pictures. If you removed "
                   "them from your pictures folder after adding them to the master, "
                   "these are the ONLY copies." if master_pictures else "")
    if with_data and QMessageBox.warning(
            main_window, "Remove Master",
            f"Delete {human(data_plan['size'])} of SpeciesViewer's files for "
            f"“{name}” permanently?\n\nThere is no backup. Your pictures folder is "
            f"not touched.{only_copies}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
        return
    # Zipping the settings and deleting the data can take minutes: both run in
    # a wait box with a picture, and the window keeps repainting (Gilbert,
    # 2026-10-18 — was a bare hourglass).
    from gui.wait_picture import run_waiting
    try:
        zip_path = run_waiting(main_window, f"Removing “{name}”…",
                               lambda: masters.remove(name), picture="hippo")
    except (masters.MasterError, OSError) as e:
        QMessageBox.warning(main_window, "Remove Master", str(e))
        return
    main_window.logger.info(f"Master “{name}” removed; its settings are in {zip_path}")
    if with_data:
        report = run_waiting(main_window,
                             f"Deleting {human(data_plan['size'])} of “{name}”’s files…",
                             lambda: master_purge.delete(data_plan), picture="hippo")
        # With shared folders the deleted trips/albums were in THIS master's
        # lists too; the panels read the folders once at start, so re-read.
        for panel in ("trip_panel", "virttrip_panel"):
            try:
                getattr(main_window, panel).rebuild()
            except Exception as e:
                main_window.logger.info(f"Remove Master: {panel} refresh skipped: {e}")
        QMessageBox.information(main_window, "Remove Master",
                                f"“{name}” is removed.\n\n" + "\n".join(report))
    main_window.rebuild_menu()


def _restore(main_window) -> None:
    import datetime
    trashed = masters.trashed_masters()
    if not trashed:
        return
    labels = [f"{entry['name']} — removed "
              f"{datetime.datetime.fromtimestamp(z.stat().st_mtime):%Y-%m-%d %H:%M}"
              for z, entry in trashed]
    choice, ok = QInputDialog.getItem(main_window, "Restore Master",
                                      "Master to restore:", labels, 0, False)
    if not ok:
        return
    zip_path = trashed[labels.index(choice)][0]
    try:
        entry = masters.restore(zip_path)
    except (masters.MasterError, OSError) as e:
        QMessageBox.warning(main_window, "Restore Master", str(e))
        return
    main_window.logger.info(f"Master “{entry['name']}” restored from {zip_path.name}.")
    main_window.rebuild_menu()
    _ask_restart(main_window, entry["name"], f"The master “{entry['name']}” is back.")


def _has_collection() -> bool:
    from utils.config import _config
    return bool(_config.get("master", "masterbiotadatabase", fallback="").strip())


def create_collection(main_window, folder=None) -> None:
    """Choose an empty folder (or take `folder`: the Setup Checklist's
    suggestion), make it a master collection, restart into it."""
    from pathlib import Path
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QFileDialog
    from engine.master_create import CreateMasterError, check_folder, create_master_collection
    if folder is None:
        from utils.start_folder import start_folder
        chosen = QFileDialog.getExistingDirectory(
            main_window, "Choose an EMPTY folder for the new master collection",
            start_folder())
        if not chosen:
            return
        folder = chosen
    folder = Path(folder)
    try:
        check_folder(folder)
    except CreateMasterError as e:
        QMessageBox.warning(main_window, "Create Master Collection", str(e))
        return
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        create_master_collection(folder)
    except Exception as e:
        QApplication.restoreOverrideCursor()
        main_window.logger.warning(f"Create Master Collection failed: {e}")
        QMessageBox.warning(main_window, "Create Master Collection",
                            f"The collection could not be created:\n{e}\n\n"
                            "This master's settings are unchanged.")
        return
    QApplication.restoreOverrideCursor()
    _ask_restart(main_window, masters.ACTIVE["name"],
                 f"The master collection is ready in {folder}.\n"
                 "SpeciesViewer needs a restart to open it.")


def masters_menu(main_window) -> dict:
    """The Master ▸ Masters submenu, built from masters.json. The open master
    is ticked and greyed (you are in it); every other one switches to it."""
    # Read fresh, not masters.IS_MAIN: "Make … main" changes the flag on disk
    # while this process runs, and the menu is rebuilt right after.
    data = masters.load()

    def switcher(name):
        # *_ swallows QAction.triggered's `checked` argument.
        return lambda *_: _switch(main_window, name)

    menu = {}
    open_is_main = False
    for m in data["masters"]:
        is_open = m["name"] == masters.ACTIVE["name"]
        open_is_main = open_is_main or (is_open and bool(m.get("main")))
        label = ("✓ " if is_open else "    ") + m["name"] + ("  (main)" if m.get("main") else "")
        menu[label] = MenuSpec(
            callback=switcher(m["name"]),
            enabled=(lambda o=is_open: not o),
            tooltip="Open now" if is_open else "Close and reopen SpeciesViewer with this master")
    menu[f"{MenuSpec.separator()}_masters_0"] = None
    menu["New Master…"] = MenuSpec(callback=lambda *_: _new_master(main_window))
    if not open_is_main:
        menu[f"Make “{masters.ACTIVE['name']}” the Main Master…"] = MenuSpec(
            callback=lambda *_: _make_main(main_window))
    menu["Rename Master…"] = MenuSpec(callback=lambda *_: _rename(main_window))
    menu["Remove Master…"] = MenuSpec(callback=lambda *_: _remove(main_window))
    menu["Restore Master…"] = MenuSpec(
        callback=lambda *_: _restore(main_window),
        enabled=lambda: bool(masters.trashed_masters()),
        tooltip="Bring back a removed master from the trash")
    menu[f"{MenuSpec.separator()}_masters_1"] = None
    # One master, one collection: offered only while this master has none.
    menu["Create Master Collection…"] = MenuSpec(
        callback=lambda *_: create_collection(main_window),
        enabled=lambda: not _has_collection(),
        tooltip="Start a new, empty collection in an empty folder "
                "(only while this master has none)")
    # Moved here from File ▸ Settings: these folders belong to the OPEN master.
    menu["Repository Settings…"] = MenuSpec(
        callback=lambda *_: RepoSettingsDialog(main_window).exec(),
        tooltip="Where this master's data, trips and albums live")
    return menu
