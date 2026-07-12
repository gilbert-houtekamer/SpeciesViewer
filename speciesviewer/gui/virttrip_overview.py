import csv
import re
from datetime import date
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QMenu,
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

TAB_TITLE = "Active Virtual Trip"


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


def _open_blog_editor(parent, day_dirs: list, first_date: date,
                      virttrip_root: Path, jump_to_day_num: int = 0):
    """Open the tabbed blog editor, optionally jumping to a specific day's tab."""
    from gui.virttrip_blog_editor import VirtTripBlogEditorDialog
    blog_dir = virttrip_root / "metadata" / "blog"
    dlg = VirtTripBlogEditorDialog(parent, blog_dir, day_dirs, first_date)
    if jump_to_day_num:
        dlg.jump_to_day(jump_to_day_num)
    dlg.exec()


def _open_day_viewer(parent, day_dir: Path, jpeg_only: bool, virttrip_root: Path):
    from gui.image_viewer import ImagePreviewDialog
    file_list = _build_file_list(day_dir / "data_files.csv", jpeg_only)
    if not file_list:
        return
    dlg = ImagePreviewDialog(
        parent=parent,
        file_list=file_list,
        source_label=str(day_dir),
        readonly=True,
    )
    dlg.exec()


def _count_pictures_from_csv(csv_path: Path):
    """Return (pictures, jpeg_files) parsed from a data_files.csv: distinct
    UniquePictureNames (pictures) and JPEG file rows. A RAW and its sidecars
    share one UPN, so they count as one picture, not several files."""
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            upns = set()
            jpegs = 0
            for row in reader:
                u = (row.get("UniquePictureName") or "").strip()
                if u:
                    upns.add(u)
                if row.get("WindowsExtension", "").lower() in ("jpg", "jpeg"):
                    jpegs += 1
        return len(upns), jpegs
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

    # Only include YYYY-MM-DD directories; the 'frozen/' subdir created by
    # svv4BuildWebsite must not appear as a fake day row.
    _DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    day_dirs = sorted(
        [d for d in review_root.iterdir() if d.is_dir() and _DATE_RE.match(d.name)],
        key=lambda p: p.name,
    )
    if not day_dirs:
        return

    trip_data = read_virttrip_ini(virttrip_root)
    trip_name = trip_data.get("trip_name", virttrip_path.name) if trip_data else virttrip_path.name

    self.virttrip_table = QTableWidget()
    self.virttrip_table.setColumnCount(5)
    self.virttrip_table.setRowCount(len(day_dirs))
    self.virttrip_table.setHorizontalHeaderLabels(
        ["Date", "Pictures", "JPEG\nFiles", "Write\nBlog", "Build\nWebsite"]
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

    blog_dir  = virttrip_path / "metadata" / "blog"
    first_date = date.fromisoformat(day_dirs[0].name)

    # Website is built trip-wide (not per-day); detect from the all-species summary page.
    web_built = (virttrip_path / "web" / "summaries" / "all biota species-nl.html").exists()

    for i, day_dir in enumerate(day_dirs):
        total, jpegs = _count_pictures_from_csv(day_dir / "data_files.csv")

        self.virttrip_table.setItem(i, DATE_COL,  QTableWidgetItem(day_dir.name))
        self.virttrip_table.setItem(i, FILES_COL, QTableWidgetItem(str(total)))
        self.virttrip_table.setItem(i, JPGS_COL,  QTableWidgetItem(str(jpegs)))

        # BLOG_COL: grey when no JPEGs; otherwise show ini-exists state
        blog_ini = blog_dir / f"blog_{day_dir.name}.ini"
        if jpegs == 0:
            blog_icon, blog_tooltip = "○", "No JPEG files — blog page not generated"
        elif blog_ini.exists():
            blog_icon, blog_tooltip = "✅", "Blog ini exists — double-click to edit."
        else:
            blog_icon, blog_tooltip = "⚪", "No blog ini yet. Click 'Write All Blogs' to auto-create."
        blog_item = QTableWidgetItem(blog_icon)
        blog_item.setToolTip(blog_tooltip)
        blog_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        blog_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.virttrip_table.setItem(i, BLOG_COL, blog_item)

        # WEB_COL: grey when no JPEGs; otherwise show trip-wide build state
        if jpegs == 0:
            web_icon, web_tooltip = "○", "No JPEG files — website not generated"
        elif web_built:
            web_icon, web_tooltip = "✅", "Species pages built and uploaded."
        else:
            web_icon, web_tooltip = "⚪", "Click 'Build Website' to generate species pages."
        web_item = QTableWidgetItem(web_icon)
        web_item.setToolTip(web_tooltip)
        web_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        web_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.virttrip_table.setItem(i, WEB_COL, web_item)

    overview_widget = QWidget()
    layout = QVBoxLayout(overview_widget)

    header_row = QHBoxLayout()
    header_label = QLabel(f"Virtual Trip: {trip_name}")
    header_label.setStyleSheet("font-weight: bold; font-size: 16px;")
    header_row.addWidget(header_label)
    header_row.addStretch()

    map_btn = QPushButton("🗺 Map whole trip")
    map_btn.setToolTip("Plot every day of this virtual trip on one map (all species), "
                       "with the travel path. The pictures come from the master, so "
                       "the map only shows them — it does not change them.")
    header_row.addWidget(map_btn)

    edit_blogs_btn = QPushButton("Edit Blogs")
    edit_blogs_btn.setToolTip("Open the tabbed blog editor to edit titles, text and settings for every day.")
    write_blog_btn = QPushButton("Write All Blogs")
    write_blog_btn.setToolTip("Auto-create blog ini files and upload all day blogs.")
    build_web_btn  = QPushButton("Build Website")
    build_web_btn.setToolTip("Generate species HTML pages for this virtual trip.")
    header_row.addWidget(edit_blogs_btn)
    header_row.addWidget(write_blog_btn)
    header_row.addWidget(build_web_btn)

    layout.addLayout(header_row)
    layout.addWidget(self.virttrip_table)

    self.yellow_tabs.addTab(overview_widget, TAB_TITLE)
    self.yellow_tabs.setCurrentWidget(overview_widget)

    def _on_edit_blogs():
        _open_blog_editor(self, day_dirs, first_date, virttrip_path)

    def _on_write_blog():
        from gui.virttrip_process import VirttripWriteBlogDialog
        VirttripWriteBlogDialog(self).exec()

    def _on_build_website():
        from gui.virttrip_process import VirttripBuildWebsiteDialog
        VirttripBuildWebsiteDialog(self).exec()

    edit_blogs_btn.clicked.connect(_on_edit_blogs)
    write_blog_btn.clicked.connect(_on_write_blog)
    build_web_btn.clicked.connect(_on_build_website)
    map_btn.clicked.connect(lambda _=False: self.open_virttrip_map())

    def _on_context_menu(pos):
        row = self.virttrip_table.rowAt(pos.y())
        if row < 0 or row >= len(day_dirs):
            return
        menu = QMenu(self.virttrip_table)
        map_day = menu.addAction("🗺 Map this day")
        map_all = menu.addAction("🗺 Map whole trip")
        chosen = menu.exec(self.virttrip_table.viewport().mapToGlobal(pos))
        if chosen == map_day:
            self.open_virttrip_map([day_dirs[row]])
        elif chosen == map_all:
            self.open_virttrip_map()

    self.virttrip_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    self.virttrip_table.customContextMenuRequested.connect(_on_context_menu)

    def _on_cell_double_clicked(row, col):
        if row < 0 or row >= len(day_dirs):
            return
        if col <= JPGS_COL:
            jpeg_only = (col == JPGS_COL)
            _open_day_viewer(self, day_dirs[row], jpeg_only, virttrip_path)
        elif col == BLOG_COL:
            day_num = (date.fromisoformat(day_dirs[row].name) - first_date).days + 1
            _open_blog_editor(self, day_dirs, first_date, virttrip_path,
                              jump_to_day_num=day_num)

    self.virttrip_table.cellDoubleClicked.connect(_on_cell_double_clicked)
