"""File ▸ Settings… — every settings page in one window, one tab per subject.

ONE job: put the existing settings dialogs side by side. Each page IS the old
dialog, unchanged, shown inside its tab: its own Save / Cancel / Close still do
what they did, and when one of them finishes the page, the window closes — the
same as closing the separate dialog used to. Seven menu items that looked alike
(Global Settings … Interfaces) made it easy to open the wrong one (2026-10-21).

A page is built the first time its tab is opened, so the window opens fast and
a page that reads a lot (Database) only does so when asked.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QTabWidget, QVBoxLayout, QWidget

from gui.wait_picture import picture_column


def _pages():
    """(tab title, dialog class) — or (tab title, [sub-pages]) for a group."""
    from settings.globalsettings import GlobalSettingsDialog
    from settings.lookfeel import LookFeelDialog
    from settings.dbsettings import DatabaseSettingsDialog
    from settings.rawsettings import RawProcessingDialog
    from settings.locationsettings import LocationSettingsDialog
    from settings.photolablocation import PhotoLabLocationDialog
    from settings.photolabdenoise import PhotoLabDenoiseDialog
    from settings.sidecarsettings import SidecarSettingsDialog
    return [
        ("Global", GlobalSettingsDialog),
        ("Look && Feel", LookFeelDialog),
        ("Database", DatabaseSettingsDialog),
        ("Decode quality", RawProcessingDialog),
        ("Location", LocationSettingsDialog),
        ("Lightroom && PhotoLab", [
            ("PhotoLab location", PhotoLabLocationDialog),
            ("PhotoLab denoise", PhotoLabDenoiseDialog),
            ("Sidecar behaviour", SidecarSettingsDialog),
        ]),
    ]


class SettingsWindow(QDialog):
    def __init__(self, parent, tab: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._parent = parent
        self._built = {}                       # placeholder QWidget -> page
        column = picture_column(self, "albatross")
        self.tabs = QTabWidget()
        column.addWidget(self.tabs, 1)
        for title, what in _pages():
            if isinstance(what, list):
                sub = QTabWidget()
                for st, cls in what:
                    sub.addTab(self._placeholder(cls), st)
                sub.currentChanged.connect(lambda _i, s=sub: self._build(s.currentWidget()))
                self.tabs.addTab(sub, title)
            else:
                self.tabs.addTab(self._placeholder(what), title)
        self.tabs.currentChanged.connect(lambda _i: self._build_current())
        if tab:
            names = [self.tabs.tabText(i).replace("&&", "&") for i in range(self.tabs.count())]
            if tab in names:
                self.tabs.setCurrentIndex(names.index(tab))
        self._build_current()
        self.resize(1180, 680)

    def _placeholder(self, cls) -> QWidget:
        w = QWidget()
        w._page_class = cls
        QVBoxLayout(w).setContentsMargins(0, 0, 0, 0)
        return w

    def _build_current(self):
        w = self.tabs.currentWidget()
        if isinstance(w, QTabWidget):
            w = w.currentWidget()
        self._build(w)

    def _build(self, holder):
        """Make the page inside `holder` the first time it is shown."""
        if holder is None or holder in self._built:
            return
        cls = getattr(holder, "_page_class", None)
        if cls is None:
            return
        page = cls(self._parent)
        # A dialog shown as part of this window, not as a window of its own.
        page.setWindowFlags(Qt.WindowType.Widget)
        page.finished.connect(lambda _r: self.close())
        holder.layout().addWidget(page)
        page.show()
        self._built[holder] = page
