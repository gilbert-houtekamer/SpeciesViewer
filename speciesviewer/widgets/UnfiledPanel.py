"""Left-panel list of unfiled days — pictures off a card that no trip or Day
claims yet. The Unfiled counterpart of DaysPanel, and deliberately the same
shape: a narrow column of dates you scan, while the yellow table beside it is
where you act.

Two things it does NOT have, both on purpose:

  * no active-day pointer. The inbox has no "active" item — the Days lane needs
    one because its menu acts on it, this one's actions all live on the table
    row you double-click. Clicking here only selects.
  * no remove. Nothing in the Unfiled lane deletes pictures; a day leaves by
    being filed under a trip, tracked as a Day, or moved elsewhere.
"""
import os

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu
from PyQt6.QtGui import QFont, QCursor, QColor
from PyQt6.QtCore import Qt

from engine import unfiled
from engine import day_note
from utils.gui_config import get_all_gui_usage, record_gui_usage
from utils.sort_modes import sort_items, DEFAULT_MODE
from utils.open_path import open_path
from gui import theme

_FOLDER_ROLE = Qt.ItemDataRole.UserRole + 1
_FONT = theme.FONT_MONO


def _same(a, b):
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


class UnfiledPanel(QWidget):
    def __init__(self, parent, sort_mode=DEFAULT_MODE):
        super().__init__()
        self.main_window = parent
        self._sort_mode = sort_mode
        self._selected = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 10)
        layout.setSpacing(6)
        self.setStyleSheet(f"{_FONT} background-color: {theme.PANEL_BG};")

        header = QLabel("Unfiled:")
        header.setStyleSheet(_FONT)
        layout.addWidget(header)

        self.list = QListWidget()
        self.list.setStyleSheet(_FONT)
        layout.addWidget(self.list)
        self.list.itemClicked.connect(self._on_click)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)

        self.rebuild()

    def rebuild(self, sort_mode=None):
        """Re-scan the inbox and repaint. sort_mode=None keeps the current mode."""
        if sort_mode is not None:
            self._sort_mode = sort_mode
        self.list.clear()
        rows = unfiled.scan_unfiled()
        usage = get_all_gui_usage()
        by_folder = {r["folder"]: r for r in rows}
        for folder in sort_items(list(by_folder.keys()), self._sort_mode, usage):
            r = by_folder[folder]
            item = QListWidgetItem(r["day"])
            item.setData(_FOLDER_ROLE, folder)
            note = day_note.read_line(folder)
            item.setToolTip(f"{folder}\n\n{note}" if note else folder)
            if self._selected and _same(self._selected, folder):
                item.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
                item.setForeground(QColor(theme.ACTIVE_FG))
            self.list.addItem(item)
        if self.list.count() == 0:
            placeholder = QListWidgetItem("(nothing unfiled)")
            placeholder.setForeground(Qt.GlobalColor.gray)
            self.list.addItem(placeholder)

    def select_folder(self, folder):
        """Mirror a selection made in the yellow table, without echoing back."""
        self._selected = str(folder or "")
        self.rebuild()

    def _on_click(self, item):
        folder = item.data(_FOLDER_ROLE)
        if not folder:
            return
        record_gui_usage(folder)
        self._selected = folder
        self.rebuild()
        mirror = getattr(self.main_window, "mirror_unfiled_selection", None)
        if callable(mirror):
            mirror(folder)

    def _context_menu(self, pos):
        item = self.list.itemAt(pos)
        if not item:
            return
        folder = item.data(_FOLDER_ROLE)
        if not folder:
            return
        menu = QMenu()
        open_action = menu.addAction("Open Folder")
        action = menu.exec(QCursor.pos())
        if action == open_action and os.path.isdir(folder):
            open_path(folder)
