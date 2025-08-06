from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenuBar, QMenu

class AppMenuBar(QMenuBar):
    def __init__(self, parent, callbacks, menu_spec):
        super().__init__(parent)

        for menu_name, actions in menu_spec.items():
            menu = self.addMenu(menu_name)
            for action_name, callback_key in actions.items():
                callback = callbacks.get(callback_key)
                if callback:
                    act = QAction(action_name, self)
                    act.triggered.connect(callback)
                    menu.addAction(act)

def create_menubar(parent, spec):
    menubar = QMenuBar(parent)

    for menu_name, actions in spec.items():
        menu = QMenu(menu_name, parent)
        for label, spec_obj in actions.items():
            if not spec_obj.is_enabled():
                continue

            action = QAction(spec_obj.get_title(label), parent)
            action.triggered.connect(spec_obj.callback)

            if spec_obj.shortcut:
                action.setShortcut(spec_obj.shortcut)

            menu.addAction(action)

        menubar.addMenu(menu)

    return menubar

class MenuSpec:
    def __init__(
        self,
        callback,
        title=None,
        shortcut=None,
        enabled=None,
        icon=None,
        tooltip=None,
        statusname=""
    ):
        """
        Encapsulates metadata and behavior for a single menu action.

        Parameters:
        - callback: Function to call when triggered.
        - title: Optional display label override.
        - shortcut: Optional keyboard shortcut (e.g. "Ctrl+Q").
        - enabled: Optional function to determine if action is enabled.
        - icon: Optional icon path (QIcon accepted or string).
        - tooltip: Optional hover text for the action.
        - statusname: Optional name for statemachine
        """
        self.callback = callback
        self.title = title
        self.shortcut = shortcut
        self.enabled = enabled or (lambda: True)
        self.icon = icon
        self.tooltip = tooltip
        self.statusname = statusname

    def is_enabled(self):
        return bool(self.enabled())

    def get_title(self, default):
        return self.title or default

    def get_icon(self):
        return self.icon

    def get_tooltip(self):
        return self.tooltip
    
    def get_statusname(self):
        return self.statusname

