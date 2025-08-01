import sys
import configparser

from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter
from PyQt6.QtWidgets import QPlainTextEdit, QMenuBar, QMenu, QLabel, QSizePolicy, QLineEdit, QTabWidget
from PyQt6.QtGui import QAction, QIcon, QKeySequence
from PyQt6.QtGui import QPixmap, QFont
from PyQt6.QtCore import Qt, QTimer

from qt.gui.layout_colorwidget import Color

from utils.config import config, CONFIG_FILE
from dialogs.setting import AboutDialog
from settings.globalsettings import GlobalSettingsDialog
from settings.reposettings import RepoSettingsDialog
from gui.menu import create_menubar
from widgets.log_panel import LogPanel
from gui.menuspec import build_menu_spec

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
        self.setWindowTitle("Species Viewer 0.1")
        self.resize(1200, 900)
        self.logger = None

        root_field = QLineEdit()  # Shared reference across tabs
        root_field.setText(config["trip"]["tripbiotadatabase"])        
        # layout

        layout2 = QHBoxLayout()
        layout2.addWidget(Color("red"))
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
        # self.yellow_builder = YellowPanelBuilder(root_field, self.config)

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