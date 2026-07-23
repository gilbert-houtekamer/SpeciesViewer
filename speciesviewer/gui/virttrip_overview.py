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
    QTableWidget, QTableWidgetItem, QMenu, QCheckBox,
    QHeaderView, QAbstractItemView,
)

from utils.config import _config
from utils.virttrip_config import (read_virttrip_ini, blog_review_state,
                                   read_excluded_days, write_excluded_days,
                                   BLOG_DRAFT, BLOG_REVIEWED)

JPEG_EXTS = {"jpg", "jpeg"}

DATE_COL   = 0
FILES_COL  = 1
JPGS_COL   = 2
GPS_COL    = 3
INBLOG_COL = 4
BLOG_COL   = 5
WEB_COL    = 6

TAB_TITLE = "Active Album"


def _open_master_db():
    """The master DB if one is configured and present, else None (callers fall back
    to the legacy review CSVs). Cheap — the DB is a warm global cache."""
    try:
        mroot = (_config.get("master", "masterbiotadatabase", fallback="")
                 or "").replace('"', "").replace("'", "").strip()
        if mroot and Path(mroot).is_dir():
            from db.master_db import MasterDB
            return MasterDB(mroot)
    except Exception as e:
        logger.debug(f"album: master DB unavailable: {e}")
    return None


def _day_data_from_master(db, day_label: str) -> tuple:
    """(picture rows, file rows) for ONE day straight from the master by date — the
    day viewer's replacement for reading review/<day>/data_*.csv. An album day IS the
    master's pictures whose camera DateTimeOriginal falls on that day, so this is a
    live query, not a snapshot read."""
    by_day = db.pictures_in_daterange(day_label, day_label)
    pics = by_day.get(day_label, [])
    files = list(db.files_for_upns([p["UniquePictureName"] for p in pics]).values())
    return pics, files


def _area(dims: str) -> int:
    try:
        w, h = re.split(r"[x ]+", (dims or "").strip())[:2]
        return int(w) * int(h)
    except (ValueError, IndexError):
        return 0


def _file_list_from_files(file_rows, jpeg_only: bool) -> list:
    """Full Path per file row; when jpeg_only, only .jpg/.jpeg. Files live at their
    master location — no copies are made."""
    paths = []
    for row in file_rows:
        ext = (row.get("WindowsExtension") or "").lower()
        if jpeg_only and ext not in JPEG_EXTS:
            continue
        directory = row.get("File:Directory", "")
        filename  = row.get("File:FileName",  "")
        if directory and filename:
            paths.append(Path(directory) / filename)
    return paths


def _crop_hints_from_files(file_rows) -> dict:
    """{jpg full path -> True when it's (probably) a crop}: its ImageSize is much
    smaller than the biggest (RAW) ImageSize sharing its UPN. Same size-only cue as
    gui.picture_provider.jpg_is_crop_by_size; a JPG-only UPN stays False."""
    from gui.picture_provider import jpg_is_crop_by_size
    native: dict[str, str] = {}                # upn -> biggest ImageSize seen (the RAW)
    jpgs: list[tuple[str, str, str]] = []       # (full path, upn, this JPG's ImageSize)
    for row in file_rows:
        upn = (row.get("UniquePictureName") or "").strip()
        dims = row.get("ImageSize", "")
        if _area(dims) > _area(native.get(upn, "")):
            native[upn] = dims
        if (row.get("WindowsExtension") or "").lower() in JPEG_EXTS:
            directory = row.get("File:Directory", "")
            filename  = row.get("File:FileName",  "")
            if directory and filename:
                jpgs.append((str(Path(directory) / filename), upn, dims))
    return {path: jpg_is_crop_by_size(native.get(upn, ""), jpg_dims)
            for path, upn, jpg_dims in jpgs}


def _name_hints_from(pic_rows, file_rows) -> dict:
    """{file full path -> its species-name row}, so the metadata-less viewer can caption
    in the chosen language instead of the scientific folder name. Names from the picture
    rows (per UPN); the path->UPN link from the file rows."""
    pics: dict[str, dict] = {}
    for r in pic_rows:
        upn = (r.get("UniquePictureName") or "").strip()
        if upn:
            pics[upn] = {k: r.get(k, "") for k in
                         ("NLSoortNaam", "EngSoortNaam", "WetSoortNaam")}
    hints: dict[str, dict] = {}
    for row in file_rows:
        upn = (row.get("UniquePictureName") or "").strip()
        directory = row.get("File:Directory", "")
        filename  = row.get("File:FileName",  "")
        if upn in pics and directory and filename:
            hints[str(Path(directory) / filename)] = pics[upn]
    return hints


def _read_csv_rows(csv_path: Path) -> list:
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f, delimiter=";"))
    except (FileNotFoundError, OSError, csv.Error):
        return []


def _build_file_list(csv_path: Path, jpeg_only: bool) -> list:
    """Legacy CSV path: full Path per file row in data_files.csv (see
    _file_list_from_files). Used only when no master is configured."""
    return _file_list_from_files(_read_csv_rows(csv_path), jpeg_only)


def _build_crop_hints(csv_path: Path) -> dict:
    """Legacy CSV path: crop markers from data_files.csv (see _crop_hints_from_files)."""
    return _crop_hints_from_files(_read_csv_rows(csv_path))


def _build_name_hints(day_dir: Path) -> dict:
    """Legacy CSV path: name captions from the day's two CSVs (see _name_hints_from)."""
    return _name_hints_from(_read_csv_rows(day_dir / "data_pictures.csv"),
                            _read_csv_rows(day_dir / "data_files.csv"))


def _open_blog_editor(parent, day_dirs: list, first_date: date,
                      virttrip_root: Path, jump_to_day_num: int = 0):
    """Open the tabbed blog editor, optionally jumping to a specific day's tab."""
    from gui.virttrip_blog_editor import VirtTripBlogEditorDialog
    from utils.virttrip_config import read_excluded_days
    blog_dir = virttrip_root / "metadata" / "blog"
    # Days unticked in the "In Blog" column are not published, so the editor greys
    # their tab and leaves them out of its "still a draft" count — matching how the
    # overview and svv3 already treat them.
    excluded = read_excluded_days(virttrip_root)
    dlg = VirtTripBlogEditorDialog(parent, blog_dir, day_dirs, first_date,
                                   excluded_days=excluded)
    if jump_to_day_num:
        dlg.jump_to_day(jump_to_day_num)
    # Non-modal so the day's Species viewer can sit open beside it while you write
    # the blog for that day. The editor already refreshes the overview in its own
    # closeEvent, so there is nothing to do after it closes.
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    parent._blog_editor_dlg = dlg        # keep a ref (PyQt GC)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


def _open_day_viewer(parent, day_dir: Path, jpeg_only: bool, virttrip_root: Path):
    from gui.image_viewer import ImagePreviewDialog
    # Primary: the day IS the master's pictures whose camera DateTimeOriginal falls on
    # this day — a live query, no snapshot. Fallback: the legacy review/<day>/ CSVs,
    # for a machine with no master configured.
    db = _open_master_db()
    if db is not None:
        pics, files = _day_data_from_master(db, day_dir.name)
        file_list  = _file_list_from_files(files, jpeg_only)
        crop_hints = _crop_hints_from_files(files)
        name_hints = _name_hints_from(pics, files)
    else:
        file_list  = _build_file_list(day_dir / "data_files.csv", jpeg_only)
        crop_hints = _build_crop_hints(day_dir / "data_files.csv")
        name_hints = _build_name_hints(day_dir)
    if not file_list:
        return
    # The species names per JPG let the caption follow the name-language profile
    # instead of the metadata-less viewer's scientific folder name. The profile is
    # this virtual trip's override (virttrip.ini) or the global default.
    from utils.name_profile import effective_profile
    from utils.virttrip_config import read_virttrip_ini
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
    # Bind the master DB + this day's rows so the right-click card menu offers the full
    # master surface (sex/age/plumage, pin best-JPG, delete-JPG, move-to-species, add
    # files, delete) — the album's pictures ARE the master's. Editable gate handled by
    # the collection (master menu hidden on a read-only album).
    if db is not None:
        dlg.enable_master_ops(db, db.master_root, pics, files)
    # Non-modal so it can sit open beside the Blog Editor while you write that day's
    # blog. The viewer can geotag (Get GPS from location, and the map it opens), and
    # that write lands in the sidecars the No-GPS column counts — so repaint the panel
    # when the viewer closes, instead of on the way back from a blocking exec().
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    parent._day_viewer_dlg = dlg         # keep a ref (PyQt GC)
    dlg.finished.connect(lambda _=0: parent.refresh_gps_counts("virttrip"))
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


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


def _day_counts_from_query(db, fromdate: str, todate: str) -> dict:
    """Per-day (pictures, jpeg_files, no_gps) straight from the master, by date range —
    the replacement for the per-day CSV snapshot. An album is a live query over the
    master (pictures whose camera DateTimeOriginal falls in fromdate..todate), not a
    copied-out set of day CSVs, so there is exactly one copy of the truth and a picture
    removed from the master since the album was created simply stops appearing.

    Two indexed queries total (the pictures, then their files), not one CSV read per
    day. Returns {"YYYY-MM-DD": (pictures, jpeg_files, no_gps)}:
      - pictures  = distinct UPNs in range (the pictures table is per-UPN);
      - jpeg_files = jpg/jpeg file rows for those UPNs;
      - no_gps    = pictures with NO coordinate anywhere. A picture has GPS if its
        master pictures.GPSPosition is set (a placed coordinate, mirrored by apply_gps)
        OR any of its file rows carries a CAMERA coordinate (Composite:GPSPosition /
        EXIF:GPSLatitude). The picture row alone is not enough: the master doesn't
        always copy the camera's fix up to the picture level (Gran Canaria has 233
        pictures whose files hold camera GPS but whose picture row is empty), so
        checking only the picture row over-counts 'without GPS'."""
    by_day = db.pictures_in_daterange(fromdate, todate)
    all_upns = [p["UniquePictureName"] for pics in by_day.values() for p in pics]
    files = db.files_for_upns(all_upns)
    jpeg_by_upn: dict = {}
    file_gps_upns: set = set()          # UPNs with a camera coordinate in a file row
    for r in files.values():
        u = r.get("UniquePictureName", "")
        if not u:
            continue
        if (r.get("WindowsExtension") or "").lower() in JPEG_EXTS:
            jpeg_by_upn[u] = jpeg_by_upn.get(u, 0) + 1
        if (r.get("Composite:GPSPosition") or "").strip() or (r.get("EXIF:GPSLatitude") or "").strip():
            file_gps_upns.add(u)
    counts: dict = {}
    for day, pics in by_day.items():
        upns = [p["UniquePictureName"] for p in pics]
        jpegs = sum(jpeg_by_upn.get(u, 0) for u in upns)
        no_gps = sum(1 for p in pics
                     if not (p.get("GPSPosition") or "").strip()
                     and p["UniquePictureName"] not in file_gps_upns)
        counts[day] = (len(pics), jpegs, no_gps)
    return counts


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

    trip_data = read_virttrip_ini(virttrip_root) or {}
    trip_name = trip_data.get("trip_name", virttrip_path.name) or virttrip_path.name
    fromdate  = (trip_data.get("fromdate") or "").strip()
    todate    = (trip_data.get("todate")   or "").strip()

    # Days the user has marked as not worth a blog page ("In Blog" unticked). A
    # mutable set: the checkbox handler updates it and re-persists.
    excluded = read_excluded_days(virttrip_path)

    # No-GPS, and now the whole day list, come from the master's own rows — a fast
    # indexed query by the album's date range — instead of re-reading every picture's
    # sidecar (which froze the switch for ~30 s on a big album). None = no master
    # configured → fall back to the legacy per-day review CSVs below.
    master_db = _open_master_db()

    # Primary source: the live master date-range query (an album is a window ON the
    # master, not a copy). Fallback: the legacy per-day review CSV snapshot, for a
    # machine with no master configured.
    _DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    query_counts = None
    if master_db is not None and fromdate and todate:
        try:
            query_counts = _day_counts_from_query(master_db, fromdate, todate)
        except Exception as e:
            logger.warning(f"overview: master date-range query failed, using CSV: {e}")

    if query_counts is not None:
        day_names = sorted(query_counts)
        day_dirs  = [review_root / n for n in day_names]
        def _day_counts(day_dir):
            return query_counts[day_dir.name]
    else:
        # Only include YYYY-MM-DD directories; the 'frozen/' subdir created by
        # svv4BuildWebsite must not appear as a fake day row.
        if not review_root.is_dir():
            return
        day_dirs = sorted(
            [d for d in review_root.iterdir() if d.is_dir() and _DATE_RE.match(d.name)],
            key=lambda p: p.name,
        )
        def _day_counts(day_dir):
            return _count_pictures_from_csv(day_dir / "data_files.csv", db=master_db)

    if not day_dirs:
        return

    self.virttrip_table = QTableWidget()
    self.virttrip_table.setColumnCount(7)
    self.virttrip_table.setRowCount(len(day_dirs))
    self.virttrip_table.setHorizontalHeaderLabels(
        ["Date", "Pictures", "JPEG\nFiles", "GPS", "In\nBlog", "Write\nBlog", "Build\nWebsite"]
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

    def _set_blog_cell(row, day_name, jpegs):
        """(Re)paint the Write-Blog cell. A day the user excluded from the blog is
        grey — no page is written or published for it — regardless of any draft it
        may still carry, so it stops reading as a to-do."""
        blog_ini = blog_dir / f"blog_{day_name}.ini"
        if day_name in excluded:
            icon, tip = "○", ("Excluded from the blog — no page is written or "
                              "published for this day.\nTick 'In Blog' to include it.")
        elif jpegs == 0:
            icon, tip = "○", "No JPEG files — blog page not generated"
        else:
            state = blog_review_state(blog_ini)
            if state == BLOG_DRAFT:
                icon, tip = "🟠", ("Auto-written draft — nobody has reviewed it.\n"
                                   "Double-click to read and edit it.")
            elif state == BLOG_REVIEWED:
                icon, tip = "🟢", "Blog text reviewed — double-click to edit."
            else:
                icon, tip = "⚪", "No blog yet. Click 'Write All Blogs' to write a draft."
        item = QTableWidgetItem(icon)
        item.setToolTip(tip)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.virttrip_table.setItem(row, BLOG_COL, item)

    def _on_inblog_toggled(row, checked):
        """Persist the In-Blog choice and, without a full re-query, grey/restore the
        day's Write-Blog cell and refresh the 'drafts to review' count."""
        day_name = day_dirs[row].name
        if checked:
            excluded.discard(day_name)
        else:
            excluded.add(day_name)
        write_excluded_days(virttrip_path, excluded)
        jpgs_item = self.virttrip_table.item(row, JPGS_COL)
        try:
            jpegs = int(jpgs_item.text()) if jpgs_item else 0
        except ValueError:
            jpegs = 0
        _set_blog_cell(row, day_name, jpegs)
        _update_draft_label()

    # Website is built trip-wide (not per-day); detect from the all-species summary page.
    web_built = (virttrip_path / "web" / "summaries" / "all biota species-nl.html").exists()

    no_gps_by_day: dict = {}
    logger.info(f"Checking GPS status for {len(day_dirs)} day(s)...")
    for i, day_dir in enumerate(day_dirs):
        total, jpegs, no_gps = _day_counts(day_dir)
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

        # INBLOG_COL: a checkbox to keep a thin day (e.g. a single photo) out of
        # the blog. A cell WIDGET (not a checkable item) so the tick sits centered
        # like the ✅ columns and no stray cell-editor box appears on select.
        inblog_cb = QCheckBox()
        inblog_cb.setChecked(day_dir.name not in excluded)
        inblog_cb.setToolTip(
            "Include this day in the blog.\nUntick to leave a thin day (e.g. a single "
            "photo) out — 'Write All Blogs' then skips it and it drops from the index.")
        inblog_cb.toggled.connect(lambda checked, r=i: _on_inblog_toggled(r, checked))
        inblog_holder = QWidget()
        _inblog_lay = QHBoxLayout(inblog_holder)
        _inblog_lay.setContentsMargins(0, 0, 0, 0)
        _inblog_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _inblog_lay.addWidget(inblog_cb)
        self.virttrip_table.setCellWidget(i, INBLOG_COL, inblog_holder)

        # BLOG_COL: grey when there is nothing to show (or the day is excluded);
        # otherwise amber for an auto-written draft nobody has read, green once yours.
        _set_blog_cell(i, day_dir.name, jpegs)

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
                f"({'from the master date-range query' if query_counts is not None else 'from CSV only'}).")

    overview_widget = QWidget()
    layout = QVBoxLayout(overview_widget)

    header_row = QHBoxLayout()
    header_label = QLabel(f"Album: {trip_name}")
    header_label.setStyleSheet("font-weight: bold; font-size: 16px;")
    header_row.addWidget(header_label)

    # Excluded days never count as drafts to review (they are not published at all).
    draft_label = QLabel("")
    draft_label.setStyleSheet("color:#e65100; margin-left:10px;")
    draft_label.setToolTip(
        "Day blogs written automatically that nobody has read yet. They will be "
        "published as they are — double-click an amber row to review it.")

    def _update_draft_label():
        n = sum(1 for d in day_dirs
                if d.name not in excluded
                and blog_review_state(blog_dir / f"blog_{d.name}.ini") == BLOG_DRAFT)
        draft_label.setText(f"🟠 {n} draft blog{'s' if n != 1 else ''} to review")
        draft_label.setVisible(n > 0)

    _update_draft_label()
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
        # The blog and website steps now read the master LIVE by date range — no
        # extract step. After registering a render in the master (a day card's
        # right-click → "Add new renders to the master"), re-writing the day's blog
        # picks it up straight away.
        reblog    = menu.addAction("📝 Re-write this day's blog")
        rebuild   = menu.addAction("🌐 Rebuild website (whole album)")
        chosen = menu.exec(self.virttrip_table.viewport().mapToGlobal(pos))
        day_name = day_dirs[row].name
        if chosen == map_day:
            self.open_virttrip_map([day_dirs[row]])
        elif chosen == map_all:
            self.open_virttrip_map()
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
