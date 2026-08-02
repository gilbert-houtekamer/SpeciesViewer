from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem, QMenu
)
from PyQt6.QtGui import QFont, QCursor
from PyQt6.QtCore import Qt
from pathlib import Path
import os
from utils.trip_config import read_trip_ini
from utils.config import _config, persist_config, get_trips_root
from utils.gui_config import load_gui_cache, get_all_gui_usage, record_gui_usage
from utils.sort_modes import sort_items, DEFAULT_MODE
from utils.open_path import open_path
from gui import theme

import re as _re
_PATH_ROLE      = Qt.ItemDataRole.UserRole + 1  # trip_dir for all items (highlight)
_BASE_TEXT_ROLE = Qt.ItemDataRole.UserRole + 2  # base label text without ▶
_DAY_RE         = _re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TripInfoPanel(QWidget):
    def __init__(self, parent, trip_project_map, on_trip_selected, sort_mode=DEFAULT_MODE):
        super().__init__()
        self.main_window = parent
        self.trip_project_map = trip_project_map
        self.on_trip_selected = on_trip_selected
        self._sort_mode = sort_mode

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        font_style = theme.FONT_MONO
        self.setStyleSheet(f"{font_style} background-color: {theme.PANEL_BG};")

        # Master Repository/URL now lives only in the right-hand info rail
        # (widgets/SidebarInfoRail.py, ch.15 layout pass) — this panel used to
        # duplicate it here, which read as redundant once the rail always
        # shows it regardless of which left-mode is active.
        trips_label = QLabel("Trips:")
        trips_label.setStyleSheet(font_style)
        layout.addWidget(trips_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(font_style)
        layout.addWidget(self.tree)

        self.tree.itemClicked.connect(self.handle_trip_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_context_menu)

        self._populate_tree()

    def rebuild(self, sort_mode=None):
        """Re-sort and repaint the tree. sort_mode=None keeps the panel's
        current mode (used for the mainwindow-driven sort-button handler;
        pass the new mode explicitly there)."""
        if sort_mode is not None:
            self._sort_mode = sort_mode
        self._populate_tree()

    def _populate_tree(self):
        self.tree.clear()
        # Guard the empty case: get_trips_root() returns "" when no trips root is
        # configured, and Path("") is the current working directory — iterating it
        # would list the code repo folders instead of trips. Only build the tree
        # from a real, non-empty root.
        trips_root_str = get_trips_root()
        trip_root = Path(trips_root_str) if trips_root_str else None
        if trip_root is not None and trip_root.is_dir():
            usage = get_all_gui_usage()
            trip_dirs = [d for d in trip_root.iterdir() if d.is_dir()]
            for trip_dir in sort_items(trip_dirs, self._sort_mode, usage):
                trip_item = QTreeWidgetItem([trip_dir.name])
                trip_item.setData(0, Qt.ItemDataRole.UserRole, None)  # default

                try:
                    trip_data = read_trip_ini(trip_dir)
                    trip_name = trip_data.get("trip_name", "").strip()
                    title = trip_data.get("title_nl", "(geen titel)")
                    message = trip_data.get("message_nl", "")
                    title_en = trip_data.get("title_en", "(geen titel)")
                    message_en = trip_data.get("message_en", "")

                    tooltip_html = f"""
                        <html><body>
                            <p><b>{title}</b></p><p>{message}</p><p></p>
                            <p><b>{title_en}</b></p><p>{message_en}</p>
                        </body></html>
                    """
                    trip_item.setToolTip(0, tooltip_html)

                    trip_key = f"/trips/{trip_name}"
                    if trip_key in self.trip_project_map:
                        trip_item.setText(0, f"🔗 {trip_dir.name}")
                        trip_item.setFont(0, QFont("Consolas", 10, QFont.Weight.Bold))
                        trip_item.setForeground(0, Qt.GlobalColor.darkGreen)
                        trip_item.setData(0, Qt.ItemDataRole.UserRole, trip_dir)

                except Exception as e:
                    trip_item.setToolTip(0, f"Error reading trip.ini: {e}")

                trip_item.setData(0, _PATH_ROLE, trip_dir)
                trip_item.setData(0, _BASE_TEXT_ROLE, trip_item.text(0))
                self.tree.addTopLevelItem(trip_item)
                review_path = trip_dir / "metadata" / "review"
                if review_path.is_dir():
                    for day_dir in sorted(review_path.iterdir()):
                        if day_dir.is_dir():
                            day_item = QTreeWidgetItem([f"    {day_dir.name}"])
                            trip_item.addChild(day_item)
        else:
            self.tree.addTopLevelItem(QTreeWidgetItem(["(No trips found)"]))

        self._refresh_highlights()

    def _refresh_highlights(self):
        active = _config.get("trip", "tripbiotadatabase", fallback="")
        active_path = Path(active) if active else None
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            path      = item.data(0, _PATH_ROLE)
            base_text = item.data(0, _BASE_TEXT_ROLE)
            if not path or base_text is None:
                continue
            if active_path and path == active_path:
                item.setText(0, f"▶ {base_text}")
                item.setFont(0, QFont("Consolas", 10, QFont.Weight.Bold))
                item.setForeground(0, Qt.GlobalColor.darkMagenta)
            else:
                item.setText(0, base_text)
                if base_text.startswith("🔗"):
                    item.setFont(0, QFont("Consolas", 10, QFont.Weight.Bold))
                    item.setForeground(0, Qt.GlobalColor.darkGreen)
                else:
                    item.setFont(0, QFont("Consolas", 10))
                    item.setForeground(0, self.tree.palette().color(
                        self.tree.foregroundRole()))

    def handle_trip_click(self, item, column):
        trip_dir = item.data(0, Qt.ItemDataRole.UserRole)
        if trip_dir and (trip_dir !=  Path(_config["trip"]["tripbiotadatabase"])):
            record_gui_usage(trip_dir)
            trip_data = read_trip_ini(trip_dir)
            trip_name = trip_data.get("trip_name", "").strip()
            _config["trip"]["tripbiotadatabase"] = str(trip_dir)
            _config["trip"]["tripbiotawebroot"] = "/trips/" + trip_name
            _config["trip"]["tripsrootdirectory"] = str(trip_dir.parent)
            pic = self._resolve_picturedirectory(trip_dir, trip_name)
            if pic:
                _config["project"]["picturedirectory"] = pic
            persist_config()
            self._refresh_highlights()
            self.on_trip_selected(self.main_window)

    def _resolve_picturedirectory(self, trip_dir, trip_name):
        """The day folder to make active when a trip is selected. WP8: prefer the
        per-machine cache entry if it still points at a real folder (remembers the
        last-used day), else derive from the trip's own picture_root (first day
        subdir). Uses .get so a missing/slug-mismatched cache entry no longer
        raises — it just falls back to the self-contained trip.ini value."""
        cached = self.trip_project_map.get("/trips/" + trip_name, "")
        # Only trust the cache if it is an existing DAY folder — a pre-WP8 cache
        # entry could hold the trip root, which must never become picturedirectory.
        if (cached and os.path.isdir(cached)
                and _DAY_RE.match(os.path.basename(os.path.normpath(cached)))):
            return cached
        from utils.trip_identity import resolve_trip_picture_root
        root = resolve_trip_picture_root(str(trip_dir), kind="trip",
                                         active_picturedirectory="")
        if root and os.path.isdir(root):
            days = sorted(d for d in os.listdir(root)
                          if _DAY_RE.match(d) and os.path.isdir(os.path.join(root, d)))
            return os.path.join(root, days[0]) if days else root
        return ""

    def show_context_menu(self, position):
        item = self.tree.itemAt(position)
        if not item:
            return

        trip_dir = item.data(0, _PATH_ROLE)
        if not trip_dir:
            return  # a day row or the "(No trips found)" placeholder

        menu = QMenu()
        open_action = menu.addAction("Open Trip Folder")
        retire_action = menu.addAction("Retire trip (delete data)…")
        action = menu.exec(QCursor.pos())
        if action == open_action:
            if os.path.isdir(str(trip_dir)):
                open_path(str(trip_dir))
        elif action == retire_action:
            from gui.trip_retire import retire_trip_dialog
            retire_trip_dialog(self.main_window, str(trip_dir))

    def remove_trip(self, trip_dir):
        """Drop a retired trip's row from the tree (called after a delete)."""
        target = os.path.normcase(os.path.normpath(str(trip_dir)))
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            path = item.data(0, _PATH_ROLE)
            if path and os.path.normcase(os.path.normpath(str(path))) == target:
                self.tree.takeTopLevelItem(i)
                return