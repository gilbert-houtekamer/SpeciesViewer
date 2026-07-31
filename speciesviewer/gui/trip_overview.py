import csv
import logging

from fastcrc import crc32
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QAbstractItemView, QCheckBox
)
from pathlib import Path
from engine.statemachine import StateMachine, NOTHING_TO_DO_PREFIX, is_nothing_to_do
from utils.config import _config
from utils.virttrip_config import read_excluded_days, write_excluded_days

SYNC_COL = 3
STATE_COL_START = 4

# ── Trip-overview pipeline state columns ─────────────────────────────────────
# Single source of truth shared by trip_overview (rendering) and mainwindow
# (the double-click / column-run / context-menu handlers). Each column maps to
# one or more statemachine keys:
#   - "Read & Summarize" combines Read Directory (sv2) + Write Summary (sv3).
#     run_keys are listed in RUN ORDER so updateState's downstream invalidation
#     resolves correctly (set ReadDirExif first, then WritePictures).
#   - "gate" is the key used for eligibility (stateOKtorun / explainAbort) and
#     the WriteBlog no-JPGs special case; a multi-key column is ✅ only when ALL
#     its run_keys are done.
#   - "menu" is the Trip-menu DICT KEY used to look up the action to run when a
#     column header/cell is double-clicked. It must match the key in the Trip
#     menu spec (which stays unnumbered even though the display titles carry the
#     1.–5. prefixes), so it is NOT the same as the numbered "label" shown here.
STATE_COLUMNS = [
    {"label": "1 Read &\nSummarize", "menu": "Read & Summarize",
     "gate": "ReadDirExif", "run_keys": ["ReadDirExif", "WritePictures"],
     "before": "Reading and summarizing picture directory...",
     "after": "✅ Read & Summarize complete."},
    {"label": "2 Write\nBlog", "menu": "Write Blog",
     "gate": "WriteBlog", "run_keys": ["WriteBlog"],
     "before": "Create a blog entry ...",
     "after": "✅ Blog successfully created."},
    # Build Website (sv5) retired — Freeze already builds the site; Publish Day
    # (Freeze + Commit) covers it. Publish Day combines Freeze (sv6) + Write
    # Commit (sv7) — ✅ only when BOTH keys are done; gate is Freeze (the first,
    # for eligibility). Mirrors the combined "Read & Summarize" column.
    {"label": "3 Publish\nDay", "menu": "Publish Day",
     "gate": "Freeze", "run_keys": ["Freeze", "WriteCommit"],
     "before": "Publishing the day (freeze + website commit) ...",
     "after": "✅ Day published."},
]
STATE_LABELS = [c["label"] for c in STATE_COLUMNS]


# A day without JPGs can never grow a blog, so that 🚫 cell is dead (not
# clickable). The other 🚫 — a step that ran but had nothing to build — MUST stay
# clickable: add a species to the day and re-running it becomes meaningful.
NO_JPG_TOOLTIP = "No JPGs available — blog step suppressed."


def cell_is_inert(symbol, tooltip):
    """True when a trip-overview cell must not react to a click."""
    return symbol == "🚫" and tooltip == NO_JPG_TOOLTIP


def state_cell_symbol(sm, col, jpgs):
    """(symbol, tooltip) for one trip-overview state column from a StateMachine.

    A multi-key column (Read & Summarize) is ✅ only when ALL run_keys are done;
    failure and eligibility use the gate key. Mirrors the symbol rules used
    across populate / refresh so both paths stay identical."""
    keys = col["run_keys"]
    gate = col["gate"]
    write_commit_done = sm.Taskstate.get("WriteCommit", [False])[0]

    if any(k not in sm.Taskstate for k in keys):
        return "⚪", "Unknown step."

    states = [sm.Taskstate[k] for k in keys]
    all_done = all(done and (msg == "Successfully run." or is_nothing_to_do(msg))
                   for done, msg, *_ in states)
    failed = next(((done, msg) for done, msg, *_ in states
                   if (not done) and "failed" in msg.lower()), None)

    if all_done:
        # A step that ran fine but had nothing to produce (a day without any
        # recognized species) is not a ✅ — nothing was published. Show the
        # suppressed sign with the module's own explanation.
        nothing = next((msg for _done, msg, *_ in states if is_nothing_to_do(msg)), None)
        if nothing:
            return "🚫", nothing[len(NOTHING_TO_DO_PREFIX):].capitalize()
        if write_commit_done:
            return "⬜", "Step completed. Workflow is frozen after WriteCommit."
        return "✅", "Successfully run."
    if failed:
        return "❌", failed[1]
    if gate == "WriteBlog" and jpgs == 0:
        return "🚫", NO_JPG_TOOLTIP
    if sm.stateOKtorun(gate):
        return "🔘", "Click on the sphere to run this step."
    return "⚪", sm.explainAbort(gate, raiseSV=False)


def state_cell_item(sm, col, jpgs):
    """state_cell_symbol wrapped in a centered QTableWidgetItem."""
    symbol, tooltip = state_cell_symbol(sm, col, jpgs)
    item = QTableWidgetItem(symbol)
    item.setToolTip(tooltip)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    if cell_is_inert(symbol, tooltip):
        item.setFlags(Qt.ItemFlag.NoItemFlags)
    return item

# Extensions the image preview dialog considers "photo files" — matches the
# affirmative filter in ImagePreviewDialog.discover_images. _day_health uses
# the same set so its no-GPS count aligns with the dialog's view (PNG
# screenshots and other non-image rows in data_files.csv are ignored).
PHOTO_EXTENSIONS = {"orf", "nef", "cr2", "cr3", "arw", "rw2", "raf", "dng",
                    "heic", "jpg", "jpeg"}

# Families whose location must be hidden on the public site (GPS stripped from
# the website copy). Keep in sync with engine/websupport.py OBSCURE_FAMILIES.
# A day whose only special status is such "obscured" species is marked with a
# lock 🔒 rather than a warning ⚠️ (which is reserved for real species typos).
OBSCURE_FAMILIES = {"orchidaceae"}


def _verified_crc_set(trip_root):
    """crc32 keys whose translation a human has verified, read straight from the
    per-trip cache (NOT the GUI's master translator) so the count reflects THIS
    trip. Matches CachedTranslate.getCRC32 (crc32.aixm of the utf-8 text)."""
    cachefile = trip_root / "admin" / "TransCache.csv"
    verified = set()
    if not cachefile.exists():
        return verified
    try:
        with open(cachefile, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f, delimiter="^")
            next(reader, None)  # header
            for row in reader:
                if len(row) > 3 and row[0] and row[3] == "1":
                    verified.add(int(row[0]))
    except Exception:
        pass
    return verified


def _has_supported_file(files_cell):
    """True if a data_pictures.csv 'Files' cell (e.g. "['a.HEIC', 'a_DxO.jpg']")
    lists at least one supported photo file — excludes PNG-only screenshots."""
    for fn in files_cell.strip("[]").replace("'", "").replace('"', "").split(","):
        fn = fn.strip()
        if "." in fn and fn.rsplit(".", 1)[-1].lower() in PHOTO_EXTENSIONS:
            return True
    return False


def _translation_counts(review_day_path, verified_crcs):
    """(total, unverified) distinct Headline/Description texts for the day, read
    from data_pictures.csv — the per-UPN RESOLVED text the panel validates and
    the blog renders (NOT data_files.csv, whose per-file rows can hold divergent
    variants and over-count). None if data_pictures.csv is absent. Normalization
    mirrors the render (Description '#'-stripped); PNG-only UPNs are excluded."""
    pics_csv = review_day_path / "data_pictures.csv"
    if not pics_csv.exists():
        return None
    texts = set()
    try:
        with open(pics_csv, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                if not _has_supported_file(row.get("Files") or ""):
                    continue
                h = row.get("Headline") or ""
                if h != "":
                    texts.add(h)
                d = row.get("Description") or ""
                if d != "":
                    if d[0:1] == "#":
                        d = d[1:]
                    texts.add(d)
    except Exception:
        return None
    unverified = sum(1 for t in texts
                     if crc32.aixm(t.encode("utf-8")) not in verified_crcs)
    return len(texts), unverified


def _format_translations_item(review_day_path, verified_crcs):
    """Trip-overview cell: a sphere showing the day's translation-validation
    state — 🟢 all verified, 🔵 at least one pending, blank when there are no
    translatable texts or Read Directory hasn't run. Count is in the tooltip."""
    counts = _translation_counts(review_day_path, verified_crcs)
    if counts is None:
        text, tip = "", "Write Summary not yet run for this day."
    else:
        total, unverified = counts
        if total == 0:
            text, tip = "", "No texts to translate on this day."
        elif unverified == 0:
            text, tip = "🟢", f"All {total} translation{'s' if total != 1 else ''} verified."
        else:
            text, tip = "🔵", (f"{unverified} of {total} translation"
                               f"{'s' if total != 1 else ''} still need validation.")
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    item.setToolTip(tip)
    return item


def _count_pictures(day_path):
    """Distinct UniquePictureNames among the day folder's own picture files
    (top level only) — the picture count, matching what PhotoLab shows. A RAW
    and its XMP sidecar / DxO render share one UPN, so they count once (not as
    separate files)."""
    from engine.filecsv import deriveUniquePictureName
    from engine.trip_delete_scan import PICTURE_EXTS
    # Count only primary images, not sidecars: an orphan .xmp/.dop is not a
    # picture, and a present one shares its RAW's UPN anyway.
    primary = PICTURE_EXTS - {".xmp", ".dop"}
    upns = set()
    for f in day_path.glob("*"):
        if f.is_file() and f.suffix.lower() in primary:
            u = (deriveUniquePictureName(f.name) or "").strip()
            if u:
                upns.add(u)
    return len(upns)


def _format_total_files_item(day_path, review_root):
    """Build the Pictures cell with health markers and tooltip. Re-used by
    refresh_day_health so the trip-overview row updates after the image
    preview dialog fixes species/GPS issues. Shows the picture (UPN) count,
    not the raw file count."""
    total = _count_pictures(day_path)
    health = _day_health(review_root / day_path.name)
    if health is None:
        text = str(total)
        tooltip = "Read Directory not yet run for this day."
    else:
        warnings, no_gps, obscured = health
        # Priority: a real species typo (⚠️) outranks an obscured-location
        # species (🔒); a clean day is ✅. Obscured species are intentional
        # (location hidden on the website), so they must not look like an error.
        if warnings > 0:
            species_mark = "⚠️"
            species_tip = f"{warnings} species typo{'s' if warnings != 1 else ''}"
        elif obscured > 0:
            species_mark = "🔒"
            species_tip = f"{obscured} obscured-location species (GPS hidden on the website)"
        else:
            species_mark = "✅"
            species_tip = "All species OK"
        gps_mark = "📍" if no_gps == 0 else "❓"
        text = f"{total}  {species_mark} {gps_mark}"
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
        logger.debug(f"refresh_day_health: no trip_table on main_window for {day_name}")
        return
    try:
        from utils.trip_identity import resolve_trip_picture_root
        image_root = Path(resolve_trip_picture_root(
            _config.get("trip", "tripbiotadatabase", fallback=""), kind="trip")
            or Path(_config.get("project", "picturedirectory")).parent)
        trip_root = Path(_config.get("trip", "tripbiotadatabase"))
    except Exception as e:
        logger.debug(f"refresh_day_health: config lookup failed: {e}")
        return
    review_root = trip_root / "metadata" / "review"
    day_path = image_root / day_name
    if not day_path.exists():
        logger.debug(f"refresh_day_health: day folder doesn't exist: {day_path}")
        return
    for row in range(table.rowCount()):
        cell = table.item(row, 0)
        if cell and cell.text() == day_name:
            new_item = _format_total_files_item(day_path, review_root)
            table.setItem(row, 1, new_item)
            logger.debug(
                f"refresh_day_health: row {row} for {day_name} → "
                f"{new_item.text()!r}"
            )
            return
    logger.debug(f"refresh_day_health: no matching row for {day_name}")


def _day_health(review_day_path):
    """Return (warnings_count, no_gps_count, obscured_count) for the day.

    GPS is read from data_files.csv (same source the image preview dialog
    uses, so the trip-overview marker stays consistent with the dialog's
    "clean day" determination) and aggregated by UniquePictureName — a
    UPN has GPS if ANY of its file rows (ORF / XMP / JPG) has it.

    Warnings come from data_pictures.csv (errormessage is a UPN-only column).
    Obscured = pictures whose TargetPath is in an OBSCURE_FAMILY (location
    hidden on the website); surfaced as a 🔒 marker, not a warning.

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
        logger.debug(
            f"_day_health: {review_day_path.name} has {no_gps} UPN(s) "
            f"without GPS in data_files.csv. Sample: {sample}"
        )

    warnings = 0
    obscured = 0
    if pictures_csv.exists():
        try:
            with open(pictures_csv, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    if (row.get("errormessage") or "").strip():
                        warnings += 1
                    tp = (row.get("TargetPath") or "").lower()
                    if any(fam in tp for fam in OBSCURE_FAMILIES):
                        obscured += 1
        except Exception:
            pass
    return warnings, no_gps, obscured

def populate_trip_overview(self):
    self.update_trip_label()
    # Keep the left Trips panel's active-trip ▶ marker in sync after any trip
    # switch (panel click, create/select-trip dialog, add-to-master, …).
    panel = getattr(self, "trip_panel", None)
    if panel is not None:
        try:
            panel._refresh_highlights()
        except Exception:
            pass
    self.remove_existing_overview_tab()
    # No active trip (e.g. just after retiring one) is a legitimate empty state,
    # not an error — the tab is cleared above; return quietly instead of logging
    # the "Invalid directory paths." warning meant for a genuinely broken trip.
    if not _config.get("trip", "tripbiotadatabase", fallback="").strip():
        return
    # Get image and statemachine directories. WP8: the trip's picture root comes
    # from its explicit picture_root when present, else the active-day parent.
    from utils.trip_identity import resolve_trip_picture_root
    image_root = Path(resolve_trip_picture_root(
        _config.get("trip", "tripbiotadatabase", fallback=""), kind="trip")
        or Path(_config.get("project", "picturedirectory")).parent)
    trip_root = Path(_config.get("trip", "tripbiotadatabase"))
    review_root = trip_root / "metadata" / "review"

    if not image_root.exists() or not review_root.exists():
        self.logger.warning("Invalid directory paths.")
        return

    trip_name = image_root.name

    # Get all image day folders. Skip dot-folders (e.g. .preview_cache) — they are
    # tool caches, not trip days, and the EXIF scan already prunes them too.
    image_days = sorted([d for d in image_root.iterdir()
                         if d.is_dir() and not d.name.startswith(".")], key=lambda p: p.name)
    self._trip_overview_day_snapshot = tuple(d.name for d in image_days)

    def count_files(path):
        all_files = list(path.glob("*"))
        jpg_files = [f for f in all_files if f.suffix.lower() == ".jpg"]
        return len(all_files), len(jpg_files)

    # Pipeline state columns come from the shared STATE_COLUMNS registry.
    # "Read & Summarize" is one column tracking both sv2 (Read Directory) and
    # sv3 (Write Summary) — see STATE_COLUMNS at the top of this module.
    self.state_labels = STATE_LABELS
    self.state_keys = [c["gate"] for c in STATE_COLUMNS]

    expected_messages = {
        "sv1makerepository": "MakeRepository succeeded.",
        "sv2readdirexif": "Successfully run.",
        "sv3writepictures": "Successfully run.",
        "sv4writeblog": "Successfully run.",
        "sv5buildwebsite": "Successfully run.",
        "sv6freeze": "Successfully run.",
        "sv7writecommit": "Successfully run."
    }

    # Translations status is read once per trip from the per-trip cache.
    verified_crcs = _verified_crc_set(trip_root)
    translations_col = STATE_COL_START + len(self.state_labels) + 1
    # In-Blog checkbox column: append it AFTER Translations so the state-column
    # indices and the double-click handlers that map column -> step stay
    # untouched. Unticking a day (e.g. the 'iphone' folder) keeps it out of the
    # blog — sv4WriteBlog then drops it from the index, like an empty day.
    inblog_col = STATE_COL_START + len(self.state_labels) + 2
    # The "Write Blog" state cell to grey out immediately when a day is excluded.
    blog_state_col = STATE_COL_START + next(
        (j for j, c in enumerate(STATE_COLUMNS) if c["gate"] == "WriteBlog"), 1)
    excluded = read_excluded_days(trip_root)

    self.trip_table = QTableWidget()
    # +1 for Progress, +1 for Translations, +1 for the trailing In-Blog column.
    # Translations and In-Blog are LAST on purpose so the state-column indices
    # (and the double-click handlers that map column -> step) are untouched.
    self.trip_table.setColumnCount(STATE_COL_START + len(self.state_labels) + 3)
    self.trip_table.setRowCount(len(image_days))
    self.trip_table.setHorizontalHeaderLabels(
        ["Day", "Pictures", "JPEG\nFiles", "Sync\nSidecars"] + self.state_labels
        + ["Progress", "Trans-\nlations", "In\nBlog"]
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
    # Pictures and JPEG Files auto-size to their content (Pictures
    # carries "N ✅ 📍" markers that don't fit a stretched column).
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(translations_col, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(inblog_col, QHeaderView.ResizeMode.ResizeToContents)

    def _paint_blog_state_excluded(row, day_name):
        """Grey the day's Write-Blog cell to show it is excluded (or leave the
        live state when re-included; a refresh restores the real symbol)."""
        if day_name in excluded:
            cell = QTableWidgetItem("🚫")
            cell.setToolTip("Excluded from the blog ('In Blog' unticked) — "
                            "no blog page is written or published for this day.")
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.trip_table.setItem(row, blog_state_col, cell)

    def _on_trip_inblog_toggled(row, checked, day_name):
        if checked:
            excluded.discard(day_name)
        else:
            excluded.add(day_name)
        write_excluded_days(trip_root, excluded)
        _paint_blog_state_excluded(row, day_name)

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

        # Loop through each state column (Read & Summarize is one column
        # tracking sv2+sv3 — see STATE_COLUMNS / state_cell_item).
        for j, col in enumerate(STATE_COLUMNS):
            self.trip_table.setItem(i, STATE_COL_START + j,
                                    state_cell_item(sm, col, jpgs))

        # Determine progress summary
        summary_item = self.get_progress_summary_item(sm,  review_root / day.name)
        self.trip_table.setItem(i, STATE_COL_START + len(self.state_labels), summary_item)

        # Translations column: texts on this day still needing validation
        self.trip_table.setItem(i, translations_col,
                                _format_translations_item(review_root / day.name, verified_crcs))

        # In-Blog column (last): a checkbox to keep a day out of the blog. A cell
        # WIDGET (not a checkable item) so the tick sits centered and no stray
        # cell-editor box appears on select. Mirrors the album overview.
        inblog_cb = QCheckBox()
        inblog_cb.setChecked(day.name not in excluded)
        inblog_cb.setToolTip(
            "Include this day in the blog.\nUntick to keep a day out (e.g. the "
            "'iphone' folder) — the blog step then skips it and it drops from the "
            "index.")
        inblog_cb.toggled.connect(
            lambda checked, r=i, dn=day.name: _on_trip_inblog_toggled(r, checked, dn))
        inblog_holder = QWidget()
        _inblog_lay = QHBoxLayout(inblog_holder)
        _inblog_lay.setContentsMargins(0, 0, 0, 0)
        _inblog_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _inblog_lay.addWidget(inblog_cb)
        self.trip_table.setCellWidget(i, inblog_col, inblog_holder)

        # If already excluded, reflect it in the Write-Blog cell now.
        _paint_blog_state_excluded(i, day.name)

    # Layout
    overview_widget = QWidget()
    layout = QVBoxLayout()
    header_row = QHBoxLayout()
    header_label = QLabel(f"Trip Overview: {trip_name}")
    header_label.setStyleSheet("font-weight: bold; font-size: 16px;")
    header_row.addWidget(header_label)
    header_row.addStretch()
    trip_map_btn = QPushButton("🗺 Map whole trip")
    trip_map_btn.setToolTip("Plot every day of this trip on one map (all species), "
                            "with the travel path")
    trip_map_btn.clicked.connect(self.open_trip_map)
    header_row.addWidget(trip_map_btn)
    layout.addLayout(header_row)
    layout.addWidget(self.trip_table)
    overview_widget.setLayout(layout)

    self.yellow_tabs.addTab(overview_widget, "Active Trip")
    self.yellow_tabs.setCurrentWidget(overview_widget)

    self.trip_table.cellDoubleClicked.connect(self.handle_trip_table_double_click)
    self.trip_table.verticalHeader().sectionDoubleClicked.connect(self.handle_row_rerun)
    self.trip_table.horizontalHeader().sectionDoubleClicked.connect(self.handle_column_run_all)
    self.trip_table.customContextMenuRequested.connect(self.handle_trip_table_context_menu)
    # Keep Trip menu in sync with the selected row
    self.trip_table.currentCellChanged.connect(self.handle_trip_table_current_changed)
