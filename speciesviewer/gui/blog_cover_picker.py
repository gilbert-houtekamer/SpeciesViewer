"""
The picture that stands for one day's blog on the blog index (blog.ini
[blog_text] blogjpg). Empty = automatic: the highest-rated picture in the blog.

Only a picture that IS in the blog can be its cover — the index links to the
copy in the blog's own folder. Which pictures those are is known only after the
blog was built once: blog_admin.csv keeps them per day (BestJPGs), and the
small copies sit in <trip>/web/biotablog/<blog>/. So the picker reads those;
before the first build it says so instead of guessing.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QLabel, QListView, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from engine.handling import parseListFromCSV

THUMB = 140


def blog_pictures_for_day(trip_root, day_name: str, group: str = "biota"):
    """(blog name, [JPG file names]) of the day's blog as last built, or
    ("", []) when it was never built. Reads <trip_root>/admin/blog_admin.csv."""
    admin = Path(trip_root) / "admin" / "blog_admin.csv"
    try:
        with io.open(admin, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter=";"):
                if (row.get("Project") == day_name
                        and (row.get("SelectedGroup") or "biota") == group
                        and row.get("blog")):
                    return row["blog"], parseListFromCSV(row.get("BestJPGs", ""))
    except OSError:
        pass
    return "", []


class BlogCoverPicker(QWidget):
    """Thumbnails of the blog's pictures; click one to make it the cover.
    The first tile, "Automatic", leaves the choice to the blog (blogjpg empty)."""

    def __init__(self, trip_root, day_name: str, group: str, current: str = ""):
        super().__init__()
        self._trip_root, self._day, self._group = trip_root, day_name, group
        self._wanted = current
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._note = QLabel()
        self._note.setWordWrap(True)
        lay.addWidget(self._note)
        self._list = QListWidget()
        self._list.setViewMode(QListView.ViewMode.IconMode)
        self._list.setIconSize(QSize(THUMB, THUMB))
        self._list.setResizeMode(QListView.ResizeMode.Adjust)
        self._list.setMovement(QListView.Movement.Static)
        self._list.setSpacing(6)
        self._list.setWordWrap(True)
        # The chosen picture must be unmistakable — the same frame as Trip ▸ Trash.
        self._list.setStyleSheet(
            "QListWidget::item:selected { border: 5px solid #2f7de1; }")
        lay.addWidget(self._list, 1)
        self.reload()

    def reload(self):
        """(Re)read the blog's pictures — after a Create Blog the list changes."""
        keep = self.choice() if self._list.count() else self._wanted
        self._list.clear()
        blog, names = blog_pictures_for_day(self._trip_root, self._day, self._group)
        auto = QListWidgetItem("Automatic\n(highest rated)")
        auto.setData(Qt.ItemDataRole.UserRole, "")
        auto.setSizeHint(QSize(THUMB + 16, THUMB + 40))
        auto.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._list.addItem(auto)
        folder = Path(self._trip_root) / "web" / "biotablog" / blog
        for name in names:
            item = QListWidgetItem(Path(name).stem.split(" - ")[-1])
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(name)
            small = folder / f"{Path(name).stem}-small.jpg"
            pix = QPixmap(str(small if small.exists() else folder / name))
            if not pix.isNull():
                item.setIcon(QIcon(pix.scaled(THUMB, THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)))
            self._list.addItem(item)
        if not names:
            self._note.setText("Create the blog once; then you can choose here which of "
                               "its pictures stands for it on the blog index.")
        elif keep and keep not in names:
            self._note.setText(f"The chosen picture ({keep}) is no longer in the blog — "
                               f"choose another, or Automatic.")
        else:
            self._note.setText("Click the picture that stands for this blog on the blog "
                               "index. It takes effect at the next Create Blog.")
        self._select(keep if keep in names else "")

    def _select(self, name: str):
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == name:
                self._list.setCurrentRow(i)
                return

    def choice(self) -> str:
        """The chosen file name; "" = automatic."""
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else self._wanted
