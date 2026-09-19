from pathlib import Path
import logging
import os

from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QStatusBar, QApplication
from PyQt6.QtWidgets import QPlainTextEdit, QLabel, QSizePolicy, QLineEdit, QTabWidget, QTableWidgetItem, QMessageBox
from PyQt6.QtWidgets import QStackedWidget, QPushButton, QButtonGroup
from PyQt6.QtCore import Qt, QTimer

from utils.config import _config, persist_config
from gui.window_state import restore_window, save_window
from gui.menu import create_menubar
from gui.menuspec import build_menu_spec
from gui.trip_overview import populate_trip_overview
from gui.virttrip_overview import populate_virttrip_overview, TAB_TITLE as VIRTTRIP_TAB_TITLE
from gui.days_overview import populate_days_overview, TAB_TITLE as DAYS_TAB_TITLE
from gui.unfiled_overview import populate_unfiled_overview, TAB_TITLE as UNFILED_TAB_TITLE
from engine.translatelib import CachedTranslate
from widgets.TripInfoPanel import TripInfoPanel
from widgets.VirtTripInfoPanel import VirtTripInfoPanel
from widgets.DaysPanel import DaysPanel
from widgets.UnfiledPanel import UnfiledPanel
from widgets.SidebarInfoRail import SidebarInfoRail
from widgets.fold_chip import FoldChipButton, VerticalLabel
from gui import theme
from utils.sort_modes import (
    ALL_MODES, DEFAULT_MODE, SORT_ALPHA_ASC, SORT_ALPHA_DESC, SORT_RECENT, SORT_FREQUENT,
)
from gui.trip_overview import populate_trip_overview
from engine.statemachine import StateMachine
from gui.image_viewer import ImagePreviewDialog
from utils.gui_config import load_gui_cache  # or wherever your cache functions live
from utils.ini_io import read_ini

logger = logging.getLogger("speciesviewer")

MODE_UNFILED = "Unfiled"
MODE_DAY = "Day"
MODE_TRIP = "Trip"
MODE_ALBUM = "Album"

# Left to right, the order pictures actually travel: off a card into the inbox,
# claimed as a Day or filed under a Trip, published as an Album.
_MODES = (MODE_UNFILED, MODE_DAY, MODE_TRIP, MODE_ALBUM)

_LEFT_COLLAPSED_W = 44
_RAIL_COLLAPSED_W = 44
_RAIL_EXPANDED_W = 220
_WIDGET_MAX = 16777215  # QWIDGETSIZE_MAX — releases a setFixedWidth() pin

_MODE_PILL_STYLE = f"""
QPushButton {{
    background: transparent; color: {theme.INK_MUTED}; font-weight: 600; font-size: 9pt;
    padding: 4px 12px; border: 1px solid transparent; border-radius: 11px;
}}
QPushButton:checked {{
    background-color: {theme.ACCENT}; color: {theme.ACCENT_INK};
}}
QPushButton:hover:!checked {{
    background-color: {theme.ACCENT_SOFT};
}}
"""

_SORT_PILL_STYLE = f"""
QPushButton {{
    background: transparent; color: {theme.INK_FAINT}; font-weight: 600; font-size: 8pt;
    padding: 3px 9px; border: 1px solid transparent; border-radius: 9px;
}}
QPushButton:checked {{
    background-color: {theme.ACCENT_SOFT_BORDER}; color: {theme.ACCENT_INK_SOFT};
}}
QPushButton:hover:!checked {{
    background-color: {theme.ROW_HOVER};
}}
"""




class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.trip_table = None
        self.virttrip_table = None
        self.state_keys = None
        self.state_labels = None
        self.setWindowTitle("Species Viewer 0.2")
        # Create and attach status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)        
        self.resize(1200, 900)
        self.logger = None
        # False until the window is fully assembled — see sync_yellow_to_mode.
        self._ui_ready = False
        # Label of the one long-running operation (SD-card read, a backup)
        # allowed to run at a time, or None. These dialogs run their real
        # work on a QThread and are non-modal, so nothing else stops two of
        # them stepping on the same files — this replaces the accidental
        # mutex .exec() used to provide. See _try_start_background_op.
        self._busy_operation: str | None = None

        root_field = QLineEdit()  # Shared reference across tabs
        root_field.setText(_config["trip"]["tripbiotadatabase"])        
        # layout

        # Create the splitter
        self.hsplitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Left column: one Day/Trip/Album mode strip + a shared stack ----
        # (ch.15 layout pass) Each mode shows its OWN list — TripInfoPanel,
        # DaysPanel, VirtTripInfoPanel are unchanged internally, only moved
        # from "three panels split across two columns, always visible" into
        # "one at a time, picked by the strip above them."
        left_panel = QWidget()
        left_panel.setStyleSheet(f"background-color: {theme.PANEL_BG};")
        left_outer = QVBoxLayout(left_panel)
        left_outer.setContentsMargins(0, 0, 0, 0)
        left_outer.setSpacing(0)

        mode_row = QWidget()
        mode_row_layout = QHBoxLayout(mode_row)
        mode_row_layout.setContentsMargins(8, 8, 8, 6)
        mode_row_layout.setSpacing(4)
        self._mode_buttons = {}
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        for mode in _MODES:
            btn = QPushButton(mode)
            btn.setCheckable(True)
            btn.setStyleSheet(_MODE_PILL_STYLE)
            btn.clicked.connect(lambda checked, m=mode: self._set_left_mode(m))
            mode_row_layout.addWidget(btn)
            self._mode_group.addButton(btn)
            self._mode_buttons[mode] = btn
        mode_row_layout.addStretch(1)
        self._left_fold = FoldChipButton(collapse_char="‹", expand_char="›")
        self._left_fold.clicked.connect(self._toggle_left_collapsed)
        mode_row_layout.addWidget(self._left_fold)
        left_outer.addWidget(mode_row)

        # One shared sort control for all three lists (decided over three
        # independent per-list settings — simpler, one thing to persist).
        # The A-Z button doubles as Z-A on a re-click while already active,
        # so Days (short-lived, previously always newest-first) can get that
        # ordering back without a 4th button or a Days-only special case.
        initial_sort_mode = _config.get("layout", "list_sort_mode", fallback=DEFAULT_MODE)
        if initial_sort_mode not in ALL_MODES:
            initial_sort_mode = DEFAULT_MODE
        self._current_sort_mode = initial_sort_mode

        sort_row = QWidget()
        sort_row_layout = QHBoxLayout(sort_row)
        sort_row_layout.setContentsMargins(8, 0, 8, 8)
        sort_row_layout.setSpacing(4)
        self._sort_alpha_btn = QPushButton(
            "Z-A" if initial_sort_mode == SORT_ALPHA_DESC else "A-Z")
        self._sort_recent_btn = QPushButton("Recent")
        self._sort_frequent_btn = QPushButton("Frequent")
        self._sort_group = QButtonGroup(self)
        self._sort_group.setExclusive(True)
        for btn in (self._sort_alpha_btn, self._sort_recent_btn, self._sort_frequent_btn):
            btn.setCheckable(True)
            btn.setStyleSheet(_SORT_PILL_STYLE)
            sort_row_layout.addWidget(btn)
            self._sort_group.addButton(btn)
        sort_row_layout.addStretch(1)
        self._sort_alpha_btn.clicked.connect(self._on_sort_alpha_clicked)
        self._sort_recent_btn.clicked.connect(lambda: self._apply_sort_mode(SORT_RECENT))
        self._sort_frequent_btn.clicked.connect(lambda: self._apply_sort_mode(SORT_FREQUENT))
        if initial_sort_mode == SORT_RECENT:
            self._sort_recent_btn.setChecked(True)
        elif initial_sort_mode == SORT_FREQUENT:
            self._sort_frequent_btn.setChecked(True)
        else:
            self._sort_alpha_btn.setChecked(True)
        self._sort_row_widget = sort_row
        left_outer.addWidget(sort_row)

        self._left_stack = QStackedWidget()
        cache = load_gui_cache()
        trip_project_map = cache.get("trip_project_map", {})  # ✅ actual dict
        # Keep a reference so populate_trip_overview can refresh the active-trip
        # ▶ marker after any trip switch (panel click, create/select dialog, …).
        self.trip_panel = TripInfoPanel(self, trip_project_map, populate_trip_overview,
                                         sort_mode=initial_sort_mode)
        self.days_panel = DaysPanel(self, sort_mode=initial_sort_mode)
        self.virttrip_panel = VirtTripInfoPanel(self, populate_virttrip_overview,
                                                 sort_mode=initial_sort_mode)
        self.unfiled_panel = UnfiledPanel(self, sort_mode=initial_sort_mode)
        self._left_stack.addWidget(self.days_panel)      # index 0 = Day
        self._left_stack.addWidget(self.trip_panel)      # index 1 = Trip
        self._left_stack.addWidget(self.virttrip_panel)  # index 2 = Album
        self._left_stack.addWidget(self.unfiled_panel)   # index 3 = Unfiled
        left_outer.addWidget(self._left_stack, 1)

        # Shown instead of the stack when collapsed, so a collapsed left
        # column still reads (e.g. "TRIP") rather than going fully blank. Sits
        # right under the fold chip (stretch 0) rather than filling/centering
        # in the leftover height — it's a label next to the toggle, not a
        # footer.
        self._left_vertical_label = VerticalLabel(MODE_TRIP)
        self._left_vertical_label.setVisible(False)
        left_outer.addWidget(self._left_vertical_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self._left_panel_widget = left_panel
        self.hsplitter.addWidget(left_panel)

        self._left_collapsed = False
        self._left_width = 220
        self._mode_buttons[MODE_TRIP].setChecked(True)
        self._left_stack.setCurrentIndex(1)

        # --- Center: yellow panel (the ✦ Maximize button was removed — it read
        # as clutter and the pane is large enough; the toggle method stays but is
        # now unreachable) ---------------------------------------------------
        yellow_container = QWidget()
        yellow_outer = QVBoxLayout(yellow_container)
        yellow_outer.setContentsMargins(0, 0, 0, 0)
        yellow_outer.setSpacing(0)
        self._maximize_btn = None

        # Yellow panel. Style the pane so it stays the intentional yellow
        # "active" accent. The tab BAR itself is hidden — the left mode strip
        # is now the single visible selector (ch.15); the QTabWidget stays as
        # a QTabWidget internally so trip_overview.py/virttrip_overview.py
        # need no changes at all (they still addTab/setCurrentWidget as before).
        self.yellow_tabs = QTabWidget()
        self.yellow_tabs.setStyleSheet(f"""
            QTabWidget::pane {{ background-color: {theme.ACTIVE_BG}; border: 1px solid {theme.ACTIVE_BORDER}; }}
        """)
        self.yellow_tabs.tabBar().setVisible(False)
        yellow_outer.addWidget(self.yellow_tabs, 1)
        self.hsplitter.addWidget(yellow_container)

        # --- Right: collapsible info rail (Master Repository + quick stats) -
        self.info_rail = SidebarInfoRail(self)
        rail_container = QWidget()
        rail_outer = QVBoxLayout(rail_container)
        rail_outer.setContentsMargins(0, 0, 0, 0)
        rail_outer.setSpacing(0)
        rail_header = QWidget()
        rail_header_layout = QHBoxLayout(rail_header)
        rail_header_layout.setContentsMargins(6, 6, 6, 6)
        self._rail_fold = FoldChipButton(collapse_char="›", expand_char="‹", collapsed=True)
        self._rail_fold.clicked.connect(self._toggle_rail_collapsed)
        rail_header_layout.addWidget(self._rail_fold)
        rail_header_layout.addStretch(1)
        rail_outer.addWidget(rail_header)
        rail_outer.addWidget(self.info_rail, 1)
        self._rail_container_widget = rail_container
        self.hsplitter.addWidget(rail_container)

        self._rail_collapsed = True
        self._yellow_maximized = False
        # Starts collapsed — pin the width now, not just via setSizes() below,
        # since a splitter's stretch-factor recompute can otherwise creep it
        # back wider on the first relayout.
        self._rail_container_widget.setFixedWidth(_RAIL_COLLAPSED_W)

        # Optional: set initial sizes
        self.hsplitter.setSizes([220, 720, _RAIL_COLLAPSED_W])

        # Optional: make panels expand
        self.hsplitter.setStretchFactor(0, 1)
        self.hsplitter.setStretchFactor(1, 3)
        self.hsplitter.setStretchFactor(2, 0)

        # Wrap in a container widget
        color_row = QWidget()
        layout = QVBoxLayout(color_row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.hsplitter)
        color_row.setMinimumHeight(100)
 

        self.green_log_box = QPlainTextEdit()
        self.green_log_box.setReadOnly(True)
        self.green_log_box.setStyleSheet(f"background-color: {theme.LOG_BG};")
        self.green_log_box.setMinimumHeight(60)
        self.green_log_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Log gets its own header (fold chip + label) so it collapses like the
        # left stack and the right rail — same affordance, ch.15 layout pass.
        log_container = QWidget()
        log_outer = QVBoxLayout(log_container)
        log_outer.setContentsMargins(0, 0, 0, 0)
        log_outer.setSpacing(0)
        log_header = QWidget()
        log_header.setStyleSheet(f"background-color: {theme.LOG_BG};")
        log_header_layout = QHBoxLayout(log_header)
        log_header_layout.setContentsMargins(8, 3, 8, 3)
        self._log_fold = FoldChipButton(collapse_char="▾", expand_char="▸")
        self._log_fold.clicked.connect(self._toggle_log_collapsed)
        log_header_layout.addWidget(self._log_fold)
        log_label = QLabel("Log")
        log_label.setStyleSheet(f"font-weight: bold; color: {theme.LOG_INK};")
        log_header_layout.addWidget(log_label)
        log_header_layout.addStretch(1)
        log_outer.addWidget(log_header)
        log_outer.addWidget(self.green_log_box, 1)

        self.vsplitter = QSplitter(Qt.Orientation.Vertical)
        self.vsplitter.addWidget(color_row)
        self.vsplitter.addWidget(log_container)

        sizes = _config.get("layout", "split_sizes", fallback="500,400")
        sizes = [int(s) for s in sizes.split(",")]
        self.vsplitter.setSizes(sizes)

        self._log_collapsed = False
        self._log_prev_height = None

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(self.vsplitter)
        widget.setLayout(layout)
        self.setCentralWidget(widget)

        # define menu structures
        self.menu_spec = build_menu_spec(self)
        self.menu_bar = create_menubar(self, self.menu_spec)
        self.setMenuBar(self.menu_bar)
        # translation just in case we need it
        self.translator = CachedTranslate(_config["master"]["masterbiotadatabase"])
        # self.yellow_builder = YellowPanelBuilder(root_field, self.config)
        # populate_trip_overview(self)
        # Restore the WINDOW's own size/position first (it was never remembered before,
        # so the panel widths — absolute pixels below — were being applied to a
        # different width each launch). Then the splitter sizes fit that width.
        restore_window(self, "MainWindow", default_size=(1200, 900))

        # Horizontal splitter
        if _config.has_option("layout", "split_sizes_horizontal"):
            h_sizes = _config.get("layout", "split_sizes_horizontal")
            try:
                self.hsplitter.setSizes([int(s) for s in h_sizes.split(",")])
            except ValueError:
                pass

        # Vertical splitter
        if _config.has_option("layout", "split_sizes_vertical"):
            v_sizes = _config.get("layout", "split_sizes_vertical")
            try:
                self.vsplitter.setSizes([int(s) for s in v_sizes.split(",")])
            except ValueError:
                pass
        
        # On a laptop the master is a SNAPSHOT, and its age decides whether the
        # lifer verdicts and species suggestions can be believed. It sits beside
        # the trip name because it is the same kind of fact: which data am I
        # looking at right now. Empty (and hidden) on a machine holding the real
        # master, which has no snapshot date and needs none.
        self.master_snapshot_label = QLabel("")
        self.status_bar.addPermanentWidget(self.master_snapshot_label)
        self.trip_label = QLabel("")
        self.status_bar.addPermanentWidget(self.trip_label)
        self.update_trip_label()
        self.update_master_snapshot_label()
        self.status_bar.showMessage("Ready")

        # Restore virtual trip overview if one was active at last close
        virt_root = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        if virt_root and os.path.isdir(virt_root):
            populate_virttrip_overview(self)

        # Auto-refresh trip overview when the app regains focus, so day
        # folders added by external tools (Explorer, card importers) appear
        # without requiring the user to switch trips.
        self._trip_overview_day_snapshot = None
        QApplication.instance().applicationStateChanged.connect(
            self._on_application_state_changed
        )

        self._prune_preview_cache_daily()
        # _report_drive_space() is NOT called here — self.logger is only assigned
        # after this constructor returns, so the report would log into a handler
        # that does not exist yet. SpeciesViewer.launch_main_window calls it.

    def _report_drive_space(self):
        """State how full every drive is, once, when the program starts.

        The counterpart to the pre-flight check in the card read: rather than
        only refusing a copy at the moment it would fail, say where things stand
        on a day when nothing is going wrong yet — which is when a filling drive
        is still cheap to deal with. One line per local or removable volume;
        under 10% free comes out as a warning, under 5% as an alarm, and the log
        panel colours those by level on its own.

        In a daemon thread for the same reason the cache prune is: a card reader
        or a drive that has spun down can take seconds to answer, and the
        start-up path must never wait on one. Nothing here writes."""
        import threading

        def _work():
            try:
                from utils.disk_space import report_volumes, format_volume_row
                rows = report_volumes()
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Could not read drive space: {e}")
                return
            if not self.logger or not rows:
                return
            low = sum(1 for r in rows if r["level"] != "ok")
            tail = f", {low} low on space" if low else ""
            self.logger.info(f"Drive space — {len(rows)} volume(s){tail}:")
            for row in rows:
                line = "   " + format_volume_row(row)
                if row["level"] == "alert":
                    self.logger.error(line)
                elif row["level"] == "warn":
                    self.logger.warning(line)
                else:
                    self.logger.info(line)

        threading.Thread(target=_work, daemon=True, name="drive-space").start()

    def _prune_preview_cache_daily(self):
        """Keep the preview cache bounded, once a day, without being asked.

        The cache only ever grew: it kept the extracted previews of culled, deleted
        and retired trip RAWs for ever. The sweep drops entries whose picture is
        gone and entries nobody has opened in 90 days — the same net the EXIF cache
        gets on every Read Directory, which is where this idea comes from.

        In a plain daemon thread: it only stats and unlinks files (no Qt objects),
        and a cache of a hundred thousand previews takes long enough to walk that
        doing it on the UI thread would show as a stutter on startup. The day stamp
        is written BEFORE the work, so a failure costs one skipped day rather than a
        retry loop. Also available on demand — Manage Caches ▸ Prune stale."""
        import threading
        import time
        today = int(time.time() // 86400)
        try:
            last = int(_config.get("caches", "preview_pruned_day", fallback="0") or 0)
        except ValueError:
            last = 0
        if last == today:
            return
        if not _config.has_section("caches"):
            _config.add_section("caches")
        _config.set("caches", "preview_pruned_day", str(today))
        persist_config()

        def _work():
            try:
                from utils.preview_cache import prune
                prune()
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"preview cache: daily prune failed: {e}")

        threading.Thread(target=_work, daemon=True, name="preview-prune").start()
        self._backup_master_daily()

    def _backup_master_daily(self):
        """One gzipped snapshot of the master CSVs a day, without being asked.

        The master has two kings — the CSVs are canonical, the SQLite DB is a derived
        mirror that writes back to them — and on 2026-07-14 the mirror won an argument
        it should have lost and destroyed 2718 species rows. A guardrail now refuses the
        catastrophic write; this is the belt to that pair of braces.

        NOT a Lightroom-style "shall I back up?" prompt: all three CSVs are 154 MB raw
        but 7.3 MB gzipped, written in about a second. Asking permission for that would
        imply a cost that is not there, and a prompt you always say yes to is one you
        eventually click through without reading. It is visible in Database Settings
        instead, which is where you would look for it.

        The check GATES the backup: a snapshot of a corrupt master is not a backup, it
        is a time bomb, because rotation would push the last good one out. Daemon thread
        (it can wait on the rebuild lock), and the day stamp is the backup folder itself
        rather than a config key, so it stays true per-master."""
        import threading
        master_root = _config.get("master", "masterbiotadatabase", fallback="").strip()
        if not master_root:
            return

        def _work():
            try:
                from db.master_db import MasterDB
                from db import master_health
                if not master_health.backup_due(master_root):
                    return
                db = MasterDB(master_root)
                db.ensure_fresh()
                res = master_health.daily_backup(db, master_root)
                if res and not res.get("ok") and self.logger:
                    self.logger.error(
                        f"master backup skipped — {res.get('error')} "
                        f"Fix the master and use Database Settings ▸ Back up now.")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"master: daily backup failed: {e}")

        threading.Thread(target=_work, daemon=True, name="master-backup").start()

    def _current_trip_day_snapshot(self):
        pic_dir = _config.get("project", "picturedirectory", fallback="").strip()
        if not pic_dir:
            return None
        image_root = Path(pic_dir).parent
        if not image_root.exists():
            return None
        try:
            return tuple(sorted(d.name for d in image_root.iterdir() if d.is_dir()))
        except OSError:
            return None

    def _on_application_state_changed(self, state):
        if state != Qt.ApplicationState.ApplicationActive:
            return
        snapshot = self._current_trip_day_snapshot()
        if snapshot is None or snapshot == self._trip_overview_day_snapshot:
            return
        populate_trip_overview(self)

    def closeEvent(self, event):
        # Shutdown below can take a moment (flushing any open culling grids,
        # stopping the persistent exiftool writer) with nothing on screen to
        # say so — same problem, and same fix, as the slow dialogs: show a
        # message and force it to paint before doing the actual work.
        closing_lbl = QLabel("Closing Species Viewer…")
        closing_lbl.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        closing_lbl.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        closing_lbl.setStyleSheet(
            f"background: {theme.SURFACE}; color: {theme.INK}; "
            f"padding: 18px; font-size: 13px; border: 1px solid {theme.INK_FAINT};")
        closing_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        closing_lbl.adjustSize()
        geo = self.frameGeometry()
        closing_lbl.move(geo.center() - closing_lbl.rect().center())
        closing_lbl.show()
        QApplication.processEvents()

        # A culling / preview grid open over a day buffers its day-CSV writes
        # (rating / colour / species are debounced). Closing the MAIN window
        # DESTROYS that child dialog without sending it a closeEvent, so those
        # edits were being lost. Close each one explicitly FIRST — its own
        # closeEvent flushes the CSV + saves learned species + refreshes the
        # health row. `_suppress_rerun` keeps it from ALSO kicking off the async
        # Read & Summarize: that opens its own window mid-shutdown and the app
        # then never quits. The edits are safe in data_files.csv; re-run Read &
        # Summarize for the day (or close just the grid, not the app) to push
        # them through the pipeline.
        for w in list(QApplication.topLevelWidgets()):
            if w is not self and w.isVisible() and hasattr(w, "_flush_collection_csv"):
                try:
                    w._suppress_rerun = True
                    w.close()
                except RuntimeError:
                    pass
        # logger.info("Close and save screen sizes")
        _config["layout"]["split_sizes_vertical"] = ",".join(str(s) for s in self.vsplitter.sizes())
        _config["layout"]["split_sizes_horizontal"] = ",".join(str(s) for s in self.hsplitter.sizes())
        save_window(self, "MainWindow")   # the window's own size/position
        persist_config()
        # The shared write-side exiftool stays open for the life of the app so a
        # single colour or rating costs 11 ms instead of 270. Shut it down here
        # rather than leaving an orphan process behind.
        try:
            from engine.exif_writer import close_persistent_writer
            close_persistent_writer()
        except Exception:
            pass
        super().closeEvent(event)
        # Force the process to end: a lingering QThreadPool / QFileSystemWatcher
        # (image loader, sidecar watch) can otherwise keep app.exec() spinning
        # after the last window is gone.
        QApplication.instance().quit()
    
    _MODE_STACK_INDEX = {MODE_DAY: 0, MODE_TRIP: 1, MODE_ALBUM: 2, MODE_UNFILED: 3}

    _MODE_TAB_TITLE = {
        MODE_UNFILED: UNFILED_TAB_TITLE,
        MODE_DAY: DAYS_TAB_TITLE,
        MODE_TRIP: "Active Trip",
        MODE_ALBUM: VIRTTRIP_TAB_TITLE,
    }

    # What to tell the user to do when a mode's tab is missing. Trip and Album
    # need a selection; Unfiled and Days build from a scan and a registry, so a
    # missing tab there is a bug, not something the user can fix.
    _MODE_TAB_FIX = {
        MODE_TRIP: "Select a trip to rebuild it.",
        MODE_ALBUM: "Select an album to rebuild it.",
    }

    def current_left_mode(self):
        """Which of Unfiled / Day / Trip / Album the left column is on."""
        for mode, btn in self._mode_buttons.items():
            if btn.isChecked():
                return mode
        return MODE_TRIP

    def sync_yellow_to_mode(self):
        """Point the yellow panel at the scope the left column is on.

        THE INVARIANT: the left column leads. Whatever the mode strip says, the
        yellow panel shows that scope. All four modes now have a panel — Day and
        Unfiled were the deferred half of ch.15 Phase 1 and are no longer an
        exception, so there is no early return for any mode.

        This is the ONE place that chooses the yellow tab. The overview builders
        used to call setCurrentWidget() themselves, which meant any refresh
        yanked the panel to its own scope regardless of the mode — most sneakily
        the focus-refresh in _on_application_state_changed, which rebuilds the
        TRIP overview whenever the app is re-activated and a day folder changed.
        Sitting in Album mode, alt-tabbing away and back was enough to leave the
        strip saying Album and the panel showing the trip.
        """
        mode = self.current_left_mode()
        want_title = self._MODE_TAB_TITLE.get(mode)
        if not want_title:
            return
        for i in range(self.yellow_tabs.count()):
            if self.yellow_tabs.tabText(i) == want_title:
                self.yellow_tabs.setCurrentIndex(i)
                return
        # No such tab: an album that is unset or whose folder is gone makes
        # populate_virttrip_overview remove the tab and return. Silently leaving
        # the other scope's panel on screen is exactly the disconnect this method
        # exists to prevent, so say so instead. Not during construction, though:
        # the overviews are built one after another, so the first one
        # legitimately runs while the others' tabs do not exist yet.
        if not self._ui_ready:
            return
        logger.warning(
            f"{mode} mode is selected but there is no “{want_title}” panel to "
            f"show — the panel below still belongs to another scope. "
            + self._MODE_TAB_FIX.get(mode, ""))

    def _set_left_mode(self, mode):
        """Switch which of the four left-column lists is showing, and bring the
        yellow panel with it (see sync_yellow_to_mode for the invariant)."""
        self._left_stack.setCurrentIndex(self._MODE_STACK_INDEX.get(mode, 1))
        self._left_vertical_label.setText(mode)
        # Check the pill BEFORE building anything: the builders end by calling
        # sync_yellow_to_mode(), which asks which pill is checked. Building first
        # would have them select the tab we are leaving.
        btn = self._mode_buttons.get(mode)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        # The two scan-backed lanes read the filesystem, so what they showed a
        # moment ago may already be stale — a card was read, a day was filed in
        # Explorer. Re-read on the way in rather than making the user hit Refresh.
        # Trip and Album are not rebuilt here: theirs is a selection, not a scan,
        # and rebuilding them on every mode click would be needless work.
        if mode == MODE_UNFILED:
            self.unfiled_panel.rebuild()
            populate_unfiled_overview(self)
        elif mode == MODE_DAY:
            self.days_panel.rebuild()
            populate_days_overview(self)
        self.sync_yellow_to_mode()
        if not _config.has_section("layout"):
            _config.add_section("layout")
        _config["layout"]["active_left_mode"] = mode

    def _restore_active_left_mode(self):
        """Re-select the mode the user last had active. Called once after
        startup population (every overview forces its own yellow tab
        selected as it builds, so this has to run last to win) — the same
        ordering trick the old active_yellow_tab restore used."""
        want = _config.get("layout", "active_left_mode", fallback="").strip()
        if want not in self._mode_buttons:
            want = MODE_TRIP
        self._ui_ready = True      # both overviews exist by now
        self._set_left_mode(want)

    def _on_sort_alpha_clicked(self):
        """The A-Z button doubles as Z-A: a re-click while it's already the
        active mode flips direction instead of doing nothing (QButtonGroup
        still fires `clicked` on a re-click of the checked button)."""
        if self._current_sort_mode == SORT_ALPHA_ASC:
            new_mode = SORT_ALPHA_DESC
        else:
            new_mode = SORT_ALPHA_ASC
        self._sort_alpha_btn.setText("Z-A" if new_mode == SORT_ALPHA_DESC else "A-Z")
        self._apply_sort_mode(new_mode)

    def _apply_sort_mode(self, mode):
        """Re-sort all four lists (matches this app's existing "always full
        rebuild" pattern — these lists are tens of items, not master-scale).
        Persisted immediately, like the trip/album selection, not deferred to
        close — it's a real preference, cheap to save."""
        self._current_sort_mode = mode
        if not _config.has_section("layout"):
            _config.add_section("layout")
        _config["layout"]["list_sort_mode"] = mode
        persist_config()
        self.trip_panel.rebuild(mode)
        self.days_panel.rebuild(mode)
        self.virttrip_panel.rebuild(mode)
        self.unfiled_panel.rebuild(mode)
        # Days and Unfiled put the same collection in the yellow table, so it
        # re-sorts with the list — otherwise the two halves of one mode would
        # disagree about the order.
        if getattr(self, "days_table", None) is not None:
            populate_days_overview(self)
        if getattr(self, "unfiled_table", None) is not None:
            populate_unfiled_overview(self)

    def _relayout_hsplitter(self):
        """Recompute the 3 hsplitter widths from the current collapse/maximize
        flags. Collapse/maximize state is session-only by design (confirmed
        with the user) — nothing here reads or writes _config."""
        sizes = self.hsplitter.sizes()
        total = sum(sizes) or (self.width() or 1200)
        if self._yellow_maximized:
            left_w, rail_w = 0, 0
        else:
            left_w = _LEFT_COLLAPSED_W if self._left_collapsed else max(self._left_width, 160)
            rail_w = _RAIL_COLLAPSED_W if self._rail_collapsed else _RAIL_EXPANDED_W
        middle_w = max(total - left_w - rail_w, 200)
        self.hsplitter.setSizes([left_w, middle_w, rail_w])

    def _toggle_left_collapsed(self):
        # A plain hsplitter.setSizes() request can get silently overridden by
        # the splitter's own stretch-factor recompute — setFixedWidth() pins
        # the pane so the collapse actually sticks. Cleared (min 0 / max
        # QWIDGETSIZE_MAX) on expand so the user can drag-resize normally.
        if not self._left_collapsed:
            current = self.hsplitter.sizes()[0]
            if current > _LEFT_COLLAPSED_W:
                self._left_width = current
        self._left_collapsed = not self._left_collapsed
        self._left_fold.set_collapsed(self._left_collapsed)
        self._left_stack.setVisible(not self._left_collapsed)
        self._left_vertical_label.setVisible(self._left_collapsed)
        for btn in self._mode_buttons.values():
            btn.setVisible(not self._left_collapsed)
        self._sort_row_widget.setVisible(not self._left_collapsed)
        if self._left_collapsed:
            self.hsplitter.setStretchFactor(0, 0)
            self._left_panel_widget.setFixedWidth(_LEFT_COLLAPSED_W)
        else:
            self._left_panel_widget.setMinimumWidth(0)
            self._left_panel_widget.setMaximumWidth(_WIDGET_MAX)
            self.hsplitter.setStretchFactor(0, 1)
        self._relayout_hsplitter()

    def _toggle_rail_collapsed(self):
        self._rail_collapsed = not self._rail_collapsed
        self._rail_fold.set_collapsed(self._rail_collapsed)
        self.info_rail.set_collapsed(self._rail_collapsed)
        if self._rail_collapsed:
            self._rail_container_widget.setFixedWidth(_RAIL_COLLAPSED_W)
        else:
            self._rail_container_widget.setMinimumWidth(0)
            self._rail_container_widget.setMaximumWidth(_WIDGET_MAX)
        self._relayout_hsplitter()

    def _toggle_yellow_maximized(self):
        self._yellow_maximized = not self._yellow_maximized
        if self._maximize_btn is not None:
            self._maximize_btn.setText("✦ Restore" if self._yellow_maximized else "✦ Maximize")
        self._relayout_hsplitter()

    def _toggle_log_collapsed(self):
        self._log_collapsed = not self._log_collapsed
        self._log_fold.set_collapsed(self._log_collapsed)
        self.green_log_box.setVisible(not self._log_collapsed)
        sizes = self.vsplitter.sizes()
        total = sum(sizes) or 900
        if self._log_collapsed:
            self._log_prev_height = sizes[1]
            self.vsplitter.setSizes([total - 30, 30])
        else:
            restore = self._log_prev_height or 200
            self.vsplitter.setSizes([max(total - restore, 200), restore])

    def init_panels(self):
        self.yellow_builder.populate_tabs(self.yellow_tabs)

    
    def load_yellow_panel(self):
        if hasattr(self, 'yellow_tabs_built') and self.yellow_tabs_built:
            return  # Already built

        self.yellow_builder.populate_tabs(self.yellow_tabs)
        self.yellow_tabs_built = True         

    def rebuild_menu(self):
        self.menuBar().clear()
        self.menu_spec = build_menu_spec(self)  # Rebuild spec with updated state
        new_menu_bar = create_menubar(self, self.menu_spec)
        self.setMenuBar(new_menu_bar)

    def update_trip_cell(self, day_name, step_key, symbol, tooltip, day_path=None):
        from gui.trip_overview import (SYNC_COL, STATE_COL_START, STATE_COLUMNS,
                                       cell_is_inert, _tint_write_blog_cell)
        for row in range(self.trip_table.rowCount()):
            if self.trip_table.item(row, 0).text() == day_name:
                if step_key == "Sync\nSidecars":
                    col_index = SYNC_COL
                    col_spec = None
                else:
                    step_idx = self.state_labels.index(step_key)
                    col_index = step_idx + STATE_COL_START
                    col_spec = STATE_COLUMNS[step_idx]
                item = QTableWidgetItem(symbol)
                item.setToolTip(tooltip)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if cell_is_inert(symbol, tooltip):
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                if (symbol == "✅" and col_spec is not None
                        and col_spec["gate"] == "WriteBlog" and day_path is not None):
                    _tint_write_blog_cell(item, tooltip, day_path)
                self.trip_table.setItem(row, col_index, item)
                break

    def refresh_trip_row(self, sm, day_name):
        from gui.trip_overview import (GPS_COL, STATE_COL_START, STATE_COLUMNS,
                                       state_cell_symbol, _verified_crc_set,
                                       _format_translations_item,
                                       _format_total_files_item, _format_gps_item,
                                       count_files)

        trip_root = Path(_config.get("trip", "tripbiotadatabase"))
        review_root = trip_root / "metadata" / "review"
        review_day = review_root / day_name

        row = next((r for r in range(self.trip_table.rowCount())
                    if self.trip_table.item(r, 0).text() == day_name), None)

        # Pictures, GPS and JPEG Files cells: recompute all three from disk —
        # otherwise a Read & Summarize re-run (e.g. after exporting JPGs from
        # PhotoLab) flips the checkmarks green but the JPEG Files cell — and
        # the jpgs==0 gate that blocks Write Blog — stays stuck at whatever it
        # read the first time, since it was never re-painted here before.
        if row is not None:
            image_root = self._trip_picture_root()
            day_path = image_root / day_name
            self.trip_table.setItem(
                row, 1, _format_total_files_item(day_path, review_root))
            self.trip_table.setItem(
                row, GPS_COL, _format_gps_item(day_path, review_root))
            _total, jpgs = count_files(day_path)
            jpgs_item = QTableWidgetItem(str(jpgs))
            jpgs_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.trip_table.setItem(row, 2, jpgs_item)
        else:
            jpgs = 0
            day_path = None

        # Note: Sync Sidecars column is intentionally NOT refreshed here
        # (it is not tracked in the statemachine). state_cell_symbol applies the
        # same rules as populate_trip_overview, including the combined
        # "Read & Summarize" column that tracks both ReadDirExif + WritePictures.
        for col in STATE_COLUMNS:
            symbol, tooltip = state_cell_symbol(sm, col, jpgs)
            self.update_trip_cell(day_name, col["label"], symbol, tooltip, day_path=day_path)

        # Update Translations column too — Write Summary rewrites
        # data_pictures.csv, so the unverified-text count may have changed.
        verified_crcs = _verified_crc_set(trip_root)
        translations_col = STATE_COL_START + len(self.state_labels)
        for row in range(self.trip_table.rowCount()):
            if self.trip_table.item(row, 0).text() == day_name:
                self.trip_table.setItem(row, translations_col,
                                        _format_translations_item(review_day, verified_crcs))
                break



    def handle_trip_table_current_changed(self, row, column, prev_row, prev_column):
        """When the active row changes, sync picturedirectory to that day and
        rebuild the Trip menu so enabled/disabled reflects the new row's state."""
        if row < 0:
            return
        day_item = self.trip_table.item(row, 0)
        if not day_item:
            return
        day_str = day_item.text()
        current_pd = _config.get("project", "picturedirectory", fallback="")
        project_root = Path(current_pd).parent if current_pd else Path()
        new_dir = project_root / day_str
        if str(new_dir) != current_pd:
            _config.set("project", "picturedirectory", str(new_dir))
            self.rebuild_menu()

    def confirm_no_pending_edits(self, action_label="this step") -> bool:
        """Guard: refuse to launch a pipeline step while an open culling window
        still has edits that haven't been folded back into the day (a pending
        Read & Summarize). Running the step on the stale review CSVs would drop
        the edit — the 'I forgot to close the dialog' bug. The user closes the
        window first (its closeEvent saves), then re-runs. Returns True when it is
        safe to proceed. See [[project_frozen_day_guard]] Bug B."""
        pending = []
        for attr in ("image_viewer_dialog", "dlgImagePreviewDialog"):
            dlg = getattr(self, attr, None)
            if dlg is None:
                continue
            try:
                if dlg.isVisible() and dlg.has_unsaved_edits():
                    day = Path(dlg.image_dir).name if getattr(dlg, "image_dir", "") else "a day"
                    pending.append(day)
            except (RuntimeError, AttributeError):
                pass  # dialog's C++ object gone, or not a culling viewer
        if pending:
            days = ", ".join(sorted(set(pending)))
            QMessageBox.warning(
                self, "Unsaved edits are open",
                f"Close the editing window for {days} before running "
                f"{action_label}.\n\nIt has changes that aren't saved into the day "
                f"yet — closing the window saves them (a quick Read & Summarize), so "
                f"the step runs on the up-to-date data.")
            return False
        return True

    def _trip_picture_root(self) -> Path:
        """The active trip's picture root — the folder holding its YYYY-MM-DD
        day subdirs. Reads trip.ini's explicit `picture_root` (WP8) so this
        doesn't depend on [project] picturedirectory, which drifts every time
        the Unfiled lane or another trip is visited and previously got
        blindly `.parent`'d, silently corrupting it back into config (the
        "2026 Goeree folder missing from the path" bug). Falls back to the
        historic parent-of-active-day for trips that predate WP8."""
        from utils.trip_identity import resolve_trip_picture_root
        trip_root = _config.get("trip", "tripbiotadatabase", fallback="")
        root = resolve_trip_picture_root(trip_root, kind="trip") if trip_root else ""
        return Path(root) if root else Path(_config.get("project", "picturedirectory")).parent

    def _try_start_background_op(self, label: str) -> bool:
        """Gate the handful of dialogs (SD-card read, the two backups) that
        do real work on a QThread and are shown non-modally — nothing else
        stops two of them running at once and stepping on the same files,
        which .exec()'s accidental app-wide block used to prevent. Returns
        False (and tells the user) if another one is already running."""
        if self._busy_operation is not None:
            QMessageBox.information(
                self, "Busy",
                f"{self._busy_operation} is already running — wait for it "
                f"to finish before starting {label.lower()}.")
            return False
        self._busy_operation = label
        return True

    def _end_background_op(self):
        self._busy_operation = None

    def handle_trip_table_double_click(self, row, column):
        from gui.trip_overview import (GPS_COL, GEOTAG_COL, SYNC_COL, BACKUP_COL,
                                       RAW_BACKUP_COL, STATE_COL_START, STATE_COLUMNS)
        # Translations column: open the per-day validation panel. No separate
        # Progress column exists (see trip_overview.state_cell_symbol) — this is
        # the first trailing non-action column after the 3 state columns.
        translations_col = STATE_COL_START + len(self.state_labels)
        if column == translations_col:
            day_item = self.trip_table.item(row, 0)
            if day_item:
                self.open_translation_validator(day_item.text())
            return
        # Ignore the Day column / headers, and anything past Translations —
        # except the two backup columns, which now sit last (they are the
        # evening job, so that is where they belong in the day's timeline)
        # and are still clickable.
        if (column > translations_col and column not in (BACKUP_COL, RAW_BACKUP_COL)) \
                or column < 1:
            return

        day_item = self.trip_table.item(row, 0)
        action_item = self.trip_table.horizontalHeaderItem(column)

        if not day_item or not action_item:
            self.logger.warning("Invalid cell selection.")
            return

        day_str = day_item.text()
        action_label = action_item.text().replace("\n", " ")  # e.g., "Write Blog"

        # Update PictureDirectory
        project_root = self._trip_picture_root()
        new_dir = project_root / day_str
        _config.set("project", "picturedirectory", str(new_dir))

        trip_root = Path(_config.get("trip", "tripbiotadatabase"))
        review_day = trip_root / "metadata" / "review" / day_str
        sm = StateMachine(str(trip_root), str(review_day), readState=True)

        # DISABLE logic: check if this step should be blocked (pipeline columns only)
        step_index = column - STATE_COL_START
        if 0 <= step_index < len(self.state_labels):
            # gate key drives eligibility; the combined column gates on ReadDirExif.
            step_name = STATE_COLUMNS[step_index]["gate"]
            jpgs = int(self.trip_table.item(row, 2).text())

            # Case 1: frozen after WriteCommit
            if sm.Taskstate.get("WriteCommit", [False])[0]:
                self.logger.warning(f"Step '{action_label}' is disabled: workflow frozen after WriteCommit.")
                return

            # Case 2: WriteBlog with no JPGs
            if step_name == "WriteBlog" and jpgs == 0:
                self.logger.warning(f"Step '{action_label}' is disabled: no JPGs available.")
                return

            # Case 3: state not OK to run
            if not sm.stateOKtorun(step_name):
                reason = sm.explainAbort(step_name, raiseSV=False)
                self.logger.warning(f"Step '{action_label}' is disabled: {reason}")
                return

        # Total Files column — show all file types
        if column == 1:
            self.image_viewer_dialog = ImagePreviewDialog(self, image_dir=str(new_dir), trip_root_dir=str(trip_root), mode="all")
            self.image_viewer_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
            self.image_viewer_dialog.show()

        # JPG column
        elif column == 2:
            self.dlgImagePreviewDialog = ImagePreviewDialog(self, image_dir=str(new_dir), trip_root_dir=str(trip_root), mode="jpg")
            self.dlgImagePreviewDialog.show()

        # GPS column — the same viewer the Pictures column opens (Trip has no
        # per-day map today, unlike Album's open_virttrip_map), but forced
        # straight to the no-GPS filter — start_no_gps takes priority over the
        # generic warnings-first cascade, so this is deterministic even on a
        # day that also has species warnings.
        elif column == GPS_COL:
            self.image_viewer_dialog = ImagePreviewDialog(self, image_dir=str(new_dir), trip_root_dir=str(trip_root), mode="all", start_no_gps=True)
            self.image_viewer_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
            self.image_viewer_dialog.show()

        # Geotag column — the same per-day action Unfiled and Days already run
        # (open_tracklog_geotag_for_folder), pointed at this trip's day folder.
        # Modal, so refreshing afterwards picks up whatever GPS/altitude it wrote
        # and repaints the GPS cell beside it.
        elif column == GEOTAG_COL:
            # Shared with the context-menu entry. Repaints the row afterwards; the
            # GPS cell beside it reads data_pictures.csv and the geotag writes XMP
            # sidecars only, so that count legitimately doesn't move until
            # Read & Summarize runs again.
            self._run_trip_geotag(day_str)

        # Back up column — one ZIP per day's WORK to a spare card. Opens the
        # picker with every day that NEEDS a backup already ticked (trip-wide,
        # not just the day you clicked) — the common case, "back up whatever
        # changed", is one click regardless of which row you opened this from.
        # Non-modal (the ZIP work runs on its own QThread) so the rest of SV
        # stays usable while it runs; the row is repainted once it closes.
        elif column == BACKUP_COL:
            from gui.work_backup_dialog import BackupWorkDialog
            if not self._try_start_background_op("Back up work"):
                return
            trip_days = sorted(d for d in Path(new_dir).parent.iterdir() if d.is_dir())
            dlg = BackupWorkDialog(self, trip_days, trip_root=str(trip_root))
            self._backup_work_dlg = dlg  # keep alive while non-modal

            def _on_backup_finished(*_a, _trip_root=trip_root, _review_day=review_day, _day_str=day_str):
                self._end_background_op()
                sm2 = StateMachine(str(_trip_root), str(_review_day), readState=True)
                self.refresh_trip_row(sm2, _day_str)
                self._backup_work_dlg = None

            dlg.finished.connect(_on_backup_finished)
            dlg.show()

        # RAW backup column — a plain folder copy of the day's PICTURES to a
        # spare card/HDD (the other half of "protect today's work" beside
        # Back up above). No day is pre-ticked: unlike the ZIP's manifest,
        # knowing which days still need copying would mean stat-ing every
        # picture at the destination up front — see _format_raw_backup_item.
        # A run can copy several days at once, not just the one clicked, so a
        # single-row refresh (like Back up's) is not enough — the whole
        # overview is repainted once the dialog closes. Non-modal (the copy
        # runs on its own QThread) so the rest of SV stays usable meanwhile.
        elif column == RAW_BACKUP_COL:
            from gui.raw_backup_dialog import RawBackupDialog
            if not self._try_start_background_op("RAW backup"):
                return
            picture_root = Path(new_dir).parent
            trip_days = sorted(d for d in picture_root.iterdir() if d.is_dir())
            dlg = RawBackupDialog(self, trip_days, trip_name=picture_root.name)
            self._raw_backup_dlg = dlg  # keep alive while non-modal

            def _on_raw_backup_finished(*_a):
                self._end_background_op()
                populate_trip_overview(self)
                self._raw_backup_dlg = None

            dlg.finished.connect(_on_raw_backup_finished)
            dlg.show()

        # Batch action
        else:
            # Don't run a pipeline step while an open culling window has edits not
            # yet saved into the day — it would run on stale review CSVs.
            if not self.confirm_no_pending_edits(action_label):
                return
            trip_menu = self.menu_spec.get("Trips", {})
            # Map the column to its Trip-menu action by the stable dict key
            # (STATE_COLUMNS["menu"]) — the header text carries a numbered,
            # newline-formatted label that does not match the dict key.
            if 0 <= step_index < len(STATE_COLUMNS):
                lookup_key = STATE_COLUMNS[step_index]["menu"]
            else:
                lookup_key = action_label
            spec_obj = trip_menu.get(lookup_key)

            if spec_obj and spec_obj.callback:
                spec_obj.callback()
                # Re-read state after the batch run so refresh sees the updated results
                sm = StateMachine(str(trip_root), str(review_day), readState=True)
                self.refresh_trip_row(sm, day_str)
            else:
                self.logger.warning(f"No menu action found for: {action_label}")

    def _find_trip_menu_spec(self, want, menu=None):
        """Find a Trip-menu MenuSpec by its dict key, descending into submenus
        (e.g. Advanced ▸ Reset Trip, Advanced ▸ Legacy ▸ Write Summary) — a
        plain top-level .get() misses anything demoted into a submenu."""
        if menu is None:
            menu = self.menu_spec.get("Trips", {})
        for k, v in menu.items():
            if isinstance(v, dict):
                hit = self._find_trip_menu_spec(want, v)
                if hit:
                    return hit
            elif k == want and v is not None:
                return v
        return None

    def handle_row_rerun(self, row):
        """Double-click on the vertical row header: rerun all ✅ steps for that day."""
        import configparser
        from gui.batch import ChainedBatchRunner
        from utils.batch_config import generate_svconfig_ini

        day_item = self.trip_table.item(row, 0)
        if not day_item:
            return
        day_str = day_item.text()

        # Point config at this day's directory
        project_root = self._trip_picture_root()
        new_dir = project_root / day_str
        _config.set("project", "picturedirectory", str(new_dir))

        trip_root = Path(_config.get("trip", "tripbiotadatabase"))
        review_day = trip_root / "metadata" / "review" / day_str
        sm = StateMachine(str(trip_root), str(review_day), readState=True)

        # All steps in pipeline order with their menu labels and messages
        pipeline = [
            ("ReadDirExif",   "Read Directory", "Reading picture directory...",          "✅ Directory scan complete."),
            ("WritePictures", "Write Summary",  "Reading meta data for all pictures ...", "✅ Writing Summary data."),
            ("WriteBlog",     "Write Blog",     "Create a blog entry ...",               "✅ Blog successfully created."),
            ("Freeze",        "Freeze Data",    "Freeze all collected data ...",          "✅ Data frozen."),
            ("WriteCommit",   "Write Website Commit", "Update the trip website ...",      "✅ Please review updated Website."),
        ]

        completed = [
            (sname, label, bmsg, amsg)
            for sname, label, bmsg, amsg in pipeline
            if sm.Taskstate.get(sname, [False])[0] and sm.Taskstate[sname][1] == "Successfully run."
        ]

        if not completed:
            QMessageBox.information(self, "Nothing to rerun", f"No completed steps found for {day_str}.")
            return

        step_list = "\n".join(f"  • {label}" for _, label, _, _ in completed)
        reply = QMessageBox.question(
            self, "Rerun Steps",
            f"The following steps will be re-run for {day_str}:\n\n{step_list}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Reset completed steps so they can run again
        for sname, _, _, _ in completed:
            sm.Taskstate[sname][0] = False
            sm.Taskstate[sname][1] = ""
        sm.writeState()

        # Build (module_called, statusname, before_msg, after_msg) list from menu specs
        generate_svconfig_ini()
        module_config = configparser.ConfigParser()
        read_ini(module_config, "svconfig.ini")

        steps_to_run = []
        for sname, label, bmsg, amsg in completed:
            spec = self._find_trip_menu_spec(label)
            if spec:
                bp = spec.get_batch_parms()
                if bp:
                    steps_to_run.append((bp["module_called"], sname, bmsg, amsg))

        if not steps_to_run:
            return

        directory_name = new_dir.name

        def on_complete():
            sm2 = StateMachine(str(trip_root), str(review_day), readState=True)
            self.refresh_trip_row(sm2, day_str)

        runner = ChainedBatchRunner(self, self, steps_to_run, module_config, sm, directory_name, on_complete)
        runner.start()

    def handle_column_run_all(self, column):
        """Double-click on a column header: run that step for all eligible rows."""
        from gui.trip_overview import SYNC_COL, STATE_COL_START, STATE_COLUMNS
        from gui.batch import ColumnBatchRunner
        from utils.batch_config import generate_svconfig_ini

        # Sync Sidecars column — all rows are eligible, no statemachine gating
        if column == SYNC_COL:
            self._run_sync_all_days()
            return

        # Only respond to state columns
        state_col_end = STATE_COL_START + len(self.state_labels) - 1
        if column < STATE_COL_START or column > state_col_end:
            return

        step_index = column - STATE_COL_START
        col = STATE_COLUMNS[step_index]
        step_name = col["gate"]                     # eligibility/guards key
        primary = col["run_keys"][-1]               # statemachine key set last
        extra_statusnames = col["run_keys"][:-1]    # set first (combined step)

        action_label = self.trip_table.horizontalHeaderItem(column).text().replace("\n", " ")

        project_root = self._trip_picture_root()
        trip_root = Path(_config.get("trip", "tripbiotadatabase"))

        before_msg, after_msg = col["before"], col["after"]

        # Collect eligible rows
        day_steps = []
        for row in range(self.trip_table.rowCount()):
            day_item = self.trip_table.item(row, 0)
            cell_item = self.trip_table.item(row, column)
            if not day_item or not cell_item:
                continue
            symbol = cell_item.text()
            if symbol not in ("🔘", "✅"):
                continue

            day_str = day_item.text()
            review_day = trip_root / "metadata" / "review" / day_str
            sm = StateMachine(str(trip_root), str(review_day), readState=True)

            # Additional guards (same as cell double-click)
            if sm.Taskstate.get("WriteCommit", [False])[0]:
                continue
            jpgs = int(self.trip_table.item(row, 2).text())
            if step_name == "WriteBlog" and jpgs == 0:
                continue

            needs_reset = (symbol == "✅")
            # For ready rows, verify state machine agrees
            if not needs_reset and not sm.stateOKtorun(step_name):
                continue

            trip_menu = self.menu_spec.get("Trips", {})
            # Look up by the stable STATE_COLUMNS key, not the numbered header text.
            spec_obj = trip_menu.get(col["menu"])
            if not spec_obj:
                continue
            bp = spec_obj.get_batch_parms()
            if not bp:
                continue

            day_steps.append({
                "day_str":      day_str,
                "project_root": project_root,
                "trip_root":    trip_root,
                "module_called": bp["module_called"],
                "statusname":   primary,
                "extra_statusnames": extra_statusnames,
                "before_msg":   before_msg,
                "after_msg":    after_msg,
                "needs_reset":  needs_reset,
            })

        if not day_steps:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Nothing to run", f"No eligible rows for '{action_label}'.")
            return

        step_list = "\n".join(f"  • {s['day_str']}" for s in day_steps)
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, f"Run '{action_label}' for all days",
            f"The following days will run '{action_label}':\n\n{step_list}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        generate_svconfig_ini()

        runner = ColumnBatchRunner(self, self, action_label, day_steps, lambda: None)
        runner.start()

    def _run_sync_all_days(self):
        """Run Sync Sidecars for all days in the trip."""
        from gui.batch import ColumnBatchRunner
        from utils.batch_config import generate_svconfig_ini

        project_root = self._trip_picture_root()
        trip_root = Path(_config.get("trip", "tripbiotadatabase"))

        trip_menu = self.menu_spec.get("Trips", {})
        spec_obj = trip_menu.get("Sync Sidecars")
        if not spec_obj:
            return
        bp = spec_obj.get_batch_parms()
        if not bp:
            return

        day_steps = []
        for row in range(self.trip_table.rowCount()):
            day_item = self.trip_table.item(row, 0)
            if not day_item:
                continue
            day_str = day_item.text()
            day_steps.append({
                "day_str":       day_str,
                "project_root":  project_root,
                "trip_root":     trip_root,
                "module_called": bp["module_called"],
                "statusname":    "SyncSidecars",
                "before_msg":    bp["before_msg"],
                "after_msg":     bp["after_msg"],
                "needs_reset":   False,
            })

        if not day_steps:
            return

        step_list = "\n".join(f"  • {s['day_str']}" for s in day_steps)
        reply = QMessageBox.question(
            self, "Run 'Sync Sidecars' for all days",
            f"The following days will run 'Sync Sidecars':\n\n{step_list}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        generate_svconfig_ini()

        runner = ColumnBatchRunner(self, self, "Sync Sidecars", day_steps, lambda: None)
        runner.start()

    def _new_trip_context_menu(self):
        """A QMenu parented to the main window, so it doesn't inherit the yellow
        background from yellow_tabs (which made hovered text unreadable)."""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background-color: {theme.SURFACE}; color: {theme.INK}; }}")
        return menu

    def _add_trip_geotag_action(self, menu, day_str):
        """Add the per-day "Geotag from tracklog…" entry, or None when the click
        wasn't on a day row.

        Per ROW, not per cell: unlike the pipeline steps it needs no selection,
        so it is offered alongside the run-selection entry rather than inside it.
        Same action as the 🛰️ column and as the Unfiled/Days row action."""
        if not day_str:
            return None
        if not menu.isEmpty():
            menu.addSeparator()
        return menu.addAction(f"Geotag from tracklog… ({day_str})")

    def _run_trip_geotag(self, day_str):
        """Open the tracklog geotag review for one trip day (🛰️ column and the
        context-menu entry share this)."""
        from gui.tracklog_geotag_dialog import open_tracklog_geotag_for_folder
        day_dir = self._trip_picture_root() / day_str
        if not day_dir.is_dir():
            self.logger.warning(
                f"Geotag from tracklog: {day_dir} is not on this machine.")
            return
        _config.set("project", "picturedirectory", str(day_dir))
        trip_root = Path(_config.get("trip", "tripbiotadatabase"))
        review_day = trip_root / "metadata" / "review" / day_str
        sm = StateMachine(str(trip_root), str(review_day), readState=True)
        open_tracklog_geotag_for_folder(self, str(day_dir))
        self.refresh_trip_row(sm, day_str)

    def handle_trip_table_context_menu(self, pos):
        """Right-click on the Trip Overview: offer to run the user's selected
        pipeline cells, plus the per-day actions that need no selection."""
        from gui.trip_overview import STATE_COL_START, STATE_COLUMNS
        from gui.batch import ColumnBatchRunner
        from utils.batch_config import generate_svconfig_ini

        state_col_end = STATE_COL_START + len(self.state_labels) - 1

        # The day under the cursor — per-row actions don't care what is selected.
        clicked_row = self.trip_table.indexAt(pos).row()
        clicked_item = self.trip_table.item(clicked_row, 0) if clicked_row >= 0 else None
        clicked_day = clicked_item.text() if clicked_item else ""

        selected = [
            idx for idx in self.trip_table.selectedIndexes()
            if STATE_COL_START <= idx.column() <= state_col_end
        ]
        if not selected:
            # No pipeline cells selected used to mean NO menu at all. The per-day
            # actions still apply, so offer them on their own.
            menu = self._new_trip_context_menu()
            geo_action = self._add_trip_geotag_action(menu, clicked_day)
            if geo_action is None:
                return
            if menu.exec(self.trip_table.viewport().mapToGlobal(pos)) == geo_action:
                self._run_trip_geotag(clicked_day)
            return

        project_root = self._trip_picture_root()
        trip_root = Path(_config.get("trip", "tripbiotadatabase"))
        trip_menu = self.menu_spec.get("Trips", {})

        # Group the selection by day so each day can be evaluated as its own
        # mini-pipeline. A ⚪ cell on its own is "not runnable now" — but
        # within a multi-step selection, ⚪ becomes runnable once a preceding
        # step in the same day's selection completes. Without this grouping
        # the per-cell stateOKtorun check rejects every ⚪ even when its
        # prerequisite is being run in the very same right-click action.
        from collections import defaultdict
        day_cells = defaultdict(list)
        skipped = 0
        for idx in selected:
            row, col = idx.row(), idx.column()
            day_item = self.trip_table.item(row, 0)
            cell_item = self.trip_table.item(row, col)
            if not day_item or not cell_item:
                skipped += 1
                continue
            day_cells[day_item.text()].append({
                "row":        row,
                "col":        col,
                "step_index": col - STATE_COL_START,
                "step_name":  STATE_COLUMNS[col - STATE_COL_START]["gate"],
                "symbol":     cell_item.text(),
            })

        day_steps = []
        for day_str, cells in day_cells.items():
            cells.sort(key=lambda c: c["step_index"])

            review_day = trip_root / "metadata" / "review" / day_str
            sm = StateMachine(str(trip_root), str(review_day), readState=True)

            # Frozen workflow — entire day is dead.
            if sm.Taskstate.get("WriteCommit", [False])[0]:
                skipped += len(cells)
                continue

            jpgs = int(self.trip_table.item(cells[0]["row"], 2).text())

            # Walk the day's selection in pipeline order. Once we see a cell
            # that's eligible *now* (🔘 / ✅), the chain is considered
            # active — subsequent ⚪ cells in this day's selection will
            # become eligible as preceding steps complete, so we queue them
            # too. ⚪ cells before the chain activates have no upstream
            # selection to unblock them and stay skipped.
            chain_active = False
            for c in cells:
                symbol = c["symbol"]
                step_name = c["step_name"]
                col = c["col"]

                if step_name == "WriteBlog" and jpgs == 0:
                    skipped += 1
                    continue

                # Look up the Trip-menu action by the STABLE dict key, NOT the
                # header text: the headers are numbered ("2 Write Blog") and never
                # match the menu key ("Write Blog"), so header-text lookup returned
                # None and skipped every selected cell (the "run selection" feature
                # looked broken). Same fix as the single-cell double-click path.
                lookup_key = STATE_COLUMNS[c["step_index"]]["menu"]
                spec_obj = trip_menu.get(lookup_key)
                if not spec_obj:
                    skipped += 1
                    continue
                bp = spec_obj.get_batch_parms()
                if not bp:
                    skipped += 1
                    continue

                if symbol in ("🔘", "✅"):
                    chain_active = True
                elif not (symbol == "⚪" and chain_active):
                    skipped += 1
                    continue

                needs_reset = (symbol == "✅")
                colspec = STATE_COLUMNS[c["step_index"]]
                day_steps.append({
                    "day_str":      day_str,
                    "project_root": project_root,
                    "trip_root":    trip_root,
                    "module_called": bp["module_called"],
                    "statusname":   colspec["run_keys"][-1],
                    "extra_statusnames": colspec["run_keys"][:-1],
                    "before_msg":   colspec["before"],
                    "after_msg":    colspec["after"],
                    "needs_reset":  needs_reset,
                })

        menu = self._new_trip_context_menu()
        if not day_steps:
            disabled = menu.addAction("No eligible steps in selection")
            disabled.setEnabled(False)
            geo_action = self._add_trip_geotag_action(menu, clicked_day)
            chosen = menu.exec(self.trip_table.viewport().mapToGlobal(pos))
            if geo_action is not None and chosen == geo_action:
                self._run_trip_geotag(clicked_day)
            return

        n_cells = len(day_steps)
        n_days = len({s["day_str"] for s in day_steps})
        n_steps = len({s["statusname"] for s in day_steps})
        label = (
            f"Run {n_cells} selected cell{'s' if n_cells != 1 else ''} "
            f"({n_steps} step{'s' if n_steps != 1 else ''}, "
            f"{n_days} day{'s' if n_days != 1 else ''})"
        )
        if skipped:
            label += f"  — {skipped} ineligible skipped"
        run_action = menu.addAction(label)
        geo_action = self._add_trip_geotag_action(menu, clicked_day)
        chosen = menu.exec(self.trip_table.viewport().mapToGlobal(pos))
        if geo_action is not None and chosen == geo_action:
            self._run_trip_geotag(clicked_day)
            return
        if chosen != run_action:
            return

        generate_svconfig_ini()
        runner = ColumnBatchRunner(self, self, "Selected steps", day_steps, lambda: None)
        runner.start()

    def rerun_read_summarize_day(self, day_name, reason="", confirm=True):
        """Re-run Read & Summarize for ONE day so an edit reaches the reports.
        Used after an edit/delete on a not-yet-frozen day in the image preview:
        the day on disk changed, so it must be re-read and re-summarized. Phase 3
        keeps it cheap — if the resulting data_pictures.csv is unchanged,
        downstream stays valid.

        confirm=True asks first (Yes/No). confirm=False saves silently behind a
        "Saving changes…" spinner that auto-closes when done — the re-read is fast
        enough that a prompt is just friction (the auto-save-on-close path)."""
        import configparser
        import batch.sv2ReadDirExif
        import batch.sv3WritePictures
        from gui.batch import ChainedBatchRunner
        from utils.batch_config import generate_svconfig_ini

        trip_root_str = _config.get("trip", "tripbiotadatabase", fallback="")
        if not trip_root_str:
            return
        trip_root = Path(trip_root_str)
        review_day = trip_root / "metadata" / "review" / day_name

        if confirm:
            prompt = (f"{reason}\n\n" if reason else "")
            prompt += (f"Re-run Read & Summarize for {day_name} so the change "
                       f"appears in the blog and website?")
            if QMessageBox.question(
                    self, "Re-run Read & Summarize", prompt,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                return

        project_root = self._trip_picture_root()
        _config.set("project", "picturedirectory", str(project_root / day_name))
        generate_svconfig_ini()
        module_config = configparser.ConfigParser()
        read_ini(module_config, "svconfig.ini")
        sm = StateMachine(str(trip_root), str(review_day), readState=True)
        steps = [
            (batch.sv2ReadDirExif.main, "ReadDirExif",
             "Reading picture directory...", "✅ Directory scan complete."),
            (batch.sv3WritePictures.main, "WritePictures",
             "Reading meta data for all pictures ...", "✅ Summary written."),
        ]

        def on_complete():
            sm2 = StateMachine(str(trip_root), str(review_day), readState=True)
            self.refresh_trip_row(sm2, day_name)

        runner = ChainedBatchRunner(self, self, steps, module_config, sm,
                                    day_name, on_complete,
                                    title=("Even geduld..." if confirm
                                           else "Saving changes…"))
        runner.start()

    def rerun_summary_all_days(self, reason=""):
        """Confirm, then re-run Write Summary (sv3) for every eligible day so a
        trip-wide species-NAME change (the English/Dutch dialog) reaches the
        reports. Names live in speciescollection.csv and don't touch
        data_files.csv, so a re-read is unnecessary — Write Summary alone
        suffices. Phase 3 conditional invalidation means days the edited species
        doesn't appear on keep their built Blog/Website/Freeze (their
        data_pictures.csv comes back byte-identical)."""
        import configparser
        from gui.batch import ColumnBatchRunner
        from utils.batch_config import generate_svconfig_ini

        if not self.trip_table:
            return
        trip_root_str = _config.get("trip", "tripbiotadatabase", fallback="")
        if not trip_root_str:
            return
        trip_root = Path(trip_root_str)
        project_root = self._trip_picture_root()

        eligible = []
        for row in range(self.trip_table.rowCount()):
            day_item = self.trip_table.item(row, 0)
            if not day_item:
                continue
            day_str = day_item.text()
            review_day = trip_root / "metadata" / "review" / day_str
            if not (review_day / "data_files.csv").exists():
                continue  # Read Directory never run for this day
            sm = StateMachine(str(trip_root), str(review_day), readState=True)
            if sm.Taskstate.get("WriteCommit", [False])[0]:
                continue  # frozen day
            if "WritePictures" not in sm.Taskstate:
                continue
            eligible.append(day_str)

        if not eligible:
            QMessageBox.information(self, "Re-run Write Summary",
                                    "No days with summary data to refresh.")
            return

        prompt = (f"{reason}\n\n" if reason else "")
        prompt += (f"Re-run Write Summary for all {len(eligible)} day(s) so the "
                   f"updated names appear in the reports?")
        if QMessageBox.question(
                self, "Re-run Write Summary", prompt,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return

        # "Write Summary" lives under Trips ▸ Advanced ▸ Legacy, so search the
        # submenus recursively rather than only the top level (a plain .get here
        # returned None once the step was demoted into Advanced — this re-run was
        # silently a no-op).
        spec_obj = self._find_trip_menu_spec("Write Summary")
        if not spec_obj or not spec_obj.get_batch_parms():
            return
        module_called = spec_obj.get_batch_parms()["module_called"]

        day_steps = []
        for day_str in eligible:
            review_day = trip_root / "metadata" / "review" / day_str
            sm = StateMachine(str(trip_root), str(review_day), readState=True)
            needs_reset = (sm.Taskstate.get("WritePictures", [False])[0]
                           and sm.Taskstate["WritePictures"][1] == "Successfully run.")
            day_steps.append({
                "day_str":      day_str,
                "project_root": project_root,
                "trip_root":    trip_root,
                "module_called": module_called,
                "statusname":   "WritePictures",
                "before_msg":   "Reading meta data for all pictures ...",
                "after_msg":    "✅ Writing Summary data.",
                "needs_reset":  needs_reset,
            })

        generate_svconfig_ini()
        runner = ColumnBatchRunner(self, self, "Write Summary", day_steps, lambda: None)
        runner.start()

    def invalidate_render_day(self, day_name, reason=""):
        """Mark the day's already-built publish steps (Write Blog / Build Website)
        stale after a translation or source-text change — WITHOUT running them.
        Write Blog/Build Website upload to the web server (bad on lodge bandwidth),
        and the data (CSVs + translation cache) is already current, so there is
        nothing local to re-run; the user re-publishes deliberately when it suits.
        This mirrors what a Read & Summarize re-run does to downstream via the
        statemachine cascade. Returns the list of invalidated step labels."""
        trip_root_str = _config.get("trip", "tripbiotadatabase", fallback="")
        if not trip_root_str:
            return []
        trip_root = Path(trip_root_str)
        review_day = trip_root / "metadata" / "review" / day_name
        sm = StateMachine(str(trip_root), str(review_day), readState=True)

        # A committed (frozen) day is locked — leave it alone.
        if sm.Taskstate.get("WriteCommit", [False])[0]:
            return []

        note = "Needs re-run: " + (reason or "content changed")
        invalidated = []
        for step, label in (("WriteBlog", "Write Blog"),):
            st = sm.Taskstate.get(step)
            if st and st[0] and st[1] == "Successfully run.":
                st[0] = False
                st[1] = note
                invalidated.append(label)
        if invalidated:
            sm.writeState()
            self.refresh_trip_row(sm, day_name)
        return invalidated

    def _live_dialog(self, attr):
        """The dialog stored under `attr` if it still exists (not closed and its
        underlying C++ object not deleted), else None. Lets the openers below
        REUSE a window rather than spawning a duplicate on every jump."""
        dlg = getattr(self, attr, None)
        if dlg is None:
            return None
        try:
            dlg.objectName()   # raises RuntimeError if the C++ object is gone
            return dlg
        except RuntimeError:
            return None

    @staticmethod
    def _raise_dialog(dlg):
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def open_rating_editor(self, initial_sci=None):
        from gui.rating_editor import RatingEditorDialog
        trip_root = _config.get("trip", "tripbiotadatabase", fallback="")
        if not trip_root:
            QMessageBox.warning(self, "Trip Species Viewer", "No trip is configured.")
            return
        # On a Biota Edit-panel jump (initial_sci set): reuse an already-open
        # viewer for THIS trip so the ping-pong doesn't stack windows. A different
        # trip → close the stale one first. Plain menu-opens keep their old
        # behaviour (honour the day selected in the trip table).
        existing = self._live_dialog("_rating_editor_dlg") if initial_sci else None
        if existing is not None:
            if str(getattr(existing, "_trip_root", "")) == str(Path(trip_root)):
                existing.show_species(initial_sci)
                self._raise_dialog(existing)
                return
            existing.close()
        # Launched from the Biota Browser on a species → open whole-Trip scope on
        # it (a day scope could hide a species shot on another day). Otherwise
        # follow the day selected in the trip table.
        day_str = None
        if initial_sci is None and self.trip_table:
            row = self.trip_table.currentRow()
            if row >= 0:
                item = self.trip_table.item(row, 0)
                if item:
                    day_str = item.text()
        # Keep a strong reference on the main window. A non-modal dialog held
        # only in a local goes out of scope when this method returns; Python's
        # GC (triggered by the heavy master CSV read) then reclaims it and its
        # widgets mid-operation → "wrapped C/C++ object ... deleted" crashes.
        # (We recreate per open — our converged RatingEditorDialog has no reload().)
        self._rating_editor_dlg = RatingEditorDialog(self, trip_root, day_str,
                                                     initial_sci=initial_sci)
        self._rating_editor_dlg.show()

    def open_master_species_viewer(self, initial_sci=None):
        """Standalone viewer over the master collection, one species at a time.
        Needs no active trip — the species list + pictures come from the master
        DB. Editing follows the machine's master read-only policy. `initial_sci`
        opens straight on that species (the Biota Browser's Edit-panel jump)."""
        from gui.rating_editor import RatingEditorDialog
        master_root = _config.get("master", "masterbiotadatabase", fallback="")
        if not master_root:
            QMessageBox.warning(self, "Master Species Viewer",
                                "No master database is configured.")
            return
        # On a Biota Edit-panel jump: reuse an open viewer rather than stacking.
        existing = self._live_dialog("_master_species_viewer_dlg") if initial_sci else None
        if existing is not None:
            existing.show_species(initial_sci)
            self._raise_dialog(existing)
            return
        # The same dialog the Album viewer is, opened with no date window — see
        # docs/plans/species_viewer_convergence_plan.md. Strong ref on the main
        # window: see open_rating_editor for the why.
        self._master_species_viewer_dlg = RatingEditorDialog(
            self, mode="master", initial_sci=initial_sci)
        self._master_species_viewer_dlg.show()

    def open_biota_browser(self, start_mode="tree", start_sci=None):
        """Desktop twin of the website's biota tree — walk the taxonomy for the
        master, drill to a species to see its pictures. Needs no active trip.
        `start_mode="allspecies"` opens straight to the flat all-species index;
        `start_sci` opens straight to that species' pretty page (the Master
        Species Viewer's "✦ Species page" jump)."""
        from gui.biota_browser import BiotaBrowserDialog
        master_root = _config.get("master", "masterbiotadatabase", fallback="")
        if not master_root:
            QMessageBox.warning(self, "Biota Browser",
                                "No master database is configured.")
            return
        # On a "✦ Species…" jump: reuse an open MASTER browser (no repo/album
        # scope) rather than stacking. Plain menu-opens create fresh as before.
        existing = self._live_dialog("_biota_browser_dlg") if start_sci else None
        if existing is not None and not getattr(existing, "_repo_root", None) \
                and not getattr(existing, "_album_from", None):
            existing.show_species_page(start_sci)
            self._raise_dialog(existing)
            return
        # Strong ref on the main window — see open_rating_editor for the why.
        self._biota_browser_dlg = BiotaBrowserDialog(
            self, start_mode=start_mode, start_sci=start_sci)
        self._biota_browser_dlg.show()

    def open_trip_biota_browser(self, start_sci=None, start_mode="tree"):
        """Biota Browser over the ACTIVE TRIP's repository (read-only). A trip repo
        is master-shaped, so it browses through the same MasterBiotaSource — with
        its own SQLite cache (never touching master.db) and no editing (that stays
        in the authorized Tech panels). `start_sci` opens straight onto a species'
        pretty page (the Trip Species Viewer's "✦ Species…" jump)."""
        import hashlib
        from gui.biota_browser import BiotaBrowserDialog
        trip_root = (_config.get("trip", "tripbiotadatabase", fallback="")
                     or _config.get("repository", "biotadatabase", fallback="")
                     ).replace('"', "").replace("'", "").strip()
        if not trip_root or not Path(trip_root).exists():
            QMessageBox.warning(self, "Biota Browser (Trip)",
                                "No active trip repository is configured.")
            return
        cache = "trip_" + hashlib.sha1(trip_root.encode("utf-8")).hexdigest()[:12]
        name = os.path.basename(trip_root.rstrip("/\\")) or "Trip"
        # On a "✦ Species…" jump: reuse an open Trip browser for THIS repo;
        # replace on a switch. Plain menu-opens create fresh as before.
        existing = self._live_dialog("_trip_biota_browser_dlg") if start_sci else None
        if existing is not None:
            if str(getattr(existing, "_repo_root", "")) == str(trip_root):
                existing.show_species_page(start_sci)
                self._raise_dialog(existing)
                return
            existing.close()
        # Strong ref on the main window — see open_rating_editor for the why.
        self._trip_biota_browser_dlg = BiotaBrowserDialog(
            self, start_mode=start_mode, repo_root=trip_root, scope_title=f"Trip · {name}",
            read_only_scope=True, cache_name=cache, start_sci=start_sci)
        self._trip_biota_browser_dlg.show()

    def open_album_biota_browser(self, start_sci=None, start_mode="tree"):
        """Biota Browser over the ACTIVE ALBUM (read-only). An album is a live date
        window on the master (no stored data of its own), so this reads the REAL
        master restricted to the album's fromdate..todate (from its virttrip.ini).
        `start_sci` opens straight onto a species' pretty page (the Album Species
        Viewer's "✦ Species…" jump)."""
        import configparser
        from gui.biota_browser import BiotaBrowserDialog
        album_root = _config.get("virttrip", "virttripbiotadatabase",
                                 fallback="").replace('"', "").replace("'", "").strip()
        if not album_root or not Path(album_root).exists():
            QMessageBox.warning(self, "Biota Browser (Album)",
                                "No active album is configured.")
            return
        ini = Path(album_root) / "virttrip.ini"
        fromdate = todate = name = ""
        if ini.exists():
            cp = configparser.ConfigParser()
            try:
                read_ini(cp, ini)
                fromdate = cp.get("trip_config", "fromdate", fallback="").strip()
                todate = cp.get("trip_config", "todate", fallback="").strip()
                name = cp.get("trip_config", "trip_name", fallback="").strip()
            except Exception:
                pass
        if not (fromdate and todate):
            QMessageBox.warning(self, "Biota Browser (Album)",
                                "This album has no date range in its virttrip.ini.")
            return
        name = name or os.path.basename(album_root.rstrip("/\\")) or "Album"
        # On a "✦ Species…" jump: reuse an open Album browser for THIS date
        # window; replace on a switch. Plain menu-opens create fresh as before.
        existing = self._live_dialog("_album_biota_browser_dlg") if start_sci else None
        if existing is not None:
            if getattr(existing, "_album_from", None) == fromdate \
                    and getattr(existing, "_album_to", None) == todate:
                existing.show_species_page(start_sci)
                self._raise_dialog(existing)
                return
            existing.close()
        # Strong ref on the main window — see open_rating_editor for the why.
        self._album_biota_browser_dlg = BiotaBrowserDialog(
            self, start_mode=start_mode, scope_title=f"Album · {name}", read_only_scope=True,
            album_from=fromdate, album_to=todate, start_sci=start_sci)
        self._album_biota_browser_dlg.show()

    def open_species_map(self):
        """Standalone species distribution map — a peer of the Species Viewer
        (grid): pick 1–4 species and see their GPS points on a map, click a
        bubble for its pictures, and open large views straight from the map.
        Opens empty; the user adds species."""
        from gui.map_view import MapView
        master_root = _config.get("master", "masterbiotadatabase", fallback="").strip()
        if not master_root or not Path(master_root).exists():
            QMessageBox.warning(self, "Species Map",
                                "No master database is configured.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            from db.master_db import MasterDB
            db = MasterDB(master_root)
            db.ensure_fresh()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            if self.logger:
                self.logger.error(f"Species Map: master load failed: {e}", exc_info=True)
            QMessageBox.warning(self, "Species Map",
                                "Master database load failed — see the log.")
            return
        finally:
            QApplication.restoreOverrideCursor()
        # Strong ref on the main window so the dialog isn't GC'd.
        self._species_map_dlg = MapView(self, db)
        self._species_map_dlg.show()

    def _review_day_dirs(self, root: str) -> list:
        """The metadata/review/<day> folders of a trip or virtual trip that carry
        read data. Both layouts are identical, so both maps read them the same.
        A day folder is a DATE — the 'frozen/' sibling that the website build drops
        in review/ is not a day."""
        import re
        from pathlib import Path
        review_root = Path(root) / "metadata" / "review"
        if not review_root.exists():
            return []
        is_day = re.compile(r"^\d{4}-\d{2}-\d{2}$").match
        return sorted(
            [d for d in review_root.iterdir()
             if d.is_dir() and is_day(d.name) and (d / "data_files.csv").exists()],
            key=lambda p: p.name)

    def _master_is_editable(self) -> bool:
        """The machine's master write policy — the same switch the Master Species
        Viewer honours. SmallBoy locks this to read-only, so no master geotagging
        can happen there."""
        return _config.get("master", "readonly",
                           fallback="yes").strip().lower() == "no"

    def _collection_from_review_days(self, day_dirs: list, *, read_only: bool = False,
                                     allow_geotag: bool = False, label: str = "Map"):
        """Accumulate the given days' review CSVs into ONE in-memory picture set →
        (collection, keys, loader). No exiftool: the GPS / date / place are already
        in the CSVs. `Files` and `Pictures` both accumulate across reads, and
        read_pictures restores each UPN's file list, so UPN→file linking survives
        the merge. Returns the same kind of collection the per-day 🗺 Map uses, so
        bubbles, filmstrip and large view work unchanged across days."""
        from engine.filepicture import FilePicture
        from engine.filecsv import Files
        from engine.picturecsv import Pictures
        from gui.preview_provider import PreviewCollection
        from gui.image_loader import AsyncImageLoader
        fi, upn = Files(None), Pictures(None)
        for d in day_dirs:
            try:
                fi.read_file(str(d / "data_files.csv"), silent=True)
                p = d / "data_pictures.csv"
                if p.exists():
                    upn.read_pictures(str(p))
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"{label}: skipping {d.name}: {e}")
        fpr = FilePicture(None)
        fpr.fi, fpr.upn = fi, upn
        self._overlay_sidecar_gps(fpr, label=label)
        loader = AsyncImageLoader(self)
        coll = PreviewCollection(fpr, loader=loader, read_only=read_only,
                                 allow_geotag=allow_geotag)
        return coll, coll.all_keys(), loader

    def _collection_from_master_daterange(self, from_date, to_date, *,
                                          read_only: bool = False,
                                          allow_geotag: bool = False,
                                          label: str = "Map"):
        """The album map's collection, read LIVE from the master by date range —
        the replacement for reading the per-day review CSV snapshot. An album is a
        window ON the master (pictures whose camera DateTimeOriginal falls in
        [from_date, to_date]), not a copy, so this reads the same rows the day viewer
        and overview now do. Returns (collection, keys, loader) shaped exactly like
        _collection_from_review_days, so the map works unchanged.

        The map reads a picture's position from its FILE rows' Composite:GPSPosition
        (gps_cached). But placed GPS lives in the picture-level mirror
        (pictures.GPSPosition) — Helgoland's camera had no receiver, so ALL its GPS is
        there — so we seed the file rows from that mirror. The sidecar overlay then
        only has to catch coordinates still unmirrored (and folds them in)."""
        from engine.filepicture import FilePicture
        from engine.filecsv import Files
        from engine.picturecsv import Pictures
        from gui.preview_provider import PreviewCollection
        from gui.image_loader import AsyncImageLoader
        from db.master_db import MasterDB
        master_root = (_config.get("master", "masterbiotadatabase", fallback="")
                       or "").replace('"', "").replace("'", "").strip()
        db = MasterDB(master_root)
        by_day = db.pictures_in_daterange(from_date, to_date)
        pics = [p for day in by_day.values() for p in day]
        file_rows = db.files_for_upns([p["UniquePictureName"] for p in pics])
        fi, upn = Files(None), Pictures(None)
        for p in pics:
            upn.UniquePictureName_dict.setdefault(p["UniquePictureName"], p)
        for fname, frow in file_rows.items():
            fi.files_dict.setdefault(fname, frow)
        self._seed_file_gps_from_pictures(fi, upn)
        fpr = FilePicture(None)
        fpr.fi, fpr.upn = fi, upn
        self._overlay_sidecar_gps(fpr, label=label)
        loader = AsyncImageLoader(self)
        coll = PreviewCollection(fpr, loader=loader, read_only=read_only,
                                 allow_geotag=allow_geotag)
        return coll, coll.all_keys(), loader

    @staticmethod
    def _seed_file_gps_from_pictures(fi, upn) -> None:
        """Copy each picture's mirrored GPSPosition / GPSProcessingMethod (the master's
        reconciled coordinate, into which every geotag is mirrored) onto its file rows'
        Composite:GPSPosition / EXIF:GPSProcessingMethod — but only where the file row
        carries no camera coordinate of its own, so a real in-camera GPS always wins.
        The map reads position from file rows; placed GPS lives in the picture mirror,
        so without this an album whose camera had no receiver plots nothing.

        A picture that has a coordinate while its FILES carry none is, by definition,
        one we PLACED (a camera fix would be in the file row) — so the seeded coordinate
        is marked MANUAL, which is what lets the map offer to drag and refine it. A
        stored method on the picture wins if present."""
        from gui.geolocate import GPS_MANUAL
        files_by_upn: dict = {}
        for frow in fi.files_dict.values():
            u = (frow.get("UniquePictureName") or "").strip()
            if u:
                files_by_upn.setdefault(u, []).append(frow)
        for u, prow in upn.UniquePictureName_dict.items():
            pos = (prow.get("GPSPosition") or "").strip()
            if not pos:
                continue
            method = (prow.get("GPSProcessingMethod") or "").strip() or GPS_MANUAL
            for frow in files_by_upn.get(u, []):
                if not (frow.get("Composite:GPSPosition") or "").strip():
                    frow["Composite:GPSPosition"] = pos
                    frow["EXIF:GPSProcessingMethod"] = method

    def _overlay_sidecar_gps(self, fpr, *, label: str = "Map") -> int:
        """Patch coordinates written to the sidecars (an XMP, or the JPG itself for a
        JPG-only picture) into the freshly-loaded CSV rows, for every picture the CSV
        thinks has no GPS.

        The day CSVs are a SNAPSHOT — Extract Days copied them out of the master, and
        the master's own CSV only changes on a re-read. Geotagging writes to the
        sidecar. So without this the whole-trip map opens on an empty world map
        saying "517 without GPS" while the day map, which reads the sidecars, shows 92
        of them sitting on Helgoland.

        GPSProcessingMethod is carried over too, not just the coordinate: it is what
        tells the map a position was placed by hand and may therefore be dragged. Miss
        it and a picture you placed would plot correctly and then refuse to move.

        Cache-first (the same reader the day viewer uses), so an untouched trip costs
        no exiftool call. Returns how many pictures gained a coordinate."""
        import os
        from gui.sidecar_provider import _read_sidecar_rows, _norm

        has_gps: dict = {}          # UPN -> the CSV already knows a coordinate
        xmp_of: dict = {}           # UPN -> (xmp path, its file row) — preferred source
        jpg_of: dict = {}           # UPN -> (jpg path, its file row) — JPG-only fallback
        for frow in fpr.fi.files_dict.values():
            u = (frow.get("UniquePictureName") or "").strip()
            if not u:
                continue
            if frow.get("Composite:GPSPosition") or frow.get("EXIF:GPSLatitude"):
                has_gps[u] = True
            ext = (frow.get("WindowsExtension") or "").lower()
            p = (frow.get("WindowsFileName") or "").strip()
            if not p:
                continue
            if ext == "xmp" and u not in xmp_of:
                xmp_of[u] = (p, frow)
            elif ext in ("jpg", "jpeg") and u not in jpg_of:
                jpg_of[u] = (p, frow)

        # Where WE re-read a placed coordinate from: the XMP sidecar if there is one,
        # else the JPG itself. A JPG-only picture (a phone shot, a friend's camera —
        # no RAW, no sidecar) carries its GPS IN the file, and geotagging writes it
        # there. Without the fallback those pictures stayed "without GPS" on every
        # re-open even though the coordinate was on disk the whole time.
        src_of = {u: xmp_of.get(u) or jpg_of[u]
                  for u in (set(xmp_of) | set(jpg_of))}

        need = [u for u, (p, _r) in src_of.items()
                if not has_gps.get(u) and os.path.isfile(p)]
        if not need:
            return 0

        try:
            rows, _c = _read_sidecar_rows([src_of[u][0] for u in need])
        except Exception as e:
            if self.logger:
                self.logger.warning(f"{label}: could not read sidecars: {e}")
            return 0

        from engine.geo import parse_gps_dms
        found = 0
        placed: dict = {}          # UPN -> (lat, lon) to fold into the master DB
        for u in need:
            path, frow = src_of[u]
            srow = rows.get(_norm(path)) or {}
            pos = str(srow.get("GPSPosition", "") or "").strip()
            if not pos:
                continue
            frow["Composite:GPSPosition"] = pos
            method = str(srow.get("GPSProcessingMethod", "") or "").strip()
            if method:
                frow["EXIF:GPSProcessingMethod"] = method
            found += 1
            ll = parse_gps_dms(pos)
            if ll:
                placed[u] = ll
        if found and self.logger:
            self.logger.info(
                f"{label}: {found} picture(s) carry a coordinate written to their "
                f"sidecar that the day CSVs predate.")
        # Fold those coordinates into the master's pictures.GPSPosition too. The
        # master read never touches sidecars, so GPS placed before the write-time
        # mirror (geolocate.write_gps) existed lives ONLY in the sidecar — which is
        # why the album overview (a fast DB read) still called them missing. Opening
        # the map, which already reads exactly these sidecars, is the natural place
        # to bring the master up to date; only the ones the DB lacks are written.
        if placed:
            self._mirror_placed_gps_to_master(placed)
        return found

    def _mirror_placed_gps_to_master(self, gps_by_upn: dict) -> None:
        """Best-effort: fold placed coordinates the master DB does not have yet into
        its pictures.GPSPosition, so the overview's fast DB read agrees with the map.
        Only master pictures are touched; a read-only master or an unreachable DB is
        a silent no-op. Idempotent — already-known coordinates are skipped."""
        try:
            master_root = (_config.get("master", "masterbiotadatabase", fallback="")
                           or "").replace('"', "").replace("'", "").strip()
            if not master_root:
                return
            if _config.get("master", "readonly", fallback="yes").strip().lower() != "no":
                return
            from db.master_db import MasterDB
            db = MasterDB(master_root)
            have = db.upns_with_gps(list(gps_by_upn))
            todo = {u: ll for u, ll in gps_by_upn.items() if u not in have}
            if todo:
                n = db.apply_gps(todo)
                if n:
                    if self.logger:
                        self.logger.info(f"{n} placed coordinate(s) folded into the master.")
                    # The album overview reads no-GPS from the DB we just updated, so
                    # repaint it (deferred) — its count corrects without a switch.
                    self.refresh_gps_counts("virttrip")
        except Exception as e:
            if self.logger:
                self.logger.debug(f"GPS master-mirror (map) skipped: {e}")

    def refresh_gps_counts(self, kind: str = "virttrip"):
        """Repaint the yellow panel after something wrote GPS into the pictures it
        counts (placing on the map, moving a bubble, Get GPS from location).

        The No-GPS column is the one number in the panel that no longer holds once a
        map is open: it is read from the sidecars, and placing a day writes exactly
        those sidecars. Without this the count stood at its pre-placement value until
        the app was restarted — the map showed the pictures on the island while the
        panel still called them missing.

        Deferred one event-loop turn on purpose: the caller can be a signal from the
        very table that the repopulate destroys."""
        fn = populate_trip_overview if kind == "trip" else populate_virttrip_overview
        QTimer.singleShot(0, lambda: fn(self))

    def open_trip_map(self):
        """Whole-trip map: plot EVERY day of the active trip on one map (all
        species) with the travel path, with the same bubbles, filmstrip and large
        view as the per-day 🗺 Map — spanning the whole trip."""
        from pathlib import Path
        from utils.trip_identity import resolve_trip_picture_root
        from gui.map_view import TripMapView
        trip_root = _config.get("trip", "tripbiotadatabase", fallback="").strip()
        if not trip_root:
            QMessageBox.warning(self, "Trip Map", "No active trip is configured.")
            return
        day_dirs = self._review_day_dirs(trip_root)
        if not day_dirs:
            QMessageBox.warning(
                self, "Trip Map",
                "No days with review data were found — run Read & Summarize first.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            coll, keys, loader = self._collection_from_review_days(
                day_dirs, label="Trip Map")
        finally:
            QApplication.restoreOverrideCursor()
        if not keys:
            QMessageBox.information(self, "Trip Map", "No pictures found for this trip.")
            return
        trip_name = Path(resolve_trip_picture_root(trip_root, kind="trip")
                         or trip_root).name
        # Strong ref on the main window so the dialog isn't GC'd.
        self._trip_map_dlg = TripMapView(
            self, coll, keys, title=f"Map — {trip_name} (whole trip)",
            loader=loader, default_path=True, trip_root=trip_root)
        self._trip_map_dlg.pictures_geotagged.connect(
            lambda _keys: self.refresh_gps_counts("trip"))
        self._trip_map_dlg.show()

    def open_virttrip_map(self, day_dirs: list | None = None):
        """Map the active virtual trip — the whole trip, or one day when the caller
        passes its review folder.

        A virtual trip does not copy pictures, it points at the master's originals,
        so this map is read-only for rating / colour / files / delete, and a bubble
        cannot be dragged to MOVE pictures that already have coordinates.

        The ONE exception is geotagging: a picture with NO GPS may be placed, and
        the write lands in the master's own sidecar. That is deliberate — it is how
        the master's missing coordinates get cleaned up, and this map (with its
        no-GPS tray, its neighbours and its time-interpolated position suggestion)
        is the best surface we have for it. Gated on the machine's master write
        policy, so a read-only master (SmallBoy) still gets the view-only map."""
        from pathlib import Path
        from gui.map_view import TripMapView
        from utils.virttrip_config import read_virttrip_ini
        root = _config.get("virttrip", "virttripbiotadatabase", fallback="").strip()
        if not root:
            QMessageBox.warning(self, "Album Map",
                                "No album is active.")
            return
        one_day = day_dirs is not None
        geotag = self._master_is_editable()
        # Read the album's pictures LIVE from the master by date range (one day, or the
        # whole trip's fromdate..todate) instead of the per-day review CSV snapshot.
        # An album is a WINDOW on the master, not a copy, so the per-day review/ folders
        # may never have been extracted to disk — don't require them for the map.
        info = read_virttrip_ini(root) or {}
        if one_day:
            day_dirs = list(day_dirs)
            fdate = tdate = day_dirs[0].name
        else:
            fdate = (info.get("fromdate") or "").strip()
            tdate = (info.get("todate") or "").strip()
        mroot = (_config.get("master", "masterbiotadatabase", fallback="")
                 or "").replace('"', "").replace("'", "").strip()
        use_query = bool(mroot and Path(mroot).is_dir() and fdate and tdate)
        # Only the CSV fallback needs the on-disk review day folders. When we can query
        # the master by date range (the normal case for an album), the whole trip maps
        # straight from the master — even with no extracted day folders on disk, which
        # is exactly why the old _review_day_dirs guard wrongly reported "extract the
        # days first" for a fresh album that reads the master live.
        if not use_query and not one_day:
            day_dirs = self._review_day_dirs(root)
        if not use_query and not day_dirs:
            QMessageBox.warning(
                self, "Album Map",
                "No pictures found for this album, and no master is configured to "
                "read them from.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if use_query:
                coll, keys, loader = self._collection_from_master_daterange(
                    fdate, tdate, read_only=True, allow_geotag=geotag,
                    label="Album Map")
            else:
                coll, keys, loader = self._collection_from_review_days(
                    day_dirs, read_only=True, allow_geotag=geotag,
                    label="Album Map")
        finally:
            QApplication.restoreOverrideCursor()
        if not keys:
            QMessageBox.information(self, "Album Map",
                                    "No pictures found for this album.")
            return
        info = read_virttrip_ini(root) or {}
        name = info.get("trip_name") or Path(root).name
        scope = day_dirs[0].name if one_day else "whole trip"
        suffix = "" if geotag else ", view only"
        # Strong ref on the main window so the dialog isn't GC'd.
        self._virttrip_map_dlg = TripMapView(
            self, coll, keys, title=f"Map — {name} ({scope}{suffix})",
            loader=loader, default_path=not one_day, read_only=True,
            allow_geotag=geotag,
            read_only_note="An album shares the master's pictures instead of "
                           "copying them, so this map only shows them — it cannot "
                           "change their location, rating or files.")
        self._virttrip_map_dlg.pictures_geotagged.connect(
            lambda _keys: self.refresh_gps_counts("virttrip"))
        self._virttrip_map_dlg.show()

    def open_virttrip_rating_editor(self, initial_sci=None):
        """Album Species Viewer over the active album — the master seen through
        the album's date window, one species at a time. Day / Album / Master
        scope are the same query with a wider window each time; Master scope is
        the standalone Master Species Viewer with the album's own pictures
        tagged. Editing follows the machine's master write policy, because the
        write goes to the master (as it does in the album day viewer).
        `initial_sci` opens straight on that species (the Biota Browser's
        Edit-panel jump)."""
        from gui.rating_editor import RatingEditorDialog
        virttrip_root = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        if not virttrip_root:
            QMessageBox.warning(self, "Trip Species Viewer",
                                "No album (Album) is active.")
            return
        # On a Biota Edit-panel jump: reuse an open Album viewer for THIS album;
        # replace on a switch. Plain menu-opens keep their old behaviour.
        existing = self._live_dialog("_virttrip_rating_editor_dlg") if initial_sci else None
        if existing is not None:
            if str(getattr(existing, "_trip_root", "")) == str(Path(virttrip_root)):
                existing.show_species(initial_sci)
                self._raise_dialog(existing)
                return
            existing.close()
        # Strong ref on the main window — see open_rating_editor for the why.
        self._virttrip_rating_editor_dlg = RatingEditorDialog(
            self, virttrip_root, day_str=None, album_mode=True, scope_label="Album",
            initial_sci=initial_sci)
        self._virttrip_rating_editor_dlg.show()

    def open_translation_validator(self, day_str):
        from gui.translation_validate import TranslationValidateDialog
        trip_root = _config.get("trip", "tripbiotadatabase", fallback="")
        if not trip_root:
            QMessageBox.warning(self, "Translation Validation", "No trip is configured.")
            return
        dlg = TranslationValidateDialog(self, trip_root, day_str)
        dlg.show()

    def update_trip_label(self):
        trip_path = _config.get("trip", "tripbiotadatabase", fallback="")
        trip_name = Path(trip_path).name if trip_path else "None"
        self.trip_label.setText(f"Trip: {trip_name}")

    def update_master_snapshot_label(self):
        """Put the SmallMaster snapshot's age in the status bar, or hide it.

        Shown when this machine holds a snapshot — either because the installed
        copy carries its date, or because the machine is locked read-only, which
        is what an install does and is the one durable sign of a SmallMaster.
        In that second case the date is estimated from the metadata files and
        says so in the tooltip, because a guess presented as a fact is worse
        than no date at all.
        """
        from engine import smallmaster_stamp as _st
        label = getattr(self, "master_snapshot_label", None)
        if label is None:
            return
        root = _config.get("master", "masterbiotadatabase", fallback="")
        info = _st.read_stamp(root)
        if info is None and _config.get("master", "readonly_locked", fallback="") == "yes":
            info = _st.estimate_from_files(root)
        if info is None:
            label.setVisible(False)
            label.setToolTip("")
            return
        from gui import theme as _th
        lvl = _st.level(info)
        colour = {"alert": _th.ALERT, "warn": _th.WARN}.get(lvl, _th.INK_SOFT)
        label.setText(f"Master snapshot: {_st.describe(info, short=True)}")
        label.setStyleSheet(f"color: {colour}; padding-right: 8px;")
        label.setToolTip(
            _st.describe(info) + "\n\n"
            "Lifer verdicts and species suggestions come from this copy of the "
            "master. Refresh it (Utilities ▸ Install Small Master) if it is "
            "older than the pictures you are culling.")
        label.setVisible(True)

    def _report_master_snapshot(self):
        """One start-up line for the same fact, so it is in the log a day later
        when the question is "what did it know at the time?". Silent on a
        machine with the real master."""
        from engine import smallmaster_stamp as _st
        if not self.logger:
            return
        root = _config.get("master", "masterbiotadatabase", fallback="")
        info = _st.read_stamp(root)
        if info is None and _config.get("master", "readonly_locked", fallback="") == "yes":
            info = _st.estimate_from_files(root)
        if info is None:
            return
        line = f"Master snapshot on this machine: {_st.describe(info)}."
        lvl = _st.level(info)
        if lvl == "alert":
            self.logger.error(line + " Refresh it before trusting lifer verdicts.")
        elif lvl == "warn":
            self.logger.warning(line + " Worth refreshing before a trip.")
        else:
            self.logger.info(line)

    def remove_existing_overview_tab(self):
        for i in range(self.yellow_tabs.count()):
            if self.yellow_tabs.tabText(i) == "Active Trip":
                self.yellow_tabs.removeTab(i)
                break

    def remove_existing_virttrip_tab(self):
        for i in range(self.yellow_tabs.count()):
            if self.yellow_tabs.tabText(i) == VIRTTRIP_TAB_TITLE:
                self.yellow_tabs.removeTab(i)
                break

    def remove_existing_days_tab(self):
        for i in range(self.yellow_tabs.count()):
            if self.yellow_tabs.tabText(i) == DAYS_TAB_TITLE:
                self.yellow_tabs.removeTab(i)
                break

    def remove_existing_unfiled_tab(self):
        for i in range(self.yellow_tabs.count()):
            if self.yellow_tabs.tabText(i) == UNFILED_TAB_TITLE:
                self.yellow_tabs.removeTab(i)
                break

    # ── Left list ↔ yellow table mirroring ─────────────────────────────────
    # The left column and the yellow table show the SAME collection in the two
    # new lanes, so a selection made in one has to land in the other. The panels
    # call these by name (guarded with getattr), which keeps them ignorant of how
    # the table is built.

    def mirror_day_selection(self, folder):
        table = getattr(self, "days_table", None)
        if table is None:
            return
        from gui.days_overview import _FOLDER_ROLE as ROLE
        from gui.overview_table import select_row_for_folder
        select_row_for_folder(table, folder, role=ROLE)

    def mirror_unfiled_selection(self, folder):
        table = getattr(self, "unfiled_table", None)
        if table is None:
            return
        from gui.unfiled_overview import _FOLDER_ROLE as ROLE
        from gui.overview_table import select_row_for_folder
        select_row_for_folder(table, folder, role=ROLE)
