from pathlib import Path
import os

from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QStatusBar
from PyQt6.QtWidgets import QPlainTextEdit, QLabel, QSizePolicy, QLineEdit, QTabWidget, QTableWidgetItem
from PyQt6.QtCore import Qt

from qt.gui.layout_colorwidget import Color
from utils.config import config, CONFIG_FILE
from gui.menu import create_menubar
from gui.menuspec import build_menu_spec
from gui.trip_overview import populate_trip_overview
from sharedlib.translatelib import CachedTranslate
from widgets.TripInfoPanel import TripInfoPanel
from gui.trip_overview import populate_trip_overview
from sharedlib.statemachine import StateMachine
from gui.image_viewer import ImagePreviewDialog

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

        self.config = config
        self.trip_table = None
        self.state_keys = None       
        self.state_labels = None 
        self.setWindowTitle("Species Viewer 0.1")
        # Create and attach status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)        
        self.resize(1200, 900)
        self.logger = None

        root_field = QLineEdit()  # Shared reference across tabs
        root_field.setText(config["trip"]["tripbiotadatabase"])        
        # layout

        # Create the splitter
        self.hsplitter = QSplitter(Qt.Orientation.Horizontal)

        # Red panel
        red_panel = QWidget()
        red_panel.setStyleSheet("background-color: #f9f9f9;")
        red_layout = QVBoxLayout(red_panel)
        red_layout.setContentsMargins(0, 0, 0, 0)
        red_layout.addWidget(TripInfoPanel(config))
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

        sizes = config.get("layout", "split_sizes", fallback="500,400")
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
        self.translator = CachedTranslate(config["master"]["masterbiotadatabase"])
        # self.yellow_builder = YellowPanelBuilder(root_field, self.config)
        populate_trip_overview(self)
        # Horizontal splitter
        if config.has_option("layout", "split_sizes_horizontal"):
            h_sizes = config.get("layout", "split_sizes_horizontal")
            try:
                self.hsplitter.setSizes([int(s) for s in h_sizes.split(",")])
            except ValueError:
                pass

        # Vertical splitter
        if config.has_option("layout", "split_sizes_vertical"):
            v_sizes = config.get("layout", "split_sizes_vertical")
            try:
                self.vsplitter.setSizes([int(s) for s in v_sizes.split(",")])
            except ValueError:
                pass

        
        trip_label = QLabel("Trip: 2017 Oostzeedb")
        self.status_bar.addPermanentWidget(trip_label)
        self.status_bar.showMessage("Ready")

    def closeEvent(self, event):
        # logger.info("Close and save screen sizes")
        config["layout"]["split_sizes_vertical"] = ",".join(str(s) for s in self.vsplitter.sizes())
        config["layout"]["split_sizes_horizontal"] = ",".join(str(s) for s in self.hsplitter.sizes())        
        with open(CONFIG_FILE, "w") as f:
            config.write(f)
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
        for row in range(self.trip_table.rowCount()):
            if self.trip_table.item(row, 0).text() == day_name:
                col_index = self.state_labels.index(step_key) + 3
                item = QTableWidgetItem(symbol)
                item.setToolTip(tooltip)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
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


    def handle_trip_table_double_click(self, row, column):
        # Ignore clicks on the "Progress" column, and headers
        if column == self.trip_table.columnCount() - 1 or column < 1:
            return

        day_item = self.trip_table.item(row, 0)
        action_item = self.trip_table.horizontalHeaderItem( column)

        if not day_item or not action_item:
            self.logger.warning("Invalid cell selection.")
            return

        day_str = day_item.text()
        action_label = action_item.text().replace("\n", " ")  # e.g., "Write Blog"

        # Update PictureDirectory
        project_root = Path(self.config.get("project", "picturedirectory")).parent
        new_dir = project_root / day_str
        self.config.set("project", "picturedirectory", str(new_dir))
        self.logger.info(f"Updated PictureDirectory to: {new_dir}")
        # trip if available
        trip_root = Path(self.config.get("trip", "tripbiotadatabase"))
        trip_dir = os.path.join(str(trip_root), 'metadata' , 'review' , day_str)
        if not os.path.exists(trip_dir): trip_root = ""

        if column == 1:  # RAW column
            self.image_viewer_dialog = ImagePreviewDialog(self, image_dir=str(new_dir), trip_root_dir=str(trip_root),  mode="raw")
            self.image_viewer_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
            self.image_viewer_dialog.show()
            # if not hasattr(self, "dlgImagePreviewDialog") or self.dlgImagePreviewDialog is None:
            #     self.dlgImagePreviewDialog = ImagePreviewDialog(self, image_dir=str(new_dir), trip_root_dir=str(trip_root),  mode="raw")
            # self.dlgImagePreviewDialog.show()
            # self.dlgImagePreviewDialog = ImagePreviewDialog(self, image_dir=str(new_dir), trip_root_dir=str(trip_root),  mode="raw")
            # self.dlgImagePreviewDialog.show()
        elif column == 2:  # JPG column
            self.dlgImagePreviewDialog = ImagePreviewDialog(self, image_dir=str(new_dir), trip_root_dir=str(trip_root),  mode="jpg")
            self.dlgImagePreviewDialog.show()
        else:
            # Look up the MenuSpec in the "Trip" section
            trip_menu = self.menu_spec.get("Trip", {})
            spec_obj = trip_menu.get(action_label)

            if spec_obj and spec_obj.callback:
                # self.logger.info(f"Invoking menu action: {action_label}")
                spec_obj.callback()
            else:
                self.logger.warning(f"No menu action found for: {action_label}")

            if column == (3 + len(self.state_labels) - 1):
                directory = self.trip_table.item(row,0).text()
                trip_root = Path(self.config.get("trip", "tripbiotadatabase"))
                new_dir = trip_root / directory
                sm = StateMachine(str(trip_root), str(new_dir), readState=True)
                ini_path = Path(directory) / "statemachine.ini"
                summary_item = self.get_progress_summary_item(sm, ini_path)

                self.trip_table.setItem(row, 3 + len(self.state_labels), summary_item)
