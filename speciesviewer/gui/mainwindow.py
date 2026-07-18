from pathlib import Path
import os

from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QStatusBar, QApplication
from PyQt6.QtWidgets import QPlainTextEdit, QLabel, QSizePolicy, QLineEdit, QTabWidget, QTableWidgetItem, QMessageBox
from PyQt6.QtCore import Qt, QTimer

from utils.config import _config, persist_config
from gui.menu import create_menubar
from gui.menuspec import build_menu_spec
from gui.trip_overview import populate_trip_overview
from gui.virttrip_overview import populate_virttrip_overview, TAB_TITLE as VIRTTRIP_TAB_TITLE
from engine.translatelib import CachedTranslate
from widgets.TripInfoPanel import TripInfoPanel
from widgets.VirtTripInfoPanel import VirtTripInfoPanel
from widgets.DaysPanel import DaysPanel
from gui.trip_overview import populate_trip_overview
from engine.statemachine import StateMachine
from gui.image_viewer import ImagePreviewDialog
from utils.gui_config import load_gui_cache  # or wherever your cache functions live




class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.trip_table = None
        self.virttrip_table = None
        self.state_keys = None
        self.state_labels = None
        self.setWindowTitle("Species Viewer 0.1")
        # Create and attach status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)        
        self.resize(1200, 900)
        self.logger = None

        root_field = QLineEdit()  # Shared reference across tabs
        root_field.setText(_config["trip"]["tripbiotadatabase"])        
        # layout

        # Create the splitter
        self.hsplitter = QSplitter(Qt.Orientation.Horizontal)

        # Red panel
        red_panel = QWidget()
        red_panel.setStyleSheet("background-color: #f9f9f9;")
        red_layout = QVBoxLayout(red_panel)
        red_layout.setContentsMargins(0, 0, 0, 0)
        cache = load_gui_cache()
        trip_project_map = cache.get("trip_project_map", {})  # ✅ actual dict
        # Keep a reference so populate_trip_overview can refresh the active-trip
        # ▶ marker after any trip switch (panel click, create/select dialog, …).
        self.trip_panel = TripInfoPanel(self, trip_project_map, populate_trip_overview)
        red_layout.addWidget(self.trip_panel, 3)
        # Days list (ad-hoc single-date folders → Master) below the trips.
        self.days_panel = DaysPanel(self)
        red_layout.addWidget(self.days_panel, 1)
        self.hsplitter.addWidget(red_panel)

        # Yellow panel
        self.yellow_tabs = QTabWidget()
        self.yellow_tabs.setStyleSheet("background-color: #fff8d2;")
        self.hsplitter.addWidget(self.yellow_tabs)

        # Virtual trip navigator panel
        self.virttrip_panel = VirtTripInfoPanel(self, populate_virttrip_overview)
        self.hsplitter.addWidget(self.virttrip_panel)

        # Optional: set initial sizes
        self.hsplitter.setSizes([220, 720, 220])

        # Optional: make panels expand
        self.hsplitter.setStretchFactor(0, 1)
        self.hsplitter.setStretchFactor(1, 3)
        self.hsplitter.setStretchFactor(2, 1)

        # Wrap in a container widget
        color_row = QWidget()
        layout = QVBoxLayout(color_row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.hsplitter)
        color_row.setMinimumHeight(100)
 

        self.green_log_box = QPlainTextEdit()
        self.green_log_box.setReadOnly(True)
        self.green_log_box.setStyleSheet("background-color: #d0f0c0;")
        self.green_log_box.setMinimumHeight(100)
        self.green_log_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.vsplitter = QSplitter(Qt.Orientation.Vertical)
        self.vsplitter.addWidget(color_row)
        self.vsplitter.addWidget(self.green_log_box)

        sizes = _config.get("layout", "split_sizes", fallback="500,400")
        sizes = [int(s) for s in sizes.split(",")]
        self.vsplitter.setSizes(sizes)        

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
        
        self.trip_label = QLabel("")
        self.status_bar.addPermanentWidget(self.trip_label)
        self.update_trip_label()
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
        # logger.info("Close and save screen sizes")
        _config["layout"]["split_sizes_vertical"] = ",".join(str(s) for s in self.vsplitter.sizes())
        _config["layout"]["split_sizes_horizontal"] = ",".join(str(s) for s in self.hsplitter.sizes())        
        persist_config()
        super().closeEvent(event)
    
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

    def update_trip_cell(self, day_name, step_key, symbol, tooltip):
        from gui.trip_overview import SYNC_COL, STATE_COL_START, cell_is_inert
        for row in range(self.trip_table.rowCount()):
            if self.trip_table.item(row, 0).text() == day_name:
                if step_key == "Sync\nSidecars":
                    col_index = SYNC_COL
                else:
                    col_index = self.state_labels.index(step_key) + STATE_COL_START
                item = QTableWidgetItem(symbol)
                item.setToolTip(tooltip)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if cell_is_inert(symbol, tooltip):
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.trip_table.setItem(row, col_index, item)
                break

    def get_progress_summary_item(self, sm, ini_path):
        item = QTableWidgetItem()
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        if not ini_path.exists():
            item.setText("🔘")
            item.setToolTip("No steps processed yet.")
            # item.setBackground(QtGui.QBrush(QtGui.QColor("whitesmoke")))
        elif sm.Taskstate.get("WriteCommit", [False])[0]:
            if sm.Taskstate.get("WriteBlog", [False])[0]:
                item.setText("✅")
                item.setToolTip("All steps successfully run.")
                # item.setBackground(QtGui.QBrush(QtGui.QColor("lightgreen")))
            else:
                item.setText("🟢")
                item.setToolTip("Website frozen, consider to add blog entry.")
                # item.setBackground(QtGui.QBrush(QtGui.QColor("lightblue")))
        else:
            item.setText("⚙️")
            item.setToolTip("Work in progress for this day.")
            # item.setBackground(QtGui.QBrush(QtGui.QColor("lightyellow")))

        return item
    
    def refresh_trip_row(self, sm, day_name):
        from gui.trip_overview import (STATE_COL_START, STATE_COLUMNS,
                                       state_cell_symbol, _verified_crc_set,
                                       _format_translations_item,
                                       _format_total_files_item)

        trip_root = Path(_config.get("trip", "tripbiotadatabase"))
        review_root = trip_root / "metadata" / "review"
        review_day = review_root / day_name

        row = next((r for r in range(self.trip_table.rowCount())
                    if self.trip_table.item(r, 0).text() == day_name), None)
        jpgs = int(self.trip_table.item(row, 2).text()) if row is not None else 0

        # Pictures cell (col 1): its species/GPS health badges come from the
        # day's freshly written data_files.csv / data_pictures.csv, so recompute
        # it here too — otherwise a Read & Summarize flips the checkmarks green
        # but leaves the badges stale until the next full repopulate.
        if row is not None:
            image_root = Path(_config.get("project", "picturedirectory")).parent
            self.trip_table.setItem(
                row, 1, _format_total_files_item(image_root / day_name, review_root))

        # Note: Sync Sidecars column is intentionally NOT refreshed here
        # (it is not tracked in the statemachine). state_cell_symbol applies the
        # same rules as populate_trip_overview, including the combined
        # "Read & Summarize" column that tracks both ReadDirExif + WritePictures.
        for col in STATE_COLUMNS:
            symbol, tooltip = state_cell_symbol(sm, col, jpgs)
            self.update_trip_cell(day_name, col["label"], symbol, tooltip)

        # Update progress column
        ini_path = review_day / "statemachine.ini"
        summary_item = self.get_progress_summary_item(sm, ini_path)
        for row in range(self.trip_table.rowCount()):
            if self.trip_table.item(row, 0).text() == day_name:
                self.trip_table.setItem(row, STATE_COL_START + len(self.state_labels), summary_item)

        # Update Translations column too — Write Summary rewrites
        # data_pictures.csv, so the unverified-text count may have changed.
        verified_crcs = _verified_crc_set(trip_root)
        translations_col = STATE_COL_START + len(self.state_labels) + 1
        for row in range(self.trip_table.rowCount()):
            if self.trip_table.item(row, 0).text() == day_name:
                self.trip_table.setItem(row, translations_col,
                                        _format_translations_item(review_day, verified_crcs))
                break
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

    def handle_trip_table_double_click(self, row, column):
        from gui.trip_overview import SYNC_COL, STATE_COL_START, STATE_COLUMNS
        # Translations column (last): open the per-day validation panel.
        translations_col = STATE_COL_START + len(self.state_labels) + 1
        if column == translations_col:
            day_item = self.trip_table.item(row, 0)
            if day_item:
                self.open_translation_validator(day_item.text())
            return
        # Ignore the remaining trailing non-action column (Progress) and the
        # Day column / headers.
        progress_col = STATE_COL_START + len(self.state_labels)
        if column >= progress_col or column < 1:
            return

        day_item = self.trip_table.item(row, 0)
        action_item = self.trip_table.horizontalHeaderItem(column)

        if not day_item or not action_item:
            self.logger.warning("Invalid cell selection.")
            return

        day_str = day_item.text()
        action_label = action_item.text().replace("\n", " ")  # e.g., "Write Blog"

        # Update PictureDirectory
        project_root = Path(_config.get("project", "picturedirectory")).parent
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

        # Batch action
        else:
            trip_menu = self.menu_spec.get("Trip", {})
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
        project_root = Path(_config.get("project", "picturedirectory")).parent
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
            ("BuildWebsite",  "Build Website",  "Build updates for later publication ...", "✅ Website updates prepared."),
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
        module_config.read("svconfig.ini")

        trip_menu = self.menu_spec.get("Trip", {})

        def _find_spec(menu, want):
            # Descend into submenus (e.g. Advanced ▸ Freeze Data / Write Website Commit).
            for k, v in menu.items():
                if isinstance(v, dict):
                    hit = _find_spec(v, want)
                    if hit:
                        return hit
                elif k == want and v is not None:
                    return v
            return None

        steps_to_run = []
        for sname, label, bmsg, amsg in completed:
            spec = _find_spec(trip_menu, label)
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

        project_root = Path(_config.get("project", "picturedirectory")).parent
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

            trip_menu = self.menu_spec.get("Trip", {})
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

        project_root = Path(_config.get("project", "picturedirectory")).parent
        trip_root = Path(_config.get("trip", "tripbiotadatabase"))

        trip_menu = self.menu_spec.get("Trip", {})
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

    def handle_trip_table_context_menu(self, pos):
        """Right-click on the Trip Overview: offer to run the user's selected pipeline cells."""
        from PyQt6.QtWidgets import QMenu
        from gui.trip_overview import STATE_COL_START, STATE_COLUMNS
        from gui.batch import ColumnBatchRunner
        from utils.batch_config import generate_svconfig_ini

        state_col_end = STATE_COL_START + len(self.state_labels) - 1

        selected = [
            idx for idx in self.trip_table.selectedIndexes()
            if STATE_COL_START <= idx.column() <= state_col_end
        ]
        if not selected:
            return

        project_root = Path(_config.get("project", "picturedirectory")).parent
        trip_root = Path(_config.get("trip", "tripbiotadatabase"))
        trip_menu = self.menu_spec.get("Trip", {})

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

        # Parent the menu to the main window so it doesn't inherit the yellow
        # background from yellow_tabs (which made hovered text unreadable).
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: white; color: black; }")
        if not day_steps:
            disabled = menu.addAction("No eligible steps in selection")
            disabled.setEnabled(False)
            menu.exec(self.trip_table.viewport().mapToGlobal(pos))
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
        chosen = menu.exec(self.trip_table.viewport().mapToGlobal(pos))
        if chosen != run_action:
            return

        generate_svconfig_ini()
        runner = ColumnBatchRunner(self, self, "Selected steps", day_steps, lambda: None)
        runner.start()

    def rerun_read_summarize_day(self, day_name, reason=""):
        """Confirm, then re-run Read & Summarize for ONE day so an edit reaches
        the reports. Used after a species-name change written to XMP from the
        image preview (Total Files / JPG columns): the XMP on disk changed, so
        the day must be re-read and re-summarized. Phase 3 keeps it cheap — if
        the resulting data_pictures.csv is unchanged, downstream stays valid."""
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

        prompt = (f"{reason}\n\n" if reason else "")
        prompt += (f"Re-run Read & Summarize for {day_name} so the change "
                   f"appears in the blog and website?")
        if QMessageBox.question(
                self, "Re-run Read & Summarize", prompt,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return

        project_root = Path(_config.get("project", "picturedirectory")).parent
        _config.set("project", "picturedirectory", str(project_root / day_name))
        generate_svconfig_ini()
        module_config = configparser.ConfigParser()
        module_config.read("svconfig.ini")
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
                                    day_name, on_complete)
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
        project_root = Path(_config.get("project", "picturedirectory")).parent

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

        spec_obj = self.menu_spec.get("Trip", {}).get("Write Summary")
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
        for step, label in (("WriteBlog", "Write Blog"),
                            ("BuildWebsite", "Build Website")):
            st = sm.Taskstate.get(step)
            if st and st[0] and st[1] == "Successfully run.":
                st[0] = False
                st[1] = note
                invalidated.append(label)
        if invalidated:
            sm.writeState()
            self.refresh_trip_row(sm, day_name)
        return invalidated

    def open_rating_editor(self):
        from gui.rating_editor import RatingEditorDialog
        trip_root = _config.get("trip", "tripbiotadatabase", fallback="")
        if not trip_root:
            QMessageBox.warning(self, "Trip Species Viewer", "No trip is configured.")
            return
        day_str = None
        if self.trip_table:
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
        self._rating_editor_dlg = RatingEditorDialog(self, trip_root, day_str)
        self._rating_editor_dlg.show()

    def open_master_species_viewer(self):
        """Standalone viewer over the master collection, one species at a time.
        Needs no active trip — the species list + pictures come from the master
        DB. Editing follows the machine's master read-only policy."""
        from gui.master_species_viewer import MasterSpeciesViewerDialog
        master_root = _config.get("master", "masterbiotadatabase", fallback="")
        if not master_root:
            QMessageBox.warning(self, "Master Species Viewer",
                                "No master database is configured.")
            return
        # Strong ref on the main window — see open_rating_editor for the why.
        self._master_species_viewer_dlg = MasterSpeciesViewerDialog(self)
        self._master_species_viewer_dlg.show()

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

        found = 0
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
        if found and self.logger:
            self.logger.info(
                f"{label}: {found} picture(s) carry a coordinate written to their "
                f"sidecar that the day CSVs predate.")
        return found

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
        day_dirs = list(day_dirs) if one_day else self._review_day_dirs(root)
        if not day_dirs:
            QMessageBox.warning(
                self, "Album Map",
                "No days with picture data were found — extract the days first.")
            return
        geotag = self._master_is_editable()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
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

    def open_virttrip_rating_editor(self):
        """Read-only Rating Editor over the active virtual trip (Album). Its
        pictures are the master's shared files, so editing is off for now —
        this is a viewer (cards, ratings, best-picks, species filter)."""
        from gui.rating_editor import RatingEditorDialog
        virttrip_root = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        if not virttrip_root:
            QMessageBox.warning(self, "Trip Species Viewer",
                                "No album (Album) is active.")
            return
        # Strong ref on the main window — see open_rating_editor for the why.
        self._virttrip_rating_editor_dlg = RatingEditorDialog(
            self, virttrip_root, day_str=None, read_only=True, scope_label="Album")
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
