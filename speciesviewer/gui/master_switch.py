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
    _start_detached({**os.environ, masters.ENV_VAR: name})


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
    computer's settings copied from the main master."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("New Test Master")
        layout = QVBoxLayout(self)
        layout.addWidget(wrapped(
            "A test master has its own settings, trips and albums. Your main "
            "master is not touched while you work in it.", self))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name, e.g. Test new user")
        layout.addWidget(self.name)
        self.empty = QRadioButton("Start empty — see SpeciesViewer as a new user does")
        self.copy = QRadioButton("Copy this computer's settings from the main master "
                                 "(PhotoLab, window sizes — no data, no website)")
        self.empty.setChecked(True)
        layout.addWidget(self.empty)
        layout.addWidget(self.copy)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(520, self.sizeHint().height())


def _new_master(main_window) -> None:
    from utils.config import CONFIG_FILENAME
    dlg = NewMasterDialog(main_window)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return
    source = None
    if dlg.copy.isChecked():
        main = masters.main_master(masters.load())
        source = masters.folder_of(main) / "config" / CONFIG_FILENAME
    try:
        entry = masters.create(dlg.name.text(), copy_machine_settings_from=source)
    except masters.MasterError as e:
        QMessageBox.warning(main_window, "New Test Master", str(e))
        return
    main_window.logger.info(f"New master “{entry['name']}” → {masters.folder_of(entry)}")
    main_window.rebuild_menu()            # list it in Master ▸ Masters
    _ask_restart(main_window, entry["name"],
                 f"The master “{entry['name']}” is ready.")


def _make_main(main_window) -> None:
    name = masters.ACTIVE["name"]
    old = masters.main_master(masters.load())["name"]
    answer = QMessageBox.question(
        main_window, "Make main master",
        f"Make “{name}” your main master?\n\n"
        f"“{old}” stays available as a test master. Nothing is moved or deleted.")
    if answer != QMessageBox.StandardButton.Yes:
        return
    masters.make_main(name)
    main_window.logger.info(f"Main master is now “{name}” (was “{old}”).")
    main_window.rebuild_menu()
    _ask_restart(main_window, name,
                 "The window shows the change (no TEST label) after a restart.")


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
    answer = QMessageBox.question(
        main_window, "Remove Master",
        f"Remove the master “{name}” from SpeciesViewer?\n\n"
        "Its settings, trips list and other remembered state are zipped to the "
        "trash first. Pictures and other data folders you chose for it are NOT "
        "deleted.")
    if answer != QMessageBox.StandardButton.Yes:
        return
    try:
        zip_path = masters.remove(name)
    except (masters.MasterError, OSError) as e:
        QMessageBox.warning(main_window, "Remove Master", str(e))
        return
    main_window.logger.info(f"Master “{name}” removed; its settings are in {zip_path}")
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


def create_collection(main_window) -> None:
    """Choose an empty folder, make it a master collection, restart into it."""
    from pathlib import Path
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QFileDialog
    from engine.master_create import CreateMasterError, check_folder, create_master_collection
    chosen = QFileDialog.getExistingDirectory(
        main_window, "Choose an EMPTY folder for the new master collection")
    if not chosen:
        return
    folder = Path(chosen)
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
    menu["New Test Master…"] = MenuSpec(callback=lambda *_: _new_master(main_window))
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
