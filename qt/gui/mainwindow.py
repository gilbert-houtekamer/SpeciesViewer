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

        layout2 = QHBoxLayout()

        red_panel = QWidget()
        red_panel.setStyleSheet("background-color: #f9f9f9;")
        red_layout = QVBoxLayout(red_panel)
        red_layout.setContentsMargins(0, 0, 0, 0)
        red_layout.addWidget(TripInfoPanel(config))
        layout2.addWidget(red_panel)

        # layout2.addWidget(TripInfoPanel(config))
        # layout2.addWidget(Color("yellow"))
        # self.yellow_tabs = YellowPanel(root_field, config)
        # layout2.addWidget(self.yellow_tabs)        
        self.yellow_tabs = QTabWidget()
        self.yellow_tabs.setStyleSheet("background-color: #fff8d2;")
        layout2.addWidget(self.yellow_tabs)
        layout2.addWidget(Color("purple"))
        layout2.setStretch(0, 1)  # red
        layout2.setStretch(1, 3)  # yellow
        layout2.setStretch(2, 1)  # purple    
        color_row = QWidget()
        color_row.setLayout(layout2)
        color_row.setMinimumHeight(100)    

        self.green_log_box = QPlainTextEdit()
        self.green_log_box.setReadOnly(True)
        self.green_log_box.setStyleSheet("background-color: #d0f0c0;")
        self.green_log_box.setMinimumHeight(100)
        self.green_log_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(color_row)
        self.splitter.addWidget(self.green_log_box)

        sizes = config.get("layout", "split_sizes", fallback="500,400")
        sizes = [int(s) for s in sizes.split(",")]
        self.splitter.setSizes(sizes)        

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(self.splitter)
        widget.setLayout(layout)
        self.setCentralWidget(widget)

        menu_spec = build_menu_spec(self)
        menu_bar = create_menubar(self, menu_spec)
        self.setMenuBar(menu_bar)
        # translation just in case we need it
        self.translator = CachedTranslate(config["master"]["masterbiotadatabase"])
        # self.yellow_builder = YellowPanelBuilder(root_field, self.config)
        populate_trip_overview(self)
        trip_label = QLabel("Trip: 2017 Oostzeedb")
        self.status_bar.addPermanentWidget(trip_label)
        self.status_bar.showMessage("Ready")

    def closeEvent(self, event):
        sizes = self.splitter.sizes()
        # logger.info("Close and save screen sizes")
        config["layout"] = {"split_sizes": ",".join(map(str, sizes))}
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