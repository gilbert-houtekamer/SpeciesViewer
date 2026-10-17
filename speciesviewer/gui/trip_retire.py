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
    QLabel, QMessageBox,
)
from PyQt6.QtGui import QFont

import engine.trip_retire_scan as trs
from gui import theme
from utils.config import _config, get_virttrips_root, persist_config
from utils.gui_config import load_gui_cache, save_gui_cache, usage_key
from utils.trip_config import read_trip_ini

logger = logging.getLogger("speciesviewer")


def _norm(p):
    return os.path.normcase(os.path.normpath(str(p))) if p else ""


# The config keys that make up the "active trip" selection (trip identity + the
# active day). Cleared to de-select; snapshotted so a cancel can restore them.
# NOTE: tripsrootdirectory is deliberately NOT here — it is the PARENT folder that
# holds every trip (get_trips_root lists it to build the Trips panel). Clearing it
# would leave the panel with no root, falling back to the current working directory
# (it would list the code repo folders instead of the trips).
_TRIP_SEL_KEYS = (
    ("trip", "tripbiotadatabase"),
    ("trip", "tripbiotawebroot"),
    ("project", "picturedirectory"),
)


def _snapshot_trip_selection():
    """Capture the current active-trip config so a cancelled retire can restore it."""
    return {sk: _config.get(sk[0], sk[1], fallback="") for sk in _TRIP_SEL_KEYS}


def _set_trip_selection(main_window, values):
    """Write a {(section,key): value} selection and repaint the GUI: the Trips-panel
    ▶ marker, the trip overview, and the menu grey-state."""
    for (section, key), val in values.items():
        if not _config.has_section(section):
            _config.add_section(section)
        _config.set(section, key, val)
    persist_config()
    try:
        main_window.trip_panel._refresh_highlights()
    except Exception as e:
        logger.info(f"trip_retire: highlight refresh skipped: {e}")
    try:
        from gui.trip_overview import populate_trip_overview
        populate_trip_overview(main_window)
    except Exception as e:
        logger.info(f"trip_retire: overview refresh skipped: {e}")
    try:
        main_window.rebuild_menu()
    except Exception as e:
        logger.info(f"trip_retire: menu rebuild skipped: {e}")


def _deselect_active_trip(main_window):
    """Clear the active-trip selection → the no-trip state. Retiring the ACTIVE trip
    needs this: blocker B3 forbids retiring the active trip, and the GUI must never
    be left holding a Project that points at a repo we are about to delete."""
    _set_trip_selection(main_window, {sk: "" for sk in _TRIP_SEL_KEYS})


def _clean_references(trip_db, trip_name=""):
    """Drop the retired trip from this machine's administration: the GUI cache's
    recent-used entry, its trip_project_map entry (so it doesn't linger in
    menus/maps) and its usage_stats row (which backs the Recent/Frequent sort
    modes — a deleted trip kept scoring there).

    Deliberately NOT touched: the master's admin/trip_admin.csv. That is the
    PUBLISHED site registry shared across machines, not a local trip list.

    trip_name is read from the trip.ini when not given; pass it when the folder
    is already gone (nothing left to read it from)."""
    tname = (trip_name or "").strip()
    if not tname:
        try:
            tname = (read_trip_ini(Path(trip_db)).get("trip_name", "") or "").strip()
        except Exception:
            tname = ""
    if not tname:
        # The folder is gone, so trip.ini can no longer be read. Fall back to the
        # <trip_name>db folder convention — good enough to clear the map entry,
        # and the path-keyed entries below are exact regardless.
        base = os.path.basename(os.path.normpath(str(trip_db)))
        tname = base[:-2] if base.lower().endswith("db") else base
    cache = load_gui_cache()
    norm = _norm(trip_db)
    recent = cache.get("recent_projects", [])
    cache["recent_projects"] = [p for p in recent if _norm(p) != norm]
    if tname:
        cache.get("trip_project_map", {}).pop(f"/trips/{tname}", None)
    cache.get("usage_stats", {}).pop(usage_key(trip_db), None)
    save_gui_cache(cache)


def _forget_trip(main_window, trip_db, trip_name=""):
    """Remove every local reference to a trip whose folder is gone: de-select it
    if it is the active trip, clean the GUI cache, drop its row from the tree."""
    if _norm(_config.get("trip", "tripbiotadatabase", fallback="")) == _norm(trip_db):
        _deselect_active_trip(main_window)
    _clean_references(trip_db, trip_name)
    try:
        main_window.trip_panel.remove_trip(trip_db)
    except Exception as e:
        logger.info(f"trip_retire: tree refresh skipped: {e}")


def _forget_missing_trip(main_window, trip_db):
    """The folder is already gone (deleted outside the app) but the app still
    points at it. Offer to clear what is left: the administration on this machine.

    Without this the scan just says "trip folder not found" and the stale entries
    stay for good — the exact dead end an Explorer delete leads to."""
    name = os.path.basename(os.path.normpath(str(trip_db))) or str(trip_db)
    if QMessageBox.question(
            main_window, "Remove trip from this machine",
            f"The folder for '{name}' is already gone:\n\n{trip_db}\n\n"
            f"Remove what is left of it on this machine — its entry in the Trips "
            f"panel and its remembered paths?\n\n"
            f"The master and the album are not touched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
        return
    _forget_trip(main_window, trip_db)
    logger.info(f"Removed leftover administration for '{name}' ({trip_db})")
    QMessageBox.information(main_window, "Remove trip from this machine",
                            f"'{name}' is no longer registered on this machine.")


def retire_trip_dialog(main_window, trip_db):
    active = _config.get("trip", "tripbiotadatabase", fallback="")
    # Deleting the ACTIVE trip requires de-selecting it first: B3 refuses to retire
    # the active trip, and the GUI must not be left pointing at a repo we delete.
    # De-selecting up-front satisfies B3; we snapshot the prior selection so a
    # CANCELLED dialog restores it (only a completed delete stays in the no-trip
    # state — cancel leaves the GUI exactly as it was).
    prior = None
    if active and _norm(active) == _norm(trip_db):
        prior = _snapshot_trip_selection()
        _deselect_active_trip(main_window)
        active = ""
    from gui.wait_picture import run_waiting
    master_db = _config.get("master", "masterbiotadatabase", fallback="")
    virt_root = get_virttrips_root()
    rep = run_waiting(main_window, "Measuring the trip…",
                      lambda: trs.scan(trip_db, virt_root, active, master_db=master_db))
    if not rep.get("ok"):
        if not os.path.isdir(str(trip_db)):
            # Folder already deleted outside the app: nothing to scan, but the
            # local administration still points at it. Offer to clear that.
            _forget_missing_trip(main_window, trip_db)
            return
        if prior is not None:
            _set_trip_selection(main_window, prior)
        QMessageBox.warning(main_window, "Retire trip", rep.get("error", "Scan failed."))
        return

    # Two shapes of the same action. A trip that passes its blockers is RETIRED
    # (the tidy endgame); one that does not can still be REMOVED FROM THIS
    # MACHINE by overriding the failed blockers by name. Wording follows which
    # one it is, so the button never over-promises.
    overriding = not rep["can_retire"]
    verb = "Remove trip from this machine" if overriding else "Retire trip"

    dlg = QDialog(main_window)
    dlg.setWindowTitle(f"{verb} — {rep['name']}")
    dlg.resize(760, 560)
    lay = QVBoxLayout(dlg)

    view = QPlainTextEdit(trs.format_report(rep), readOnly=True)
    view.setFont(QFont("Consolas", 9))
    view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    lay.addWidget(view)

    if overriding:
        # Name what is being overridden in a wrapping label (a QCheckBox label
        # does not wrap — a long blocker list would just widen the dialog), then
        # ONE checkbox that covers the warnings too, so a blocked delete is not
        # a two-checkbox ritual.
        why = QLabel("This trip does not qualify for a clean retire:\n"
                     + "\n".join(f"  •  {b}" for b in rep["failed_blockers"]))
        why.setWordWrap(True)
        why.setStyleSheet(f"color: {theme.WARN};")
        lay.addWidget(why)
        ack = QCheckBox("Delete anyway — I accept the points above and any warnings")
        ack.setVisible(True)
    else:
        ack = QCheckBox("I have reviewed the warnings and want to retire this trip")
        ack.setVisible(rep["has_warnings"])
    lay.addWidget(ack)

    row = QHBoxLayout()
    del_btn = QPushButton(
        (f"Remove from this machine — delete {trs._fmt_size(rep['size_bytes'])} permanently")
        if overriding else
        (f"Retire trip — delete {trs._fmt_size(rep['size_bytes'])} permanently"))
    del_btn.setStyleSheet("QPushButton { background:#C0392B; color:white; font-weight:bold; "
                          "padding:6px 12px; } QPushButton:disabled { background:#E6B0AA; }")
    close_btn = QPushButton("Cancel")
    close_btn.clicked.connect(dlg.reject)
    row.addWidget(del_btn)
    row.addStretch(1)
    row.addWidget(close_btn)
    lay.addLayout(row)

    def _update_enabled():
        # Overriding: the one checkbox stands for the blockers AND the warnings.
        # Qualifying: warnings still need their own acknowledgement, as before.
        needs_ack = overriding or rep["has_warnings"]
        del_btn.setEnabled(ack.isChecked() if needs_ack else True)
    ack.toggled.connect(lambda _=False: _update_enabled())
    _update_enabled()

    state = {"deleted": False}   # set once the repo is actually gone (see tail)

    def _do_delete():
        blogs = rep.get("trip_blogs") or []
        # Name the blogs at the point of no return: they are the only content in
        # a trip repo that exists nowhere else once this folder is gone.
        blog_note = ""
        if blogs:
            shown = "\n".join(f"  • {b}" for b in blogs[:12])
            more = f"\n  … and {len(blogs) - 12} more" if len(blogs) > 12 else ""
            blog_note = (f"\n\nThis destroys {len(blogs)} day blog(s) stored in the "
                         f"trip folder:\n{shown}{more}\n"
                         f"(Blogs already copied into the album are not affected.)")
        if QMessageBox.question(
                dlg, verb,
                f"Permanently delete the trip data for '{rep['name']}'?\n\n"
                f"{rep['trip_db']}\n"
                f"{trs._fmt_size(rep['size_bytes'])} in {rep.get('n_files', 0)} file(s)."
                f"{blog_note}\n\n"
                f"This cannot be undone. CurrentPictures, the master and the "
                f"album are NOT touched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        # Read the trip's own name BEFORE the folder goes: _clean_references needs
        # it for the trip_project_map key, and trip.ini is inside what we delete.
        try:
            tname = (read_trip_ini(Path(rep["trip_db"])).get("trip_name", "") or "").strip()
        except Exception:
            tname = ""
        from gui.wait_picture import run_waiting
        ok, err = run_waiting(dlg, f"Deleting {trs._fmt_size(rep['size_bytes'])} of trip data…",
                              lambda: trs.execute_retire(rep["trip_db"]))
        if not ok:
            QMessageBox.warning(dlg, verb, f"Delete failed:\n{err}")
            return
        state["deleted"] = True
        if overriding:
            logger.info(f"{verb}: '{rep['name']}' deleted with blockers overridden: "
                        + "; ".join(rep["failed_blockers"]))
        _clean_references(rep["trip_db"], tname)
        try:
            main_window.trip_panel.remove_trip(rep["trip_db"])
        except Exception as e:
            logger.info(f"trip_retire: tree refresh skipped: {e}")
        QMessageBox.information(
            dlg, verb,
            f"Removed '{rep['name']}' from this machine. Deleted "
            f"{trs._fmt_size(rep['size_bytes'])} of trip data.\n"
            f"CurrentPictures, the master and the album are intact.")
        dlg.accept()

    del_btn.clicked.connect(_do_delete)
    dlg.exec()

    # Cancelled (or closed) without deleting → put the prior selection back, so
    # de-selecting was invisible unless the delete actually happened.
    if prior is not None and not state["deleted"]:
        _set_trip_selection(main_window, prior)
