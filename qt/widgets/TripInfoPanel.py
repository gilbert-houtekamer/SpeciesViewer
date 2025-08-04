from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem
from pathlib import Path

class TripInfoPanel(QWidget):
    def __init__(self, config):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        font_style = "font-size: 10pt; font-family: Consolas, monospace;"

        # Master info
        layout.addWidget(QLabel("Master Repository:"))
        layout.addWidget(QLabel(str(config["master"]["masterbiotadatabase"])))
        layout.addWidget(QLabel("Master URL:"))
        layout.addWidget(QLabel(f"www.houtekamer.com/{config['master']['masterbiotawebroot']}"))

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

        trip_root = Path(config["trip"]["tripbiotadatabase"]).parent
        if trip_root.is_dir():
            for trip_dir in sorted(trip_root.iterdir()):
                if trip_dir.is_dir():
                    trip_item = QTreeWidgetItem([trip_dir.name])
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
