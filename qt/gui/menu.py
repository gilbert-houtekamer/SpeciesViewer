from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QIcon, QKeySequence
from PyQt6.QtWidgets import QMenuBar, QMenu

class AppMenuBar(QMenuBar):
    def __init__(self, parent, callbacks):
        super().__init__(parent)

        self.callbacks = callbacks  # Dictionary of handlers

        self._build_file_menu()
        self._build_trip_menu()
        self._build_settings_menu()
        self._build_help_menu()

    def _build_file_menu(self):
        menu = QMenu("File", self)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.callbacks.get("quit"))
        menu.addAction(quit_action)
        self.addMenu(menu)

    def _build_trip_menu(self):
        menu = QMenu("Trip", self)
        trip_read_dir_action = QAction("Read Directory", self)
        trip_read_dir_action.triggered.connect(self.callbacks.get("trip_read_dir"))
        menu.addAction(trip_read_dir_action)                                               
        self.addMenu(menu)

    def _build_settings_menu(self):
        menu = QMenu("Settings", self)
        global_action = QAction("Global Settings", self)
        repo_action = QAction("Repository Settings", self)

        global_action.triggered.connect(self.callbacks.get("global_settings"))
        repo_action.triggered.connect(self.callbacks.get("repo_settings"))

        menu.addAction(global_action)
        menu.addAction(repo_action)
        self.addMenu(menu)

    def _build_help_menu(self):
        menu = QMenu("Help", self)
        about_action = QAction("About", self)
        about_action.triggered.connect(self.callbacks.get("about"))
        menu.addAction(about_action)
        self.addMenu(menu)


def create_menubar(parent, about_callback):
    menubar = QMenuBar(parent)

    # File Menu
    file_menu = QMenu("File", parent)
    quit_action = QAction("Quit", parent)
    quit_action.triggered.connect(parent.close)
    file_menu.addAction(quit_action)

    # Trip Menu (placeholder)
    trip_menu = QMenu("Trip", parent)
    trip_read_dir = QAction("Read Directory", parent)
    trip_menu.addAction(trip_read_dir)

    # Settings Menu
    settings_menu = QMenu("Settings", parent)
    global_action = QAction("Global Settings", parent)
    repo_action = QAction("Repository Settings", parent)
    settings_menu.addAction(global_action)
    settings_menu.addAction(repo_action)

    # Help Menu
    help_menu = QMenu("Help", parent)
    about_action = QAction("About", parent)
    about_action.triggered.connect(about_callback)
    help_menu.addAction(about_action)

    # Add Menus
    menubar.addMenu(file_menu)
    menubar.addMenu(trip_menu)
    menubar.addMenu(settings_menu)
    menubar.addMenu(help_menu)

    return menubar
