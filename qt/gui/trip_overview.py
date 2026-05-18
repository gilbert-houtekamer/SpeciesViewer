import csv
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QAbstractItemView
)
from pathlib import Path
from sharedlib.statemachine import StateMachine
from utils.config import _config

SYNC_COL = 3
STATE_COL_START = 4

# Extensions the image preview dialog considers "photo files" — matches the
# affirmative filter in ImagePreviewDialog.discover_images. _day_health uses
# the same set so its no-GPS count aligns with the dialog's view (PNG
# screenshots and other non-image rows in data_files.csv are ignored).
PHOTO_EXTENSIONS = {"orf", "nef", "cr2", "cr3", "arw", "rw2", "raf", "dng",
                    "heic", "jpg", "jpeg"}


def _format_total_files_item(day_path, review_root):
    """Build the Total Files cell with health markers and tooltip. Re-used by
    refresh_day_health so the trip-overview row updates after the image
    preview dialog fixes species/GPS issues."""
    all_files = list(day_path.glob("*"))
    total = len(all_files)
    health = _day_health(review_root / day_path.name)
    if health is None:
        text = str(total)
        tooltip = "Read Directory not yet run for this day."
    else:
        warnings, no_gps = health
        species_mark = "✅" if warnings == 0 else "⚠️"
        gps_mark = "📍" if no_gps == 0 else "❓"
        text = f"{total}  {species_mark} {gps_mark}"
        species_tip = "All species OK" if warnings == 0 else f"{warnings} species typo{'s' if warnings != 1 else ''}"
        gps_tip = "All GPS OK" if no_gps == 0 else f"{no_gps} missing GPS"
        tooltip = f"{species_tip} — {gps_tip}"
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    item.setToolTip(tooltip)
    return item


def refresh_day_health(main_window, day_name):
    """Re-read data_pictures.csv for `day_name` and update its Total Files
    cell. Called from ImagePreviewDialog.closeEvent so the table reflects
    GPS / species fixes the user just made.

    Logs what it does so we can tell from the green box whether the refresh
    actually hit the row — surgical updates have failed silently before."""
    logger = logging.getLogger("speciesviewer")
    table = getattr(main_window, "trip_table", None)
    if table is None:
        logger.info(f"refresh_day_health: no trip_table on main_window for {day_name}")
        return
    try:
        image_root = Path(_config.get("project", "picturedirectory")).parent
        trip_root = Path(_config.get("trip", "tripbiotadatabase"))
    except Exception as e:
        logger.info(f"refresh_day_health: config lookup failed: {e}")
        return
    review_root = trip_root / "metadata" / "review"
    day_path = image_root / day_name
    if not day_path.exists():
        logger.info(f"refresh_day_health: day folder doesn't exist: {day_path}")
        return
    for row in range(table.rowCount()):
        cell = table.item(row, 0)
        if cell and cell.text() == day_name:
            new_item = _format_total_files_item(day_path, review_root)
            table.setItem(row, 1, new_item)
            logger.info(
                f"refresh_day_health: row {row} for {day_name} → "
                f"{new_item.text()!r}"
            )
            return
    logger.info(f"refresh_day_health: no matching row for {day_name}")


def _day_health(review_day_path):
    """Return (warnings_count, no_gps_count) for the day.

    GPS is read from data_files.csv (same source the image preview dialog
    uses, so the trip-overview marker stays consistent with the dialog's
    "clean day" determination) and aggregated by UniquePictureName — a
    UPN has GPS if ANY of its file rows (ORF / XMP / JPG) has it.

    Warnings come from data_pictures.csv (errormessage is a UPN-only column).

    Returns None if data_files.csv doesn't exist yet (Read Directory hasn't
    been run for this day)."""
    files_csv = review_day_path / "data_files.csv"
    pictures_csv = review_day_path / "data_pictures.csv"
    if not files_csv.exists():
        return None

    upn_has_gps = {}
    try:
        with open(files_csv, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                ext = (row.get("WindowsExtension") or "").lower()
                if ext not in PHOTO_EXTENSIONS:
                    continue  # PNG screenshots, XMP sidecars, etc.
                upn = row.get("UniquePictureName", "")
                if not upn:
                    continue
                has_gps = bool((row.get("Composite:GPSPosition") or "").strip()) \
                       or bool((row.get("EXIF:GPSLatitude") or "").strip())
                upn_has_gps[upn] = upn_has_gps.get(upn, False) or has_gps
    except Exception:
        return None
    no_gps = sum(1 for has in upn_has_gps.values() if not has)
    if no_gps:
        logger = logging.getLogger("speciesviewer")
        sample = [upn for upn, has in upn_has_gps.items() if not has][:5]
        logger.info(
            f"_day_health: {review_day_path.name} has {no_gps} UPN(s) "
            f"without GPS in data_files.csv. Sample: {sample}"
        )

    warnings = 0
    if pictures_csv.exists():
        try:
            with open(pictures_csv, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    if (row.get("errormessage") or "").strip():
                        warnings += 1
        except Exception:
            pass
    return warnings, no_gps

def populate_trip_overview(self):
    self.update_trip_label()
    self.remove_existing_overview_tab()
    # Get image and statemachine directories
    image_root = Path(_config.get("project", "picturedirectory")).parent
    trip_root = Path(_config.get("trip", "tripbiotadatabase"))
    review_root = trip_root / "metadata" / "review"

    if not image_root.exists() or not review_root.exists():
        self.logger.warning("Invalid directory paths.")
        return

    trip_name = image_root.name

    # Get all image day folders
    image_days = sorted([d for d in image_root.iterdir() if d.is_dir()], key=lambda p: p.name)
    self._trip_overview_day_snapshot = tuple(d.name for d in image_days)

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
    self.trip_table.setColumnCount(STATE_COL_START + len(self.state_labels) + 1)
    self.trip_table.setRowCount(len(image_days))
    self.trip_table.setHorizontalHeaderLabels(
        ["Day", "Total\nFiles", "JPEG\nFiles", "Sync\nSidecars"] + self.state_labels + ["Progress"]
    )
    self.trip_table.setSortingEnabled(False)
    self.trip_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    self.trip_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    self.trip_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
    self.trip_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    # Strong selection colour — the default translucent highlight is invisible
    # against the yellow tab background.
    self.trip_table.setStyleSheet("""
        QTableWidget::item:selected {
            background-color: #3478d4;
            color: white;
        }
    """)


    # Make columns resizable
    header = self.trip_table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    # Total Files and JPEG Files auto-size to their content (Total Files
    # carries "N ✅ 📍" markers that don't fit a stretched column).
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

    for i, day in enumerate(image_days):
        total, jpgs = count_files(day)
        self.trip_table.setItem(i, 0, QTableWidgetItem(day.name))
        self.trip_table.setItem(i, 1, _format_total_files_item(day, review_root))

        jpgs_item = QTableWidgetItem(str(jpgs))
        jpgs_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.trip_table.setItem(i, 2, jpgs_item)

        # Sync Sidecars column — not persisted, starts as ⚪
        sync_item = QTableWidgetItem("⚪")
        sync_item.setToolTip("Not yet run.")
        sync_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.trip_table.setItem(i, SYNC_COL, sync_item)

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
                elif jpgs == 0 and step_name == "WriteBlog":
                    symbol = "🚫"
                    tooltip = "No JPGs available — blog step suppressed."
                elif sm.stateOKtorun(step_name):
                    symbol = "🔘"
                    tooltip = "Click on the sphere to run this step."
                else:
                    symbol = "⚪"
                    tooltip = sm.explainAbort(step_name, raiseSV=False)

            item = QTableWidgetItem(symbol)
            item.setToolTip(tooltip)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if symbol == "🚫":
                item.setFlags(Qt.ItemFlag.NoItemFlags)

            self.trip_table.setItem(i, STATE_COL_START + j, item)

        # Determine progress summary
        summary_item = self.get_progress_summary_item(sm,  review_root / day.name)
        self.trip_table.setItem(i, STATE_COL_START + len(self.state_labels), summary_item)

    # Layout
    overview_widget = QWidget()
    layout = QVBoxLayout()
    header_label = QLabel(f"Trip Overview: {trip_name}")
    header_label.setStyleSheet("font-weight: bold; font-size: 16px;")
    layout.addWidget(header_label)
    layout.addWidget(self.trip_table)
    overview_widget.setLayout(layout)

    self.yellow_tabs.addTab(overview_widget, "Active Trip")

    self.trip_table.cellDoubleClicked.connect(self.handle_trip_table_double_click)
    self.trip_table.verticalHeader().sectionDoubleClicked.connect(self.handle_row_rerun)
    self.trip_table.horizontalHeader().sectionDoubleClicked.connect(self.handle_column_run_all)
    self.trip_table.customContextMenuRequested.connect(self.handle_trip_table_context_menu)
    # Keep Trip menu in sync with the selected row
    self.trip_table.currentCellChanged.connect(self.handle_trip_table_current_changed)
