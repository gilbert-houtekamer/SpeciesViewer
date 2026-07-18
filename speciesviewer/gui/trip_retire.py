# trip_retire.py: GUI for "Retire trip (delete data)" — runs the pre-flight
# safety scan (engine.trip_retire_scan), shows the blockers/warnings, and on an
# acknowledged confirmation PERMANENTLY deletes the trip repo. Then clears the
# trip's recent-used references and drops it from the trip tree.
#
# CurrentPictures, the master and the virtual trip are never touched.

import os
import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QPushButton, QCheckBox,
    QMessageBox,
)
from PyQt6.QtGui import QFont

import engine.trip_retire_scan as trs
from utils.config import _config, get_virttrips_root
from utils.gui_config import load_gui_cache, save_gui_cache
from utils.trip_config import read_trip_ini

logger = logging.getLogger("speciesviewer")


def _norm(p):
    return os.path.normcase(os.path.normpath(str(p))) if p else ""


def _clean_references(trip_db):
    """Drop the retired trip from the GUI cache: its recent-used entry and its
    trip_project_map entry (so it doesn't linger in menus/maps)."""
    try:
        tname = (read_trip_ini(Path(trip_db)).get("trip_name", "") or "").strip()
    except Exception:
        tname = ""
    cache = load_gui_cache()
    norm = _norm(trip_db)
    recent = cache.get("recent_projects", [])
    cache["recent_projects"] = [p for p in recent if _norm(p) != norm]
    if tname:
        cache.get("trip_project_map", {}).pop(f"/trips/{tname}", None)
    save_gui_cache(cache)


def retire_trip_dialog(main_window, trip_db):
    active = _config.get("trip", "tripbiotadatabase", fallback="")
    rep = trs.scan(trip_db, get_virttrips_root(), active)
    if not rep.get("ok"):
        QMessageBox.warning(main_window, "Retire trip", rep.get("error", "Scan failed."))
        return

    dlg = QDialog(main_window)
    dlg.setWindowTitle(f"Retire trip — {rep['name']}")
    dlg.resize(760, 520)
    lay = QVBoxLayout(dlg)

    view = QPlainTextEdit(trs.format_report(rep), readOnly=True)
    view.setFont(QFont("Consolas", 9))
    view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    lay.addWidget(view)

    ack = QCheckBox("I have reviewed the warnings and want to retire this trip")
    ack.setVisible(rep["can_retire"] and rep["has_warnings"])
    lay.addWidget(ack)

    row = QHBoxLayout()
    del_btn = QPushButton(f"Retire trip — delete {trs._fmt_size(rep['size_bytes'])} permanently")
    del_btn.setStyleSheet("QPushButton { background:#C0392B; color:white; font-weight:bold; "
                          "padding:6px 12px; } QPushButton:disabled { background:#E6B0AA; }")
    close_btn = QPushButton("Cancel")
    close_btn.clicked.connect(dlg.reject)
    row.addWidget(del_btn)
    row.addStretch(1)
    row.addWidget(close_btn)
    lay.addLayout(row)

    def _update_enabled():
        del_btn.setEnabled(rep["can_retire"] and (not rep["has_warnings"] or ack.isChecked()))
    ack.toggled.connect(lambda _=False: _update_enabled())
    _update_enabled()

    def _do_delete():
        if QMessageBox.question(
                dlg, "Retire trip",
                f"Permanently delete the trip data for '{rep['name']}'?\n\n"
                f"{rep['trip_db']}\n\n"
                f"This cannot be undone. CurrentPictures, the master and the "
                f"album are NOT touched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        ok, err = trs.execute_retire(rep["trip_db"])
        if not ok:
            QMessageBox.warning(dlg, "Retire trip", f"Delete failed:\n{err}")
            return
        _clean_references(rep["trip_db"])
        try:
            main_window.trip_panel.remove_trip(rep["trip_db"])
        except Exception as e:
            logger.info(f"trip_retire: tree refresh skipped: {e}")
        QMessageBox.information(
            dlg, "Retire trip",
            f"Retired '{rep['name']}'. Deleted {trs._fmt_size(rep['size_bytes'])} of trip data.\n"
            f"CurrentPictures, the master and the album are intact.")
        dlg.accept()

    del_btn.clicked.connect(_do_delete)
    dlg.exec()
