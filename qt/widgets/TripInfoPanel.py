from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem
from pathlib import Path
from utils.trip_config import read_trip_ini
from utils.config import _config, persist_config

class TripInfoPanel(QWidget):
    def __init__(self):
        super().__init__()

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

        # Apply font style to all labels
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if isinstance(widget, QLabel):
                widget.setStyleSheet(font_style)

        layout.addSpacing(10)
        layout.addWidget(QLabel("Trips:"))

        # Tree widget for trips and days
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setStyleSheet(font_style)
        layout.addWidget(tree)

        trip_root = Path(_config["trip"]["tripbiotadatabase"]).parent
        if trip_root.is_dir():
            for trip_dir in sorted(trip_root.iterdir()):
                if trip_dir.is_dir():
                    trip_item = QTreeWidgetItem([trip_dir.name])

                    # 🔍 Add tooltip from read_trip_ini
                    try:
                        trip_data = read_trip_ini(trip_dir)
                        title = trip_data.get("title_nl", "(geen titel)")
                        message = trip_data.get("message_nl", "")
                        title_en = trip_data.get("title_en", "(geen titel)")
                        message_en = trip_data.get("message_en", "")                        
                        # tooltip_text = f"📌 {title}\n📝 {message}\n\n📌 {title_en}\n📝 {message_en}"

                        tooltip_html = f"""
                            <html>
                                <head></head>
                                <body>
                                    <p><b>{title}</b></p>
                                    <p>{message}</p><p></p>
                                    <p><b>{title_en}</b></p>
                                    <p>{message_en}</p>                                    
                                </body>
                            </html>
                            """
                        trip_item.setToolTip(0, tooltip_html)
                        
                        # trip_item.setToolTip(0, tooltip_text)
                    except Exception as e:
                        trip_item.setToolTip(0, f"Error reading trip.ini: {e}")

                    tree.addTopLevelItem(trip_item)

                    # Look for days under metadata/review
                    review_path = trip_dir / "metadata" / "review"
                    if review_path.is_dir():
                        for day_dir in sorted(review_path.iterdir()):
                            if day_dir.is_dir():
                                day_item = QTreeWidgetItem([f"    {day_dir.name}"])
                                trip_item.addChild(day_item)
        else:
            tree.addTopLevelItem(QTreeWidgetItem(["(No trips found)"]))
