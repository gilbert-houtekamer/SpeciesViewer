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

def _esc(text):
    """Qt reads '&' in menu text as a keyboard-mnemonic marker and hides it.
    The app uses no mnemonics, so double every '&' to show it literally
    (e.g. "Read & Summarize")."""
    return text.replace("&", "&&")


def _populate_menu(menu, actions, parent, separator_marker):
    """Fill a QMenu from an ordered {label: MenuSpec | dict | None} mapping.

    A dict value becomes a nested submenu (recursively), so the menu spec can
    express groupings like File ▸ Settings and File ▸ Admin. A label starting
    with the separator marker (value None) inserts a divider."""
    menu.setToolTipsVisible(True)   # Qt hides action tooltips in menus otherwise
    dynamic = []   # (action, spec) pairs to re-grey each time this menu opens
    for label, spec_obj in actions.items():
        if label.startswith(separator_marker):
            menu.addSeparator()
        elif isinstance(spec_obj, dict):
            submenu = QMenu(_esc(label), parent)
            _populate_menu(submenu, spec_obj, parent, separator_marker)
            menu.addMenu(submenu)
        elif spec_obj is None:
            continue                      # non-separator placeholder — skip
        else:
            action = QAction(_esc(spec_obj.get_title(label)), parent)
            action.triggered.connect(spec_obj.callback)
            action.setEnabled(spec_obj.is_enabled())
            tooltip = spec_obj.get_tooltip()
            if tooltip:
                action.setToolTip(tooltip)
            if spec_obj.shortcut:
                action.setShortcut(spec_obj.shortcut)
            menu.addAction(action)
            dynamic.append((action, spec_obj))
    # Re-evaluate each item's enabled() every time this menu opens, so a gate
    # that depends on the current selection (active trip / album / day, or
    # whether a master is configured) greys LIVE. Without this the menu is only
    # rebuilt after a pipeline step or a day change, so switching trip or album
    # would leave the grey state stale until the next rebuild.
    if dynamic:
        def _refresh(pairs=dynamic):
            for act, spec in pairs:
                try:
                    act.setEnabled(spec.is_enabled())
                except Exception:
                    pass
        menu.aboutToShow.connect(_refresh)


def create_menubar(parent, spec):
    menubar = QMenuBar(parent)
    separator_marker = MenuSpec.separator()

    for menu_name, actions in spec.items():
        menu = QMenu(_esc(menu_name), parent)
        _populate_menu(menu, actions, parent, separator_marker)
        menubar.addMenu(menu)

    return menubar

class MenuSpec:
    REQUIRED_BATCH_KEYS = {"statusname", "module_called", "before_msg", "after_msg"}

    def __init__(
        self,
        callback,
        title=None,
        showsubdirectory=True,
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
        self.showsubdirectory = showsubdirectory,
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
    
    def get_showsubdirectory(self):
        return self.showsubdirectory
          
    @staticmethod
    def separator():
        return "__SEPARATOR__"