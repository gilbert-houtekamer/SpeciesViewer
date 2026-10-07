import json
import os
import logging
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout, QSpinBox, QPushButton, QSizePolicy,
    QLabel, QMessageBox, QHBoxLayout, QProgressDialog, QStyle, QLineEdit)
from PyQt6.QtCore import Qt, QTimer
from widgets.trip_widgets import DirectoryFieldQt
from engine.filepicture import FilePicture
from gui.species_editor import SpeciesInfoDialog
from gui.window_state import restore_window, save_window
from gui.image_loader import AsyncImageLoader
from gui.preview_provider import PreviewCollection, FileListPreviewCollection, preview_cache_dir_for
from gui.master_ops import MasterCardMenuMixin
from gui.rating_editor import PictureCard
from utils.raw_extensions import RAW_OR_HEIC, VIDEO
from utils.config import get_setting, set_setting, get_int_setting

logger = logging.getLogger("speciesviewer")

# "Start with" subsets for the culling grid: which colour labels to load on
# open, so a resumed cull never pays the full-day build (see docs/plans/
# culling_grid_performance.md). "review" = everything not yet marked for
# deletion (blue/purple); "all" = no colour filter.
_START_SUBSETS = {
    "unrated": {"Grey"},
    "review":  {"Grey", "Red", "Yellow", "Green"},
    "all":     set(),
}
_START_SUBSET_LABEL = {"unrated": "Unrated", "review": "To review", "all": "All"}

# Images-per-row is a GLOBAL setting (every day opens the same), so the card
# width — and therefore the cached thumbnail width — is stable across sessions.
_GRID_COLUMNS_DEFAULT = 5

def ensure_path(obj):
    return Path(obj) if isinstance(obj, str) else obj


class ColorFilterBar(QWidget):
    def __init__(self, on_filter_changed):
        super().__init__()
        self.selected_colors = set()
        self.on_filter_changed = on_filter_changed

        layout = QHBoxLayout()
        layout.setSpacing(4)  # Controls space *between* buttons
        layout.setContentsMargins(0, 0, 0, 0)  # Removes extra padding around the layout
        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.colors = {
            "Red": "#FF4C4C",
            "Yellow": "#FFD700",
            "Green": "#32CD32",
            "Blue": "#1E90FF",
            "Purple": "#9370DB",
            "Grey": "#A9A9A9"
        }

        self.buttons = {}
        for name, hex_color in self.colors.items():
            btn = QPushButton()
            btn.setFixedSize(16, 16)  # Slightly smaller for tighter layout
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            btn.setCheckable(True)
            btn.setToolTip(f"Filter by {name}\n(g = Red+Yellow+Green in one go, h = clear)")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    border: 2px solid {hex_color};
                    border-radius: 12px;
                }}
                QPushButton:checked {{
                    background-color: {hex_color};
                    border: 2px solid white;
                }}
            """)
            btn.clicked.connect(lambda _, n=name: self.toggle_color(n))
            layout.addWidget(btn)
            self.buttons[name] = btn

    def toggle_color(self, color_name):
        btn = self.buttons[color_name]
        if btn.isChecked():
            self.selected_colors.add(color_name)
        else:
            self.selected_colors.discard(color_name)

        active_colors = self.selected_colors if self.selected_colors else None
        self.on_filter_changed(active_colors)

    def set_selection(self, colors):
        """Replace the whole colour selection and notify ONCE. A keyboard
        shortcut that turns on Red+Yellow+Green must rebuild the grid a single
        time, not once per colour. `colors` = iterable of names, or None to clear."""
        wanted = {c for c in (colors or ()) if c in self.buttons}
        self.selected_colors = set(wanted)
        for name, btn in self.buttons.items():
            btn.setChecked(name in wanted)
        self.on_filter_changed(wanted or None)

    def reflect(self, colors):
        """Sync the visual selection to `colors` WITHOUT notifying — for setting
        the bar's state from code that will trigger its own rebuild (the start
        subset applied at open, before the grid exists)."""
        wanted = {c for c in (colors or ()) if c in self.buttons}
        self.selected_colors = set(wanted)
        for name, btn in self.buttons.items():
            btn.setChecked(name in wanted)


class ImagePreviewDialog(MasterCardMenuMixin, QDialog):
    """Per-picture (UPN) contact sheet, rendered from a picture-provider
    collection (the same seam the Rating Editor uses). Three modes:
      - day folder with metadata  → PreviewCollection (fpr, one card per UPN)
      - virtual-trip file_list     → FileListPreviewCollection (one card per file)
      - metadata-less day folder   → FileListPreviewCollection over discovered files
    Each card is a shared PictureCard; the badge row (status / 📝 / 📍) and the
    warnings / missing-GPS filters only apply in the fpr mode.
    """

    # A real trip cull may trash a JPG that isn't published in a blog
    # (_batch_delete_color relaxes the never-trash-a-JPG rule). Subclasses over a
    # scratch/ephemeral trip turn this off.
    _allow_trip_jpg_delete = True

    def __init__(self, parent=None, image_dir="", trip_root_dir="", mode="jpg",
                 file_list=None, cache_root=None, source_label=None, collection=None,
                 readonly=False, start_species=False, start_no_gps=False, allow_geotag=False,
                 allow_crop=False, allow_edit=False, crop_hints=None, name_hints=None,
                 name_profile=""):
        super().__init__(parent)
        # {full JPG path -> True} for the "#" crop marker in the metadata-less
        # (file-list) mode, precomputed by the caller from the day CSV sizes since
        # a flat path list carries no RAW-vs-JPG dimensions of its own.
        self._crop_hints = crop_hints or {}
        # {full JPG path -> {NLSoortNaam,...}} + the name-language profile, so the
        # metadata-less caption can follow the preference instead of the sci folder.
        self._name_hints = name_hints or {}
        self._name_profile = name_profile or ""
        # Own taskbar button — a minimised preview window must be findable.
        # ORing Qt.WindowType.Window onto a QDialog's existing flags does
        # NOTHING (Qt.WindowType.Dialog is defined as 0x2 | Window, so the
        # bit is already set) — the type has to be replaced outright to
        # actually drop the Dialog type and get independent taskbar/Alt-Tab
        # treatment on Windows.
        self.setWindowFlags(Qt.WindowType.Window
                            | Qt.WindowType.WindowTitleHint
                            | Qt.WindowType.WindowSystemMenuHint
                            | Qt.WindowType.WindowMinMaxButtonsHint
                            | Qt.WindowType.WindowCloseButtonHint)
        self.mode = mode
        self.trip_root_dir = trip_root_dir
        self.main_window = parent
        self._file_list = [Path(p) if isinstance(p, str) else p for p in file_list] \
                          if file_list is not None else None
        # A pre-built PictureCollection injected by the caller (e.g. the sidecar-
        # backed culling collection). When set, it overrides the fpr/file_list
        # construction below and the dialog runs in metadata-less (fpr=None) mode.
        self._injected_collection = collection
        # Keep the raw source label: for the ALBUM day viewer it is the day
        # folder INSIDE the album, which is the only clue to which album this is
        # (that viewer shows the master's files, so the pictures' own paths say
        # nothing). _viewer_label is for display and becomes "68 files" here.
        self._source_label = source_label or ""
        if collection is not None:
            self._viewer_label = source_label or "Culling"
        elif self._file_list is not None:
            self._viewer_label = f"{len(self._file_list)} files"
        else:
            self._viewer_label = Path(image_dir).name
        self.setWindowTitle(f"Species Image Preview ({self._viewer_label})")

        self.image_dir = image_dir
        # Set True when a species name is committed (SpeciesInfoDialog) so
        # closeEvent can offer to re-run Read & Summarize for this day.
        self._species_changed = False
        self.cache_dir = Path(cache_root) if cache_root else preview_cache_dir_for(Path(image_dir))
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Modal "preparing preview" spinner — closed once the first grid is built.
        self.wait_dialog = QProgressDialog("Preparing preview...", None, 0, 0, self)
        self.wait_dialog.setWindowTitle("Please Wait")
        self.wait_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.wait_dialog.setCancelButton(None)
        self.wait_dialog.setMinimumDuration(0)
        self.wait_dialog.show()

        # Column count is a GLOBAL setting now (shared by every day you open), not
        # per-window — so the card width, and therefore the cached thumbnail
        # width, stays the same session to session.
        self.columns = max(1, min(10, get_int_setting(
            "culling", "grid_columns", _GRID_COLUMNS_DEFAULT)))
        self._geo_key = "SpeciesPreview"
        # Default to ~fill-% of the screen on first open so the grid fits without
        # a horizontal scrollbar; a remembered geometry still wins.
        _scr = QApplication.primaryScreen()
        _sg = _scr.availableGeometry() if _scr else None
        _default_size = ((int(_sg.width() * 0.92), int(_sg.height() * 0.88))
                         if _sg else (1200, 800))
        restore_window(self, self._geo_key, default_size=_default_size)

        # ── Load project metadata (fpr) if present ─────────────────────────
        projectname = Path(self.image_dir).name
        # Use the pipeline (fpr) viewer only when the day's CSVs actually exist —
        # the per-day data_files.csv, not just the review/<day> folder. If they're
        # absent (never read, or deleted), fall back to the metadata-less path
        # (sidecar culling viewer / file list) instead of crashing on the read.
        day_csv = os.path.join(self.trip_root_dir, "metadata", "review",
                               projectname, "data_files.csv")
        if self._injected_collection is not None:
            self.fpr = None  # injected collection is its own backend
        elif self.image_dir and os.path.exists(day_csv):
            self.fpr = FilePicture(None)
            try:
                self.fpr.readMaster(self.trip_root_dir, projectname)
                self.fpr.readTripDay(self.trip_root_dir, projectname)
            except Exception as e:
                logger.warning(f"Could not load day metadata for {projectname} "
                               f"({e}); opening in culling mode.")
                self.fpr = None
        else:
            self.fpr = None

        # ── Provider collection ────────────────────────────────────────────
        import time as _time
        _t_coll = _time.perf_counter()
        self._loader = AsyncImageLoader(self)
        if self._injected_collection is not None:
            self._collection = self._injected_collection
        elif self.fpr is not None:
            self._collection = PreviewCollection(self.fpr, loader=self._loader)
        else:
            _t_disc = _time.perf_counter()
            paths = (self._file_list if self._file_list is not None
                     else self.discover_images(image_dir))
            logger.info("ImagePreviewDialog: discover_images %.2f s (%d files)",
                        _time.perf_counter() - _t_disc, len(paths))
            # Ingested-but-not-yet-read folder: if the files have XMP sidecars,
            # use the sidecar-backed collection (reads species/rating/colour).
            # readonly=True keeps display intact but disables all write-back
            # (virtual-trip viewer — master files must never be edited here).
            if paths and any(Path(p).with_suffix(".xmp").exists() for p in paths):
                from gui.sidecar_provider import SidecarCollection
                # allow_geotag: the ONE write a read-only (virtual-trip) viewer may
                # still make — giving the master's pictures the coordinates they lack.
                self._collection = SidecarCollection(
                    paths, cache_root=self.cache_dir, loader=self._loader,
                    readonly=readonly, allow_geotag=allow_geotag,
                    allow_crop=allow_crop, allow_edit=allow_edit)
            else:
                self._collection = FileListPreviewCollection(
                    paths, cache_root=self.cache_dir, loader=self._loader)
        logger.info("ImagePreviewDialog: collection ready %.2f s",
                    _time.perf_counter() - _t_coll)

        # Hand the precomputed "#" crop flags to a collection that can use them
        # (the metadata-less file-list mode). No-op for the others.
        if hasattr(self._collection, "set_crop_hints"):
            self._collection.set_crop_hints(self._crop_hints)
        if hasattr(self._collection, "set_name_hints"):
            self._collection.set_name_hints(self._name_hints, self._name_profile)
        # A colour / rating edit whose sidecar write fails is otherwise silent
        # (the card repaints anyway). Surface it the moment it happens so a lost
        # edit is impossible to miss. Also covers a large viewer opened from here
        # — it shares this collection.
        if hasattr(self._collection, "on_write_error"):
            self._collection.on_write_error = self._on_sidecar_write_error

        # In a real trip cull, Blue/Purple (discard) may be set on a JPG-bearing
        # picture — the JPG can be culled unless published in a blog (enforced at
        # delete). Off elsewhere (master / plain culling / the Add-to-Master
        # scratch), where a JPG stays the finished asset.
        if isinstance(self._collection, PreviewCollection):
            self._collection.allow_jpg_discard = (
                self.fpr is not None and bool(self.trip_root_dir)
                and getattr(self, "_allow_trip_jpg_delete", True))

        # ── Frozen-day guard ────────────────────────────────────────────────
        # A committed day (Freeze/WriteCommit done) is immutable: editing or
        # deleting it diverges the working layer from the trip's active/ CSVs +
        # biota/ tree (the blue-delete accounting gap). Open it read-only — the
        # collection's read_only flag blocks every apply()/delete at the source,
        # and a banner + disabled delete buttons make it visible. Only a real
        # trip pipeline day can be frozen; the ad-hoc Working-Days lane, albums
        # and the master return not-frozen (no statemachine.ini). See
        # gui/day_freeze.py. Edits to a NOT-yet-frozen day stay allowed and
        # offer a Read & Summarize re-run on close (see closeEvent).
        self._day_frozen = False
        if (self.fpr is not None and self._file_list is None
                and self._injected_collection is None
                and self.trip_root_dir and self.image_dir):
            from gui.day_freeze import day_is_frozen
            self._day_frozen, _ = day_is_frozen(self.trip_root_dir, self.image_dir)
            if self._day_frozen and hasattr(self._collection, "read_only"):
                self._collection.read_only = True
        # Set by any persisted edit/delete on a NOT-frozen day so closeEvent can
        # offer to re-run Read & Summarize (kept distinct from _species_changed,
        # which the species-name path already sets).
        self._day_edited = False

        # Grid state (Step 4 — viewport virtualization). `_order` is the single
        # source of truth: the visible keys in display order. A fixed `_pool` of
        # PictureCards is positioned by hand and recycled via card.rebind() as
        # the user scrolls; `_bound` maps key -> the pool card currently showing
        # it (only for on-screen keys). No QGridLayout, no per-key card store.
        self._order: list[str] = []
        self._pool: list[PictureCard] = []
        self._bound: dict[str, PictureCard] = {}
        self._card_h = 0                # measured from a pool card; re-measured on a width change
        self._pool_w = 0               # card width the pool cards are currently fitted to
        self._first_row = -1           # first grid row currently bound (scroll cache)
        self.cancelled = False
        self._active_filter = None
        self.warnings_only = False
        self.no_gps_only = False
        self.no_species_only = False
        self.show_all = False
        self.species_only = False
        self._warn_mode = "warnings"   # which set the ⚠ button shows (see _warnings_menu)
        self._start_species = start_species
        self._start_no_gps = start_no_gps
        self._find_query = ""
        self._findfile_query = ""
        self._color_filter = set()          # colour overlay (combines with the rest)
        # JPG-only overlay (combines with radio + colour + find). On by default
        # when opened from the JPEG-Files cell (mode == "jpg"). Never start it on
        # over a collection that HAS no JPG — the overlay would hide every card
        # and the viewer would open empty while its title said "232 pictures".
        self._jpg_only = (self.mode == "jpg")
        if self._jpg_only and hasattr(self._collection, "jpg_keys"):
            try:
                self._jpg_only = bool(self._collection.jpg_keys())
            except Exception:
                pass
        self._find_timer = QTimer(self)     # debounce typing on big sets
        self._find_timer.setSingleShot(True)
        self._find_timer.timeout.connect(self._fire_pending_find)
        self._resize_timer = QTimer(self)   # debounce window resize → re-fit the grid
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(160)
        self._resize_timer.timeout.connect(self._on_resize_settled)
        self._scroll_timer = QTimer(self)   # debounce scroll → re-bind the card pool
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(15)
        self._scroll_timer.timeout.connect(self._on_grid_scrolled)
        self._pending_find = None
        self._find_min_chars = 2            # don't search on a single character
        # Multi-select state
        self._selected_keys: set[str] = set()
        self._bracket_left: str | None = None
        self._bracket_right: str | None = None
        self._last_clicked_key: str | None = None

        # ── Build UI ───────────────────────────────────────────────────────
        layout = QVBoxLayout(self)

        # Committed-day banner: this day is frozen, so it opens read-only.
        if getattr(self, "_day_frozen", False):
            from gui.day_freeze import FROZEN_BANNER
            banner = QLabel("🔒 " + FROZEN_BANNER)
            banner.setStyleSheet(
                "background:#8a6d00; color:#fff; font-weight:bold; "
                "padding:5px 8px; border-radius:3px;")
            layout.addWidget(banner)

        dir_layout = QHBoxLayout()
        self.dir_field = None
        if self._file_list is not None or self._injected_collection is not None:
            path_label = QLabel(source_label or "Album — files from master database")
            path_label.setStyleSheet("color: #444; font-style: italic;")
            dir_layout.addWidget(path_label)
        else:
            self.dir_field = DirectoryFieldQt("Image Directory", initial_path=image_dir)
            self.dir_field.field.setReadOnly(True)
            dir_layout.addWidget(self.dir_field)
        # Refresh — re-read sidecars an external tool changed (PhotoLab / LR
        # colour, rating). Only shown for sidecar-backed (culling) collections;
        # also bound to F5 and fired automatically when the window regains focus.
        self.refresh_button = None
        if getattr(self._collection, "supports_external_refresh", False):
            self.refresh_button = QPushButton("🔄 Refresh")
            self.refresh_button.setToolTip(
                "Re-read colours, ratings and metadata changed by another tool "
                "(PhotoLab, Lightroom) — also F5, and automatic when this window "
                "regains focus.")
            self.refresh_button.clicked.connect(lambda: self.refresh_from_disk("manual"))
            dir_layout.addWidget(self.refresh_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_scan)
        dir_layout.addWidget(self.cancel_button)
        self._reset_size_btn = QPushButton("⤢")
        self._reset_size_btn.setFixedWidth(30)
        self._reset_size_btn.setToolTip("Reset the window to its default size")
        self._reset_size_btn.clicked.connect(self._reset_window_size)
        dir_layout.addWidget(self._reset_size_btn)
        layout.addLayout(dir_layout)

        controls_layout = QHBoxLayout()

        # Filter row — three mutually-exclusive toggles managed by a
        # QButtonGroup. Buttons get disabled (greyed) when their filter would
        # be empty, so a day with no warnings and no missing-GPS files shows
        # two greyed icons at a glance — a clear "clean day" signal.
        self.warnings_btn = QPushButton("⚠️")
        self.warnings_btn.setFixedSize(24, 24)
        self.warnings_btn.setCheckable(True)
        self._WARN_TIPS = {
            "warnings": "Show only pictures with a wrong / unresolved species name"
                        "\n(right-click for more)",
            "no_species": "Showing pictures with no species assigned yet"
                          "\n(right-click to switch back)",
        }
        self.warnings_btn.setToolTip(self._WARN_TIPS["warnings"])
        self.warnings_btn.setStyleSheet("""
            QPushButton { border: 2px solid #FFA500; border-radius: 4px; background: transparent; padding: 0; }
            QPushButton:checked { background: #FFA500; color: #16241f; }
            QPushButton:disabled { border-color: #CCCCCC; color: #BBBBBB; }
        """)
        self.warnings_btn.clicked.connect(self.on_warnings_toggled)
        # Right-click → pick which "species needs attention" set the button shows.
        self.warnings_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.warnings_btn.customContextMenuRequested.connect(self._warnings_menu)
        controls_layout.addWidget(self.warnings_btn)

        self.no_gps_btn = QPushButton("📍")
        self.no_gps_btn.setFixedSize(24, 24)
        self.no_gps_btn.setCheckable(True)
        self.no_gps_btn.setToolTip("Show only pictures without GPS coordinates")
        self.no_gps_btn.setStyleSheet("""
            QPushButton { border: 2px solid #C0392B; border-radius: 4px; background: transparent; padding: 0; }
            QPushButton:checked { background: #F5B7B1; color: #16241f; }
            QPushButton:disabled { border-color: #CCCCCC; color: #BBBBBB; }
        """)
        self.no_gps_btn.clicked.connect(self.on_no_gps_toggled)
        controls_layout.addWidget(self.no_gps_btn)

        # Map — plot the currently shown pictures (all species) on a slippy map,
        # with the travel path. Needs a collection that can report GPS.
        self.map_btn = QPushButton("🗺 Map")
        self.map_btn.setFixedHeight(24)
        self.map_btn.setToolTip("Show the pictures now displayed on a map (all "
                                "species), with the travel path")
        self.map_btn.clicked.connect(self._on_show_map)
        controls_layout.addWidget(self.map_btn)

        self.all_btn = QPushButton("All")
        self.all_btn.setFixedSize(40, 24)
        self.all_btn.setCheckable(True)
        self.all_btn.setToolTip("Show all pictures")
        self.all_btn.setStyleSheet("""
            QPushButton { border: 2px solid #3498DB; border-radius: 4px; background: transparent; padding: 0; }
            QPushButton:checked { background: #AED6F1; color: #16241f; }
        """)
        self.all_btn.clicked.connect(self.on_all_toggled)
        controls_layout.addWidget(self.all_btn)

        # "Species" — show only pictures that carry a species (the ones an
        # add-to-master commit would take). Enabled only when the backend can
        # report species (fpr / culling collections).
        self.species_btn = QPushButton("Species")
        self.species_btn.setFixedSize(64, 24)
        self.species_btn.setCheckable(True)
        self.species_btn.setToolTip("Show only pictures that have a species")
        self.species_btn.setStyleSheet("""
            QPushButton { border: 2px solid #27AE60; border-radius: 4px; background: transparent; padding: 0; }
            QPushButton:checked { background: #ABEBC6; color: #16241f; }
            QPushButton:disabled { border-color: #CCCCCC; color: #BBBBBB; }
        """)
        self.species_btn.clicked.connect(self.on_species_toggled)
        self.species_btn.setEnabled(hasattr(self._collection, "species_keys"))
        controls_layout.addWidget(self.species_btn)

        # Find species — plain case-insensitive substring of the species name.
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Find species…")
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.setMinimumWidth(120)
        self.find_edit.setToolTip("Show only pictures whose species name contains this text")
        self.find_edit.setEnabled(hasattr(self._collection, "find_keys"))
        self.find_edit.textChanged.connect(self._on_find_changed)
        controls_layout.addWidget(self.find_edit, 1)   # share free space with Find file

        # Find file — substring of the picture name (UPN). Works even when the
        # species is unrecognized, so it finds problem pictures by filename.
        self.findfile_edit = QLineEdit()
        self.findfile_edit.setPlaceholderText("Find file…")
        self.findfile_edit.setClearButtonEnabled(True)
        self.findfile_edit.setMinimumWidth(120)
        self.findfile_edit.setToolTip("Show only pictures whose file name contains this text (e.g. 309)")
        self.findfile_edit.setEnabled(hasattr(self._collection, "file_keys"))
        self.findfile_edit.textChanged.connect(self._on_findfile_changed)
        controls_layout.addWidget(self.findfile_edit, 1)   # share free space with Find species

        # JPG-only toggle — when on, restrict to pictures that have a JPG,
        # combined with the active radio, colour and find filters. Disabled when
        # the collection can't tell JPGs apart (the no-metadata culling path).
        # Sized like All/Species so it doesn't crowd the search boxes.
        self.jpg_btn = QPushButton("JPG")
        self.jpg_btn.setFixedSize(44, 24)
        self.jpg_btn.setCheckable(True)
        self.jpg_btn.setChecked(self._jpg_only)
        self.jpg_btn.setToolTip(
            "Show only pictures that have a JPG — combines with All / Species and the colours")
        self.jpg_btn.setEnabled(hasattr(self._collection, "jpg_keys"))
        self.jpg_btn.setStyleSheet("""
            QPushButton { border: 2px solid #16A085; border-radius: 4px; background: transparent; padding: 0; }
            QPushButton:checked { background: #A3E4D7; color: #16241f; }
            QPushButton:disabled { border-color: #CCCCCC; color: #BBBBBB; }
        """)
        self.jpg_btn.toggled.connect(self._on_jpg_toggled)
        controls_layout.addWidget(self.jpg_btn)

        # Radio-style: exactly one of these is active.
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        self._filter_group.addButton(self.warnings_btn)
        self._filter_group.addButton(self.no_gps_btn)
        self._filter_group.addButton(self.all_btn)
        self._filter_group.addButton(self.species_btn)

        self.color_filter_bar = ColorFilterBar(on_filter_changed=self.on_filter_changed)
        controls_layout.addWidget(self.color_filter_bar)

        # Blue/Purple batch-delete — enabled whenever the collection supports it
        # (PreviewCollection in fpr mode, SidecarCollection in culling mode;
        #  disabled for FileListPreviewCollection / injected collections)
        # A committed day is read-only — no colour-delete (the collection would
        # refuse anyway; greying the buttons avoids a misleading affordance).
        _can_delete = (hasattr(self._collection, 'delete_by_color')
                       and not getattr(self, "_day_frozen", False))
        self._del_blue_btn = QPushButton("✖B")
        self._del_blue_btn.setFixedSize(28, 24)
        self._del_blue_btn.setToolTip("Delete all Blue-tagged pictures (move to trash)")
        self._del_blue_btn.setStyleSheet("""
            QPushButton { border: 2px solid #1E90FF; border-radius: 4px;
                          background: transparent; font-size: 11px; color: #1E90FF;
                          font-weight: bold; padding: 0; }
            QPushButton:hover { background: rgba(30,144,255,0.15); }
            QPushButton:disabled { border-color: #cccccc; color: #cccccc; }
        """)
        self._del_blue_btn.setEnabled(_can_delete)
        self._del_blue_btn.clicked.connect(lambda: self._batch_delete_color("Blue"))
        controls_layout.addWidget(self._del_blue_btn)

        self._del_purple_btn = QPushButton("✖P")
        self._del_purple_btn.setFixedSize(28, 24)
        self._del_purple_btn.setToolTip("Delete all Purple-tagged pictures (move to trash)")
        self._del_purple_btn.setStyleSheet("""
            QPushButton { border: 2px solid #9370DB; border-radius: 4px;
                          background: transparent; font-size: 11px; color: #9370DB;
                          font-weight: bold; padding: 0; }
            QPushButton:hover { background: rgba(147,112,219,0.15); }
            QPushButton:disabled { border-color: #cccccc; color: #cccccc; }
        """)
        self._del_purple_btn.setEnabled(_can_delete)
        self._del_purple_btn.clicked.connect(lambda: self._batch_delete_color("Purple"))
        controls_layout.addWidget(self._del_purple_btn)

        self.column_spinner = QSpinBox()
        self.column_spinner.setRange(1, 10)
        self.column_spinner.setValue(self.columns)
        self.column_spinner.setPrefix("Per row: ")
        self.column_spinner.setToolTip("Images per row")
        self.column_spinner.valueChanged.connect(self.on_column_changed)
        controls_layout.addWidget(self.column_spinner)

        layout.addLayout(controls_layout)

        # ── "Start with" bar — pick the subset to load, so a resumed cull never
        #    pays the full-day build. Only where the collection can filter by
        #    colour label (fpr + sidecar culling collections).
        self._start_bar = None
        self._start_subset = "unrated"
        self._start_bar_active = False
        if hasattr(self._collection, "color_keys"):
            self._start_bar = QWidget()
            _sbl = QHBoxLayout(self._start_bar)
            _sbl.setContentsMargins(4, 2, 4, 2)
            _sbl.setSpacing(8)
            _sbl.addWidget(QLabel("Start with:"))
            self._subset_btns = {}
            for kind in ("unrated", "review", "all"):
                b = QPushButton(_START_SUBSET_LABEL[kind])
                b.setCheckable(True)
                b.clicked.connect(lambda _=False, k=kind: self._apply_start_subset(k))
                _sbl.addWidget(b)
                self._subset_btns[kind] = b
            self._subset_summary = QLabel("")
            self._subset_summary.setStyleSheet("color:#888;")
            _sbl.addWidget(self._subset_summary)
            _sbl.addStretch()
            layout.addWidget(self._start_bar)

        # Scroll area + grid (grid wrapped in a VBox with a trailing stretch so
        # rows stay top-aligned instead of expanding to fill the viewport).
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        # A culling grid always scrolls vertically, so keep the bar ON always:
        # otherwise viewport().width() is ~17 px wider while the grid is still
        # short (during the batched build), _grid_card_width computes cards too
        # wide, and the last column is clipped once the bar finally appears.
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._scroll_content = QWidget()
        _scroll_content = self._scroll_content
        _scroll_vbox = QVBoxLayout(_scroll_content)
        _scroll_vbox.setContentsMargins(0, 0, 0, 0)
        _scroll_vbox.setSpacing(0)
        # Step 4: plain canvas, no layout — the card pool is positioned by hand
        # (_relayout) and grid_widget is pinned to the full virtual size so the
        # scrollbar spans the whole set while only ~50 cards exist.
        self.grid_widget = QWidget()
        _scroll_vbox.addWidget(self.grid_widget)
        _scroll_vbox.addStretch(1)
        self.scroll_area.setWidget(_scroll_content)
        layout.addWidget(self.scroll_area)
        # As the grid scrolls, re-bind the pool to the rows now in view.
        self.scroll_area.verticalScrollBar().valueChanged.connect(
            lambda _v: self._scroll_timer.start())

        # ── Initial filter cascade ─────────────────────────────────────────
        base = set(self._base_keys())
        warnings_keys = [k for k in self._collection.warning_keys() if k in base] if self.fpr else []
        no_gps_keys = [k for k in self._collection.no_gps_keys() if k in base]

        if not self.fpr:
            # No pipeline → no "warnings", but the ⚠ button's other job —
            # "No species yet" — works off species_keys(), which the culling
            # collection has. So keep it live, defaulted to that mode.
            if hasattr(self._collection, "species_keys"):
                self._warn_mode = "no_species"
                self.warnings_btn.setToolTip(
                    "Show only pictures with no species assigned yet")
            else:
                self.warnings_btn.setEnabled(False)
            if not no_gps_keys:
                self.no_gps_btn.setEnabled(False)
        else:
            # Stays enabled even with zero warnings — its right-click menu also
            # offers "No species yet", which is useful on a warning-free day.
            if not no_gps_keys:
                self.no_gps_btn.setEnabled(False)

        species_keys = ([k for k in self._collection.species_keys() if k in base]
                        if hasattr(self._collection, "species_keys") else [])

        if self._start_species and species_keys:
            # Add-to-Master opens focused on the pictures that will be committed.
            self._active_filter = "species"
            self.species_btn.setChecked(True)
            self.species_only = True
            initial_keys = species_keys
        elif self._start_species:
            # Add-to-Master with nothing named yet: show ALL so the pictures are
            # visible and can be named here (Species view would be empty).
            self._active_filter = "all"
            self.all_btn.setChecked(True)
            self.show_all = True
            initial_keys = self._base_keys()
        elif self._start_no_gps and no_gps_keys:
            # Trip Overview's GPS cell opens focused on the pictures still
            # missing GPS — deterministic, not subject to the warnings-first
            # cascade below (a day can have both warnings and missing GPS).
            self._active_filter = "no_gps"
            self.no_gps_btn.setChecked(True)
            self.no_gps_only = True
            initial_keys = no_gps_keys
        elif self.fpr and warnings_keys:
            self._active_filter = "warnings"
            self.warnings_btn.setChecked(True)
            self.warnings_only = True
            initial_keys = warnings_keys
        elif self.fpr and no_gps_keys:
            self._active_filter = "no_gps"
            self.no_gps_btn.setChecked(True)
            self.no_gps_only = True
            initial_keys = no_gps_keys
        elif self.fpr:
            # Clean day (no warnings, no missing GPS) — the normal culling case.
            # Open into the remembered "Start with" subset (default: Unrated) so
            # a resumed cull loads only the ~20% still to decide, not the whole
            # day. A fresh day is all-unrated, so it still loads everything —
            # the win shows up exactly when resuming. "All" is one click away.
            # (This branch used to force self._base_keys() = the full build.)
            initial_keys = self._init_start_subset()
        else:
            initial_keys = self._init_start_subset()

        # Opened from the JPEG-Files cell: come up with All + JPG both selected
        # (the JPG subset is a small, explicit set, so auto-loading it is fine
        # even on an otherwise "don't auto-load" clean day). The start subset
        # would fight the explicit JPG request — clear it here.
        if self._jpg_only and hasattr(self._collection, "jpg_keys"):
            self._active_filter = "all"
            self.all_btn.setChecked(True)
            self.show_all = True
            self._color_filter = set()
            if getattr(self, "color_filter_bar", None) is not None:
                self.color_filter_bar.reflect(None)
            self._refresh_start_bar()
            self._start_bar_active = False
            initial_keys = self._visible_keys()

        if self._start_bar is not None:
            self._start_bar.setVisible(self._start_bar_active)
        self._update_title_for_filter()
        logger.info("ImagePreviewDialog: __init__ total %.2f s (grid build follows)",
                    _time.perf_counter() - _t_coll)
        if initial_keys is not None:
            QTimer.singleShot(0, lambda: self._rebuild_grid_from_keys(initial_keys))
        else:
            self.wait_dialog.close()

        # Folders handed to DxO PhotoLab via the large view's "open in DxO" this
        # session — the focus-in new-render scan is scoped to just these, so it stays
        # cheap even when the album's pictures are scattered across the whole master.
        self._pl_touched_folders: set = set()
        QApplication.instance().applicationStateChanged.connect(
            self._on_application_state_changed)
        self._setup_sidecar_watch(image_dir)

    # ── Mode / key helpers ─────────────────────────────────────────────────
    def _base_keys(self) -> list[str]:
        """All keys the collection discovered. JPG-only is applied as a toggle
        overlay in _visible_keys (see self._jpg_only), not here, so it can be
        turned off even when the viewer was opened from the JPEG-Files cell."""
        return self._collection.all_keys()

    def discover_images(self, image_dir):
        """File discovery for the no-metadata day-folder mode (no fpr)."""
        path = Path(image_dir)
        raw_exts = RAW_OR_HEIC  # canonical table — see utils/raw_extensions.py
        if not path.is_dir():
            return []
        if self.mode == "jpg":
            files = [f for f in path.glob("*.jpg")
                     if not f.name.lower().endswith("-small.jpg")]
        elif self.mode == "raw":
            files = [f for f in path.iterdir() if f.suffix.lower() in raw_exts]
        elif self.mode == "all":
            files = [f for f in path.iterdir()
                     if f.suffix.lower() in raw_exts
                     or (f.suffix.lower() == ".jpg" and not f.name.lower().endswith("-small.jpg"))]
        else:
            return []
        return sorted(files, key=lambda p: p.name)

    # ── Filters ─────────────────────────────────────────────────────────────
    # Find boxes: ("filter mode", QLineEdit attr, query attr, collection method).
    _FIND_BOXES = (
        ("find",     "find_edit",     "_find_query",     "find_keys"),
        ("findfile", "findfile_edit", "_findfile_query", "file_keys"),
    )

    # ── One combined filter: radio button AND colour AND find all apply ───────
    def _visible_keys(self) -> list[str]:
        """Intersect every active filter dimension — the radio button (subset),
        the colour overlay and the find box all combine. Order follows the base
        so the contact sheet keeps capture order."""
        base = self._base_keys()
        keys = set(base)
        coll = self._collection
        overlays_on = bool(self._color_filter or self._find_query
                           or self._findfile_query or self._jpg_only)
        f = self._active_filter
        if f is None and not overlays_on:
            return []  # clean day, nothing until a radio or a search is chosen
        if f == "species" and hasattr(coll, "species_keys"):
            keys &= set(coll.species_keys())
        elif f == "no_species" and hasattr(coll, "no_species_keys"):
            keys &= set(coll.no_species_keys())
        elif f == "warnings":
            keys &= set(coll.warning_keys())
        elif f == "no_gps":
            keys &= set(coll.no_gps_keys())
        # else "all"/None → keep the whole base, then narrow by overlays below
        if self._jpg_only and hasattr(coll, "jpg_keys"):
            keys &= set(coll.jpg_keys())
        if self._color_filter and hasattr(coll, "color_keys"):
            keys &= set(coll.color_keys(self._color_filter))
        if self._find_query and hasattr(coll, "find_keys"):
            keys &= set(coll.find_keys(self._find_query))
        elif self._findfile_query and hasattr(coll, "file_keys"):
            keys &= set(coll.file_keys(self._findfile_query))
        return [k for k in base if k in keys]

    def _apply_filters(self):
        self._update_title_for_filter()
        self._refresh_start_bar()
        self._show_filtered(self._visible_keys())

    # ── "Start with" subset ────────────────────────────────────────────────
    def _colour_counts(self) -> dict:
        """{'Grey': n, 'Red': n, …} over the whole day — how many pictures carry
        each label. In-memory colour lookups, cheap even on a 1000+ picture day."""
        import time as _time
        _t0 = _time.perf_counter()
        coll = self._collection
        out = {}
        for name in ("Grey", "Red", "Yellow", "Green", "Blue", "Purple"):
            try:
                out[name] = len(coll.color_keys({name}))
            except Exception:
                out[name] = 0
        dt = _time.perf_counter() - _t0
        if dt > 0.05:
            logger.info("_colour_counts: %.2f s over 6×%d keys", dt, len(coll.all_keys()))
        return out

    def _init_start_subset(self):
        """Set the colour filter to the remembered start subset and return the
        keys to build. Falls back to the whole day when the collection can't
        filter by colour. Called from the open-filter cascade."""
        if not hasattr(self._collection, "color_keys"):
            return self._base_keys()
        kind = get_setting("culling", "start_subset", fallback="unrated")
        if kind not in _START_SUBSETS:
            kind = "unrated"
        self._start_bar_active = True
        self._active_filter = "all"
        self.all_btn.setChecked(True)
        self.show_all = True
        # If the remembered subset is empty (a finished day reopened) but the day
        # has pictures, widen until something shows — better than a blank grid.
        for candidate in (kind, "review", "all"):
            self._start_subset = candidate
            self._color_filter = set(_START_SUBSETS[candidate])
            keys = self._visible_keys()
            if keys or not self._base_keys():
                kind = candidate
                break
        if getattr(self, "color_filter_bar", None) is not None:
            self.color_filter_bar.reflect(self._color_filter or None)
        self._refresh_start_bar()
        return keys

    def _apply_start_subset(self, kind: str):
        """A 'Start with' button was clicked — set the colour filter, remember
        the choice, and rebuild once."""
        if kind not in _START_SUBSETS:
            return
        self._start_subset = kind
        set_setting("culling", "start_subset", kind)
        self._color_filter = set(_START_SUBSETS[kind])
        if getattr(self, "color_filter_bar", None) is not None:
            self.color_filter_bar.reflect(self._color_filter or None)
        self._active_filter = "all"
        self.all_btn.setChecked(True)
        self.show_all = True
        self._apply_filters()

    def _refresh_start_bar(self):
        """Relabel the 'Start with' buttons with live counts and mark the one
        that matches the current colour filter."""
        bar = getattr(self, "_start_bar", None)
        if bar is None:
            return
        c = self._colour_counts()
        n_unrated = c["Grey"]
        n_review = c["Grey"] + c["Red"] + c["Yellow"] + c["Green"]
        n_all = len(self._base_keys())
        labels = {"unrated": n_unrated, "review": n_review, "all": n_all}
        for kind, btn in self._subset_btns.items():
            btn.setText(f"{_START_SUBSET_LABEL[kind]} ({labels[kind]})")
            btn.setChecked(set(_START_SUBSETS[kind]) == set(self._color_filter))
        self._subset_summary.setText(
            f"·  {c['Blue']} blue · {c['Purple']} purple already set")

    def _on_jpg_toggled(self, checked):
        self._jpg_only = checked
        self._apply_filters()

    def _set_radio(self, name):
        """Record the active radio subset (flags kept for the no-GPS refresh and
        the empty-grid messages) and re-apply the combined filter."""
        self._active_filter = name
        self.warnings_only = (name == "warnings")
        self.no_gps_only = (name == "no_gps")
        self.no_species_only = (name == "no_species")
        self.species_only = (name == "species")
        self.show_all = (name == "all")
        self._apply_filters()

    def on_warnings_toggled(self):
        # Left-click activates whatever mode the ⚠ button is set to (its
        # right-click menu switches between "warnings" and "no_species").
        if self.warnings_btn.isChecked():
            self._set_radio(self._warn_mode)

    def _warnings_menu(self, pos):
        """Right-click the ⚠ button → choose which 'species needs attention'
        set it shows: a wrong / unresolved name, or no species assigned yet."""
        from PyQt6.QtWidgets import QMenu
        if not hasattr(self._collection, "species_keys"):
            return
        menu = QMenu(self)
        opts = [("warnings", "Wrong / unresolved species"),
                ("no_species", "No species yet")]
        for mode, label in opts:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._warn_mode == mode)
            if mode == "warnings" and not self.fpr:
                act.setEnabled(False)   # no pipeline → no "warnings" set
            else:
                act.triggered.connect(lambda _=False, m=mode: self._set_warn_mode(m))
        menu.exec(self.warnings_btn.mapToGlobal(pos))

    def _set_warn_mode(self, mode):
        self._warn_mode = mode
        if not self.fpr:
            self.warnings_btn.setToolTip("Show only pictures with no species assigned yet")
        else:
            self.warnings_btn.setToolTip(self._WARN_TIPS[mode])
        self.warnings_btn.setChecked(True)
        self._set_radio(mode)

    def on_no_gps_toggled(self):
        if self.no_gps_btn.isChecked():
            self._set_radio("no_gps")

    def on_species_toggled(self):
        if self.species_btn.isChecked():
            self._set_radio("species")

    def on_all_toggled(self):
        if self.all_btn.isChecked():
            self._set_radio("all")

    def _on_show_map(self):
        """Open the trip/day map over ALL pictures in this view (not the current
        filter — a no-GPS/species filter would otherwise gut the map, e.g. the 📍
        filter leaves the map empty). GPS'd ones plot; no-GPS ones go to the tray."""
        keys = list(self._collection.all_keys())
        if not keys:
            QMessageBox.information(self, "Map", "No pictures are shown to map.")
            return
        from gui.map_view import TripMapView
        base = self.windowTitle().split(" — ")[0]
        # A read-only viewer stays read-only on the map for rating / colour / files:
        # the virtual-trip day viewer shows the MASTER's own pictures (a virtual trip
        # shares them rather than copying them).
        #
        # Geotagging is the ONE exception, and it must be carried through to the map,
        # not just the grid — otherwise the map says "view only" while the grid's
        # "Get GPS from location" is busy writing, AND the bubble you just seeded
        # cannot be dragged onto the right spot, which is the entire point of seeding
        # it. allow_move then still refuses any bubble holding a camera's own fix.
        read_only = getattr(self._collection, "read_only", False)
        geotag = getattr(self._collection, "allow_geotag", False)
        suffix = " (view only)" if (read_only and not geotag) else ""
        # One map per viewer. Opening a second over the same day gave two windows
        # plotting the same pictures, each with its own stale copy of the points —
        # place something on one and the other silently disagreed.
        old = getattr(self, "_map_dlg", None)
        if old is not None:
            try:
                old.close()
            except RuntimeError:
                pass                 # already destroyed by Qt
        # Delete needs the REAL trip root (metadata/review/<day> lives under it) —
        # trip_root_dir is that for a normal trip day, but the Days-lane/Unfiled
        # ad-hoc flow reuses it to hold the day folder itself, which has no such
        # tree. Only offer delete when trip.ini proves this is a real trip root.
        trip_root_dir = getattr(self, "trip_root_dir", "") or ""
        map_trip_root = (trip_root_dir
                         if trip_root_dir and (Path(trip_root_dir) / "trip.ini").is_file()
                         else "")
        dlg = TripMapView(self, self._collection, keys,
                          title=f"Map — {base}{suffix}",
                          loader=self._loader, trip_root=map_trip_root,
                          read_only=read_only, allow_geotag=geotag,
                          read_only_note="These pictures live in the master database — "
                                         "this map shows them but does not change them.")
        dlg.pictures_geotagged.connect(self._on_map_geotagged)
        self._map_dlg = dlg
        dlg.show()

    def _refresh_open_map(self):
        """The grid just changed GPS (Get GPS from location) — tell an open map to
        re-read, instead of making the user close and re-open it to see the result."""
        m = getattr(self, "_map_dlg", None)
        if m is None:
            return
        try:
            if m.isVisible():
                m.refresh_points()
        except RuntimeError:
            self._map_dlg = None     # the dialog was destroyed

    def _on_map_geotagged(self, keys):
        """The map just wrote GPS + location for these pictures. Refresh their
        cards and re-apply the filter, so one placed from the map drops out of the
        📍 'missing GPS' subset (and the title recounts) instead of lingering."""
        for k in keys:
            card = self._bound.get(k)
            if card:
                card.refresh()
            self._notify_large_view(k)
        self._apply_filters()
        self._note_location_written(keys)

    def _clear_find_silently(self, except_mode=None):
        """Clear the Find boxes without re-triggering their handlers (used when
        the other find box takes over — only one search dimension at a time)."""
        for mode, edit_attr, query_attr, _ in self._FIND_BOXES:
            if mode == except_mode:
                continue
            edit = getattr(self, edit_attr, None)
            if edit is not None and edit.text():
                edit.blockSignals(True)
                edit.clear()
                edit.blockSignals(False)
            setattr(self, query_attr, "")

    def _on_find_changed(self, text):
        self._pending_find = ("find", text)
        self._find_timer.start(250)          # debounce — big sets are slow to rebuild

    def _on_findfile_changed(self, text):
        self._pending_find = ("findfile", text)
        self._find_timer.start(250)

    def _fire_pending_find(self):
        if self._pending_find is not None:
            mode, text = self._pending_find
            self._apply_find(mode, text)

    def _apply_find(self, mode, text):
        spec = next(s for s in self._FIND_BOXES if s[0] == mode)
        _, _, query_attr, method = spec
        q = text.strip()
        # Below the minimum length the search is ignored (too broad / slow); the
        # box keeps the typed text, the filter just doesn't engage yet.
        use = q if (len(q) >= self._find_min_chars and hasattr(self._collection, method)) else ""
        if getattr(self, query_attr) == use:
            return  # no effective change (e.g. typing the 1st char) — don't rebuild
        setattr(self, query_attr, use)
        if use:
            self._clear_find_silently(except_mode=mode)  # only one search dimension
        self._apply_filters()

    def on_filter_changed(self, selected_colors):
        # Colour overlay changed (None = cleared). Combines with the radio + find.
        self._color_filter = set(selected_colors) if selected_colors else set()
        self._apply_filters()

    def _warnings_count(self) -> int:
        if not self.fpr:
            return 0
        base = set(self._base_keys())
        return len([k for k in self._collection.warning_keys() if k in base])

    def _no_gps_count(self) -> int:
        base = set(self._base_keys())
        return len([k for k in self._collection.no_gps_keys() if k in base])

    def _species_count(self) -> int:
        if not hasattr(self._collection, "species_keys"):
            return 0
        base = set(self._base_keys())
        return len([k for k in self._collection.species_keys() if k in base])

    def _no_species_count(self) -> int:
        if not hasattr(self._collection, "no_species_keys"):
            return 0
        base = set(self._base_keys())
        return len([k for k in self._collection.no_species_keys() if k in base])

    def _present_count(self) -> int | None:
        """How many pictures in the current base still have a file on disk; None
        in no-metadata mode (those keys are discovered files, always present)."""
        if not self.fpr:
            return None
        try:
            base = set(self._base_keys())
            return len([k for k in self._collection.present_keys() if k in base])
        except Exception:
            return None

    def _all_pictures_suffix(self, total: int, shown: int | None = None) -> str:
        """The 'All' radio's label. With nothing hidden it reads 'all N pictures';
        once a colour / find / JPG overlay narrows the sheet it reads
        'M of N pictures', so a culling pass can see how many survived."""
        head = (f"{shown} of {total} pictures"
                if shown is not None and shown < total
                else f"all {total} pictures")
        present = self._present_count()
        if present is not None and present < total:
            head += f" ({present} with files, {total - present} missing)"
        return head

    def _overlay_title_parts(self) -> list[str]:
        """Colour + find descriptors appended to the radio's label so the title
        reflects every active filter dimension."""
        parts = []
        if self._jpg_only:
            parts.append("JPG only")
        if self._color_filter:
            parts.append("colour " + "/".join(sorted(self._color_filter)))
        if self._find_query:
            parts.append(f"find '{self._find_query}'")
        elif self._findfile_query:
            parts.append(f"file '{self._findfile_query}'")
        return parts

    def _update_title_for_filter(self):
        base = f"Species Image Preview ({self._viewer_label})"
        total = len(self._base_keys())
        shown = len(self._visible_keys())
        overlays = self._overlay_title_parts()
        if not self.fpr:
            radio = ""
            if self._active_filter == "no_gps":
                radio = f"{self._no_gps_count()} missing GPS"
            elif self._active_filter == "no_species":
                radio = f"{self._no_species_count()} with no species yet"
            elif self._active_filter == "all":
                radio = self._all_pictures_suffix(total, shown)
            suffix = " · ".join(p for p in ([radio] + overlays) if p)
            self.setWindowTitle(f"{base} — {suffix}" if suffix else base)
            return
        if self._active_filter == "warnings":
            n = self._warnings_count()
            radio = f"{n} warning{'s' if n != 1 else ''}"
        elif self._active_filter == "no_species":
            n = self._no_species_count()
            radio = f"{n} with no species yet"
        elif self._active_filter == "no_gps":
            radio = f"{self._no_gps_count()} missing GPS"
        elif self._active_filter == "species":
            radio = f"{self._species_count()} with a species"
        elif self._active_filter == "all":
            radio = self._all_pictures_suffix(total, shown)
        elif overlays:
            # No radio chosen, just a search/colour overlay — still say how many
            # pictures it matches.
            radio = f"{shown} of {total} pictures"
        else:
            radio = f"clean day — click All to view {total} pictures"
        suffix = " · ".join(p for p in ([radio] + overlays) if p)
        self.setWindowTitle(f"{base} — {suffix}" if suffix else base)

    # ── Grid build (batched so a big day stays responsive) ──────────────────
    # Card width snaps to this grid so a small window resize between sessions
    # lands on the SAME value — the on-disk thumbnail cache (step 2C) is keyed by
    # width, and a 1-px drift would miss every entry and force a full re-decode.
    _CARD_W_STEP = 16
    _GRID_GAP = 6            # px between cards (was QGridLayout.spacing())
    _POOL_BUFFER_ROWS = 4   # extra card rows kept in the pool above + below the viewport

    def _grid_card_width(self) -> int:
        """Card width ≈ (viewport × fill%) ÷ columns, snapped to `_CARD_W_STEP`
        so it's stable across small window-size changes and reopens (which is
        what lets the thumbnail cache hit)."""
        ncols = max(1, self.columns)
        spacing = self._GRID_GAP
        vw = self.scroll_area.viewport().width()
        if vw < 120:                     # not laid out yet — sane fallback
            vw = 1100
        pct = max(40, min(100, get_int_setting("culling", "grid_fill_pct", 100)))
        # During the batched build the viewport can still be reported one
        # scrollbar-width too wide (the always-on bar's space is not subtracted
        # until the first proper layout). Subtract it defensively + slack so the
        # row can only UNDERshoot the viewport, never overflow.
        sb = self.scroll_area.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        usable = int(vw * pct / 100) - sb - 6
        raw = max(160, (usable - spacing * (ncols - 1)) // ncols)
        return max(160, (raw // self._CARD_W_STEP) * self._CARD_W_STEP)

    def _reset_window_size(self):
        """Snap the window back to ~92% of the screen — the first-open default —
        for when a stray Windows snap left it stuck small."""
        scr = self.screen() or QApplication.primaryScreen()
        if scr is None:
            return
        g = scr.availableGeometry()
        w, h = int(g.width() * 0.92), int(g.height() * 0.88)
        self.showNormal()
        self.setGeometry(g.x() + (g.width() - w) // 2,
                         g.y() + (g.height() - h) // 2, w, h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        t = getattr(self, "_resize_timer", None)
        if t is not None:
            t.start()

    def _on_resize_settled(self):
        """Window stopped resizing — re-fit the card width and re-lay the pool.
        Also settles the first-open case where the viewport wasn't final yet."""
        if not self._order:
            return
        self._card_h = 0          # width changed → card height may have too
        self._first_row = -1      # force a full re-bind
        self._rebuild_pool_if_needed()
        self._relayout(force=True)

    # ── Step 4: viewport virtualization ──────────────────────────────────────
    def _on_grid_scrolled(self):
        """Debounced scrollbar handler — re-bind the pool when the first visible
        row changed."""
        self._relayout()

    def _visible_rows(self) -> int:
        ch = self._card_h or 300
        return self.scroll_area.viewport().height() // max(1, ch + self._GRID_GAP) + 2

    def _rebuild_pool_if_needed(self):
        """Grow the card pool to cover the viewport + buffer. Cards are built
        once (seeded on any key — _relayout rebinds them immediately) and never
        destroyed; a column change just needs a few more."""
        if not self._order:
            return
        cw = self._grid_card_width()
        if self._pool and cw != self._pool_w:
            for c in self._pool:
                c.set_card_width(cw)          # re-fit the thumb to the new width (4:3)
            self._card_h = 0                  # → re-measure the row height below
        self._pool_w = cw
        ncols = max(1, self.columns)
        want = ncols * (self._visible_rows() + 2 * self._POOL_BUFFER_ROWS)
        want = min(want, len(self._order) + ncols)   # never more than the set + a row
        while len(self._pool) < want:
            seed = self._order[min(len(self._pool), len(self._order) - 1)]
            self._pool.append(self._new_pool_card(seed))
        if self._card_h == 0 and self._pool:
            self._pool[0].adjustSize()
            self._card_h = max(120, self._pool[0].sizeHint().height())
            want = ncols * (self._visible_rows() + 2 * self._POOL_BUFFER_ROWS)
            want = min(want, len(self._order) + ncols)
            while len(self._pool) < want:
                seed = self._order[min(len(self._pool), len(self._order) - 1)]
                self._pool.append(self._new_pool_card(seed))

    def _new_pool_card(self, seed_key: str) -> "PictureCard":
        show_status = self.fpr is not None or getattr(self._collection, "show_status_badges", False)
        item = self._collection.item_for_key(seed_key)
        card = PictureCard(item, self._grid_card_width(), self.grid_widget,
                           show_status=show_status, framed=True, viewer_key="preview",
                           thumb_aspect=self._card_aspect(), defer_thumb=True)
        card.card_clicked.connect(self._on_card_cursor)
        card.card_double_clicked.connect(self._open_large_view)
        card.card_right_clicked.connect(self._on_card_context_menu)
        card.status_clicked.connect(self._open_species_dialog)
        card.notes_clicked.connect(self._edit_headline_description)
        card.render_clicked.connect(self._register_pending_render)
        card.color_double_committed.connect(self._on_swatch_double_committed)
        card.card_changed.connect(self._note_collection_write)
        card.hide()
        return card

    def _relayout(self, *, force: bool = False):
        """Position + rebind the pool for the current scroll window. `_bound`
        maps each on-screen key to its pool card afterwards."""
        if not self._order:
            for c in self._pool:
                c.hide()
            self._bound = {}
            self.grid_widget.setFixedSize(10, 10)
            return
        ncols = max(1, self.columns)
        gap = self._GRID_GAP
        cw = self._grid_card_width()
        if self._pool and cw != self._pool_w:
            for c in self._pool:
                c.set_card_width(cw)
            self._pool_w = cw
            self._card_h = 0
        if self._card_h == 0 and self._pool:
            self._pool[0].adjustSize()
            self._card_h = max(120, self._pool[0].sizeHint().height())
        ch = self._card_h or 300
        total_rows = (len(self._order) + ncols - 1) // ncols
        self.grid_widget.setFixedSize(ncols * cw + (ncols - 1) * gap,
                                      total_rows * (ch + gap) + 1)
        sb = self.scroll_area.verticalScrollBar()
        vp_top = sb.value()
        first_row = max(0, (vp_top - self._POOL_BUFFER_ROWS * (ch + gap)) // (ch + gap))
        if not force and first_row == self._first_row:
            return
        self._first_row = first_row
        first_idx = first_row * ncols
        new_bound = {}
        for slot, card in enumerate(self._pool):
            idx = first_idx + slot
            if idx >= len(self._order):
                card.hide()
                continue
            key = self._order[idx]
            row, col = divmod(idx, ncols)
            try:
                card.rebind(self._collection.item_for_key(key))
            except (KeyError, ValueError):
                card.hide()
                continue
            card.setFixedWidth(cw)
            card.move(col * (cw + gap), row * (ch + gap))
            card.set_selected(key in self._selected_keys)
            card.set_cursor_focus(key == self._last_clicked_key)
            card.show()
            new_bound[key] = card
        self._bound = new_bound
        self._request_visible_thumbs()

    def _request_visible_thumbs(self):
        """Every bound card is near the viewport by construction — load them;
        release everything else in the pool back to the ThumbCache."""
        bound = set(self._bound.values())
        for card in self._pool:
            if card in bound:
                card.request_thumb_if_needed()
            else:
                card.release_thumb()

    def _row_of_key(self, key: str) -> int:
        try:
            return self._order.index(key) // max(1, self.columns)
        except ValueError:
            return -1

    def _scroll_to_key(self, key: str):
        """Scroll the minimum amount to bring `key`'s row fully into view."""
        row = self._row_of_key(key)
        if row < 0:
            return
        ch = self._card_h or 300
        gap = self._GRID_GAP
        y0 = row * (ch + gap)
        y1 = y0 + ch
        sb = self.scroll_area.verticalScrollBar()
        vp_h = self.scroll_area.viewport().height()
        if y0 < sb.value():
            sb.setValue(y0)
        elif y1 > sb.value() + vp_h:
            sb.setValue(min(sb.maximum(), y1 - vp_h))

    def _card_aspect(self) -> float:
        """Width/height to reserve for every card's thumbnail — sampled once
        from the day's own STILLS (usually 4:3), so a single-camera trip fits
        with no side bands. Cached; 3:2 fallback.

        Movies are skipped while sampling rather than counted against the 8-item
        budget: no-species pictures sort first, and a movie has no species until
        it's identified, so a day with a handful of unfiled clips could hand the
        whole grid a 16:9 baseline off the first 8 keys — every 4:3 photo then
        rendered with wide side bands, even after filtering the movies out (the
        aspect is sampled once and cached, not recomputed per filter)."""
        a = getattr(self, "_card_aspect_cache", None)
        if a is not None:
            return a
        a = 1.5
        sampled = ""
        checked = 0
        for k in self._base_keys():
            if checked >= 8:
                break
            try:
                item = self._collection.item_for_key(k)
                exts = {"." + e.lower() for e in (getattr(item, "file_exts", None) or [])}
                if exts and exts <= VIDEO:
                    continue   # video-only picture — not representative of the day's stills
                checked += 1
                sampled = item.exif.dimensions
                w, h = (int(x) for x in sampled.lower().split("x")[:2])
                if w > 0 and h > 0:
                    a = round(max(0.6, min(2.2, w / h)), 3)
                    break
            except Exception:
                checked += 1
                continue
        logger.debug("Culling grid card aspect: %.3f (from '%s')", a, sampled)
        self._card_aspect_cache = a
        return a

    def _set_grid_keys(self, keys, *, reset_selection: bool = False, keep_scroll: bool = True):
        """Point the virtualised grid at `keys` (already the desired subset).
        The collection sorts them; the pool re-binds. No card is destroyed."""
        import time as _t
        t0 = _t.monotonic()
        tc = getattr(self._collection, "_thumb_cache", None)
        if tc is not None:
            tc.reset_stats()
        self.cancelled = False
        self.cancel_button.setEnabled(False)
        self.wait_dialog.close()
        self._collection.cancel_pending_loads()
        self._collection.rebuild(list(keys))
        self._order = [self._collection.item(i).key
                       for i in range(len(self._collection))]
        if reset_selection:
            self._selected_keys.clear()
            self._bracket_left = self._bracket_right = None
            self._last_clicked_key = None
        else:
            order_set = set(self._order)
            self._selected_keys &= order_set
            if self._last_clicked_key not in order_set:
                self._last_clicked_key = None
        keep = self.scroll_area.verticalScrollBar().value() if keep_scroll else 0
        self._first_row = -1
        self._rebuild_pool_if_needed()
        self._relayout(force=True)
        sb = self.scroll_area.verticalScrollBar()
        sb.setValue(min(keep, sb.maximum()))
        self._relayout(force=True)
        self._update_selection_display()
        if not self._order:
            # Once per REBUILD, not once per layout pass — see _relayout. The
            # notice is an answer to "you asked for a filter and it matched
            # nothing", and that question is asked here, not by the layout.
            self._empty_grid_notice()
        if tc is not None and self._order:
            logger.info("Culling grid: %d pictures in %.2f s — %s",
                        len(self._order), _t.monotonic() - t0,
                        tc.stats_line(len(self._bound)))

    def _empty_grid_notice(self):
        """The message shown when a filter / radio narrows the grid to nothing —
        silent for a colour/find/JPG overlay, a short dialog for the radios.

        Called once per rebuild, from _set_grid_keys. It used to hang off
        _relayout, which runs twice per rebuild (once to lay out, once after the
        scroll position is restored) and again on every resize — so the dialog
        came up twice, and a resize behind it could raise another."""
        self.cancel_button.setEnabled(False)
        self.wait_dialog.close()
        if self._color_filter or self._find_query or self._findfile_query or self._jpg_only:
            return  # an overlay narrowed it to nothing — empty grid, no popup
        if self.warnings_only:
            QMessageBox.information(self, "No Warnings", "All provided species information is correct.")
        elif self.no_species_only:
            QMessageBox.information(self, "All identified",
                                   "Every picture has a species (or a flagged name).")
        elif self.no_gps_only:
            QMessageBox.information(self, "No Missing GPS", "All pictures have GPS coordinates.")
        elif self.species_only:
            QMessageBox.information(self, "No Species", "No pictures have a species yet — tag them first.")
        else:
            QMessageBox.warning(self, "No Images", "No pictures found, or none selected.")

    def _rebuild_grid_from_keys(self, keys):
        """Open / master-change entry — point the virtualised grid at `keys` and
        clear any selection (a fresh set)."""
        self._set_grid_keys(keys, reset_selection=True)

    def _show_filtered(self, visible_keys):
        """Filter-change entry — point the grid at `visible_keys`, keeping the
        (still-valid) selection and the scroll position."""
        self._set_grid_keys(visible_keys, reset_selection=False)
        if getattr(self, "_pending_filter", False):
            self._pending_filter = False

    def cancel_scan(self):
        # Virtualised grid has no long build to cancel; kept for the toolbar
        # button's signal.
        self.cancelled = True
        self.cancel_button.setEnabled(False)

    def on_column_changed(self, value):
        self.columns = value
        set_setting("culling", "grid_columns", value)   # global — every day opens the same
        self._update_title_for_filter()
        if not self._order:
            return
        # New column count → new card width + height + pool size.
        self._card_h = 0
        self._first_row = -1
        self._rebuild_pool_if_needed()
        self._relayout(force=True)

    # ── Card actions ────────────────────────────────────────────────────────
    def _frozen_day_blocked(self):
        """True (and warns) when this is a committed day, whose species/edit
        writes go straight to XMP and so bypass the collection's read_only lever.
        The collection blocks rating/colour/delete; this blocks the species path."""
        if getattr(self, "_day_frozen", False):
            from gui.day_freeze import FROZEN_BANNER
            QMessageBox.information(self, "Day committed", FROZEN_BANNER)
            return True
        return False

    def _open_species_dialog(self, key):
        # Respect a multi-selection: clicking ❔ on a card that's part of the
        # current selection sets the species for the WHOLE group (same as the
        # right-click "Set species…"). Clicking a card outside the selection
        # targets just that one.
        if key in self._selected_keys and len(self._selected_keys) > 1:
            self._open_species_dialog_for_keys(set(self._selected_keys))
            return
        if not self.fpr:
            self._open_culling_species_dialog({key})
            return
        if self._frozen_day_blocked():
            return
        full_path = self._collection.rep_full_path(key)
        species_name = self.fpr.getSpeciesName(full_path.name)
        error_msg = self.fpr.getErrorMsg(full_path.name)
        self.species_dialog = SpeciesInfoDialog(full_path, species_name, error_msg, self)
        self.species_dialog.show()

    def _on_card_cursor(self, key):
        prev = self._bound.get(self._last_clicked_key) if self._last_clicked_key else None
        if prev:
            prev.set_cursor_focus(False)
        self._last_clicked_key = key
        card = self._bound.get(key)
        if card:
            card.set_cursor_focus(True)

    def _open_large_view(self, key):
        prev = self._bound.get(self._last_clicked_key) if self._last_clicked_key else None
        if prev:
            prev.set_cursor_focus(False)
        self._last_clicked_key = key
        card = self._bound.get(key)
        if card:
            card.set_cursor_focus(True)
        from gui.large_image_view import LargeImageView
        keys = list(self._order)
        if key not in keys:
            return
        # Single instance — close any open large view first.
        existing = getattr(self, "_large_view", None)
        if existing is not None:
            try:
                existing.close()
            except RuntimeError:
                pass
            self._large_view = None
        dlg = LargeImageView(self, self._collection, keys, keys.index(key),
                             settings_key="SpeciesPreview", auto_advance=True,
                             initial_selection=set(self._selected_keys),
                             initial_brackets=(self._bracket_left, self._bracket_right),
                             allow_delete=hasattr(self._collection, "delete_keys"))
        dlg.picture_changed.connect(self._on_large_view_changed)
        dlg.picture_image_changed.connect(self._on_large_view_image_changed)
        dlg.picture_navigated.connect(self._on_large_view_navigated)
        dlg.selection_changed.connect(self._on_large_view_selection_changed)
        dlg.species_committed.connect(self.refresh_after_species_change)
        dlg.dxo_launched.connect(self._on_dxo_launched)
        dlg.delete_requested.connect(
            lambda ks, d=dlg: self._on_large_view_delete(d, ks))
        self._large_view = dlg
        dlg.finished.connect(lambda _=0: self._on_large_view_closed())
        dlg.show()

    def _play_video(self, _key):
        """'v' — the Quick view is the one window in the app with a player, and it
        starts playing a movie of its own accord, so playing one IS opening it on
        this picture. 'z' opens the same window for anything, movie or not."""
        self._open_quick_view()

    def _open_quick_view(self):
        """'z' — a zoomable look at the camera JPEG only (no RAW decode, no
        panel). ←/→ step, Esc closes; on close the grid cursor moves to the
        last picture shown. Does not touch the large view."""
        order = list(self._order)
        if not order:
            return
        key = self._last_clicked_key if self._last_clicked_key in order else order[0]
        existing = getattr(self, "_quick_view", None)
        if existing is not None:
            try:
                existing.close()
            except RuntimeError:
                pass
        from gui.quick_viewer import QuickViewer
        dlg = QuickViewer(self, self._collection, order, order.index(key),
                          initial_brackets=(self._bracket_left, self._bracket_right),
                          initial_selection=set(self._selected_keys))
        dlg.navigated.connect(self._cull_set_cursor)
        dlg.edited.connect(self._on_quick_view_edited)
        # [ / ] in the quick viewer mark a range exactly like the grid's own —
        # reuse the SAME handler the large viewer's selection_changed already
        # uses, so the grid's selection highlighting and bulk-action menu just
        # work on whatever range was marked while pixel-peeping.
        dlg.selection_changed.connect(self._on_large_view_selection_changed)
        dlg.action_requested.connect(self._on_quick_view_action)
        dlg.finished.connect(lambda _=0: setattr(self, "_quick_view", None))
        self._quick_view = dlg
        dlg.show()
        dlg.activateWindow()
        dlg.setFocus()

    def _cull_clipboard(self, what: str):
        """Ctrl+C / Ctrl+V / Ctrl+Shift+C / Ctrl+Shift+V pressed in this grid.
        The grid shows its own result (a name under the card, a GPS badge), so
        only the message goes to the log."""
        msg = self._on_clipboard(what)
        if msg:
            logger.info(msg)

    def _on_quick_view_action(self, what: str, keys: set):
        """'a' / 's' / 'c' pressed in the Quick view. That window owns no
        species or rating UI — it runs on THIS grid's handlers, on the keys it
        sent up (its cursor, not ours), with itself as the dialog's parent so
        nothing opens behind it."""
        qv = getattr(self, "_quick_view", None)
        keys = set(keys)
        if not keys:
            return
        if what == "species_quick":
            written = self._cull_quick_species(keys, parent=qv)
            # Only a dialog-free write consumes the range; a fallback to the
            # dialog leaves it marked, so a cancel doesn't lose the selection.
            if written and qv is not None:
                qv.clear_brackets()
                qv.flash(f"Species name set for {written} "
                         f"picture{'s' if written != 1 else ''}")
        elif what == "species":
            self._open_species_dialog_for_keys(keys, parent=qv)
        elif what == "rating":
            self._open_batch_rate_dialog(keys, parent=qv)
        else:
            # The four clipboard actions. The Quick view shows nothing of its
            # own for a write, so its badge is the only acknowledgement there is.
            msg = self._on_clipboard(what, keys=keys, parent=qv)
            if qv is not None and msg:
                qv.flash(msg)
        if qv is not None:
            qv.refresh_current()

    def _on_quick_view_edited(self, keys):
        """Rating/colour set from the quick viewer — same follow-up as an edit
        made on a card: repaint it, start the CSV-flush debounce."""
        for k in keys:
            self._cull_refresh_key(k)
        if keys:
            self._note_collection_write()

    def _on_large_view_closed(self):
        # Repaint every card from the live provider. Per-edit picture_changed
        # already refreshes the active card, but a card's coloured frame can fail
        # to repaint while the grid is occluded by the large view; refreshing on
        # close (now that the grid is visible again) guarantees it reflects edits.
        for card in list(self._bound.values()):
            try:
                card.refresh()
            except RuntimeError:
                pass
        self._large_view = None

    def _on_large_view_changed(self, key):
        # The viewer wrote the edit via the provider; repaint that card's frame +
        # badges (preview keeps capture order, so no re-sort).
        self._day_edited = True     # non-frozen day: offer a re-read on close
        self._note_collection_write()   # large-view edit also defers its CSV write
        card = self._bound.get(key)
        if card:
            card.refresh()

    def _on_large_view_image_changed(self, key):
        # A crop was applied in the large view — the card must RE-DECODE, not just
        # repaint its frame (which is all _on_large_view_changed does).
        self._day_edited = True     # non-frozen day: offer a re-read on close
        card = self._bound.get(key)
        if card:
            try:
                card.reload_thumbnail()
            except RuntimeError:
                pass

    def _on_large_view_navigated(self, key):
        # Sync the grid cursor to the large view's current picture so the user
        # can see exactly where they are in the contact sheet.
        prev = self._bound.get(self._last_clicked_key) if self._last_clicked_key else None
        if prev:
            prev.set_cursor_focus(False)
        self._last_clicked_key = key
        card = self._bound.get(key)
        if card:
            card.set_cursor_focus(True)
            self.scroll_area.ensureWidgetVisible(card)

    def _on_large_view_selection_changed(self, keys: set, bracket_left=None, bracket_right=None):
        # Adopt the child viewer's [ / ] state too, not just the resulting
        # selection — otherwise a range opened here and closed in the Large
        # Image View / Quick Viewer (or vice versa) would lose its open
        # bracket the moment focus came back to the grid.
        self._selected_keys = keys
        self._bracket_left = bracket_left
        self._bracket_right = bracket_right
        self._update_selection_display()

    def _on_large_view_delete(self, dlg, keys: set):
        """Right-click Delete from the large viewer's filmstrip: trash the keys (same
        protection + confirm-only-when-protected as the grid), refresh the grid, then
        tell the viewer to drop the removed cells and land on a neighbour."""
        self._delete_keys_ui(set(keys))   # it drops them from the viewer too

    def refresh_after_species_change(self, updated_upns):
        """Called by SpeciesInfoDialog after a commit: repaint affected cards
        and mark the dialog so closeEvent can offer to re-run Read & Summarize."""
        for card in list(self._bound.values()):
            if card.upn_key in updated_upns:
                card.refresh()
        if updated_upns:
            self._species_changed = True
        # The Quick view may be the window in front, and the Set-species dialog
        # is non-modal — so the commit lands while that window is still up, and
        # its title carries the species. Repaint it here rather than at each
        # call site, so every route to a species change is covered.
        qv = getattr(self, "_quick_view", None)
        if qv is not None and updated_upns:
            try:
                qv.refresh_current()
            except RuntimeError:
                pass   # quick view was closed/destroyed
        # Same for the Editor: the commit may come from this grid's menu,
        # borrowed by the Editor, while the Editor is the window in front.
        lv = getattr(self, "_large_view", None)
        if lv is not None and updated_upns:
            try:
                lv.refresh_after_host_action(set(updated_upns))
            except RuntimeError:
                pass   # large view was closed/destroyed

    # ── Multi-select ────────────────────────────────────────────────────────

    def _update_selection_display(self):
        for c in list(self._bound.values()):
            c.set_selected(c.upn_key in self._selected_keys)

    def _selection_from_brackets(self) -> set:
        order = list(self._order)
        left, right = self._bracket_left, self._bracket_right
        if left is None and right is None:
            return set()
        anchor = left if right is None else (right if left is None else None)
        if anchor is not None:
            return {anchor} if anchor in order else set()
        try:
            a, b = order.index(left), order.index(right)
        except ValueError:
            surviving = next((k for k in (left, right) if k in order), None)
            return {surviving} if surviving else set()
        if a > b:
            a, b = b, a
        return set(order[a:b + 1])

    def _clear_selection(self):
        self._selected_keys.clear()
        self._bracket_left = None
        self._bracket_right = None
        self._update_selection_display()
        # The selection is shared with the open Editor (it pushes its changes
        # here via selection_changed); a clear made here — after a species
        # commit, say — must reach it too, or it keeps highlighting a range
        # its right-click no longer acts on.
        lv = getattr(self, "_large_view", None)
        if lv is not None:
            try:
                lv.clear_selection()
            except RuntimeError:
                pass   # large view was closed/destroyed

    def editor_context_menu(self, key: str) -> set:
        """The Editor's right-click, when it was opened from this grid: this
        grid's own card menu, so the Editor offers exactly what the grid does
        (docs/plans/edit_window_parity.md, D5). Returns the keys an action was
        chosen for (empty when the menu was dismissed) so the Editor can
        repaint them."""
        return self._on_card_context_menu(key)

    def _on_card_context_menu(self, key: str) -> set:
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        keys_for_action = (self._selected_keys
                           if key in self._selected_keys and self._selected_keys
                           else {key})
        n = len(keys_for_action)
        lbl = f"{n} picture{'s' if n != 1 else ''}"
        try:
            item = self._collection.item_for_key(key)
        except (ValueError, KeyError):
            item = None
        menu = QMenu(self)
        # "Set species…" re-types the determination. It works in the trip viewer
        # (SpeciesInfoPanel, fpr present) and the culling grid (can_edit_species). But a
        # master picture's species is settled and NOT re-typed from an album (an actual
        # re-file goes through the master menu's "Move to existing species…"). What IS
        # editable there is the FORM — sex / age / plumage — a property of the photo, so
        # it takes this slot in the album, in place of the dead "Set species…".
        act_species = act_qualifiers = None
        if self.fpr is not None or getattr(item, "can_edit_species", False):
            act_species = menu.addAction(f"Set species…  ({lbl})")
        elif (getattr(self, "_master_ops_enabled", False) and item is not None
                and getattr(item, "from_master", False)
                and not self._collection.master_read_only):
            act_qualifiers = menu.addAction("Sex / age / plumage…")
        act_rate = menu.addAction(f"Set rating…  ({lbl})")
        # Exposure suggestion for PhotoLab/Lightroom — same sidecar mechanism as
        # the large viewer's slider (gui/exposure_widget.py), offered here for
        # applying the SAME EV to a whole selection at once.
        act_exposure = None
        if getattr(item, "can_set_exposure", False):
            act_exposure = menu.addAction(f"Set exposure…  ({lbl})")
        act_fullres = menu.addAction(f"Full resolution  ({lbl})")
        # Bake a website-ready JPG beside the RAW from the camera's embedded
        # preview + the crop — a quick blog picture without the full DxO render.
        act_create_jpg = None
        if hasattr(self._collection, "create_blog_jpg"):
            act_create_jpg = menu.addAction(f"Create JPG (embedded + crop)  ({lbl})")
            act_create_jpg.setToolTip(
                "Write a <name>.jpg next to each RAW from the camera's embedded "
                "preview, applying the crop — a quick trip-blog picture without "
                "the full render. Skips a picture that already has a JPG.")
        act_geo = menu.addAction(f"Get location  ({lbl})")
        # The mirror of "Get location": that one turns GPS into a place name, this
        # one turns the place name a picture ALREADY carries into GPS. For the older
        # trips, whose camera had no receiver but whose files say "Helgoland".
        act_gps_from_loc = menu.addAction(f"Get GPS from location  ({lbl})")
        act_gps_from_loc.setEnabled(self._can_gps_from_location(keys_for_action))
        act_gps_from_loc.setToolTip(
            "Look up the written place and give these pictures an approximate "
            "coordinate (~30 m), so they appear on the map. Marked as placed by hand, "
            "so you can then drag them to the exact spot.")
        act_editloc = menu.addAction(f"Edit location…  ({lbl})")
        # Headline / Description — normally about the ONE picture it titles, but a
        # burst of the same scene can share it, so the editor offers the whole
        # selection behind a tick (off by default). The label counts the selection
        # like every other entry; the dialog is where the scope is chosen.
        act_hd = None
        if item is not None and (getattr(item, "can_edit_headline", False)
                                 or getattr(item, "can_edit_description", False)):
            act_hd = menu.addAction(f"Edit headline / description…  ({lbl})")
        menu.addSeparator()
        # Copy GPS + place from THIS one picture; paste into the selection. Lets a
        # no-GPS shot inherit a neighbour's location when the sv2 backfill can't
        # reach it (no GPS frame within its time/distance window).
        act_copy_gps = menu.addAction("Copy GPS + place  (this picture)")
        act_copy_gps.setEnabled(bool(item and getattr(item, "has_gps", False)))
        act_paste_gps = menu.addAction(f"Paste GPS + place  ({lbl})")
        from gui.viewer_clipboard import gps_buffer
        act_paste_gps.setEnabled(gps_buffer() is not None)
        # Use ONE picture as the album's cover. Only offered when there is an
        # album to update and this picture has a JPG to make it from — a menu
        # entry that cannot do anything is worse than no entry.
        act_album_pic = None
        cover = self._cover_target()
        if cover and n == 1 and self._collection.jpg_path(key):
            _root, kind, owner = cover
            menu.addSeparator()
            act_album_pic = menu.addAction(f"Use as {kind} picture  ({owner})")
        # (Registering a freshly-rendered DxO JPG into the master is now the shared
        # master menu's "Add new renders…" item, appended below for master pictures —
        # no separate album-only entry, and no "Re-extract" step: the day reads the
        # master live, so the render shows on refresh.)
        # Delete straight to .trash (recoverable) — the intuitive per-picture cull,
        # beside the batch blue/purple-delete. Same JPG protection as blue-delete.
        act_delete = None
        if hasattr(self._collection, "delete_keys"):
            menu.addSeparator()
            act_delete = menu.addAction(f"🗑 Delete  ({lbl})")
        # The full master card menu (qualifiers / pin best-JPG / delete-JPG / move /
        # add / delete), on the right-clicked master picture. The album's pictures ARE
        # the master's, so these act straight on the master via the shared mixin.
        master_handlers: dict = {}
        if (getattr(self, "_master_ops_enabled", False) and item is not None
                and getattr(item, "from_master", False)
                and not self._collection.master_read_only):
            menu.addSeparator()
            # Qualifiers are offered up top (in the "Set species" slot), so the master
            # section here carries only the inventory ops.
            master_handlers = self._build_master_menu(
                menu, key, ops={"add", "pin", "delete_jpg", "move", "delete", "delete_file"})
        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return set()
        if chosen in master_handlers:
            master_handlers[chosen]()
        elif act_species is not None and chosen == act_species:
            self._open_species_dialog_for_keys(keys_for_action)
        elif act_qualifiers is not None and chosen == act_qualifiers:
            self._do_master_qualifiers(key)
        elif chosen == act_rate:
            self._open_batch_rate_dialog(keys_for_action)
        elif act_exposure is not None and chosen == act_exposure:
            self._open_batch_exposure_dialog(keys_for_action)
        elif chosen == act_fullres:
            self._cull_full_res(keys_for_action)
        elif act_create_jpg is not None and chosen == act_create_jpg:
            self._cull_create_jpg(keys_for_action)
        elif chosen == act_geo:
            self._cull_geolocate(keys_for_action)
        elif chosen == act_gps_from_loc:
            self._gps_from_location(keys_for_action)
        elif chosen == act_editloc:
            self._edit_location(keys_for_action)
        elif act_hd is not None and chosen == act_hd:
            self._edit_headline_description(key, keys_for_action)
        elif chosen == act_copy_gps:
            self._copy_gps(key)
        elif chosen == act_paste_gps:
            self._paste_gps(keys_for_action)
        elif act_album_pic is not None and chosen == act_album_pic:
            self._set_album_picture(key)
        elif act_delete is not None and chosen == act_delete:
            self._delete_keys_ui(keys_for_action)
        return set(keys_for_action)

    def _edit_headline_description(self, key: str, keys: set | None = None):
        """Open the shared Headline / Description editor for the clicked card and
        write + repaint (the 📝 badge follows the description).

        The clicked card is always the one shown and the one written; `keys` (the
        selection) only widens the write when the dialog's "apply to all" is
        ticked."""
        try:
            item = self._collection.item_for_key(key)
        except (ValueError, KeyError):
            return
        # Clicked picture first — apply_headline_description_to writes items[0]
        # always and the rest only on "apply to all".
        others = [k for k in (keys or set()) if k != key]
        items = [item]
        for k in others:
            try:
                items.append(self._collection.item_for_key(k))
            except (ValueError, KeyError):
                pass
        from gui.headline_desc_dialog import (edit_headline_description,
                                              apply_headline_description_to)
        values = edit_headline_description(
            self, title=item.display_name or key,
            headline=item.headline, description=item.description,
            can_edit_headline=getattr(item, "can_edit_headline", False),
            can_edit_description=getattr(item, "can_edit_description", False),
            n_selected=len(items))
        # parent= → pictures that just got a headline but have no subject are
        # offered the landscape marker (the headline alone shows nowhere).
        for written in apply_headline_description_to(items, values, parent=self):
            card = self._bound.get(written.key)
            if card is not None:
                card.refresh()

    def _cover_target(self):
        """(root, kind, name) whose cover this viewer would set, or None.

        ⚠ Derived from THIS VIEWER's own collection, never from the configured
        active album. Reading the global setting meant a picture opened from the
        2026 Kreta TRIP was written as the cover of whichever album happened to be
        selected — a Kreta orchid became the Gran Canaria album's picture. The
        menu must act on the thing you are looking at.

        trip_root_dir is set by the trip day viewer; the album day viewer instead
        shows files from the master and identifies itself by a source label under
        the album's own folder, so walk up from there looking for virttrip.ini.
        A plain culling folder matches neither and gets no menu entry.
        """
        trip_root = (getattr(self, "trip_root_dir", "") or "").strip()
        if trip_root and (Path(trip_root) / "trip.ini").is_file():
            return str(trip_root), "trip", Path(trip_root).name
        start = ((getattr(self, "_source_label", "") or "")
                 or (self.image_dir or "")
                 or (getattr(self, "_viewer_label", "") or ""))
        try:
            here = Path(start)
        except (TypeError, ValueError):
            return None
        for folder in [here, *here.parents]:
            if (folder / "virttrip.ini").is_file():
                return str(folder), "album", folder.name
            if (folder / "trip.ini").is_file():
                return str(folder), "trip", folder.name
        return None

    def _set_album_picture(self, key: str):
        """Confirm (with a preview of the crop), then write the album's cover."""
        jpg = self._collection.jpg_path(key)
        if not jpg:
            return
        cover = self._cover_target()
        if not cover:
            return
        root, kind, owner = cover
        from gui.album_picture_dialog import AlbumPictureDialog
        dlg = AlbumPictureDialog(self, jpg, root, album_name=owner, kind=kind)
        if getattr(dlg, "_failed", False):
            return
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.status_label.setText(
                    f"Album picture set from {Path(jpg).name}.")
            except Exception:
                pass

    # ── shared master card menu (MasterCardMenuMixin hooks) ───────────────────
    def enable_master_ops(self, db, master_root, pic_rows, file_rows) -> None:
        """Album day viewer: bind the master DB + this day's rows to the collection so
        the shared master card menu (pin / delete-JPG / move / add / delete / qualifiers)
        can act on these master pictures. No-op if the collection can't be bound."""
        bind = getattr(self._collection, "bind_master", None)
        if not callable(bind):
            return
        try:
            bind(db, master_root, pic_rows, file_rows)
            self._master_ops_enabled = True
        except Exception as e:
            logger.warning(f"enable_master_ops failed: {e}")

    def _master_status(self, msg: str) -> None:
        try:
            self.status_label.setText(msg)
        except Exception:
            pass

    def _after_master_change(self) -> None:
        # Repaint so a pin (best JPG) / qualifier (caption) shows immediately, and a
        # just-added render (a new JPG beside the RAW) folds onto its card. Structural
        # changes (delete / move) drop the file from the master; the card is refreshed
        # on the next open — the status line says what happened.
        try:
            # _set_grid_keys keeps the scroll position across the rebuild.
            attach = getattr(self._collection, "_attach_sibling_jpgs", None)
            if callable(attach):
                attach()   # "Add new renders" wrote a JPG on disk — pick it up now
            self._rebuild_grid_from_keys(self._collection.all_keys())
        except Exception as e:
            logger.debug(f"album master refresh skipped: {e}")

    def _culling_sc(self):
        """Lazily load (and cache) the standalone species collection for the
        fpr-free culling species panel."""
        if not hasattr(self, "_culling_sc_cache"):
            from gui.culling_species import load_species_classification
            self._culling_sc_cache = load_species_classification()
        return self._culling_sc_cache

    def _flush_learned_species(self):
        """Persist species learned during this session. Both classifications
        this viewer may have touched: the standalone culling one, and the
        pipeline's own (fpr.sc) when a species was named through the editor."""
        # Never let this stop the window closing.
        try:
            from gui.culling_species import flush_learned_species_from
            flush_learned_species_from(
                (getattr(self, "_culling_sc_cache", None),
                 getattr(self.fpr, "sc", None) if self.fpr is not None else None),
                parent=self)
        except Exception as e:
            logger.warning(f"Saving learned species failed: {e}")

    def _open_culling_species_dialog(self, keys: set, parent=None):
        """Smart species assignment for the sidecar/culling viewer (no fpr):
        write the chosen name to each selected picture's XMP:Subject."""
        keys = {k for k in keys if k}
        if not keys:
            return
        item0 = self._collection.item_for_key(next(iter(keys)))
        if not getattr(item0, "can_edit_species", False):
            return
        from gui.culling_species import CullingSpeciesDialog, species_prefill_for_keys

        def _species_of(k):
            try:
                return self._collection.item_for_key(k).species or ""
            except (ValueError, KeyError):
                return ""

        def _is_landscape(k):
            try:
                return bool(self._collection.item_for_key(k).is_blog_extra)
            except (ValueError, KeyError):
                return False

        current, preset_summary, n_landscape = species_prefill_for_keys(
            keys, _species_of, _is_landscape)
        dlg = CullingSpeciesDialog(self._culling_sc(), current, len(keys),
                                   parent or self,
                                   n_landscape=n_landscape,
                                   preset_summary=preset_summary)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = dlg.chosen_name()
            # One exiftool call for the whole selection where the collection
            # can do it — twenty-two pictures one at a time is twenty-two
            # process spawns, ~270 ms each, for a write that takes no time.
            batched = getattr(self._collection, "write_tag_many", None)
            if batched is not None:
                try:
                    batched(list(keys), "XMP:Subject", name, "Subject")
                except Exception as e:
                    logger.error(f"set_species (batched) failed: {e}")
            else:
                for k in keys:
                    try:
                        self._collection.item_for_key(k).set_species(name)
                    except Exception as e:
                        logger.error(f"set_species failed for {k}: {e}")
            self.refresh_after_species_change(set(keys))
            self._clear_selection()

    def _open_species_dialog_for_keys(self, keys: set, parent=None):
        # `parent` overrides this grid as the dialog's parent — the Quick view
        # passes itself, so the dialog is its child and can never open behind it.
        if not keys:
            return
        if not self.fpr:
            self._open_culling_species_dialog(set(keys), parent=parent)
            return
        if self._frozen_day_blocked():
            return
        from gui.species_editor import SpeciesInfoPanel
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        file_paths, species_names, error_msgs = [], [], []
        for k in keys:
            path = self._collection.rep_full_path(k)
            if path:
                file_paths.append(path)
                # getSubject (the raw, literal XMP:Subject) — not getSpeciesName,
                # which prefers NLSoortNaam and would silently swap in the Dutch
                # name here while the single-picture prefill (below, in
                # SpeciesInfoPanel) uses getSubject directly. The two disagreed
                # for any picture whose resolved NL/EN names differ from what was
                # actually typed/committed.
                species_names.append(self.fpr.getSubject(path.name) or "")
                error_msgs.append(self.fpr.getErrorMsg(path.name) or "")
        if not file_paths:
            return
        dlg = QDialog(parent or self)
        dlg.setWindowTitle(f"Set species — {len(file_paths)} picture{'s' if len(file_paths) != 1 else ''}")
        dlg.setMinimumWidth(600)
        dlg_layout = QVBoxLayout(dlg)
        panel = SpeciesInfoPanel(self.fpr, file_paths, species_names, error_msgs, parent=dlg)
        panel.committed.connect(lambda upns: (
            self.refresh_after_species_change(upns),
            self._clear_selection(),
            dlg.accept()
        ))
        dlg_layout.addWidget(panel)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(cancel_btn)
        dlg_layout.addLayout(row)
        dlg.show()

    def _open_batch_rate_dialog(self, keys: set, parent=None):
        if not keys or self._collection is None:
            return
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox
        from gui.rating_editor import StarWidget, ColorSelector
        n = len(keys)
        dlg = QDialog(parent or self)
        dlg.setWindowTitle(f"Set rating / color — {n} picture{'s' if n != 1 else ''}")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(f"Apply to {n} picture{'s' if n != 1 else ''}:"))

        star_row = QHBoxLayout()
        star_row.addWidget(QLabel("Rating:"))
        star_w = StarWidget(0)
        star_row.addWidget(star_w)
        star_row.addStretch()
        layout.addLayout(star_row)

        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Color:"))
        color_w = ColorSelector("")
        color_row.addWidget(color_w)
        color_row.addStretch()
        layout.addLayout(color_row)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            stars = star_w.rating
            color = color_w.color
            key_list = list(keys)
            progress = QProgressDialog("Updating…", None, 0, len(key_list) + 1, self)
            progress.setWindowTitle("Updating pictures")
            progress.setMinimumDuration(0)
            progress.show()

            def _tick(done, total):
                progress.setValue(done)
                progress.setLabelText(
                    f"Collecting {done} of {total}…" if done < total
                    else f"Writing {total} picture(s) in one go…")
                QApplication.processEvents()

            # ONE exiftool call for the whole selection, and the stars and the
            # colour in the SAME call. Doing them per picture and separately
            # meant two process spawns each — ~270 ms apiece, so about 43
            # seconds for eighty pictures, nearly all of it starting exiftool.
            batched = getattr(self._collection, "apply_many", None)
            if batched is not None:
                batched(key_list, new_stars=stars,
                        new_color=(color or None), progress=_tick)
            else:
                for i, k in enumerate(key_list):
                    _tick(i, len(key_list))
                    try:
                        item = self._collection.item(self._collection.index_of(k))
                        item.set_rating(stars)
                        if color:
                            item.set_color(color)
                    except (ValueError, IndexError):
                        pass

            # Repaint after the write, not between writes.
            for k in key_list:
                card = self._bound.get(k)
                if card:
                    card.refresh()
                self._notify_large_view(k)
            progress.setValue(len(key_list) + 1)

    def _open_batch_exposure_dialog(self, keys: set):
        """Suggest ONE exposure value for a whole selection.

        The dialog itself now lives in gui/exposure_batch, so the Editor and
        the Trip Species Viewer offer the same one instead of each growing its
        own — this grid used to be the only place it existed, which is why it
        could be remembered and not found.
        """
        if not keys or self._collection is None:
            return
        from gui.exposure_batch import open_batch_exposure_dialog
        open_batch_exposure_dialog(
            self, self._collection, keys,
            # reload_image=True: an open Editor must re-render (and re-sync
            # its exposure slider) with the new value, not just refresh metadata
            # widgets — the image itself changed.
            on_written=lambda k: self._notify_large_view(k, reload_image=True))

    def _cull_set_cursor(self, key):
        prev = self._bound.get(self._last_clicked_key) if self._last_clicked_key else None
        if prev:
            prev.set_cursor_focus(False)
        self._last_clicked_key = key
        # The target row may be off-screen (keyboard nav) — scroll it in. If that
        # moved the viewport, _relayout re-binds; otherwise just ring the card.
        before = self.scroll_area.verticalScrollBar().value()
        self._scroll_to_key(key)
        if self.scroll_area.verticalScrollBar().value() != before or key not in self._bound:
            self._relayout(force=True)
        c = self._bound.get(key)
        if c:
            c.set_cursor_focus(True)

    def _cull_refresh_key(self, key):
        c = self._bound.get(key)
        if c:
            c.refresh()
        self._notify_large_view(key)

    def _on_swatch_double_committed(self, key):
        """A colour swatch was double-clicked on a card. The colour is already
        written; if the new colour means the card no longer matches the active
        filter, drop it from the grid live (no full rebuild) so the user can
        keep going and watch the "N of M" count fall — no Refresh, no wait."""
        self._note_collection_write()    # the write is deferred — start the flush debounce
        if key not in self._order:
            return
        if key in set(self._visible_keys()):
            return                       # still in scope (e.g. no colour filter on)
        self._drop_card_live(key)

    def _drop_card_live(self, key):
        """A picture left the active filter (e.g. double-click-a-swatch to tag +
        move on) — pull it out of `_order` and re-lay the pool. It's still in
        the day, so re-widening the filter brings it back with no rebuild."""
        if key not in self._order:
            return
        self._order.remove(key)
        self._selected_keys.discard(key)
        if self._last_clicked_key == key:
            self._last_clicked_key = None
        self._first_row = -1
        self._relayout(force=True)
        self._update_selection_display()
        self._update_title_for_filter()
        self._notify_large_view(key)

    def refresh_from_disk(self, source: str = "manual"):
        """Re-read sidecar metadata an external tool changed (PhotoLab / LR colour,
        rating, …) and repaint just the affected cards — keeps scroll + selection.
        Cheap: only files whose mtime changed are re-read. No-op for collections
        without on-disk sidecars.

        `source="watch"` marks the call as coming from the folder watcher: our
        own colour/rating write trips it ~400 ms later, and that echo is not an
        external edit — skip the full disk re-read (the rows are already current).
        F5 / the Refresh button / regaining focus still do a real refresh."""
        coll = self._collection
        if coll is None or not getattr(coll, "supports_external_refresh", False):
            return
        if source == "watch":
            echo = getattr(coll, "consume_self_write_echo", None)
            if callable(echo) and echo():
                self._rearm_sidecar_watch()
                return
        # A real refresh is about to re-read and re-persist — land any debounced
        # CSV writes first so disk and memory agree.
        flush = getattr(coll, "flush_csv", None)
        if callable(flush):
            flush()
        try:
            changed = coll.refresh()
        except Exception as e:
            logger.warning(f"Refresh from disk failed: {e}")
            return
        self._rearm_sidecar_watch()   # always: an atomic write drops the file watch
        if not changed:
            return
        image_changed = getattr(coll, "_image_changed", set())
        for k in changed:
            card = self._bound.get(k)
            if card:
                card.refresh()
                if k in image_changed:
                    card.reload_thumbnail()   # re-decode: new render / re-crop
            self._notify_large_view(k, reload_image=(k in image_changed))
        logger.info(f"Refreshed {len(changed)} picture(s) from disk (external edits).")

    def _flush_collection_csv(self):
        """Persist the collection's debounced day-CSV writes now."""
        f = getattr(self._collection, "flush_csv", None)
        if callable(f):
            f()

    def _note_collection_write(self, *_):
        """A card/large-view edit wrote through the collection — (re)start the
        debounce so the day CSVs land shortly after the burst settles."""
        t = getattr(self, "_csv_flush_timer", None)
        if t is not None:
            t.start()

    def _setup_sidecar_watch(self, image_dir):
        """Watch the day folder + its .xmp sidecars so an external colour/rating
        write (PhotoLab, Lightroom) refreshes the grid the moment it lands —
        PhotoLab flushes the XMP lazily, so waiting for window focus alone misses
        it. Debounced (a save can emit several events); only on the culling
        viewer."""
        self._fs_watcher = None
        self._fs_debounce = None
        if not getattr(self._collection, "supports_external_refresh", False):
            return
        from PyQt6.QtCore import QFileSystemWatcher
        self._fs_debounce = QTimer(self)
        self._fs_debounce.setSingleShot(True)
        self._fs_debounce.setInterval(400)
        self._fs_debounce.timeout.connect(lambda: self.refresh_from_disk("watch"))
        # Colour/rating edits defer their day-CSV write (see PreviewCollection);
        # flush ~1.2 s after the burst settles. Also flushed on focus-out/close.
        self._csv_flush_timer = QTimer(self)
        self._csv_flush_timer.setSingleShot(True)
        self._csv_flush_timer.setInterval(1200)
        self._csv_flush_timer.timeout.connect(self._flush_collection_csv)
        self._fs_watcher = QFileSystemWatcher(self)
        paths = list(self._collection.sidecar_paths())
        if image_dir and Path(image_dir).is_dir():
            paths.append(str(image_dir))      # catches new sidecars + atomic renames
        if paths:
            self._fs_watcher.addPaths(paths)
        self._fs_watcher.directoryChanged.connect(lambda _p: self._fs_debounce.start())
        self._fs_watcher.fileChanged.connect(lambda _p: self._fs_debounce.start())

    def _rearm_sidecar_watch(self):
        """Re-add sidecar paths the watcher drops after an atomic write (exiftool /
        PhotoLab write a temp file and rename, which removes the original from the
        watch list)."""
        w = getattr(self, "_fs_watcher", None)
        if w is None:
            return
        watched = set(w.files())
        for p in self._collection.sidecar_paths():
            if p not in watched and Path(p).exists():
                w.addPath(p)

    def _notify_large_view(self, key, reload_image=False):
        """Push a grid-side edit (cull colour/rating) into the open large view so
        the two windows stay in sync. reload_image=True also re-decodes the large
        image + FILE note (an external re-crop or new render). The reverse
        direction (large → grid) flows through the large view's picture_changed."""
        lv = getattr(self, "_large_view", None)
        if lv is not None:
            try:
                lv.refresh_external(key, reload_image=reload_image)
            except RuntimeError:
                pass   # large view was closed/destroyed

    def _cull_keys_target(self):
        """Selection if any, else the focused picture — what 's'/'c' act on."""
        return set(self._selected_keys) or (
            {self._last_clicked_key} if self._last_clicked_key else set())

    def _ordered(self, keys):
        """`keys` in the grid's display order, with anything no longer on
        screen dropped. A selection is a set, so it has no order of its own —
        any rule that means "the first one" needs this first."""
        wanted = set(keys)
        out = [k for k in self._order if k in wanted]
        return out or list(keys)

    def _cull_open_species(self):
        keys = self._cull_keys_target()
        if keys:
            self._open_species_dialog_for_keys(keys)

    def _cull_open_rating(self):
        keys = self._cull_keys_target()
        if keys:
            self._open_batch_rate_dialog(keys)

    def _species_subject_of(self, key):
        """This picture's stored species subject, read exactly the way the
        Set-species dialog reads it: getSubject (the literal XMP:Subject) in
        pipeline mode — NOT getSpeciesName, which would hand back the resolved
        Dutch name — and the collection's own species in culling mode."""
        if self.fpr:
            path = self._collection.rep_full_path(key)
            return (self.fpr.getSubject(path.name) or "") if path else ""
        try:
            return self._collection.item_for_key(key).species or ""
        except (ValueError, KeyError):
            return ""

    def _write_species_to_keys(self, keys, subject: str, parent=None) -> int:
        """Write `subject` to every key, in as few exiftool calls as the engine
        allows, and report how many pictures it reached (0 = nothing landed).

        The one write shared by the two dialog-free species routes — 'a' (the
        subject the selection already agreed on) and Ctrl+V (the subject in the
        clipboard). They differ only in where the string comes from; having two
        copies of the write is how the two would come to disagree about
        sidecar-authoring, the recents bump or the zero-files guard."""
        keys = list(keys)
        if not keys:
            return 0
        n = len(keys)
        if self.fpr:
            if self._frozen_day_blocked():
                return 0
            paths = [p for p in (self._collection.rep_full_path(k) for k in keys) if p]
            if not paths:
                return 0
            updated, files_written = self.fpr.updateSubjects(paths, subject)
            # A commit that writes nothing is a failed commit and has to look
            # like one — the same rule the Set-species panel states, and there
            # is no status line here to carry it, so it has to be a message box.
            if files_written == 0:
                logger.error(f"Species write: 0 files for {n} picture(s); "
                             f"{subject!r} is NOT on disk.")
                QMessageBox.warning(
                    parent or self, "Species not stored",
                    f"Nothing was written to disk, so '{subject}' is NOT stored "
                    f"on these {n} pictures. See the log.")
                return 0
            logger.info(f"Species write: {subject!r} -> {len(updated)} picture(s), "
                        f"{files_written} file(s) written.")
            self.refresh_after_species_change(updated)
            n = len(updated)
        else:
            item0 = self._collection.item_for_key(keys[0])
            if not getattr(item0, "can_edit_species", False):
                return 0
            # One exiftool call for the whole selection, same as the dialog's
            # commit — twenty-two pictures one at a time is twenty-two spawns.
            batched = getattr(self._collection, "write_tag_many", None)
            if batched is not None:
                try:
                    batched(keys, "XMP:Subject", subject, "Subject")
                except Exception as e:
                    logger.error(f"Species write (batched) failed: {e}")
                    return 0
            else:
                for k in keys:
                    try:
                        self._collection.item_for_key(k).set_species(subject)
                    except Exception as e:
                        logger.error(f"Species write failed for {k}: {e}")
            logger.info(f"Species write: {subject!r} -> {n} picture(s).")
            self.refresh_after_species_change(set(keys))
        # The dialogs bump the recents on commit; these dialog-free routes have
        # no dialog to do it, and a name used on a burst is exactly the one that
        # should be at the top of the quick-pick list next time.
        from gui.culling_species import remember_species_from_subject
        remember_species_from_subject(subject)
        self._clear_selection()
        return n

    def _cull_quick_species(self, keys=None, parent=None):
        """'a' — the dialog-free half of 's': copy the species the selection
        already agrees on onto every picture in it, in one batched write.

        The burst workflow this is for: name one guide frame, select the burst
        with [ ... ], press 'a'. Sex/age/plumage ride along, because a qualifier
        lives in the subject string next to the name ("greater ani, male").

        Whenever the selection does NOT say one unambiguous thing — nothing
        named, several different names, or just one picture to act on — this
        opens the regular Set-species dialog instead, so 'a' is never a guess.
        quick_species_subject() owns that rule.

        `keys` / `parent` are for the Quick view, which has its own cursor and
        must say what it means: this grid's cursor is not updated while that
        window is open, so falling back to it here would act on the wrong
        picture.

        Returns the number of pictures written without a dialog (0 when it fell
        back to one, or wrote nothing) — the caller uses that both to tell
        whether the selection was consumed and to report what happened."""
        keys = keys if keys else self._cull_keys_target()
        if not keys:
            return 0
        from gui.culling_species import quick_species_subject
        subject = quick_species_subject(keys, self._species_subject_of)
        if not subject:
            self._open_species_dialog_for_keys(set(keys), parent=parent)
            return 0
        return self._write_species_to_keys(keys, subject, parent=parent)

    def _on_clipboard(self, what: str, keys=None, parent=None) -> str:
        """The four copy/paste keys. Returns a one-line message for the caller
        to show — the Quick view badges it, a grid logs it.

        A grid also SHOWS the result: a pasted name appears under the card, a
        pasted fix lights the GPS badge. The messages here are therefore mostly
        for the cases where nothing visible happens — an empty buffer, a picture
        with no GPS, a collection with nowhere to write."""
        keys = list(keys) if keys else list(self._cull_keys_target())
        if not keys:
            return ""
        from gui import viewer_clipboard as clip
        plural = "s" if len(keys) != 1 else ""
        if what == "copy_species":
            subject = clip.copy_species_from(keys, self._species_subject_of)
            if not subject:
                return ("Nothing to copy — no species on these pictures, "
                        "or more than one between them.")
            logger.info(f"Copied species {subject!r} — Ctrl+V pastes it.")
            return f"Copied species: {subject}"
        if what == "paste_species":
            subject = clip.species_buffer()
            if not subject:
                return "Nothing copied yet — Ctrl+C copies a species first."
            n = self._write_species_to_keys(keys, subject, parent=parent)
            if not n:
                return ""      # _write_species_to_keys already said why
            return f"Species name set for {n} picture{'s' if n != 1 else ''}"
        if what == "copy_gps":
            # A copy is singular, so a selection has to resolve to ONE picture.
            # Take the first in DISPLAY order that actually has a fix: `keys`
            # comes from a set and has no order of its own, and the grid cursor
            # is stale whenever the Quick view is driving this. Picking the
            # first picture that can answer is deterministic either way, and on
            # a range it means "the place this group was taken".
            for k in self._ordered(keys):
                msg = clip.copy_gps_from(self._collection, k)
                if msg:
                    return msg
            return "No GPS on these pictures to copy."
        if what == "paste_gps":
            n, msg = clip.paste_gps_into(self._collection, keys,
                                         refresh_key=self._cull_refresh_key)
            if n:
                self._note_location_written(keys)
                for k in keys:
                    self._notify_large_view(k)
            logger.info(f"Paste GPS ({len(keys)} picture{plural}): {msg}")
            return msg
        return ""

    def _cull_set_color_quick(self, color: str):
        """Stamp `color` onto the selection (else the focused picture) in one
        write, no dialog — 'x' for prepping a batch as Blue before deleting —
        then advances past whatever it just stamped: one picture forward for
        a lone focus (same as the digit colour keys), or past the END of a
        selected group, clearing the selection so the next key press lands on
        a single fresh picture instead of re-acting on the group."""
        keys = self._cull_keys_target()
        if not keys or self._collection is None:
            return
        had_selection = bool(self._selected_keys)
        key_list = list(keys)
        batched = getattr(self._collection, "apply_many", None)
        if batched is not None:
            batched(key_list, new_color=color)
        else:
            for k in key_list:
                try:
                    item = self._collection.item(self._collection.index_of(k))
                    item.set_color(color)
                except (ValueError, IndexError):
                    pass
        for k in key_list:
            card = self._bound.get(k)
            if card:
                card.refresh()
            self._notify_large_view(k)
        order = list(self._order)
        idx_max = max((order.index(k) for k in key_list if k in order), default=None)
        if idx_max is not None:
            if had_selection:
                self._clear_selection()
            self._cull_set_cursor(order[min(idx_max + 1, len(order) - 1)])

    def _cull_full_res(self, keys=None):
        """Pre-decode the selection (else focused picture) to the full-res cache,
        skipping Blue/Purple. Lets you queue a big group and walk away."""
        keys = keys if keys else self._cull_keys_target()
        raws = []
        for k in keys:
            try:
                item = self._collection.item_for_key(k)
            except (ValueError, KeyError):
                continue
            if (item.color or "").capitalize() in ("Blue", "Purple"):
                continue
            r = self._collection.raw_path(k)
            if r:
                raws.append(r)
        if not raws:
            logger.info("Full resolution: nothing to decode (no RAWs / all Blue or Purple).")
            return
        from gui.fullres import run_fullres_batch
        run_fullres_batch(self, raws)

    def _cull_create_jpg(self, keys=None):
        """Bake a website-ready JPG beside each selected RAW from its embedded
        preview + crop (no scaling) — a quick blog picture. Skips a picture that
        already has a JPG so nothing is clobbered."""
        if not hasattr(self._collection, "create_blog_jpg"):
            return
        keys = keys if keys else self._cull_keys_target()
        order = list(self._order)
        ordered = [k for k in order if k in keys] or list(keys)
        created_paths, skipped, no_raw, failed, made = [], 0, 0, 0, []
        already_jpg = 0
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for k in ordered:
                res = self._collection.create_blog_jpg(k)
                st = res.get("status")
                if st == "created":
                    created_paths.append(res.get("jpg"))
                    made.append(k)
                elif st == "skipped":
                    skipped += 1
                elif st == "no_raw":
                    no_raw += 1
                elif st == "already_jpg":
                    already_jpg += 1
                else:
                    failed += 1
        finally:
            QApplication.restoreOverrideCursor()
        # Repaint the cards that gained a JPG (the filename line + JPG tag follow).
        for k in made:
            card = self._bound.get(k)
            if card:
                card.refresh()
        from gui.create_jpg_result import show_create_jpg_result
        show_create_jpg_result(self, created_paths, skipped, no_raw, failed,
                               already_jpg)

    def _cull_geolocate(self, keys=None):
        """Reverse-geocode the selection (else focused picture) in display order
        and write the location into each picture's XMP. Dedup-by-distance + the
        Nominatim throttle live in run_geolocate_batch."""
        keys = keys if keys else self._cull_keys_target()
        order = list(self._order)
        ordered = [k for k in order if k in keys] or list(keys)
        targets = []
        for k in ordered:
            try:
                item = self._collection.item_for_key(k)
            except (ValueError, KeyError):
                continue
            src = self._collection.gps_source(k)        # path only — batch reads GPS off-thread
            files = self._collection.location_targets(k)
            if src and files:
                targets.append((item.display_name or k, src, files))
        if not targets:
            logger.info("Get location: no GPS / writable files in the selection.")
            return
        from gui.geolocate import run_geolocate_batch
        run_geolocate_batch(self, targets,
                            on_done=lambda: self._refresh_location_after_geocode(ordered))

    def _can_gps_from_location(self, keys) -> bool:
        """Enabled only when there is actually something to do: a picture with NO
        GPS, some written place to look up, and a sidecar we may write to. Greys out
        on a fully-geotagged day, so the menu never offers a no-op."""
        from gui.gps_from_location import location_query
        coll = self._collection
        for k in keys:
            try:
                if coll.has_gps(k):
                    continue
                if not coll.location_targets(k):
                    continue
                if location_query(coll.read_location(k) or {}):
                    return True
            except (ValueError, KeyError, AttributeError):
                continue
        return False

    def _gps_from_location(self, keys):
        """Turn the place text these pictures already carry into an approximate
        coordinate — the mirror of 'Get location'. One lookup per distinct place (93
        Helgoland pictures = 1 query), reviewed in a table before anything is written,
        stamped MANUAL so the map will let you drag them to the exact spot after."""
        from gui.gps_from_location import run_gps_from_location
        written = run_gps_from_location(self, self._collection, list(keys))
        if not written:
            return
        for k in written:
            card = self._bound.get(k)
            if card:
                card.refresh()
            self._notify_large_view(k)
        self._note_location_written(written)
        # A picture that just gained GPS leaves the "missing GPS" filter.
        if self.no_gps_only:
            self._apply_filters()
        self._refresh_open_map()     # the map shows them now, not on the next re-open

    def _note_location_written(self, keys):
        """Flag the day after anything wrote LOCATION or GPS straight into the
        XMP — Get location, Edit location, GPS-from-place, paste GPS, a map
        placement.

        The place text and the coordinate are on disk the moment they are
        written, but data_files.csv learns them only from a Read & Summarize.
        Without the flag the viewer closes without offering that re-run, so the
        trip overview keeps reporting the old GPS/location counts and anything
        built from the CSVs is written from stale rows.

        Every one of those routes has forgotten this at least once — the map
        placement did, and Get location did. Call this and it cannot happen
        again."""
        if keys:
            self._day_edited = True

    def _refresh_location_after_geocode(self, keys):
        """After 'Get location' wrote place text into the XMP, refresh the cards'
        location lines from the just-written files in ONE batched read (so the
        new place name shows live without re-opening the viewer)."""
        import os
        from gui.geolocate import read_location_fields_batch
        src_by_key = {k: self._collection.gps_source(k) for k in keys}
        src_by_key = {k: s for k, s in src_by_key.items() if s}
        if not src_by_key:
            return
        read = read_location_fields_batch(list(src_by_key.values()))
        by_key = {k: read.get(os.path.normcase(os.path.normpath(str(src))), {})
                  for k, src in src_by_key.items()}
        self._collection.note_locations_written({k: f for k, f in by_key.items() if f})
        for k in src_by_key:
            card = self._bound.get(k)
            if card:
                card.refresh()
            self._notify_large_view(k)
        self._note_location_written(list(src_by_key))

    def _edit_location(self, keys):
        """Manually type a Location (and City/State/Country) into the XMP for the
        selected pictures — for places Nominatim can't supply (an unmapped
        reserve, so 'Get location' only finds the village/road). Writes only the
        fields the user changed; blank fields leave the existing value as-is."""
        keys = [k for k in keys if k]
        if not keys:
            return
        try:
            current = self._collection.read_location(keys[0]) or {}
        except Exception:
            current = {}
        from gui.location_edit import edit_location_dialog
        edited = edit_location_dialog(self, current, len(keys))
        if edited is None:
            return
        changed = {k: v for k, v in edited.items() if v and v != (current.get(k) or "")}
        if not changed:
            logger.info("Edit location: nothing changed.")
            return
        files = []
        for k in keys:
            files.extend(self._collection.location_targets(k))
        if not files:
            logger.info("Edit location: no writable files in the selection.")
            return
        from gui.geolocate import write_location_batch
        n = write_location_batch(files, changed, overwrite=True)
        self._collection.note_locations_written({k: changed for k in keys})
        for k in keys:
            card = self._bound.get(k)
            if card:
                card.refresh()
            self._notify_large_view(k)
        self._note_location_written(keys)
        place = changed.get("Location") or changed.get("City") or ""
        logger.info(f"Edit location: wrote {place!r} to {n} file(s) "
                    f"across {len(keys)} picture(s).")

    # ── Copy / paste GPS + place ──────────────────────────────────────────────
    def _copy_gps(self, key):
        """Context-menu "Copy GPS + place". Same buffer as Ctrl+Shift+C — the
        menu and the key are two doors into one drawer, and two drawers is how
        you end up copying with the menu and pasting something else with the
        key."""
        from gui import viewer_clipboard as clip
        msg = clip.copy_gps_from(self._collection, key)
        logger.info(msg or "Copy GPS: this picture has no GPS to copy.")

    def _paste_gps(self, keys):
        """Context-menu "Paste GPS + place" — the menu door onto Ctrl+Shift+V."""
        msg = self._on_clipboard("paste_gps", keys=keys)
        if msg:
            logger.info(msg)

    def keyPressEvent(self, event):
        from gui.viewer_keys import (handle_cull_key, handle_grid_arrows,
                                     handle_video_key, text_input_focused)
        if event.key() == Qt.Key.Key_F5:
            self.refresh_from_disk()
            event.accept()
            return
        if handle_cull_key(event, order=list(self._order),
                           current_key=self._last_clicked_key,
                           item_for_key=self._collection.item_for_key,
                           set_cursor=self._cull_set_cursor,
                           refresh_key=self._cull_refresh_key,
                           open_species=self._cull_open_species,
                           open_rating=self._cull_open_rating,
                           quick_species=self._cull_quick_species,
                           clipboard=self._cull_clipboard):
            event.accept()
            return
        if handle_grid_arrows(event, order=list(self._order),
                              current_key=self._last_clicked_key,
                              columns=self.columns, set_cursor=self._cull_set_cursor):
            event.accept()
            return
        if event.key() == Qt.Key.Key_F and not text_input_focused():
            self._cull_full_res()
            event.accept()
            return
        if event.key() == Qt.Key.Key_L and not text_input_focused():
            self._cull_geolocate()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Z and not text_input_focused():
            self._open_quick_view()
            event.accept()
            return
        if handle_video_key(event, current_key=self._last_clicked_key,
                            item_for_key=self._collection.item_for_key,
                            play=self._play_video):
            event.accept()
            return
        if event.key() == Qt.Key.Key_X and not text_input_focused():
            self._cull_set_color_quick("Blue")
            event.accept()
            return
        if (event.key() == Qt.Key.Key_A
                and (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                and not text_input_focused()):
            self._selected_keys = set(self._order)
            self._update_selection_display()
            event.accept()
            return
        if (event.key() == Qt.Key.Key_D
                and (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                and not text_input_focused()):
            self._clear_selection()
            event.accept()
            return
        # g — the keepers filter (Red+Yellow+Green) in ONE rebuild, toggling off
        # when it is already exactly that; h — clear the colour filter. Doing the
        # three colours from the toolbar is three separate grid rebuilds.
        if (event.key() in (Qt.Key.Key_G, Qt.Key.Key_H)
                and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                and not text_input_focused()):
            bar = getattr(self, "color_filter_bar", None)
            logger.debug(f"g/h colour filter: key={event.key()} bar={bar is not None} "
                         f"current={sorted(self._color_filter)}")
            if bar is not None:
                keepers = {"Red", "Yellow", "Green"}
                if event.key() == Qt.Key.Key_H:
                    bar.set_selection(None)
                else:
                    bar.set_selection(None if self._color_filter == keepers else keepers)
            event.accept()
            return
        k = event.key()
        if k == Qt.Key.Key_BracketLeft:          # [ — set/move left bracket at cursor
            if self._last_clicked_key:
                self._bracket_left = self._last_clicked_key
                self._selected_keys = self._selection_from_brackets()
                self._update_selection_display()
        elif k == Qt.Key.Key_BracketRight:       # ] — set right bracket at cursor
            if self._last_clicked_key:
                self._bracket_right = self._last_clicked_key
                self._selected_keys = self._selection_from_brackets()
                self._update_selection_display()
        elif k == Qt.Key.Key_Escape:
            if self._selected_keys:
                self._clear_selection()
            else:
                super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    # ── No-GPS refresh from disk (fpr mode only) ────────────────────────────
    def _files_of(self, upn_key):
        """(filename, file_row) pairs for every file of a UPN."""
        upn_row = self.fpr.upn.UniquePictureName_dict.get(upn_key, {})
        files = upn_row.get("Files", [])
        if not isinstance(files, list):
            return []
        out = []
        for fname in files:
            frow = self.fpr.fi.files_dict.get(fname)
            if frow:
                out.append((fname, frow))
        return out

    def _refresh_no_gps_from_disk(self):
        """Re-read GPS for the visible no-GPS pictures and refresh their badges.
        Triggered on ApplicationActive when the no-GPS filter is on — catches GPS
        that Lightroom (or another tool) wrote to the XMP sidecar while the user
        was away. The RAW file itself doesn't change, so we also probe the .xmp
        sibling explicitly."""
        if not self.fpr:
            return
        if not self.no_gps_only:
            return
        if not self.isVisible():
            return
        visible_keys = list(self._order)
        if not visible_keys:
            return

        # Per visible picture, probe every on-disk file plus its xmp sibling.
        probes_by_key = {}
        all_probes = []
        for key in visible_keys:
            probes = []
            for _fname, frow in self._files_of(key):
                p = Path(frow.get("WindowsFileName", ""))
                if p.exists():
                    probes.append(p)
                for xmp in (p.with_suffix(".xmp"), p.with_suffix(".XMP")):
                    if xmp.exists():
                        probes.append(xmp)
                        break
            probes_by_key[key] = probes
            all_probes.extend(probes)
        if not all_probes:
            return

        try:
            # Argfile, not the command line: all_probes is every picture in the day
            # that still has no GPS, which on a freshly read day is all of them —
            # far past Windows' ~32 KB limit (see run_exiftool_files, and the
            # WinError 206 that killed the sibling refresh in preview_provider).
            from engine.exif_writer import run_exiftool_files
            result = run_exiftool_files(
                ["-G", "-json", "-q",
                 "-Composite:GPSPosition",
                 "-EXIF:GPSLatitude", "-EXIF:GPSLongitude",
                 "-XMP:GPSLatitude", "-XMP:GPSLongitude"],
                [str(p) for p in all_probes], timeout=30)
        except Exception as e:
            logger.warning(f"No-GPS refresh: exiftool call failed: {e}")
            return
        if not result.stdout:
            logger.warning(
                f"No-GPS refresh: exiftool returned no stdout "
                f"(rc={result.returncode}, stderr={result.stderr.strip()!r}).")
            return
        try:
            entries = json.loads(result.stdout)
        except ValueError as e:
            logger.warning(f"No-GPS refresh: failed to parse exiftool JSON: {e}")
            return

        gps_by_source = {}
        for entry in entries:
            src = entry.get("SourceFile", "")
            gps_pos = entry.get("Composite:GPSPosition", "")
            gps_lat = entry.get("EXIF:GPSLatitude", "") or entry.get("XMP:GPSLatitude", "")
            if gps_pos or gps_lat:
                gps_by_source[src] = (gps_pos, gps_lat)

        updated_keys = set()
        updated_upns = set()
        for key, probes in probes_by_key.items():
            gps_pos = gps_lat = ""
            for p in probes:
                hit = gps_by_source.get(str(p)) or gps_by_source.get(str(p).replace("\\", "/"))
                if hit:
                    gps_pos = gps_pos or hit[0]
                    gps_lat = gps_lat or hit[1]
            if not (gps_pos or gps_lat):
                continue
            # Fill every file row of this picture so has_gps flips to True.
            for _fname, frow in self._files_of(key):
                if gps_pos and not frow.get("Composite:GPSPosition"):
                    frow["Composite:GPSPosition"] = gps_pos
                if gps_lat and not frow.get("EXIF:GPSLatitude"):
                    frow["EXIF:GPSLatitude"] = gps_lat
            upn_obj = self.fpr.upn.UniquePictureName_dict.get(key)
            if upn_obj is not None and gps_pos:
                upn_obj["GPSPosition"] = gps_pos
                updated_upns.add(key)
            updated_keys.add(key)

        if not updated_keys:
            return
        logger.info(f"No-GPS refresh: {len(updated_keys)} picture(s) now have GPS after Lightroom edit.")
        for card in list(self._bound.values()):
            if card.upn_key in updated_keys:
                card.refresh()
        # Persist so the fix survives dialog close and reaches downstream steps.
        try:
            self.fpr.fi.write_project_files()
            if updated_upns:
                self.fpr.upn.write_project_summary()
        except Exception as e:
            logger.warning(f"No-GPS refresh: failed to persist CSV(s): {e}")

    def _on_application_state_changed(self, state):
        if state != Qt.ApplicationState.ApplicationActive:
            # Leaving the app (usually to PhotoLab/LR) — land any deferred
            # colour/rating CSV writes now so the other tool / a crash can't
            # find the day CSVs behind the sidecars.
            self._flush_collection_csv()
            return
        if state == Qt.ApplicationState.ApplicationActive:
            self._refresh_no_gps_from_disk()
            self.refresh_from_disk()   # culling viewer: pick up PhotoLab/LR colour edits
            # Auto-notice a fresh render: on returning to the app (typically after a
            # DxO/LR session), scan the folders you handed to PhotoLab this session
            # for a JPG the master doesn't track yet, and badge those cards. Scoped
            # to PL-touched folders so it stays cheap; a no-op when you didn't use
            # the "open in DxO" link (the manual "➕ Add new renders" scan still
            # covers that case).
            self._scan_pending_renders(self._pl_touched_folders)

    def _on_dxo_launched(self, raw_path: str):
        """The large view opened this RAW in DxO PhotoLab — remember its folder so
        the next focus-in scan checks it for the render that comes back."""
        try:
            folder = Path(raw_path).parent
        except (TypeError, ValueError):
            return
        self._pl_touched_folders.add(
            os.path.normcase(os.path.normpath(str(folder))))

    def _scan_pending_renders(self, folders=None):
        """Run the master new-render detector over `folders` (None = every loaded
        picture) and repaint any card whose badge state changed. Silent + free on a
        collection with no master bound (pending_renders returns {})."""
        coll = self._collection
        if coll is None or not hasattr(coll, "pending_renders"):
            return
        before = set(getattr(coll, "_pending_renders", {}))
        try:
            pend = coll.pending_renders(only_folders=folders)
        except Exception as e:
            logger.warning(f"pending-render scan failed: {e}")
            return
        after = set(pend)
        for k in before | after:
            card = self._bound.get(k)
            if card:
                card.refresh()
        gained = after - before
        if gained:
            total = sum(len(pend[k]) for k in gained)
            logger.info(f"Noticed {total} new render(s) awaiting registration "
                        f"in {len(gained)} picture(s).")

    def _register_pending_render(self, key: str):
        """The amber ➕ badge was clicked — register the untracked render(s) into the
        master via the same journalled add the card menu uses, then re-scan that one
        folder so the badge clears (or stays, if the add was blocked)."""
        if not (getattr(self, "_master_ops_enabled", False)
                and not self._collection.master_read_only):
            return
        self._do_master_add(key)   # add + checkpoint + recompute Rate + rebuild grid
        folder = self._collection._folder_for_stem(key)
        if folder:
            self._scan_pending_renders({folder})

    # ── Batch delete by colour ───────────────────────────────────────────────
    def _color_delete_trash_root(self):
        """Where blue/purple deletes are trashed. Culling mode: beside the source
        folder; fpr mode (a trip day): <pictures folder>/.trash/<day>, on the same
        disk as the pictures — never the trip repository (engine/trip_trash.py).
        Subclasses (the Add-to-Master review
        window, whose trip_root is a throwaway scratch) override this so deletes
        go to a persistent, recoverable location instead.

        ⚠ image_dir is EMPTY when the viewer was opened on an explicit file list
        (the SD-card cull hands over the copied paths, not a folder). `Path("") /
        ".trash"` is the RELATIVE path `.trash`, so trashed originals landed in
        whatever the process working directory happened to be — for a dev run,
        the source repo. Fall back to the folder the pictures actually live in."""
        if self.fpr is not None and self.trip_root_dir:
            if self.image_dir and Path(self.trip_root_dir) != Path(self.image_dir):
                from engine.trip_trash import day_trash_dir
                return day_trash_dir(self.image_dir)          # a real trip day
            # Days / Unfiled lanes pass the day folder as its own root: unchanged.
            return Path(self.trip_root_dir) / ".trash" / Path(self.image_dir).name
        base = self.image_dir or self._pictures_folder()
        return Path(base) / ".trash"

    def _pictures_folder(self) -> str:
        """The folder the collection's pictures live in — for a viewer opened on a
        file list, where image_dir is empty.

        An SD-card cull spans several day folders, so this returns their COMMON
        parent (the project pictures root), which is where a .trash for the whole
        read belongs. Falls back to the first picture's own folder, and "" when
        nothing resolves (the caller then has no safe target)."""
        # file_directory() is the PictureCollection-level API every provider
        # implements (rep_full_path is preview-only — asking a SidecarCollection
        # for it raised AttributeError right where a delete needed an answer).
        folders = []
        for key in self._collection.all_keys():
            try:
                f = self._collection.file_directory(key)
            except (ValueError, KeyError):
                continue
            if f and f not in folders:
                folders.append(f)
        if not folders:
            return ""
        if len(folders) == 1:
            return folders[0]
        try:
            return os.path.commonpath(folders)
        except ValueError:      # different drives — no common root
            return folders[0]

    def _blog_protected_jpgs(self):
        """The set of JPG filenames published in this trip's blogs (every row's
        BestJPGs in <trip_root>/admin/blog_admin.csv). A trip JPG in this set stays
        protected from blue/purple delete — it is live on the website.

        Returns an empty set when the trip simply has no blogs yet (delete freely),
        but None when the file exists and could NOT be parsed — the caller then
        keeps the strict never-trash-a-JPG rule rather than guess it is safe."""
        import csv
        import ast
        if not self.trip_root_dir:
            return set()
        path = Path(self.trip_root_dir) / "admin" / "blog_admin.csv"
        if not path.exists():
            return set()               # blogged nothing yet → nothing to protect
        out = set()
        try:
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f, delimiter=";"):
                    raw = (row.get("BestJPGs", "") or "").strip()
                    if not raw:
                        continue
                    names = ast.literal_eval(raw)
                    for name in (names if isinstance(names, (list, tuple)) else []):
                        if isinstance(name, str) and name.strip():
                            out.add(name.strip())
        except Exception as e:
            logger.warning(f"Could not read {path.name} ({e}); keeping all JPGs "
                           f"protected from delete.")
            return None                # unknown → be safe, protect every JPG
        return out

    def _jpg_protection_plan(self, keys):
        """Split `keys` by JPG-delete policy → (allow_jpg_keys, blogged, strict).

        allow_jpg_keys — JPG-bearing keys that MAY be trashed (a trip cull where the
          JPG is not published in a blog). Pass these to delete_keys/delete_by_color.
        blogged — JPG-bearing keys kept because the JPG is in a blog (warn, don't delete).
        strict  — JPG-bearing keys kept by the hard rule (not a relaxed trip cull).
        Non-JPG keys never appear here — they are always deletable."""
        if not hasattr(self._collection, "jpg_keys"):
            return set(), set(), set()
        jpg_set = set(self._collection.jpg_keys())
        jpg_keys = [k for k in keys if k in jpg_set]
        if not jpg_keys:
            return set(), set(), set()
        relax = (self.fpr is not None and bool(self.trip_root_dir)
                 and getattr(self, "_allow_trip_jpg_delete", True)
                 and hasattr(self._collection, "jpg_names"))
        blog_jpgs = self._blog_protected_jpgs() if relax else None
        if not relax or blog_jpgs is None:      # not a trip cull / unreadable → strict
            return set(), set(), set(jpg_keys)
        allow, blogged = set(), set()
        for k in jpg_keys:
            (blogged if (self._collection.jpg_names(k) & blog_jpgs) else allow).add(k)
        return allow, blogged, set()

    def _batch_delete_color(self, color: str):
        if not hasattr(self._collection, 'delete_by_color'):
            return
        matching = self._collection.color_keys({color})
        if not matching:
            QMessageBox.information(self, f"No {color} pictures",
                                    f"No {color.lower()} pictures found here.")
            return
        allow_jpg_keys, blogged, strict = self._jpg_protection_plan(matching)
        protected = len(blogged) + len(strict)
        deletable = len(matching) - protected

        n = len(matching)
        lines = [f"{n} {color.lower()} picture{'s' if n != 1 else ''} found."]
        if blogged:
            lines.append(f"Kept (JPG published in a blog): {len(blogged)}")
        if strict:
            lines.append(f"Skipped (have JPG): {len(strict)}")
        lines.append(f"Will be moved to .trash: {deletable}")
        msg = "\n".join(lines)

        if deletable == 0:
            QMessageBox.information(self, f"Delete {color}", msg)
            return
        if QMessageBox.question(
                self, f"Delete {color} pictures", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return

        trash_root = self._color_delete_trash_root()
        # Moving files (esp. over a network/Dropbox path) can take a while — a
        # cancelable wait dialog so the app doesn't look stuck, and the user can
        # stop partway (whatever already moved stays moved, in .trash). Keep it
        # open (autoClose/autoReset off — Qt's default hides it the instant the
        # value hits the max) through the CSV write-back and the grid rebuild
        # too: those run after the last file move and were finishing invisibly
        # before, which is why the dialog looked done well before the app was.
        progress = QProgressDialog(
            f"Moving {n} picture{'s' if n != 1 else ''} to .trash…",
            "Cancel", 0, n, self)
        progress.setWindowTitle(f"Delete {color}")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        def _tick(done, _total):
            progress.setValue(done)
            QApplication.processEvents()
            return not progress.wasCanceled()

        if allow_jpg_keys:
            _total, _prot, deleted = self._collection.delete_by_color(
                color, trash_root, allow_jpg_keys=frozenset(allow_jpg_keys), progress_cb=_tick)
        else:
            _total, _prot, deleted = self._collection.delete_by_color(
                color, trash_root, progress_cb=_tick)
        was_cancelled = progress.wasCanceled()
        logger.info(f"Batch delete {color}: {deleted} picture(s) moved to trash "
                    f"({len(blogged)} kept — in a blog)"
                    f"{' (cancelled)' if was_cancelled else ''}.")
        if deleted:
            self._day_edited = True     # non-frozen day: offer a re-read on close
        if not was_cancelled:
            progress.setLabelText("Refreshing the view…")
            progress.setCancelButton(None)
            QApplication.processEvents()
        self._reapply_filter()
        progress.close()

    def _delete_keys_ui(self, keys):
        """Right-click Delete: trash the given keys (the clicked picture, or the whole
        selection when it is part of one), honouring the same JPG protection as
        blue-delete. Per the user's choice, this CONFIRMS ONLY when something is
        protected — a deletable picture goes straight to .trash (recoverable)."""
        if not hasattr(self._collection, "delete_keys"):
            return set()
        keys = {k for k in keys if k}
        if not keys:
            return set()
        allow_jpg_keys, blogged, strict = self._jpg_protection_plan(keys)
        protected = blogged | strict
        deletable = [k for k in keys if k not in protected]

        if protected:      # confirm-only-when-protected: say what is kept and why
            lines = []
            if blogged:
                lines.append(f"{len(blogged)} kept — the JPG is published in a blog.")
            if strict:
                lines.append(f"{len(strict)} kept — a JPG is the finished asset here.")
            if not deletable:
                QMessageBox.information(self, "Delete", "\n".join(lines))
                return set()
            lines.append("")
            lines.append(f"Move the other {len(deletable)} to .trash?")
            if QMessageBox.question(
                    self, "Delete", "\n".join(lines),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
            ) != QMessageBox.StandardButton.Yes:
                return set()

        if not deletable:
            return set()
        trash_root = self._color_delete_trash_root()
        _prot, deleted = self._collection.delete_keys(
            deletable, trash_root, allow_jpg_keys=frozenset(allow_jpg_keys))
        logger.info(f"Right-click delete: {deleted} picture(s) moved to trash "
                    f"({len(blogged)} kept — in a blog).")
        if deleted:
            self._day_edited = True     # non-frozen day: offer a re-read on close
        # Stay on the filter the user was using (e.g. Species), minus the removed.
        self._reapply_filter()
        # Whichever window asked (the grid, or the Editor borrowing this menu),
        # the open Editor must lose the trashed pictures and land on a neighbour.
        lv = getattr(self, "_large_view", None)
        if lv is not None:
            try:
                lv.drop_keys(set(deletable))
            except RuntimeError:
                pass   # large view was closed/destroyed
        return set(deletable)

    def _reapply_filter(self):
        """Rebuild the grid for the current combined filter (after a delete the
        displayed set must reflect the same radio + colour + find, minus the
        removed pictures). _set_grid_keys keeps the scroll position."""
        self._apply_filters()

    def has_unsaved_edits(self) -> bool:
        """True when this culling window holds edits not yet folded into the day's
        review CSVs — i.e. a Read & Summarize re-run is pending (the same
        condition closeEvent uses to auto-save). A pipeline step must NOT run
        while this is True: it would read the stale review CSVs and drop the edit
        (the 'I forgot to close the dialog' bug). Closing the window saves.
        See [[project_frozen_day_guard]] Bug B."""
        return ((bool(getattr(self, "_day_edited", False))
                 or bool(getattr(self, "_species_changed", False)))
                and self.fpr is not None and self._file_list is None
                and not getattr(self, "_day_frozen", False))

    # ── Close ───────────────────────────────────────────────────────────────
    def _on_sidecar_write_error(self, msg: str):
        """A colour / rating edit did not reach disk. Modal on purpose — culling
        stops until the user has seen it, because the alternative is closing the
        app and silently losing the label."""
        self._write_error_count = getattr(self, "_write_error_count", 0) + 1
        try:
            self.status_label.setText(
                f"⚠ {self._write_error_count} colour/rating edit(s) NOT saved — see the log")
        except Exception:
            pass
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(
            self, "Edit not saved",
            f"{msg}\n\nLikely a file lock — PhotoLab open on this folder, or "
            f"Dropbox mid-sync. Close whatever has the .xmp open and set the "
            f"label again; the retry already waited a moment and still failed.")

    # Independent taskbar entry: Windows hides any window with a native
    # "owner" HWND from the taskbar regardless of Qt's WindowType, and this
    # dialog keeps its Qt parent intentionally — see
    # gui.window_state.give_independent_taskbar_entry for why that's patched
    # after the fact instead of restructuring the parent/child relationship.
    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_taskbar_fix_scheduled", False):
            return
        self._taskbar_fix_scheduled = True
        from gui.window_state import give_independent_taskbar_entry
        QTimer.singleShot(0, lambda: give_independent_taskbar_entry(self))

    def closeEvent(self, event):
        save_window(self, self._geo_key, cols=self.columns)
        self._collection.cancel_pending_loads()
        # Land any debounced colour/rating day-CSV writes before we tear down or
        # trigger the pipeline re-read below.
        self._flush_collection_csv()
        # Species recognised during this session were buffered rather than
        # written one at a time; save them now, in one append per file.
        self._flush_learned_species()
        try:
            QApplication.instance().applicationStateChanged.disconnect(
                self._on_application_state_changed)
        except (TypeError, RuntimeError):
            pass
        # Refresh the trip-overview row for this day so its species/GPS health
        # markers reflect any fixes made during this session.
        try:
            from gui.trip_overview import refresh_day_health
            if self.image_dir:
                refresh_day_health(self.main_window, Path(self.image_dir).name)
        except Exception as e:
            logger.warning(f"refresh_day_health failed: {e}")
        # An edit on a NOT-yet-frozen day (species name, rating/colour, crop, or
        # a delete) changed the day on disk → offer to re-run Read & Summarize so
        # the change flows through the pipeline (the statemachine cascade also
        # invalidates WritePictures→Blog→Website→Freeze). Real day folder only;
        # NOT in culling/sidecar mode (fpr is None: culling is isolated from the
        # pipeline), and NOT on a frozen day (it was read-only — nothing changed).
        species_changed = getattr(self, "_species_changed", False)
        day_edited = getattr(self, "_day_edited", False)
        if ((species_changed or day_edited) and self.main_window
                and self.image_dir and self._file_list is None
                and self.fpr is not None
                and not getattr(self, "_day_frozen", False)
                and not getattr(self, "_suppress_rerun", False)):
            day_name = Path(self.image_dir).name
            reason = ("A species name was changed for one or more pictures."
                      if species_changed else
                      "One or more pictures were edited or deleted for this day.")
            rerun = getattr(self.main_window, "rerun_read_summarize_day", None)
            if rerun:
                # Auto-save: the re-read is fast, so save silently behind a
                # "Saving changes…" spinner rather than asking every time.
                QTimer.singleShot(0, lambda: rerun(day_name, reason, confirm=False))
        super().closeEvent(event)
