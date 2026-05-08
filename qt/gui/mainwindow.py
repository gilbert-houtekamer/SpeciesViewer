from pathlib import Path
import os

from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QStatusBar
from PyQt6.QtWidgets import QPlainTextEdit, QLabel, QSizePolicy, QLineEdit, QTabWidget, QTableWidgetItem, QMessageBox
from PyQt6.QtCore import Qt

from utils.config import _config, persist_config
from gui.menu import create_menubar
from gui.menuspec import build_menu_spec
from gui.trip_overview import populate_trip_overview
from gui.virttrip_overview import populate_virttrip_overview, TAB_TITLE as VIRTTRIP_TAB_TITLE
from sharedlib.translatelib import CachedTranslate
from widgets.TripInfoPanel import TripInfoPanel
from gui.trip_overview import populate_trip_overview
from sharedlib.statemachine import StateMachine
from gui.image_viewer import ImagePreviewDialog
from utils.gui_config import load_gui_cache  # or wherever your cache functions live


def Color(name):
    label = QLabel(name)
    label.setStyleSheet(f"background-color: {name};")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setMinimumHeight(100)
    # label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    return label



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
        red_layout.addWidget(TripInfoPanel(self, trip_project_map, populate_trip_overview))
        self.hsplitter.addWidget(red_panel)

        # Yellow panel
        self.yellow_tabs = QTabWidget()
        self.yellow_tabs.setStyleSheet("background-color: #fff8d2;")
        self.hsplitter.addWidget(self.yellow_tabs)

        # Purple panel
        purple_panel = Color("purple")  # Assuming this is a QWidget subclass
        self.hsplitter.addWidget(purple_panel)

        # Optional: set initial sizes
        self.hsplitter.setSizes([200, 600, 200])  # Adjust as needed

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
        from gui.trip_overview import SYNC_COL, STATE_COL_START
        for row in range(self.trip_table.rowCount()):
            if self.trip_table.item(row, 0).text() == day_name:
                if step_key == "Sync\nSidecars":
                    col_index = SYNC_COL
                else:
                    col_index = self.state_labels.index(step_key) + STATE_COL_START
                item = QTableWidgetItem(symbol)
                item.setToolTip(tooltip)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if symbol == "🚫":
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
        from gui.trip_overview import STATE_COL_START
        state_label_dict = {
            "Read\nDirectory" : "ReadDirExif",
            "Write\nSummary" : "WritePictures",
            "Write\nBlog" : "WriteBlog",
            "Build\nWebsite": "BuildWebsite",
            "Freeze" : "Freeze",
            "Write\nCommit" : "WriteCommit"
        }

        trip_root = Path(_config.get("trip", "tripbiotadatabase"))
        review_day = trip_root / "metadata" / "review" / day_name

        # Note: Sync Sidecars column is intentionally NOT refreshed here
        # (it is not tracked in the statemachine)
        for step_key in self.state_labels:
            step_name = state_label_dict[step_key]
            if step_name not in sm.Taskstate:
                symbol = "⚪"
                tooltip = "Unknown step."
            else:
                completed, message, _, _ = sm.Taskstate[step_name]
                if completed and message == "Successfully run.":
                    if sm.Taskstate.get("WriteCommit", [False])[0]:
                        symbol = "⬜"
                        tooltip = "Step completed. Workflow is frozen after WriteCommit."
                    else:
                        symbol = "✅"
                        tooltip = message
                elif not completed and "failed" in message.lower():
                    symbol = "❌"
                    tooltip = message
                elif step_name == "WriteBlog":
                    row = next((r for r in range(self.trip_table.rowCount()) if self.trip_table.item(r, 0).text() == day_name), None)
                    jpgs = int(self.trip_table.item(row, 2).text()) if row is not None else 0
                    if jpgs == 0:
                        symbol = "🚫"
                        tooltip = "No JPGs available — blog step suppressed."
                    elif not sm.stateOKtorun(step_name):
                        symbol = "⚪"
                        tooltip = sm.explainAbort(step_name, raiseSV=False)
                    else:
                        symbol = "🔘"
                        tooltip = "Click on the sphere to run this step."
                elif sm.stateOKtorun(step_name):
                    symbol = "🔘"
                    tooltip = "Click on the sphere to run this step."
                else:
                    symbol = "⚪"
                    tooltip = sm.explainAbort(step_name, raiseSV=False)

            self.update_trip_cell(day_name, step_key, symbol, tooltip)

        # Update progress column
        ini_path = review_day / "statemachine.ini"
        summary_item = self.get_progress_summary_item(sm, ini_path)
        for row in range(self.trip_table.rowCount()):
            if self.trip_table.item(row, 0).text() == day_name:
                self.trip_table.setItem(row, STATE_COL_START + len(self.state_labels), summary_item)
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
        from gui.trip_overview import SYNC_COL, STATE_COL_START
        # Ignore clicks on the "Progress" column, and headers
        if column == self.trip_table.columnCount() - 1 or column < 1:
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
            step_name = ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite", "Freeze", "WriteCommit"][step_index]
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
            spec_obj = trip_menu.get(action_label)

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
            ("Freeze",        "Freeze",         "Freeze all collected data ...",          "✅ Data frozen."),
            ("WriteCommit",   "Write Commit",   "Update the trip website ...",            "✅ Please review updated Website."),
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
        steps_to_run = []
        for sname, label, bmsg, amsg in completed:
            spec = trip_menu.get(label)
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
        from gui.trip_overview import SYNC_COL, STATE_COL_START
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
        step_names = ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite", "Freeze", "WriteCommit"]
        step_name = step_names[step_index]

        action_label = self.trip_table.horizontalHeaderItem(column).text().replace("\n", " ")

        project_root = Path(_config.get("project", "picturedirectory")).parent
        trip_root = Path(_config.get("trip", "tripbiotadatabase"))

        pipeline_msgs = {
            "ReadDirExif":   ("Reading picture directory...",            "✅ Directory scan complete."),
            "WritePictures": ("Reading meta data for all pictures ...",  "✅ Writing Summary data."),
            "WriteBlog":     ("Create a blog entry ...",                 "✅ Blog successfully created."),
            "BuildWebsite":  ("Build updates for later publication ...", "✅ Website updates prepared."),
            "Freeze":        ("Freeze all collected data ...",           "✅ Data frozen."),
            "WriteCommit":   ("Update the trip website ...",             "✅ Please review updated Website."),
        }
        before_msg, after_msg = pipeline_msgs[step_name]

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
            spec_obj = trip_menu.get(action_label)
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
                "statusname":   step_name,
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
        from gui.trip_overview import STATE_COL_START
        from gui.batch import ColumnBatchRunner
        from utils.batch_config import generate_svconfig_ini

        state_col_end = STATE_COL_START + len(self.state_labels) - 1
        step_names = ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite", "Freeze", "WriteCommit"]
        pipeline_msgs = {
            "ReadDirExif":   ("Reading picture directory...",            "✅ Directory scan complete."),
            "WritePictures": ("Reading meta data for all pictures ...",  "✅ Writing Summary data."),
            "WriteBlog":     ("Create a blog entry ...",                 "✅ Blog successfully created."),
            "BuildWebsite":  ("Build updates for later publication ...", "✅ Website updates prepared."),
            "Freeze":        ("Freeze all collected data ...",           "✅ Data frozen."),
            "WriteCommit":   ("Update the trip website ...",             "✅ Please review updated Website."),
        }

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
                "step_name":  step_names[col - STATE_COL_START],
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

                action_label = self.trip_table.horizontalHeaderItem(col).text().replace("\n", " ")
                spec_obj = trip_menu.get(action_label)
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
                before_msg, after_msg = pipeline_msgs[step_name]
                day_steps.append({
                    "day_str":      day_str,
                    "project_root": project_root,
                    "trip_root":    trip_root,
                    "module_called": bp["module_called"],
                    "statusname":   step_name,
                    "before_msg":   before_msg,
                    "after_msg":    after_msg,
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
