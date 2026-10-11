"""Help ▸ Setup checklist — what a new user has to set up, and what is optional
(docs/plans/setup_checklist_plan.md).

ONE JOB: show, per setting, whether it is done — DERIVED FROM THE REAL SETTINGS,
never from "the user clicked Next" — with a button that opens the dialog that
already exists for it. So the same window answers "why is everything grey?"
at any later moment, not only on day one.

Opened by itself at start-up while the open master has no collection.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PyQt6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel,
                             QPushButton, QVBoxLayout)

from gui import theme
from gui.widgets import wrapped
from utils.config import (_config, get_currentpictures_root, get_trips_root,
                          get_virttrips_root)

# Marks: done, needed and missing, optional and not set up, works on defaults.
OK, MISSING, OPTIONAL, DEFAULT = "ok", "missing", "optional", "default"
_MARK = {OK: ("✓", theme.GATE_FG), MISSING: ("✗", theme.ALERT),
         OPTIONAL: ("–", theme.INK_FAINT), DEFAULT: ("·", theme.INK_FAINT)}


def _folder(path: str, missing_text: str):
    if not path:
        return MISSING, missing_text
    if not Path(path).is_dir():
        return MISSING, f"{path} — this folder does not exist"
    return OK, path


# ── the checks: each returns (mark, text) ─────────────────────────────────────

def master_collection_state():
    path = _config.get("master", "masterbiotadatabase", fallback="").strip()
    if not path:
        return MISSING, ("No collection yet. Create one with Master ▸ Masters ▸ "
                         "Create Master Collection…, or choose an existing one.")
    if not (Path(path) / "metadata" / "active" / "data_pictures.csv").is_file():
        return MISSING, f"{path} — not a master collection (no metadata/active/ found)"
    return OK, path


def website_state():
    """Required: where the website goes — this computer, or a web server."""
    from utils import site_config
    target = site_config.publish_target()
    if target == site_config.LOCAL:
        return OK, f"On this computer — {site_config.site_base()}/"
    if target == site_config.SERVER:
        if site_config.publishing_set_up():
            return OK, f"On your web server — {site_config.sftp_host()}"
        return MISSING, ("Web server chosen, but the server, account or website "
                         "folder is not filled in yet.")
    return MISSING, "Choose where your website goes: on this computer, or on your web server."


def pictures_folder_state():
    return _folder(get_currentpictures_root(),
                   "Not chosen — where your working copies live, e.g. D:\\CurrentPictures.")


def trips_folder_state():
    return _folder(get_trips_root(), "Not chosen — where your trips are kept.")


def albums_folder_state():
    return _folder(get_virttrips_root(), "Not chosen — where your albums are kept.")


def exiftool_state():
    from utils.exiftool_path import describe
    path, where = describe()
    if path:
        return OK, f"{path} ({where})"
    return MISSING, ("Not found. SpeciesViewer reads and writes picture data with "
                     "ExifTool — Locate… tells you where to get it.")


# Windows only for now: there is no macOS or Linux download of SpeciesViewer
# yet, so a how-to for them would be premature (Gilbert, 2026-10-11).
EXIFTOOL_HOWTO = (
    "SpeciesViewer reads and writes picture data with ExifTool, free software "
    "by Phil Harvey. SpeciesViewer comes with a tested version; choose another "
    "one here only to use a newer version.\n\n"
    "Use the official version from exiftool.org: download the Windows 64-bit "
    "package and unzip it. Rename “exiftool(-k).exe” to “exiftool.exe” and keep "
    "the “exiftool_files” folder beside it. Then choose that exiftool.exe here."
    if sys.platform.startswith("win") else
    "SpeciesViewer reads and writes picture data with ExifTool, free software "
    "by Phil Harvey. Install the official version from exiftool.org.")


def locate_exiftool(parent) -> None:
    """Explain where to get exiftool, then choose it; keep it only if it
    answers with a version."""
    import subprocess
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    from utils.config import persist_config
    box = QMessageBox(QMessageBox.Icon.Information, "Locate exiftool", EXIFTOOL_HOWTO,
                      parent=parent)
    website = box.addButton("Open exiftool.org", QMessageBox.ButtonRole.ActionRole)
    choose = box.addButton("Choose exiftool…", QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    if box.clickedButton() is website:
        QDesktopServices.openUrl(QUrl("https://exiftool.org"))
        return
    if box.clickedButton() is not choose:
        return
    chosen, _ = QFileDialog.getOpenFileName(parent, "Choose exiftool")
    if not chosen:
        return
    try:
        out = subprocess.run([chosen, "-ver"], capture_output=True, text=True, timeout=20)
        version = out.stdout.strip()
        if out.returncode != 0 or not version:
            raise RuntimeError(out.stderr.strip() or "no version reported")
    except Exception as e:
        QMessageBox.warning(parent, "Locate exiftool",
                            f"{chosen} does not answer like exiftool:\n{e}")
        return
    _config.set("Global", "exiftool_path", chosen)
    persist_config()
    QMessageBox.information(parent, "Locate exiftool",
                            f"exiftool {version} will be used from now on. "
                            "Restart SpeciesViewer to use it everywhere.")


def decode_quality_state():
    if _config.has_section("fullres") and _config.items("fullres"):
        return OK, "Your own settings."
    return DEFAULT, "Defaults — fine to leave as they are."


def photolab_state():
    from settings.photolablocation import _DEFAULT
    path = _config.get("Global", "dxo_path", fallback=_DEFAULT).strip()
    if path and Path(path).is_file():
        return OK, path
    return OPTIONAL, "Not found — only needed to open pictures in DxO PhotoLab."


class SetupChecklistDialog(QDialog):
    """Non-modal: it stays open beside the dialogs it opens."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.setWindowTitle("Setup Checklist")
        outer = QVBoxLayout(self)
        outer.addWidget(wrapped(
            "What SpeciesViewer needs, and what is optional. Start at the top: "
            "with a master collection you can already copy days off a card, add "
            "them to the master and browse it. Trips and albums need their folder "
            "first; until then those menus stay grey.", self))
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setColumnStretch(2, 1)
        outer.addLayout(self.grid)
        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        outer.addLayout(buttons)
        self._rows()
        self.resize(760, self.sizeHint().height())

    def _open(self, make_dialog):
        """Open one of the existing settings dialogs, then re-check everything."""
        make_dialog().exec()
        self.refresh()

    def _rows(self):
        from settings.globalsettings import GlobalSettingsDialog
        from settings.photolablocation import PhotoLabLocationDialog
        from settings.rawsettings import RawProcessingDialog
        from settings.reposettings import RepoSettingsDialog
        mw = self.main_window

        def repo(tab):
            return lambda: self._open(lambda: RepoSettingsDialog(mw, tab=tab))

        # (title, needed for, check, [(button, action)]) — Gilbert's order.
        self.spec = [
            ("Master collection", "everything", master_collection_state,
             [("Choose…", repo(RepoSettingsDialog.TAB_MASTER))]),
            ("Website", "species pages, blogs", website_state,
             [("Choose…", lambda: self._open(lambda: GlobalSettingsDialog(mw)))]),
            ("Pictures folder", "unfiled days, trips", pictures_folder_state,
             [("Choose…", repo(RepoSettingsDialog.TAB_PICTURES))]),
            ("Trips folder", "trips", trips_folder_state,
             [("Choose…", repo(RepoSettingsDialog.TAB_TRIP))]),
            ("Albums folder", "albums", albums_folder_state,
             [("Choose…", repo(RepoSettingsDialog.TAB_ALBUM))]),
            ("exiftool", "reading pictures", exiftool_state,
             [("Locate…", lambda: (locate_exiftool(self), self.refresh()))]),
            ("Decode Quality", "the 100% view", decode_quality_state,
             [("Change…", lambda: self._open(lambda: RawProcessingDialog(mw)))]),
            ("DxO PhotoLab", "editing (optional)", photolab_state,
             [("Locate…", lambda: self._open(lambda: PhotoLabLocationDialog(mw)))]),
        ]
        for col, head in enumerate(("", "Setting", "", "Needed for", "")):
            if head:
                h = QLabel(head)
                h.setStyleSheet(f"color: {theme.INK_FAINT};")
                self.grid.addWidget(h, 0, col)
        self.marks, self.texts = [], []
        for r, (title, needed, _check, actions) in enumerate(self.spec, start=1):
            mark = QLabel()
            mark.setStyleSheet("font-weight: bold; font-size: 15px;")
            name = QLabel(title)
            name.setStyleSheet("font-weight: bold;")
            text = wrapped("", self)
            self.grid.addWidget(mark, r, 0)
            self.grid.addWidget(name, r, 1)
            self.grid.addWidget(text, r, 2)
            self.grid.addWidget(QLabel(needed), r, 3)
            row_buttons = QHBoxLayout()
            for label, action in actions:
                b = QPushButton(label)
                b.clicked.connect(lambda _=False, a=action: a())
                row_buttons.addWidget(b)
            row_buttons.addStretch()
            self.grid.addLayout(row_buttons, r, 4)
            self.marks.append(mark)
            self.texts.append(text)
        self.refresh()

    def refresh(self):
        for (title, _needed, check, _actions), mark, text in zip(
                self.spec, self.marks, self.texts):
            state, detail = check()
            symbol, colour = _MARK[state]
            mark.setText(symbol)
            mark.setStyleSheet(f"font-weight: bold; font-size: 15px; color: {colour};")
            text.setText(detail)


def master_needs_setup() -> bool:
    """True while a REQUIRED row is ✗ — the master collection, where the
    website goes, exiftool. Without them nothing gets far; the checklist then
    opens at start instead of the user finding out from a failing step
    (Write Blog in a new master, 2026-10-11). Trips/albums folders are not
    required: they only unlock their own menus."""
    return any(check()[0] == MISSING
               for check in (master_collection_state, website_state, exiftool_state))


def show_checklist(main_window) -> None:
    """Open (or raise) the one checklist window. A reference is kept on the main
    window: a parented-but-unreferenced dialog can be garbage-collected by
    Python while it is still on screen."""
    dlg = getattr(main_window, "_setup_checklist", None)
    if dlg is None:
        dlg = main_window._setup_checklist = SetupChecklistDialog(main_window)
    dlg.refresh()
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
