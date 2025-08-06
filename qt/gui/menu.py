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
    REQUIRED_BATCH_KEYS = {"statusname", "module_called", "before_msg", "after_msg"}

    def __init__(
        self,
        callback,
        title=None,
        shortcut=None,
        enabled=None,
        icon=None,
        tooltip=None,
        batch_parms=None
    ):
        """
        Encapsulates metadata and behavior for a single menu action.

        Parameters:
        - callback: Function to execute when triggered.
        - title: Optional display label.
        - shortcut: Optional keyboard shortcut (e.g. "Ctrl+Q").
        - enabled: Optional function to determine if action is active.
        - icon: Optional icon path (QIcon or string).
        - tooltip: Optional hover message.
        - batch_parms: Optional dictionary:
            {
                'statusname': str,
                'module_called': callable,
                'before_msg': str,
                'after_msg': str
            }
        """
        self.callback = callback
        self.title = title
        self.shortcut = shortcut
        self.enabled = enabled or (lambda: True)
        self.icon = icon
        self.tooltip = tooltip
        self.batch_parms = batch_parms or {}

        if self.batch_parms:
            missing = self.REQUIRED_BATCH_KEYS - self.batch_parms.keys()
            if missing:
                raise ValueError(f"Missing batch_parms keys: {', '.join(missing)}")

    def is_enabled(self):
        return bool(self.enabled())

    def get_title(self, default):
        return self.title or default

    def get_icon(self):
        return self.icon

    def get_tooltip(self):
        return self.tooltip

    def get_batch_parms(self):
        return self.batch_parms
    
    def get_statusname(self):
        return self.batch_parms.get("statusname", "")

