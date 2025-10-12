from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem, QMenu
)
from PyQt6.QtGui import QFont, QCursor
from PyQt6.QtCore import Qt
from pathlib import Path
import os
import subprocess
from utils.trip_config import read_trip_ini
from utils.config import _config, persist_config
from utils.gui_config import load_gui_cache

class TripInfoPanel(QWidget):
    def __init__(self, parent, trip_project_map, on_trip_selected):
        super().__init__()
        self.main_window = parent
        self.trip_project_map = trip_project_map
        self.on_trip_selected = on_trip_selected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        font_style = "font-size: 10pt; font-family: Consolas, monospace;"
        self.setStyleSheet(f"{font_style} background-color: #f9f9f9;")

        # Master info
        layout.addWidget(QLabel("Master Repository:"))
        layout.addWidget(QLabel(str(_config["master"]["masterbiotadatabase"])))
        layout.addWidget(QLabel("Master URL:"))
        layout.addWidget(QLabel(f"www.houtekamer.com/{_config['master']['masterbiotawebroot']}"))

        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if isinstance(widget, QLabel):
                widget.setStyleSheet(font_style)

        layout.addSpacing(10)
        layout.addWidget(QLabel("Trips:"))

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(font_style)
        layout.addWidget(self.tree)

        self.tree.itemClicked.connect(self.handle_trip_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_context_menu)

        trip_root = Path(_config["trip"]["tripbiotadatabase"]).parent
        if trip_root.is_dir():
            for trip_dir in sorted(trip_root.iterdir()):
                if trip_dir.is_dir():
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
                        if trip_key in trip_project_map:
                            trip_item.setText(0, f"🔗 {trip_dir.name}")
                            trip_item.setFont(0, QFont("Consolas", 10, QFont.Weight.Bold))
                            trip_item.setForeground(0, Qt.GlobalColor.darkGreen)
                            trip_item.setData(0, Qt.ItemDataRole.UserRole, trip_dir)

                    except Exception as e:
                        trip_item.setToolTip(0, f"Error reading trip.ini: {e}")

                    self.tree.addTopLevelItem(trip_item)
                    review_path = trip_dir / "metadata" / "review"
                    if review_path.is_dir():
                        for day_dir in sorted(review_path.iterdir()):
                            if day_dir.is_dir():
                                day_item = QTreeWidgetItem([f"    {day_dir.name}"])
                                trip_item.addChild(day_item)
        else:
            self.tree.addTopLevelItem(QTreeWidgetItem(["(No trips found)"]))

    def handle_trip_click(self, item, column):
        trip_dir = item.data(0, Qt.ItemDataRole.UserRole)
        if trip_dir and (trip_dir !=  Path(_config["trip"]["tripbiotadatabase"])):
            trip_data = read_trip_ini(trip_dir)
            trip_name = trip_data.get("trip_name", "").strip()
            _config["trip"]["tripbiotadatabase"] = str(trip_dir)
            _config["trip"]["tripbiotawebroot"] = "/trips/" + trip_name
            # load corresponding project
            _config["project"]["picturedirectory"] = self.trip_project_map["/trips/" + trip_name]     
            persist_config()
            self.on_trip_selected(self.main_window)

    def show_context_menu(self, position):
        item = self.tree.itemAt(position)
        if not item:
            return

        trip_key = item.data(0, Qt.ItemDataRole.UserRole)
        if trip_key and trip_key in self.trip_project_map:
            menu = QMenu()
            open_action = menu.addAction("Open Trip Folder")
            action = menu.exec(QCursor.pos())
            if action == open_action:
                folder = self.trip_project_map[trip_key]
                if os.path.isdir(folder):
                    subprocess.Popen(f'explorer "{folder}"')