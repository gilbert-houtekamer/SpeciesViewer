"""Left-panel list of "Days" (ad-hoc single-date folders sent to the Master),
shown under the Trips list. Clicking a day makes it the active day that the
Days menu actions operate on. See working_days_plan.md.
"""
import os

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu
from PyQt6.QtGui import QFont, QCursor
from PyQt6.QtCore import Qt

from utils import days_registry
from utils.open_path import open_path

_FOLDER_ROLE = Qt.ItemDataRole.UserRole + 1
_FONT = "font-size: 10pt; font-family: Consolas, monospace;"


def _same(a, b):
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


class DaysPanel(QWidget):
    def __init__(self, parent):
        super().__init__()
        self.main_window = parent
        if not hasattr(parent, "active_day_folder"):
            parent.active_day_folder = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 10)
        layout.setSpacing(6)
        self.setStyleSheet(f"{_FONT} background-color: #f9f9f9;")

        header = QLabel("Days:")
        header.setStyleSheet(_FONT)
        layout.addWidget(header)

        self.list = QListWidget()
        self.list.setStyleSheet(_FONT)
        layout.addWidget(self.list)
        self.list.itemClicked.connect(self._on_click)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)

        self.rebuild()

    def rebuild(self):
        self.list.clear()
        active = getattr(self.main_window, "active_day_folder", "")
        for d in days_registry.load_days():
            folder = d["folder"]
            date = d.get("date", "")
            done = d.get("status") == days_registry.STATUS_DONE
            label = date + ("   ✓ done" if done else "")
            if active and _same(active, folder):
                label = "▶ " + label
            item = QListWidgetItem(label)
            item.setData(_FOLDER_ROLE, folder)
            item.setToolTip(folder)
            if not os.path.isdir(folder):
                item.setForeground(Qt.GlobalColor.gray)
                item.setToolTip(folder + "  (folder not found)")
            elif active and _same(active, folder):
                item.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
                item.setForeground(Qt.GlobalColor.darkMagenta)
            elif done:
                item.setForeground(Qt.GlobalColor.darkGray)
            self.list.addItem(item)
        if self.list.count() == 0:
            placeholder = QListWidgetItem("(no days yet)")
            placeholder.setForeground(Qt.GlobalColor.gray)
            self.list.addItem(placeholder)

    def _on_click(self, item):
        folder = item.data(_FOLDER_ROLE)
        if not folder:
            return
        self.main_window.active_day_folder = folder
        self.rebuild()
        try:
            self.main_window.status_bar.showMessage(f"Active day: {os.path.basename(folder)}")
        except Exception:
            pass

    def _context_menu(self, pos):
        item = self.list.itemAt(pos)
        if not item:
            return
        folder = item.data(_FOLDER_ROLE)
        if not folder:
            return
        menu = QMenu()
        open_action = menu.addAction("Open folder")
        remove_action = menu.addAction("Remove from list")
        action = menu.exec(QCursor.pos())
        if action == open_action:
            if os.path.isdir(folder):
                open_path(folder)
        elif action == remove_action:
            days_registry.remove_day(folder)
            if _same(getattr(self.main_window, "active_day_folder", ""), folder):
                self.main_window.active_day_folder = ""
            self.rebuild()
