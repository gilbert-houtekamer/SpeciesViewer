"""Actions for the Days menu — ad-hoc single-date folders reviewed and sent to
the Master (no trip website). See working_days_plan.md. The heavy lifting is
reused: the culling/review viewer (ImagePreviewDialog) and Add-to-Master
(add_to_master.AddToMasterDialog / micro_trip). This module just points them at
the active Day and keeps the registry + panel in sync.
"""
import os
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox
from PyQt6.QtCore import Qt

from utils import days_registry


def _refresh_panel(main_window):
    """Repaint both surfaces that show the Days list.

    The left-column list and the yellow Days table hold the same collection, so
    anything that changes the registry has to repaint both — one of them going
    stale is how you end up acting on a day that is no longer there.
    """
    panel = getattr(main_window, "days_panel", None)
    if panel is not None:
        panel.rebuild()
    if getattr(main_window, "days_table", None) is not None:
        # Deferred: some callers are handlers ON that table, and rebuilding it
        # replaces the widget currently emitting the signal.
        from PyQt6.QtCore import QTimer
        from gui.days_overview import populate_days_overview
        QTimer.singleShot(0, lambda: populate_days_overview(main_window))


def _active_day(main_window, action):
    """Return the active Day folder, or None (with a nudge) if none is selected
    or it has gone missing on disk."""
    folder = getattr(main_window, "active_day_folder", "")
    if not folder:
        QMessageBox.information(
            main_window, "Days",
            f"Select a day in the Days list on the left first, then {action}.")
        return None
    if not os.path.isdir(folder):
        QMessageBox.warning(
            main_window, "Days",
            f"That day's folder is no longer on disk:\n{folder}")
        return None
    return folder


def register_existing_day(main_window):
    """Register an existing YYYY-MM-DD folder (no copying)."""
    start = days_registry.load_days()
    from utils.start_folder import start_folder
    start_dir = start_folder(start[0]["folder"] if start else "",
                             _config_lastdir(main_window))
    chosen = QFileDialog.getExistingDirectory(
        main_window, "Choose a day folder (named YYYY-MM-DD)", start_dir)
    if not chosen:
        return
    ok, msg = days_registry.add_day(chosen)
    if ok:
        main_window.active_day_folder = str(chosen)
        days_registry.set_active_day(str(chosen))
        _refresh_panel(main_window)
        try:
            main_window.status_bar.showMessage(msg)
        except Exception:
            pass
    else:
        QMessageBox.warning(main_window, "Register day", msg)


def copy_day_from_card(main_window):
    """Copy pictures off a card into YYYY-MM-DD folder(s) and register them as
    Days. Reuses the SD-card ingest dialog in 'days mode' (date-bucketing on;
    registers the created date folders when the copy finishes). Non-modal —
    the copy runs on its own QThread — so the rest of SV stays usable."""
    from gui.trip_process import ReadSDCardDialog
    if not main_window._try_start_background_op("Read SD-card or Folder"):
        return
    dlg = ReadSDCardDialog(main_window, None, days_mode=True)
    main_window._read_sdcard_dlg = dlg  # keep alive while non-modal
    dlg.finished.connect(lambda *_: (main_window._end_background_op(),
                                     setattr(main_window, "_read_sdcard_dlg", None)))
    dlg.show()


def _config_lastdir(main_window):
    try:
        from utils.config import _config
        return _config.get("project", "picturedirectory", fallback="")
    except Exception:
        return ""


def review_day(main_window):
    """Open the review/rating viewer on the active Day's folder (culling mode —
    edits go straight to the sidecars in that folder)."""
    folder = _active_day(main_window, "review its pictures")
    if not folder:
        return
    from gui.image_viewer import ImagePreviewDialog
    # No trip metadata for a plain day folder → the viewer opens in culling mode
    # (sidecar-backed), exactly like reviewing an SD-card folder.
    dlg = ImagePreviewDialog(main_window, image_dir=folder, trip_root_dir=folder, mode="all")
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    main_window._day_review_dlg = dlg   # keep a ref (PyQt GC)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


def add_day_to_master(main_window):
    """Point the Add-to-Master session at the active Day and scan it."""
    folder = _active_day(main_window, "add it to the Master")
    if not folder:
        return
    from gui.add_to_master import AddToMasterDialog
    dlg = getattr(main_window, "_add_to_master_dialog", None)
    if dlg is None:
        dlg = AddToMasterDialog(main_window)
        main_window._add_to_master_dialog = dlg
    # Days mode: path locked to this day's folder; scans on open.
    dlg.open_session(fixed_dir=folder)


def geotag_from_tracklog(main_window):
    """Run the tracklog geotag review dialog against the active Day,
    independent of a card being present — the day's own auto-detected GPS
    log (if any) is pre-loaded. Modal, so refresh after it returns."""
    folder = _active_day(main_window, "geotag it from a tracklog")
    if not folder:
        return
    from gui.tracklog_geotag_dialog import open_tracklog_geotag_for_folder
    open_tracklog_geotag_for_folder(main_window, folder)
    _refresh_panel(main_window)


def remove_day_from_list(main_window):
    """Remove the active Day from the list (its pictures are left untouched)."""
    folder = getattr(main_window, "active_day_folder", "")
    if not folder:
        QMessageBox.information(main_window, "Days", "Select a day first.")
        return
    name = Path(folder).name
    if QMessageBox.question(
            main_window, "Remove from list",
            f"Remove day {name} from the list?\n\nThe pictures on disk are not "
            f"touched — only the list entry is removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
        return
    days_registry.remove_day(folder)
    main_window.active_day_folder = ""
    days_registry.set_active_day("")
    _refresh_panel(main_window)
