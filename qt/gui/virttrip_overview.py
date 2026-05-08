import csv
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView,
)

from utils.config import _config
from utils.virttrip_config import read_virttrip_ini

JPEG_EXTS = {"jpg", "jpeg"}

DATE_COL  = 0
FILES_COL = 1
JPGS_COL  = 2
BLOG_COL  = 3
WEB_COL   = 4

TAB_TITLE = "Virtual Trip"


def _build_file_list(csv_path: Path, jpeg_only: bool) -> list:
    """Read data_files.csv and return full Path objects for each file.

    Files live in the master database at their original location — no
    copies are made. When jpeg_only is True only .jpg / .jpeg rows are
    returned.
    """
    paths = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                ext = row.get("WindowsExtension", "").lower()
                if jpeg_only and ext not in JPEG_EXTS:
                    continue
                directory = row.get("File:Directory", "")
                filename  = row.get("File:FileName",  "")
                if directory and filename:
                    paths.append(Path(directory) / filename)
    except (FileNotFoundError, OSError):
        pass
    return paths


def _open_day_viewer(parent, day_dir: Path, jpeg_only: bool, virttrip_root: Path):
    from gui.image_viewer import ImagePreviewDialog
    file_list = _build_file_list(day_dir / "data_files.csv", jpeg_only)
    if not file_list:
        return
    cache_root = virttrip_root / "admin" / ".preview_cache"
    dlg = ImagePreviewDialog(
        parent=parent,
        file_list=file_list,
        cache_root=str(cache_root),
    )
    dlg.exec()


def _count_files_from_csv(csv_path: Path):
    """Return (total_rows, jpeg_rows) parsed from a data_files.csv."""
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            total = jpegs = 0
            for row in reader:
                total += 1
                if row.get("WindowsExtension", "").lower() in ("jpg", "jpeg"):
                    jpegs += 1
        return total, jpegs
    except (FileNotFoundError, KeyError, OSError):
        return 0, 0


def populate_virttrip_overview(self):
    """Populate (or refresh) the Virtual Trip tab in yellow_tabs."""
    self.remove_existing_virttrip_tab()

    virttrip_root = _config.get("virttrip", "virttripbiotadatabase", fallback="")
    if not virttrip_root:
        return
    virttrip_path = Path(virttrip_root)
    if not virttrip_path.is_dir():
        return

    review_root = virttrip_path / "metadata" / "review"
    if not review_root.is_dir():
        return

    day_dirs = sorted([d for d in review_root.iterdir() if d.is_dir()], key=lambda p: p.name)
    if not day_dirs:
        return

    trip_data = read_virttrip_ini(virttrip_root)
    trip_name = trip_data.get("trip_name", virttrip_path.name) if trip_data else virttrip_path.name

    self.virttrip_table = QTableWidget()
    self.virttrip_table.setColumnCount(5)
    self.virttrip_table.setRowCount(len(day_dirs))
    self.virttrip_table.setHorizontalHeaderLabels(
        ["Date", "Total\nFiles", "JPEG\nFiles", "Write\nBlog", "Build\nWebsite"]
    )
    self.virttrip_table.setSortingEnabled(False)
    self.virttrip_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    self.virttrip_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    self.virttrip_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    self.virttrip_table.setStyleSheet("""
        QTableWidget::item:selected {
            background-color: #3478d4;
            color: white;
        }
    """)

    header = self.virttrip_table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(DATE_COL, QHeaderView.ResizeMode.ResizeToContents)

    for i, day_dir in enumerate(day_dirs):
        total, jpegs = _count_files_from_csv(day_dir / "data_files.csv")

        self.virttrip_table.setItem(i, DATE_COL,  QTableWidgetItem(day_dir.name))
        self.virttrip_table.setItem(i, FILES_COL, QTableWidgetItem(str(total)))
        self.virttrip_table.setItem(i, JPGS_COL,  QTableWidgetItem(str(jpegs)))

        for col in (BLOG_COL, WEB_COL):
            item = QTableWidgetItem("⚪")
            item.setToolTip("Not yet implemented.")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.virttrip_table.setItem(i, col, item)

    overview_widget = QWidget()
    layout = QVBoxLayout(overview_widget)
    header_label = QLabel(f"Virtual Trip: {trip_name}")
    header_label.setStyleSheet("font-weight: bold; font-size: 16px;")
    layout.addWidget(header_label)
    layout.addWidget(self.virttrip_table)

    self.yellow_tabs.addTab(overview_widget, TAB_TITLE)
    self.yellow_tabs.setCurrentWidget(overview_widget)

    def _on_cell_double_clicked(row, col):
        if col > JPGS_COL or row < 0 or row >= len(day_dirs):
            return
        jpeg_only = (col == JPGS_COL)
        _open_day_viewer(self, day_dirs[row], jpeg_only, virttrip_path)

    self.virttrip_table.cellDoubleClicked.connect(_on_cell_double_clicked)
