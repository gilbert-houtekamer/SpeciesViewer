import csv
import logging
import re
from datetime import date
from pathlib import Path

logger = logging.getLogger("speciesviewer")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QMenu,
    QHeaderView, QAbstractItemView,
)

from utils.config import _config
from utils.virttrip_config import (read_virttrip_ini, blog_review_state,
                                   BLOG_DRAFT, BLOG_REVIEWED)

JPEG_EXTS = {"jpg", "jpeg"}

DATE_COL  = 0
FILES_COL = 1
JPGS_COL  = 2
GPS_COL   = 3
BLOG_COL  = 4
WEB_COL   = 5

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


def _build_crop_hints(csv_path: Path) -> dict:
    """Map each JPG file's full path -> True when it's (probably) a crop: its
    ImageSize is much smaller than the biggest (RAW) ImageSize sharing its UPN.
    These are baked DxO renders — the crop lives in the pixels, not a readable
    sidecar — so size is the only cue (see gui.picture_provider.jpg_is_crop_by_size).
    data_files.csv already carries every file's size, so this needs no per-card
    disk read. A JPG-only UPN (no RAW to compare against) stays False."""
    from gui.picture_provider import jpg_is_crop_by_size

    def area(dims: str) -> int:
        try:
            w, h = re.split(r"[x ]+", (dims or "").strip())[:2]
            return int(w) * int(h)
        except (ValueError, IndexError):
            return 0

    native: dict[str, str] = {}                # upn -> biggest ImageSize seen (the RAW)
    jpgs: list[tuple[str, str, str]] = []       # (full path, upn, this JPG's ImageSize)
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter=";"):
                upn = (row.get("UniquePictureName") or "").strip()
                dims = row.get("ImageSize", "")
                if area(dims) > area(native.get(upn, "")):
                    native[upn] = dims
                if row.get("WindowsExtension", "").lower() in JPEG_EXTS:
                    directory = row.get("File:Directory", "")
                    filename  = row.get("File:FileName",  "")
                    if directory and filename:
                        jpgs.append((str(Path(directory) / filename), upn, dims))
    except (FileNotFoundError, OSError, csv.Error):
        return {}
    return {path: jpg_is_crop_by_size(native.get(upn, ""), jpg_dims)
            for path, upn, jpg_dims in jpgs}


def _build_name_hints(day_dir: Path) -> dict:
    """Map each file's full path -> its species-name row {NLSoortNaam,
    EngSoortNaam, WetSoortNaam}, so the metadata-less virtual-trip viewer can show
    the caption in the chosen language instead of the scientific folder name.
    Names come from data_pictures.csv (per UPN); the path->UPN link from
    data_files.csv. Empty on any read error → the folder name is used."""
    pics: dict[str, dict] = {}       # upn -> name row
    try:
        with open(day_dir / "data_pictures.csv", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter=";"):
                upn = (row.get("UniquePictureName") or "").strip()
                if upn:
                    pics[upn] = {k: row.get(k, "") for k in
                                 ("NLSoortNaam", "EngSoortNaam", "WetSoortNaam")}
    except (FileNotFoundError, OSError, csv.Error):
        return {}
    hints: dict[str, dict] = {}
    try:
        with open(day_dir / "data_files.csv", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter=";"):
                upn = (row.get("UniquePictureName") or "").strip()
                directory = row.get("File:Directory", "")
                filename  = row.get("File:FileName",  "")
                if upn in pics and directory and filename:
                    hints[str(Path(directory) / filename)] = pics[upn]
    except (FileNotFoundError, OSError, csv.Error):
        return {}
    return hints


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
    # Precompute the "#" crop marker per JPG from the day CSV's sizes (no per-card
    # disk read) — these baked DxO renders keep no readable crop rectangle.
    crop_hints = _build_crop_hints(day_dir / "data_files.csv")
    # And the species names per JPG, so the caption follows the name-language
    # profile instead of the metadata-less viewer's scientific folder name. The
    # profile is this virtual trip's override (virttrip.ini) or the global default.
    from utils.name_profile import effective_profile
    from utils.virttrip_config import read_virttrip_ini
    name_hints = _build_name_hints(day_dir)
    _vt_override = ((read_virttrip_ini(str(virttrip_root)) or {}).get("name_profile") or "").strip()
    # Read-only for rating/colour/species/files — these are the master's pictures.
    # The one exception is geotagging: "Get GPS from location" is how a picture that
    # only ever had a written place ("Helgoland") gets a coordinate at all.
    dlg = ImagePreviewDialog(
        parent=parent,
        file_list=file_list,
        source_label=str(day_dir),
        readonly=True,
        allow_geotag=parent._master_is_editable(),
        # A suggested crop is written to the master RAW's sidecar for PhotoLab to
        # act on — a real, useful write even though this read-only viewer changes
        # nothing itself. Same carve-out as geotag, gated on the same editable check.
        allow_crop=parent._master_is_editable(),
        crop_hints=crop_hints,
        name_hints=name_hints,
        name_profile=effective_profile(_vt_override),
    )
    dlg.exec()
    # The viewer can geotag (Get GPS from location, and the map it opens), and that
    # write lands in the sidecars the No-GPS column counts. Repaint on the way out so
    # the panel does not keep quoting the count from before the viewer was opened.
    parent.refresh_gps_counts("virttrip")


def _gps_from_sidecars(src_by_upn: dict, stats: dict | None = None) -> set:
    """{UPN} for those whose GPS source now carries a coordinate. The source is the
    XMP sidecar where there is one, else the JPG itself (a JPG-only picture keeps its
    GPS in the file, not in a sidecar we never made).

    Reuses the day viewer's own cache-first reader, so this is the SAME answer the
    map gives — no second opinion about what "has GPS" means. Unchanged sources are
    served from the per-folder cache; only ones whose mtime moved cost an exiftool
    read, and that is one batched call, not one per file."""
    if not src_by_upn:
        return set()
    from gui.sidecar_provider import _read_sidecar_rows, _norm
    paths = [p for p in src_by_upn.values() if p and Path(p).is_file()]
    if not paths:
        return set()
    try:
        rows, _caches = _read_sidecar_rows(paths, quiet_stats=stats)
    except Exception as e:
        logger.warning(f"No-GPS count: could not read sidecars: {e}")
        return set()
    out = set()
    for upn, p in src_by_upn.items():
        row = rows.get(_norm(p)) or {}
        if str(row.get("GPSPosition", "") or "").strip() \
                or str(row.get("GPSLatitude", "") or "").strip():
            out.add(upn)
    return out


def _count_pictures_from_csv(csv_path: Path, stats: dict | None = None):
    """Return (pictures, jpeg_files, no_gps) for one day.

    pictures = distinct UniquePictureNames (a RAW and its sidecars share one UPN,
    so they are one picture, not several files).

    no_gps is the count that says which days still need placing on the map, and it
    CANNOT be answered from this CSV alone. The CSV is a SNAPSHOT that Extract Days
    took from the master, and geotagging writes to the pictures' XMP SIDECARS — so a
    day you have just placed still reads as 93-without-GPS here, while the map shows
    them all on the island. (The master's own CSV is equally stale until a re-read;
    the sidecar is the only thing that is current.)

    So: the CSV answers "did the CAMERA record a coordinate" (that GPS lives in the
    RAW, which we never touch), and for every picture it says has none we ask the
    sidecar, which is where anything WE placed ended up. Cache-first — a day whose
    sidecars have not changed costs no exiftool call at all."""
    from gui.trip_overview import PHOTO_EXTENSIONS
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            upns = set()
            jpegs = 0
            upn_has_gps: dict = {}
            xmp_by_upn: dict = {}   # UPN -> XMP sidecar path (preferred GPS source)
            jpg_by_upn: dict = {}   # UPN -> JPG path (fallback for a JPG-only picture)
            for row in reader:
                u = (row.get("UniquePictureName") or "").strip()
                if u:
                    upns.add(u)
                ext = (row.get("WindowsExtension") or "").lower()
                if ext in JPEG_EXTS:
                    jpegs += 1
                if not u:
                    continue
                if ext == "xmp":
                    p = (row.get("WindowsFileName") or "").strip()
                    if p:
                        xmp_by_upn.setdefault(u, p)
                elif ext in PHOTO_EXTENSIONS:
                    has_gps = bool((row.get("Composite:GPSPosition") or "").strip()) \
                           or bool((row.get("EXIF:GPSLatitude") or "").strip())
                    upn_has_gps[u] = upn_has_gps.get(u, False) or has_gps
                    if ext in JPEG_EXTS:
                        p = (row.get("WindowsFileName") or "").strip()
                        if p:
                            jpg_by_upn.setdefault(u, p)

        # A geotag writes to the XMP sidecar, or — for a JPG-only picture (a phone
        # shot with no RAW, no sidecar) — straight into the JPG. Re-read from the XMP
        # when there is one, else from the JPG, so a placed JPG-only picture stops
        # being counted as missing.
        missing = [u for u, has in upn_has_gps.items() if not has]
        if missing:
            src_by_upn = {u: xmp_by_upn.get(u) or jpg_by_upn.get(u)
                          for u in missing if u in xmp_by_upn or u in jpg_by_upn}
            placed = _gps_from_sidecars(src_by_upn, stats=stats)
            for u in placed:
                upn_has_gps[u] = True

        no_gps = sum(1 for has in upn_has_gps.values() if not has)
        return len(upns), jpegs, no_gps
    except (FileNotFoundError, KeyError, OSError):
        return 0, 0, 0


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
    self.virttrip_table.setColumnCount(6)
    self.virttrip_table.setRowCount(len(day_dirs))
    self.virttrip_table.setHorizontalHeaderLabels(
        ["Date", "Pictures", "JPEG\nFiles", "GPS", "Write\nBlog", "Build\nWebsite"]
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

    no_gps_by_day: dict = {}
    logger.info(f"Checking GPS status for {len(day_dirs)} day(s)...")
    sidecar_stats: dict = {}
    for i, day_dir in enumerate(day_dirs):
        total, jpegs, no_gps = _count_pictures_from_csv(day_dir / "data_files.csv", stats=sidecar_stats)
        no_gps_by_day[day_dir.name] = no_gps

        self.virttrip_table.setItem(i, DATE_COL,  QTableWidgetItem(day_dir.name))
        self.virttrip_table.setItem(i, FILES_COL, QTableWidgetItem(str(total)))
        self.virttrip_table.setItem(i, JPGS_COL,  QTableWidgetItem(str(jpegs)))

        # GPS_COL: how many of the day's pictures still have no coordinate. A day
        # where EVERY picture is missing GPS is the one that ends up titled
        # "onbekend", because the blog takes its title from the day's places.
        if no_gps == 0:
            gps_item = QTableWidgetItem("✅")
            gps_item.setToolTip("Every picture of this day has GPS.")
        else:
            all_missing = (no_gps == total)
            gps_item = QTableWidgetItem(f"📍 {no_gps}")
            gps_item.setForeground(QColor("#b26500"))
            gps_item.setToolTip(
                (f"ALL {no_gps} pictures of this day are missing GPS — this is why the "
                 f"day has no place name.\n" if all_missing else
                 f"{no_gps} of {total} pictures have no GPS.\n")
                + "Double-click to open this day's map and place them: drag them from "
                  "the tray onto the spot, or right-click one to have a position "
                  "suggested from the photos taken just before and after it.")
        gps_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        gps_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.virttrip_table.setItem(i, GPS_COL, gps_item)

        # BLOG_COL: grey when there is nothing to show; otherwise amber for an
        # auto-written draft nobody has read, green once the text is yours.
        blog_ini = blog_dir / f"blog_{day_dir.name}.ini"
        state = blog_review_state(blog_ini)
        if jpegs == 0:
            blog_icon, blog_tooltip = "○", "No JPEG files — blog page not generated"
        elif state == BLOG_DRAFT:
            blog_icon, blog_tooltip = "🟠", ("Auto-written draft — nobody has reviewed it.\n"
                                             "Double-click to read and edit it.")
        elif state == BLOG_REVIEWED:
            blog_icon, blog_tooltip = "🟢", "Blog text reviewed — double-click to edit."
        else:
            blog_icon, blog_tooltip = "⚪", "No blog yet. Click 'Write All Blogs' to write a draft."
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

    if sidecar_stats.get("stale"):
        logger.info(f"GPS status checked for {len(day_dirs)} day(s): "
                    f"{sidecar_stats.get('cached', 0)} sidecar(s) cached, "
                    f"{sidecar_stats['stale']} read via exiftool.")
    else:
        logger.info(f"GPS status checked for {len(day_dirs)} day(s) (all cached).")

    overview_widget = QWidget()
    layout = QVBoxLayout(overview_widget)

    header_row = QHBoxLayout()
    header_label = QLabel(f"Virtual Trip: {trip_name}")
    header_label.setStyleSheet("font-weight: bold; font-size: 16px;")
    header_row.addWidget(header_label)

    drafts = sum(1 for d in day_dirs
                 if blog_review_state(blog_dir / f"blog_{d.name}.ini") == BLOG_DRAFT)
    if drafts:
        draft_label = QLabel(f"🟠 {drafts} draft blog{'s' if drafts != 1 else ''} to review")
        draft_label.setStyleSheet("color:#e65100; margin-left:10px;")
        draft_label.setToolTip(
            "Day blogs written automatically that nobody has read yet. They will be "
            "published as they are — double-click an amber row to review it.")
        header_row.addWidget(draft_label)

    missing_gps = sum(no_gps_by_day.values())
    if missing_gps:
        gps_label = QLabel(f"📍 {missing_gps} without GPS")
        gps_label.setStyleSheet("color:#b26500; margin-left:10px;")
        gps_label.setToolTip(
            "Pictures in the master with no coordinate. Double-click a day's No-GPS "
            "cell to open that day's map and place them — the write goes into the "
            "master's own sidecar, which is how the master gets cleaned up.")
        header_row.addWidget(gps_label)

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
        elif col == GPS_COL:
            # Straight to the tool that fixes it — the day's own map.
            self.open_virttrip_map([day_dirs[row]])
        elif col == BLOG_COL:
            day_num = (date.fromisoformat(day_dirs[row].name) - first_date).days + 1
            _open_blog_editor(self, day_dirs, first_date, virttrip_path,
                              jump_to_day_num=day_num)

    self.virttrip_table.cellDoubleClicked.connect(_on_cell_double_clicked)
