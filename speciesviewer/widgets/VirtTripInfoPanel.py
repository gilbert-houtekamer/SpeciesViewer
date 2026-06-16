from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem, QMenu
from PyQt6.QtGui import QFont, QCursor
from PyQt6.QtCore import Qt
from pathlib import Path
import os
from utils.virttrip_config import read_virttrip_ini
from utils.config import _config, persist_config, get_virttrips_root
from utils.open_path import open_path

_PATH_ROLE = Qt.ItemDataRole.UserRole          # trip_dir Path object
_BASE_ROLE = Qt.ItemDataRole.UserRole + 1      # base label text without ▶


class VirtTripInfoPanel(QWidget):
    def __init__(self, parent, on_virttrip_selected):
        super().__init__()
        self.main_window = parent
        self.on_virttrip_selected = on_virttrip_selected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        font_style = "font-size: 10pt; font-family: Consolas, monospace;"
        self.setStyleSheet(f"{font_style} background-color: #f9f9f9;")

        virttrip_root = get_virttrips_root()
        layout.addWidget(self._header_label("Virtual Trip Root:"))
        layout.addWidget(self._header_label(virttrip_root or "(not configured)"))

        layout.addSpacing(10)
        layout.addWidget(self._header_label("Virtual Trips:"))

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(f"{font_style} background-color: white;")
        layout.addWidget(self.tree)

        self.tree.itemClicked.connect(self._handle_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)

        self._populate_tree()

    def _header_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "font-size: 10pt; font-family: Consolas, monospace; background-color: transparent;")
        lbl.setWordWrap(True)
        return lbl

    def _populate_tree(self):
        """Clear and re-populate the tree from the current virttrips root."""
        self.tree.clear()
        virttrip_root = get_virttrips_root()
        root_path = Path(virttrip_root) if virttrip_root else None
        if root_path and root_path.is_dir():
            for d in sorted(root_path.iterdir()):
                if not d.is_dir():
                    continue
                trip_data = read_virttrip_ini(str(d))
                if trip_data is None:
                    continue

                web_built = (d / "web" / "summaries").is_dir()
                blog_dir  = d / "metadata" / "blog"
                has_blogs = blog_dir.is_dir() and any(blog_dir.glob("*.ini"))
                icon = "✅" if web_built else ("📝" if has_blogs else "⚪")
                base_text = f"{icon} {d.name}"

                item = QTreeWidgetItem([base_text])
                item.setData(0, _PATH_ROLE, d)
                item.setData(0, _BASE_ROLE, base_text)

                fromdate  = trip_data.get("fromdate", "")
                todate    = trip_data.get("todate", "")
                title_nl  = trip_data.get("title_nl", "")
                title_en  = trip_data.get("title_en", "")
                message_nl = trip_data.get("message_nl", "")
                message_en = trip_data.get("message_en", "")
                date_range = f"{fromdate} – {todate}" if fromdate else ""

                tooltip_html = f"""
                    <html><body>
                        <p><b>{title_nl}</b></p><p>{message_nl}</p><p></p>
                        <p><b>{title_en}</b></p><p>{message_en}</p>
                        {f'<p><i>{date_range}</i></p>' if date_range else ''}
                    </body></html>
                """
                item.setToolTip(0, tooltip_html)
                self.tree.addTopLevelItem(item)
        else:
            self.tree.addTopLevelItem(QTreeWidgetItem(["(No virtual trips found)"]))

        self._refresh_highlights()

    def rebuild(self):
        """Re-enumerate the virttrips root and refresh the tree (e.g. after creation)."""
        self._populate_tree()

    def _refresh_highlights(self):
        virttrip_active = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        active_path = Path(virttrip_active) if virttrip_active else None
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            d         = item.data(0, _PATH_ROLE)
            base_text = item.data(0, _BASE_ROLE)
            if not d or base_text is None:
                continue
            if active_path and d == active_path:
                item.setText(0, f"▶ {base_text}")
                item.setFont(0, QFont("Consolas", 10, QFont.Weight.Bold))
                item.setForeground(0, Qt.GlobalColor.darkMagenta)
            else:
                item.setText(0, base_text)
                item.setFont(0, QFont("Consolas", 10))
                item.setForeground(0, self.tree.palette().color(self.tree.foregroundRole()))

    def _handle_click(self, item, column):
        trip_dir = item.data(0, _PATH_ROLE)
        if not trip_dir:
            return
        active = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        if str(trip_dir) == active:
            return  # already active
        if not _config.has_section("virttrip"):
            _config.add_section("virttrip")
        _config.set("virttrip", "virttripbiotadatabase", str(trip_dir))
        _config.set("virttrip", "virttriprootdirectory", str(trip_dir.parent))
        trip_data = read_virttrip_ini(str(trip_dir))
        if trip_data:
            trip_name = trip_data.get("trip_name", "").strip()
            if trip_name:
                _config.set("virttrip", "virttripbiotawebroot", f"/virttrips/{trip_name}")
        persist_config()
        self._refresh_highlights()
        self.on_virttrip_selected(self.main_window)

    def _show_context_menu(self, position):
        item = self.tree.itemAt(position)
        if not item:
            return
        trip_dir = item.data(0, _PATH_ROLE)
        if not trip_dir:
            return
        menu = QMenu()
        open_action = menu.addAction("Open Virtual Trip Folder")
        action = menu.exec(QCursor.pos())
        if action == open_action and os.path.isdir(trip_dir):
            open_path(trip_dir)
