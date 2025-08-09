from configparser import ConfigParser
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QAbstractItemView
)
from pathlib import Path
from sharedlib.statemachine import StateMachine

def populate_trip_overview(self):
    # Get image and statemachine directories
    image_root = Path(self.config.get("project", "picturedirectory")).parent
    trip_root = Path(self.config.get("trip", "tripbiotadatabase"))
    review_root = trip_root / "metadata" / "review"

    if not image_root.exists() or not review_root.exists():
        self.logger.warning("Invalid directory paths.")
        return

    trip_name_nl = image_root.name
    trip_translations = {
        "Oostzee zeilzomer": "Oostzee sailing summer"
    }
    trip_name_en = trip_translations.get(trip_name_nl, trip_name_nl)

    # Get all image day folders
    image_days = sorted([d for d in image_root.iterdir() if d.is_dir()], key=lambda p: p.name)

    def count_files(path):
        all_files = list(path.glob("*"))
        jpg_files = [f for f in all_files if f.suffix.lower() == ".jpg"]
        return len(all_files), len(jpg_files)
    
    self.state_labels = [
        "Read\nDirectory",
        "Write\nSummary",
        "Write\nBlog",
        "Build\nWebsite",
        "Freeze",
        "Write\nCommit"
    ]    

    self.state_keys = [
        "sv2readdirexif",
        "sv3writepictures",
        "sv4writeblog",
        "sv5buildwebsite",
        "sv6freeze",
        "sv7writecommit"
    ]

    expected_messages = {
        "sv1makerepository": "MakeRepository succeeded.",
        "sv2readdirexif": "Successfully run.",
        "sv3writepictures": "Successfully run.",
        "sv4writeblog": "Successfully run.",
        "sv5buildwebsite": "Successfully run.",
        "sv6freeze": "Successfully run.",
        "sv7writecommit": "Successfully run."
    }

    self.trip_table = QTableWidget()
    self.trip_table.setColumnCount(3 + len(self.state_labels) + 1)
    self.trip_table.setRowCount(len(image_days))
    self.trip_table.setHorizontalHeaderLabels(
        ["Day", "Total\nFiles", "JPEG\nFiles"] + self.state_labels + ["Progress"]
    )
    self.trip_table.setSortingEnabled(True)
    self.trip_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)


    # Make columns resizable
    header = self.trip_table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    for i, day in enumerate(image_days):
        total, jpgs = count_files(day)
        self.trip_table.setItem(i, 0, QTableWidgetItem(day.name))
        self.trip_table.setItem(i, 1, QTableWidgetItem(str(total)))
        self.trip_table.setItem(i, 2, QTableWidgetItem(str(jpgs)))

        # Match statemachine folder
        review_day = review_root / day.name
        # Create a StateMachine instance for this day
        sm = StateMachine(str(trip_root), str(review_day), readState=True)

        # Loop through each step
        for j, step_name in enumerate([
                    "ReadDirExif", "WritePictures", "WriteBlog", 
                    "BuildWebsite", "Freeze", "WriteCommit"]):
            if step_name not in sm.Taskstate:
                symbol = "⚪"
                tooltip = "Unknown step."
            else:
                completed, message, _, _ = sm.Taskstate[step_name]
                expected = "Successfully run."

                if completed and message == expected:
                    if completed and sm.Taskstate.get("WriteCommit", [False])[0]:
                        symbol = "⬜"
                        tooltip = "Step completed. Workflow is frozen after WriteCommit."
                    else:
                        symbol = "✅"
                        tooltip = message
                elif not completed and "failed" in message.lower():
                    symbol = "❌"
                    tooltip = message
                elif sm.stateOKtorun(step_name):
                    symbol = "🔘"
                    tooltip = "Click on the sphere to run this step."
                else:
                    symbol = "⚪"
                    tooltip = sm.explainAbort(step_name, raiseSV=False)

            item = QTableWidgetItem(symbol)
            item.setToolTip(tooltip)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.trip_table.setItem(i, 3 + j, item)

        # Determine progress summary
        summary_item = self.get_progress_summary_item(sm,  review_root / day.name)
        self.trip_table.setItem(i, 3 + len(self.state_labels), summary_item)

    # Layout
    overview_widget = QWidget()
    layout = QVBoxLayout()
    header_label = QLabel(f"Trip Overview: {trip_name_en}")
    header_label.setStyleSheet("font-weight: bold; font-size: 16px;")
    layout.addWidget(header_label)
    layout.addWidget(self.trip_table)
    overview_widget.setLayout(layout)
 
    self.yellow_tabs.addTab(overview_widget, "Overview")

    self.trip_table.cellDoubleClicked.connect(self.handle_trip_table_double_click)
