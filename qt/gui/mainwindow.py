import sys

from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter
from PyQt6.QtWidgets import QPlainTextEdit, QMenuBar, QMenu, QLabel, QSizePolicy
from PyQt6.QtGui import QAction, QIcon, QKeySequence
from PyQt6.QtGui import QPixmap, QFont
from PyQt6.QtCore import Qt, QTimer

from qt.gui.layout_colorwidget import Color

from utils.config import config
from dialogs.setting import AboutDialog
from settings.globalsettings import GlobalSettingsDialog
from settings.reposettings import RepoSettingsDialog
from gui.menu import AppMenuBar
from widgets.log_panel import LogPanel

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
        # layout

        layout2 = QHBoxLayout()
        layout2.addWidget(Color("red"))
        layout2.addWidget(Color("yellow"))
        layout2.addWidget(Color("purple"))
        layout2.setStretch(0, 1)  # red
        layout2.setStretch(1, 3)  # yellow
        layout2.setStretch(2, 1)  # purple    
        color_row = QWidget()
        color_row.setLayout(layout2)
        color_row.setMinimumHeight(100)    

        # # in MainWindow.__init__()
        # layout1.addLayout(layout2)
        # layout1.addWidget(Color("green"))
        # Add layout2 to layout1
        # layout1 = QVBoxLayout()
        # layout1.addLayout(layout2)
        self.green_log_box = QPlainTextEdit()
        self.green_log_box.setReadOnly(True)
        self.green_log_box.setStyleSheet("background-color: #d0f0c0;")
        self.green_log_box.setMinimumHeight(100)
        self.green_log_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Splitter for vertical resizing
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(color_row)
        splitter.addWidget(self.green_log_box)
        splitter.setSizes([500, 400])  # Initial heights (adjust to taste)

        #layout1.addWidget(self.green_log_box)
        # widget = QWidget()
        # widget.setLayout(layout1)

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(splitter)
        widget.setLayout(layout)
        self.setCentralWidget(widget)


        callbacks = {
            "quit": self.close,
            "global_settings": self.open_global_settings,
            "repo_settings": self.open_repo_settings,
            "about": self.show_about
        }
        menu_bar = AppMenuBar(self, callbacks)
        self.setMenuBar(menu_bar)

    def show_about(self):
        dialog = AboutDialog(self)
        dialog.exec()

    def open_global_settings(self):
        dlg = GlobalSettingsDialog(self, self.config)
        dlg.exec()

    def open_repo_settings(self):
        dlg = RepoSettingsDialog(self, self.config)
        dlg.exec()

    def attach_log_panel(self):
        # from widgets.log_panel import LogPanel
        # self.log_panel = LogPanel()
        # self.layout1.addWidget(self.log_panel)
        pass       