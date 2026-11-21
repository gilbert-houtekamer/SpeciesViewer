"""Export Trip: take a trip away, in three numbered steps — the mirror of Import.

ONE job: lead from "a trip on this computer" to "everything another computer
needs to carry on with it", and show how far each part is.

  1  Trip      — the small trip ZIP: settings, banner, species list, lifer
                 status, translations (gui.trip_process.ExportTripDialog, embedded)
  2  Metadata  — the days' culling, names, places, crops, blogs and JPGs, one
                 ZIP per day onto the SV INDEX card (gui.work_backup_dialog panel)
  3  Pictures  — the RAWs, copied to spare cards or a drive (gui.raw_backup_dialog panel)

The trip panel's Metadata backup and RAW backup columns open this same window
at step 2 or 3 (open_trip_export), so there is one place for each job.

INVARIANTS
- Every step is a panel INSIDE this window — nothing opens behind it.
- While a step is copying, the window cannot be closed and the steps cannot be
  switched; the work runs on the panel's own thread and the rest of SV stays
  usable (the window is not modal).
- It holds the main window's background-operation guard while open, as the two
  backup windows did, so a backup never runs beside a card read.
"""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from gui import theme as _th
from gui.trip_import_window import _StepTile, _active_trip
from gui.widgets import wrapped

STEP_TRIP, STEP_METADATA, STEP_PICTURES = range(3)


def _day_dirs(picture_root) -> list[Path]:
    """The trip's YYYY-MM-DD day folders — not .trash or other folders beside them."""
    from engine import work_backup as wb
    if not picture_root or not Path(picture_root).is_dir():
        return []
    try:
        return sorted(d for d in Path(picture_root).iterdir()
                      if d.is_dir() and wb._day_folder_name(d.name))
    except OSError:
        return []


_real_days = _day_dirs


def _metadata_status(picture_root):
    """'N of M days backed up' on the SV INDEX card — the 💾 column, summed."""
    from engine import work_backup as wb
    from gui.work_backup_dialog import backup_location
    dest = backup_location()
    if not dest:
        return "no SV INDEX card chosen yet", "todo"
    done = todo = 0
    for d in _real_days(picture_root):
        try:
            state = wb.day_status(d, dest)["state"]
        except Exception:
            continue
        if state == "current":
            done += 1
        elif state in ("never", "stale"):
            todo += 1
    if not done and not todo:
        return "nothing to back up yet", "faint"
    if todo:
        return f"{done} of {done + todo} days backed up", "todo"
    return f"✅ all {done} days backed up", "done"


def _pictures_status(trip_root, picture_root):
    """'N of M days copied' to the RAW backup — the panel's own rows, summed."""
    from gui.raw_backup_dialog import COPIED_STATUSES, day_statuses, raw_backup_location
    if not raw_backup_location():
        return "no RAW backup location chosen yet", "todo"
    trip_name = Path(picture_root).name if picture_root else ""
    rows = [r for r in day_statuses(_day_dirs(picture_root), trip_root, trip_name)
            if r["total"]]
    total = len(rows)
    copied = sum(1 for r in rows if r["status"] in COPIED_STATUSES)
    if not total:
        return "no pictures here", "faint"
    if copied < total:
        return f"{copied} of {total} days copied", "todo"
    return f"✅ all {total} days copied", "done"


def _embedded_export(main_window, export_spec):
    """Export Trip's ZIP panel (the old window), shown inside step 1."""
    from gui.trip_process import ExportTripDialog

    class _Panel(ExportTripDialog):
        # An embedded dialog must never close itself (Escape, Enter).
        def reject(self):
            pass

        def accept(self):
            pass

    panel = _Panel(main_window, export_spec)
    panel.setWindowFlags(Qt.WindowType.Widget)
    panel.close_btn.hide()
    return panel


class _Page(QWidget):
    """One step: a line on what it is for, then the panel that does it."""

    def __init__(self, intro, panel, status_fn, with_next):
        super().__init__()
        self.panel = panel
        self._status_fn = status_fn
        self.loaded = False
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 8, 0, 0)
        col.addWidget(wrapped(intro))
        col.addWidget(panel, 1)
        self.next_btn = QPushButton("Next ›")
        if with_next:
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(self.next_btn)
            col.addLayout(row)

    def status(self):
        try:
            return self._status_fn()
        except Exception as e:          # a report, never a blocker
            return f"could not read the status ({e})", "faint"


class TripExportWindow(QDialog):
    def __init__(self, main_window, export_spec, step=STEP_TRIP):
        super().__init__(main_window)
        self._main = main_window
        self._trip_root, self._picture_root = _active_trip()
        name = Path(self._picture_root).name if self._picture_root else ""
        self.setWindowTitle(f"Export Trip — {name}" if name else "Export Trip")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.resize(1240, 760)

        from gui.wait_picture import picture_column
        col = picture_column(self, "short_eared_owl")

        tiles = QHBoxLayout()
        self._tiles = [_StepTile(1, "Trip"),
                       _StepTile(2, "Metadata — SV INDEX card"),
                       _StepTile(3, "Pictures — spare cards")]
        for i, t in enumerate(self._tiles):
            t.clicked.connect(lambda i=i: self.go(i))
            tiles.addWidget(t, 1)
        col.addLayout(tiles)

        from gui.raw_backup_dialog import RawBackupPanel
        from gui.work_backup_dialog import BackupWorkPanel
        days = _day_dirs(self._picture_root)
        self.trip_panel = _embedded_export(main_window, export_spec)
        self.meta_panel = BackupWorkPanel(self, days, trip_root=self._trip_root)
        self.pics_panel = RawBackupPanel(self, days, trip_name=name,
                                         trip_root=self._trip_root)

        self._pages = [
            _Page("The trip itself, small: its settings, species list, lifer status "
                  "and translations. On the other computer, <b>Import Trip</b> step 1 "
                  "reads it.", self.trip_panel, self._trip_status, True),
            _Page("The days' work. Only days that changed need writing again. "
                  "<b>Import Trip</b> step 2 reads it.",
                  self.meta_panel, lambda: _metadata_status(self._picture_root), True),
            _Page("The pictures themselves. <b>Import Trip</b> step 3 reads them back.",
                  self.pics_panel,
                  lambda: _pictures_status(self._trip_root, self._picture_root), False),
        ]
        self._stack = QStackedWidget()
        for i, p in enumerate(self._pages):
            self._stack.addWidget(p)
            p.next_btn.clicked.connect(lambda _=False, i=i: self.go(i + 1))
        col.addWidget(self._stack, 1)

        for panel in (self.meta_panel, self.pics_panel):
            panel.busy_changed.connect(self._set_busy)
            panel.run_done.connect(self._after_run)
        self.trip_panel.copy_btn.clicked.connect(self._refresh_header)

        bottom = QHBoxLayout()
        self._busy_note = QLabel("")
        self._busy_note.setStyleSheet(f"color: {_th.WARN};")
        bottom.addWidget(self._busy_note, 1)
        self._close = QPushButton("Close")
        self._close.clicked.connect(self.close)
        bottom.addWidget(self._close)
        col.addLayout(bottom)

        self.go(step)

    # ── steps ─────────────────────────────────────────────────────────────

    def _busy(self) -> bool:
        return self.meta_panel.is_busy() or self.pics_panel.is_busy()

    def go(self, step):
        if self._busy():
            return                       # a copy is running: stay on its page
        self._stack.setCurrentIndex(step)
        for i, t in enumerate(self._tiles):
            t.set_current(i == step)
        page = self._pages[step]
        if not page.loaded and hasattr(page.panel, "reload"):
            page.loaded = True
            self.show()                  # paint first: the reload can take a moment
            page.panel.reload()
        self._refresh_header()

    def _trip_status(self):
        if not self._trip_root:
            return "open a trip first", "todo"
        from utils.config import _config
        target = _config.get("utilities", "trip_export_target", fallback="").strip()
        return (f"saved to {target}" if target else "no folder chosen yet"), "faint"

    def _refresh_header(self):
        for tile, page in zip(self._tiles, self._pages):
            tile.set_status(*page.status())

    def _set_busy(self, busy):
        self._close.setEnabled(not busy)
        self._busy_note.setText("Copying — the window stays open until it is done."
                                if busy else "")

    def _after_run(self):
        self._refresh_header()
        try:
            from gui.trip_overview import populate_trip_overview
            populate_trip_overview(self._main)
        except Exception:
            pass

    # ── closing ───────────────────────────────────────────────────────────

    def reject(self):                    # Escape
        if not self._busy():
            super().reject()

    def closeEvent(self, event):         # Close button, title-bar ✕
        if self._busy():
            event.ignore()
            return
        super().closeEvent(event)


def open_trip_export(main_window, step=STEP_TRIP, export_spec=None):
    """Show Export Trip at `step` — from the Trips menu (step 1) or the trip
    panel's backup columns (2, 3). Non-modal; one at a time: a second call
    brings the open one forward at the asked step."""
    win = getattr(main_window, "_trip_export_window", None)
    if win is not None:
        win.go(step)
        win.raise_()
        win.activateWindow()
        return win
    guard = getattr(main_window, "_try_start_background_op", None)
    if guard is not None and not guard("Export Trip"):
        return None
    spec = export_spec
    if spec is None:
        try:
            spec = main_window.menu_spec["Trips"]["Export Trip…"]
        except Exception:
            pass
    win = TripExportWindow(main_window, spec, step=step)
    win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    main_window._trip_export_window = win

    def _closed(*_a):
        main_window._trip_export_window = None
        end = getattr(main_window, "_end_background_op", None)
        if end is not None:
            end()
        try:
            from gui.trip_overview import populate_trip_overview
            populate_trip_overview(main_window)
        except Exception:
            pass

    win.finished.connect(_closed)
    win.show()
    return win
