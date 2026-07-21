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

TAB_TITLE = "Active Album"


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
        # Rating / colour / website description are durable in the sidecar and write
        # straight to the master — an album is a window ON the master, not a copy —
        # so they are editable here too. Same editable gate. (Species re-determination
        # and the sex/age qualifier are handled separately.)
        allow_edit=parent._master_is_editable(),
        crop_hints=crop_hints,
        name_hints=name_hints,
        name_profile=effective_profile(_vt_override),
    )
    dlg.exec()
    # The viewer can geotag (Get GPS from location, and the map it opens), and that
    # write lands in the sidecars the No-GPS column counts. Repaint on the way out so
    # the panel does not keep quoting the count from before the viewer was opened.
    parent.refresh_gps_counts("virttrip")


def _count_pictures_from_csv(csv_path: Path, db=None):
    """Return (pictures, jpeg_files, no_gps) for one day.

    pictures = distinct UniquePictureNames (a RAW and its sidecars share one UPN,
    so they are one picture, not several files).

    no_gps is the count that says which days still need placing on the map. The CSV
    answers "did the CAMERA record a coordinate" (that GPS lives in the RAW). But the
    CSV is a SNAPSHOT Extract Days took from the master, and geotagging writes to the
    picture's XMP SIDECAR — so a day you have just placed would still read as missing
    here. For every picture the CSV says has none we therefore ask the MASTER's own
    pictures.GPSPosition (the UPN's truth, which a geotag now mirrors into) rather
    than re-opening each sidecar — a single indexed query instead of the per-picture
    exiftool read that froze the album switch."""
    from gui.trip_overview import PHOTO_EXTENSIONS
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            upns = set()
            jpegs = 0
            upn_has_gps: dict = {}
            for row in reader:
                u = (row.get("UniquePictureName") or "").strip()
                if u:
                    upns.add(u)
                ext = (row.get("WindowsExtension") or "").lower()
                if ext in JPEG_EXTS:
                    jpegs += 1
                if not u:
                    continue
                if ext in PHOTO_EXTENSIONS:
                    has_gps = bool((row.get("Composite:GPSPosition") or "").strip()) \
                           or bool((row.get("EXIF:GPSLatitude") or "").strip())
                    upn_has_gps[u] = upn_has_gps.get(u, False) or has_gps

        # For pictures the CSV says have no CAMERA GPS, ask the MASTER: its
        # pictures.GPSPosition is the UPN's own record of any coordinate we placed —
        # one indexed query, instead of re-opening every sidecar (that per-picture
        # sidecar read is what froze the album switch for ~30 s on a big trip). A
        # geotag mirrors straight into that column (geolocate.write_gps →
        # MasterDB.apply_gps), so it is current; a coordinate placed since the last
        # master read is folded in on the next re-read.
        missing = [u for u, has in upn_has_gps.items() if not has]
        if missing and db is not None:
            try:
                for u in db.upns_with_gps(missing):
                    upn_has_gps[u] = True
            except Exception as e:
                logger.warning(f"No-GPS count: master GPS query failed: {e}")

        no_gps = sum(1 for has in upn_has_gps.values() if not has)
        return len(upns), jpegs, no_gps
    except (FileNotFoundError, KeyError, OSError):
        return 0, 0, 0


class _OverviewBatchHost(QWidget):
    """Minimal host so BatchModuleRunner can run a per-day step straight from the
    overview's context menu. The runner expects a dialog-like widget carrying
    status_label + logger + passed_parent (the main window); the overview itself
    IS the main window, which has neither. A virttrip spec uses an empty
    statusname, so none of the trip-cell / statemachine paths fire — only the
    status text and the log box are touched. Hidden; parented to the main window
    so it (and the runner it holds) survive until the worker thread finishes."""
    def __init__(self, main_window):
        super().__init__(main_window)
        self.logger = main_window.logger
        self.passed_parent = main_window
        self.status_label = QLabel(self)
        self.hide()


def _run_day_step(main_window, module_fn, before_msg, after_msg,
                  extra_config=None, on_success=None):
    """Run one per-day album batch step (svv2/svv3/svv4) from the overview."""
    from gui.batch import BatchModuleRunner
    from gui.virttrip_process import _virttrip_spec
    spec = _virttrip_spec(module_fn, before_msg, after_msg)
    if extra_config:
        spec.batch_parms["extra_config"] = extra_config
    host = _OverviewBatchHost(main_window)
    runner = BatchModuleRunner(host, main_window)
    host._runner = runner                  # keep both alive past this return
    main_window._virttrip_day_host = host  # single slot; prior host GC'd after its run
    runner.run(spec, on_success=on_success)


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

    # No-GPS is answered from the master's own pictures.GPSPosition (the UPN's
    # truth) — a fast indexed query — instead of re-reading every picture's sidecar,
    # which used to freeze the switch for ~30 s on a big album. Open the master DB
    # once here and hand it to every day. None = no master configured → the column
    # falls back to the CSV's camera GPS only (no placed-GPS reconciliation).
    master_db = None
    try:
        _mroot = (_config.get("master", "masterbiotadatabase", fallback="")
                  or "").replace('"', "").replace("'", "").strip()
        if _mroot and Path(_mroot).is_dir():
            from db.master_db import MasterDB
            master_db = MasterDB(_mroot)
    except Exception as e:
        logger.debug(f"overview: master DB unavailable, GPS column from CSV only: {e}")

    no_gps_by_day: dict = {}
    logger.info(f"Checking GPS status for {len(day_dirs)} day(s)...")
    for i, day_dir in enumerate(day_dirs):
        total, jpegs, no_gps = _count_pictures_from_csv(day_dir / "data_files.csv", db=master_db)
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

    logger.info(f"GPS status checked for {len(day_dirs)} day(s) "
                f"({'from the master DB' if master_db is not None else 'from CSV only'}).")

    overview_widget = QWidget()
    layout = QVBoxLayout(overview_widget)

    header_row = QHBoxLayout()
    header_label = QLabel(f"Album: {trip_name}")
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
    map_btn.setToolTip("Plot every day of this album on one map (all species), "
                       "with the travel path. The pictures come from the master, so "
                       "the map only shows them — it does not change them.")
    header_row.addWidget(map_btn)

    edit_blogs_btn = QPushButton("Edit Blogs")
    edit_blogs_btn.setToolTip("Open the tabbed blog editor to edit titles, text and settings for every day.")
    write_blog_btn = QPushButton("Write All Blogs")
    write_blog_btn.setToolTip("Auto-create blog ini files and upload all day blogs.")
    build_web_btn  = QPushButton("Build Website")
    build_web_btn.setToolTip("Generate species HTML pages for this album.")
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
        menu.addSeparator()
        # After adding a render in PhotoLab, the day's data must be re-projected
        # from the master (Re-extract), then its blog re-written. The website's
        # species pages span days, so that stays a whole-album rebuild. The render
        # must already be REGISTERED in the master (Master viewer → "Add new files
        # for this picture"); these steps re-read the master, they don't scan disk.
        reextract = menu.addAction("🔄 Re-extract this day from the master")
        reblog    = menu.addAction("📝 Re-write this day's blog")
        rebuild   = menu.addAction("🌐 Rebuild website (whole album)")
        chosen = menu.exec(self.virttrip_table.viewport().mapToGlobal(pos))
        day_name = day_dirs[row].name
        if chosen == map_day:
            self.open_virttrip_map([day_dirs[row]])
        elif chosen == map_all:
            self.open_virttrip_map()
        elif chosen == reextract:
            import batch.svv2ExtractDays
            _run_day_step(
                self, batch.svv2ExtractDays.main,
                f"Re-extracting {day_name} from the master…",
                f"✅ {day_name} re-extracted from the master.",
                extra_config=[("extractconfig", "only_day", day_name)],
                on_success=lambda: populate_virttrip_overview(self))
        elif chosen == reblog:
            import batch.svv3WriteBlog
            _run_day_step(
                self, batch.svv3WriteBlog.main,
                f"Re-writing the blog for {day_name}…",
                f"✅ Blog for {day_name} rewritten and uploaded.",
                extra_config=[("blogconfig", "only_day", day_name)],
                on_success=lambda: populate_virttrip_overview(self))
        elif chosen == rebuild:
            import batch.svv4BuildWebsite
            _run_day_step(
                self, batch.svv4BuildWebsite.main,
                "Rebuilding the album website…",
                "✅ Album website rebuilt and uploaded.",
                on_success=lambda: populate_virttrip_overview(self))

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
