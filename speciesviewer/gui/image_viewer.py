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

logger = logging.getLogger("speciesviewer")

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
            btn.setToolTip(f"Filter by {name}")
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
                 readonly=False, start_species=False, allow_geotag=False,
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
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
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

        self.columns = 3
        # Restore per-machine remembered geometry + column count (.ini).
        self._geo_key = "SpeciesPreview"
        _st = restore_window(self, self._geo_key, default_size=(800, 600))
        if _st["cols"]:
            self.columns = max(1, min(10, _st["cols"]))

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
        self._loader = AsyncImageLoader(self)
        if self._injected_collection is not None:
            self._collection = self._injected_collection
        elif self.fpr is not None:
            self._collection = PreviewCollection(self.fpr, loader=self._loader)
        else:
            paths = (self._file_list if self._file_list is not None
                     else self.discover_images(image_dir))
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

        # Hand the precomputed "#" crop flags to a collection that can use them
        # (the metadata-less file-list mode). No-op for the others.
        if hasattr(self._collection, "set_crop_hints"):
            self._collection.set_crop_hints(self._crop_hints)
        if hasattr(self._collection, "set_name_hints"):
            self._collection.set_name_hints(self._name_hints, self._name_profile)

        # In a real trip cull, Blue/Purple (discard) may be set on a JPG-bearing
        # picture — the JPG can be culled unless published in a blog (enforced at
        # delete). Off elsewhere (master / plain culling / the Add-to-Master
        # scratch), where a JPG stays the finished asset.
        if isinstance(self._collection, PreviewCollection):
            self._collection.allow_jpg_discard = (
                self.fpr is not None and bool(self.trip_root_dir)
                and getattr(self, "_allow_trip_jpg_delete", True))

        # Grid state
        self._cards: list[PictureCard] = []
        self._cards_by_key: dict[str, PictureCard] = {}
        self._build_index = 0
        self._placement: list = []
        self.cancelled = False
        self._active_filter = None
        self.warnings_only = False
        self.no_gps_only = False
        self.show_all = False
        self.species_only = False
        self._start_species = start_species
        self._find_query = ""
        self._findfile_query = ""
        self._color_filter = set()          # colour overlay (combines with the rest)
        # JPG-only overlay (combines with radio + colour + find). On by default
        # when opened from the JPEG-Files cell (mode == "jpg").
        self._jpg_only = (self.mode == "jpg")
        self._find_timer = QTimer(self)     # debounce typing on big sets
        self._find_timer.setSingleShot(True)
        self._find_timer.timeout.connect(self._fire_pending_find)
        self._pending_find = None
        self._find_min_chars = 2            # don't search on a single character
        # Multi-select state
        self._selected_keys: set[str] = set()
        self._bracket_left: str | None = None
        self._bracket_right: str | None = None
        self._last_clicked_key: str | None = None

        # ── Build UI ───────────────────────────────────────────────────────
        layout = QVBoxLayout(self)

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
            self.refresh_button.clicked.connect(self.refresh_from_disk)
            dir_layout.addWidget(self.refresh_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_scan)
        dir_layout.addWidget(self.cancel_button)
        layout.addLayout(dir_layout)

        controls_layout = QHBoxLayout()

        # Filter row — three mutually-exclusive toggles managed by a
        # QButtonGroup. Buttons get disabled (greyed) when their filter would
        # be empty, so a day with no warnings and no missing-GPS files shows
        # two greyed icons at a glance — a clear "clean day" signal.
        self.warnings_btn = QPushButton("⚠️")
        self.warnings_btn.setFixedSize(24, 24)
        self.warnings_btn.setCheckable(True)
        self.warnings_btn.setToolTip("Show only pictures with incorrect species information")
        self.warnings_btn.setStyleSheet("""
            QPushButton { border: 2px solid #FFA500; border-radius: 4px; background: transparent; }
            QPushButton:checked { background: #FFA500; }
            QPushButton:disabled { border-color: #CCCCCC; color: #BBBBBB; }
        """)
        self.warnings_btn.clicked.connect(self.on_warnings_toggled)
        controls_layout.addWidget(self.warnings_btn)

        self.no_gps_btn = QPushButton("📍")
        self.no_gps_btn.setFixedSize(24, 24)
        self.no_gps_btn.setCheckable(True)
        self.no_gps_btn.setToolTip("Show only pictures without GPS coordinates")
        self.no_gps_btn.setStyleSheet("""
            QPushButton { border: 2px solid #C0392B; border-radius: 4px; background: transparent; }
            QPushButton:checked { background: #F5B7B1; }
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
            QPushButton { border: 2px solid #3498DB; border-radius: 4px; background: transparent; }
            QPushButton:checked { background: #AED6F1; }
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
            QPushButton { border: 2px solid #27AE60; border-radius: 4px; background: transparent; }
            QPushButton:checked { background: #ABEBC6; }
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
            QPushButton { border: 2px solid #16A085; border-radius: 4px; background: transparent; }
            QPushButton:checked { background: #A3E4D7; }
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
        _can_delete = hasattr(self._collection, 'delete_by_color')
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

        # ── Selection bar (hidden until ≥1 card is selected) ─────────────────
        self._sel_bar = QWidget()
        _sb_layout = QHBoxLayout(self._sel_bar)
        _sb_layout.setContentsMargins(4, 2, 4, 2)
        _sb_layout.setSpacing(8)
        self._sel_label = QLabel()
        self._sel_label.setStyleSheet("color: #1565c0; font-weight: bold;")
        _sb_layout.addWidget(self._sel_label)
        _sel_species_btn = QPushButton("Set species…")
        _sel_species_btn.clicked.connect(self._open_species_dialog_for_selection)
        _sb_layout.addWidget(_sel_species_btn)
        _sb_layout.addStretch()
        _sel_clear_btn = QPushButton("Clear selection")
        _sel_clear_btn.clicked.connect(self._clear_selection)
        _sb_layout.addWidget(_sel_clear_btn)
        self._sel_bar.setVisible(False)
        layout.addWidget(self._sel_bar)

        # Scroll area + grid (grid wrapped in a VBox with a trailing stretch so
        # rows stay top-aligned instead of expanding to fill the viewport).
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self._scroll_content = QWidget()
        _scroll_content = self._scroll_content
        _scroll_vbox = QVBoxLayout(_scroll_content)
        _scroll_vbox.setContentsMargins(0, 0, 0, 0)
        _scroll_vbox.setSpacing(0)
        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(6)
        _scroll_vbox.addWidget(self.grid_widget)
        _scroll_vbox.addStretch(1)
        self.scroll_area.setWidget(_scroll_content)
        layout.addWidget(self.scroll_area)

        # ── Initial filter cascade ─────────────────────────────────────────
        base = set(self._base_keys())
        warnings_keys = [k for k in self._collection.warning_keys() if k in base] if self.fpr else []
        no_gps_keys = [k for k in self._collection.no_gps_keys() if k in base]

        if not self.fpr:
            self.warnings_btn.setEnabled(False)
            if not no_gps_keys:
                self.no_gps_btn.setEnabled(False)
        else:
            if not warnings_keys:
                self.warnings_btn.setEnabled(False)
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
            # Clean day — both filters greyed. Don't auto-load (a big day would
            # be hard to cancel); require an explicit "All" click.
            self._active_filter = None
            initial_keys = None
        else:
            self._active_filter = "all"
            self.all_btn.setChecked(True)
            self.show_all = True
            initial_keys = self._base_keys()

        # Opened from the JPEG-Files cell: come up with All + JPG both selected
        # (the JPG subset is a small, explicit set, so auto-loading it is fine
        # even on an otherwise "don't auto-load" clean day).
        if self._jpg_only and hasattr(self._collection, "jpg_keys"):
            self._active_filter = "all"
            self.all_btn.setChecked(True)
            self.show_all = True
            initial_keys = self._visible_keys()

        self._update_title_for_filter()
        if initial_keys is not None:
            QTimer.singleShot(0, lambda: self._rebuild_grid_from_keys(initial_keys))
        else:
            self.wait_dialog.close()

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
        raw_exts = {".nef", ".orf", ".cr2", ".arw", ".rw2", ".heic"}
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
        self._rebuild_grid_from_keys(self._visible_keys())

    def _on_jpg_toggled(self, checked):
        self._jpg_only = checked
        self._apply_filters()

    def _set_radio(self, name):
        """Record the active radio subset (flags kept for the no-GPS refresh and
        the empty-grid messages) and re-apply the combined filter."""
        self._active_filter = name
        self.warnings_only = (name == "warnings")
        self.no_gps_only = (name == "no_gps")
        self.species_only = (name == "species")
        self.show_all = (name == "all")
        self._apply_filters()

    def on_warnings_toggled(self):
        if self.warnings_btn.isChecked():
            self._set_radio("warnings")

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
        dlg = TripMapView(self, self._collection, keys,
                          title=f"Map — {base}{suffix}",
                          loader=self._loader, read_only=read_only,
                          allow_geotag=geotag,
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
            card = self._cards_by_key.get(k)
            if card:
                card.refresh()
            self._notify_large_view(k)
        self._apply_filters()

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

    def _all_pictures_suffix(self, total: int) -> str:
        present = self._present_count()
        if present is not None and present < total:
            return f"all {total} pictures ({present} with files, {total - present} missing)"
        return f"all {total} pictures"

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
        overlays = self._overlay_title_parts()
        if not self.fpr:
            radio = ""
            if self._active_filter == "no_gps":
                radio = f"{self._no_gps_count()} missing GPS"
            elif self._active_filter == "all":
                radio = f"all {total} pictures"
            suffix = " · ".join(p for p in ([radio] + overlays) if p)
            self.setWindowTitle(f"{base} — {suffix}" if suffix else base)
            return
        if self._active_filter == "warnings":
            n = self._warnings_count()
            radio = f"{n} warning{'s' if n != 1 else ''}"
        elif self._active_filter == "no_gps":
            radio = f"{self._no_gps_count()} missing GPS"
        elif self._active_filter == "species":
            radio = f"{self._species_count()} with a species"
        elif self._active_filter == "all":
            radio = self._all_pictures_suffix(total)
        elif overlays:
            radio = ""  # no radio chosen but a search/colour is on → show just that
        else:
            radio = f"clean day — click All to view {total} pictures"
        suffix = " · ".join(p for p in ([radio] + overlays) if p)
        self.setWindowTitle(f"{base} — {suffix}" if suffix else base)

    # ── Grid build (batched so a big day stays responsive) ──────────────────
    def _grid_placement(self, total: int):
        """Return (row, col, span=1, card_w) for each of `total` positions —
        card width is viewport / columns regardless of how many are shown."""
        ncols = max(1, self.columns)
        spacing = self.grid_layout.spacing()
        sb = self.scroll_area.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        frame = 2 * self.scroll_area.frameWidth()
        avail = self.scroll_area.width() - sb - frame - spacing
        if avail < 100:
            avail = 760
        card_w = max(120, (avail - spacing * (ncols - 1)) // ncols)
        return [(idx // ncols, idx % ncols, 1, card_w) for idx in range(total)]

    def _clear_grid(self):
        self._collection.cancel_pending_loads()  # drop in-flight thumbnail loads
        self._cards_by_key.clear()
        for c in range(10):  # reset all possible column stretch values (spinner max = 10)
            self.grid_layout.setColumnStretch(c, 0)
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._cards.clear()

    def _rebuild_grid_from_keys(self, keys):
        self._clear_grid()
        self._selected_keys.clear()
        self._bracket_left = None
        self._bracket_right = None
        self._last_clicked_key = None
        self._update_selection_display()
        self.cancelled = False
        self._collection.rebuild(list(keys))
        self._build_index = 0
        n = len(self._collection)
        if n == 0:
            self.cancel_button.setEnabled(False)
            self.wait_dialog.close()
            if self._color_filter or self._find_query or self._findfile_query:
                pass  # a colour/find overlay narrowed it to nothing — empty grid, no popup
            elif self.warnings_only:
                QMessageBox.information(self, "No Warnings", "All provided species information is correct.")
            elif self.no_gps_only:
                QMessageBox.information(self, "No Missing GPS", "All pictures have GPS coordinates.")
            elif self.species_only:
                QMessageBox.information(self, "No Species", "No pictures have a species yet — tag them first.")
            else:
                QMessageBox.warning(self, "No Images", "No pictures found, or none selected.")
            return
        self._placement = self._grid_placement(n)
        for c in range(self.columns):
            self.grid_layout.setColumnStretch(c, 1)
        self.cancel_button.setEnabled(True)
        if not self.wait_dialog.isVisible():
            self.wait_dialog.show()
        self._build_next_batch()

    def _build_next_batch(self):
        BATCH = 20
        # Preview (fpr) hosts always show the status/📝/📍 row; a collection can
        # opt in too (the culling grid does — it carries species/desc/GPS itself).
        show_status = self.fpr is not None or getattr(self._collection, "show_status_badges", False)
        n = len(self._collection)
        for _ in range(BATCH):
            if self.cancelled or self._build_index >= n:
                self._finalize_load()
                return
            idx = self._build_index
            item = self._collection.item(idx)
            r, col, span, card_w = self._placement[idx]
            card = PictureCard(item, card_w, show_status=show_status)
            card.card_clicked.connect(self._on_card_cursor)
            card.card_double_clicked.connect(self._open_large_view)
            card.card_right_clicked.connect(self._on_card_context_menu)
            card.status_clicked.connect(self._open_species_dialog)
            self.grid_layout.addWidget(card, r, col, 1, span)
            self._cards.append(card)
            self._cards_by_key[item.key] = card
            self._build_index += 1
        if self._build_index < n:
            QTimer.singleShot(0, self._build_next_batch)
        else:
            self._finalize_load()

    def _finalize_load(self):
        self.cancel_button.setEnabled(False)
        self.wait_dialog.close()
        # Restore the scroll position after a rebuild meant to keep the user's place
        # (a delete): the grid rebuilds from the top, so without this it snaps up and
        # the app looks like it 'lost its location'. Deferred one tick so the final
        # layout geometry (and the scrollbar range) is settled before we clamp.
        pos = getattr(self, "_restore_scroll", None)
        if pos is not None:
            self._restore_scroll = None
            sb = self.scroll_area.verticalScrollBar()
            QTimer.singleShot(0, lambda: sb.setValue(min(pos, sb.maximum())))

    def cancel_scan(self):
        self.cancelled = True
        self.cancel_button.setEnabled(False)

    def on_column_changed(self, value):
        self.columns = value
        self._update_title_for_filter()
        # Rebuild with the currently-displayed keys at the new card width.
        if self._cards:
            keys = [c.upn_key for c in self._cards]
            self._rebuild_grid_from_keys(keys)

    # ── Card actions ────────────────────────────────────────────────────────
    def _open_species_dialog(self, key):
        if not self.fpr:
            self._open_culling_species_dialog({key})
            return
        full_path = self._collection.rep_full_path(key)
        species_name = self.fpr.getSpeciesName(full_path.name)
        error_msg = self.fpr.getErrorMsg(full_path.name)
        self.species_dialog = SpeciesInfoDialog(full_path, species_name, error_msg, self)
        self.species_dialog.show()

    def _on_card_cursor(self, key):
        prev = self._cards_by_key.get(self._last_clicked_key) if self._last_clicked_key else None
        if prev:
            prev.set_cursor_focus(False)
        self._last_clicked_key = key
        card = self._cards_by_key.get(key)
        if card:
            card.set_cursor_focus(True)

    def _open_large_view(self, key):
        prev = self._cards_by_key.get(self._last_clicked_key) if self._last_clicked_key else None
        if prev:
            prev.set_cursor_focus(False)
        self._last_clicked_key = key
        card = self._cards_by_key.get(key)
        if card:
            card.set_cursor_focus(True)
        from gui.large_image_view import LargeImageView
        keys = [c.upn_key for c in self._cards]
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
                             allow_delete=hasattr(self._collection, "delete_keys"))
        dlg.picture_changed.connect(self._on_large_view_changed)
        dlg.picture_image_changed.connect(self._on_large_view_image_changed)
        dlg.picture_navigated.connect(self._on_large_view_navigated)
        dlg.selection_changed.connect(self._on_large_view_selection_changed)
        dlg.species_committed.connect(self.refresh_after_species_change)
        dlg.delete_requested.connect(
            lambda ks, d=dlg: self._on_large_view_delete(d, ks))
        self._large_view = dlg
        dlg.finished.connect(lambda _=0: self._on_large_view_closed())
        dlg.show()

    def _on_large_view_closed(self):
        # Repaint every card from the live provider. Per-edit picture_changed
        # already refreshes the active card, but a card's coloured frame can fail
        # to repaint while the grid is occluded by the large view; refreshing on
        # close (now that the grid is visible again) guarantees it reflects edits.
        for card in self._cards:
            try:
                card.refresh()
            except RuntimeError:
                pass
        self._large_view = None

    def _on_large_view_changed(self, key):
        # The viewer wrote the edit via the provider; repaint that card's frame +
        # badges (preview keeps capture order, so no re-sort).
        card = self._cards_by_key.get(key)
        if card:
            card.refresh()

    def _on_large_view_image_changed(self, key):
        # A crop was applied in the large view — the card must RE-DECODE, not just
        # repaint its frame (which is all _on_large_view_changed does).
        card = self._cards_by_key.get(key)
        if card:
            try:
                card.reload_thumbnail()
            except RuntimeError:
                pass

    def _on_large_view_navigated(self, key):
        # Sync the grid cursor to the large view's current picture so the user
        # can see exactly where they are in the contact sheet.
        prev = self._cards_by_key.get(self._last_clicked_key) if self._last_clicked_key else None
        if prev:
            prev.set_cursor_focus(False)
        self._last_clicked_key = key
        card = self._cards_by_key.get(key)
        if card:
            card.set_cursor_focus(True)
            self.scroll_area.ensureWidgetVisible(card)

    def _on_large_view_selection_changed(self, keys: set):
        self._selected_keys = keys
        self._bracket_left = None
        self._bracket_right = None
        self._update_selection_display()

    def _on_large_view_delete(self, dlg, keys: set):
        """Right-click Delete from the large viewer's filmstrip: trash the keys (same
        protection + confirm-only-when-protected as the grid), refresh the grid, then
        tell the viewer to drop the removed cells and land on a neighbour."""
        deleted = self._delete_keys_ui(set(keys))
        if deleted:
            dlg.drop_keys(deleted)

    def refresh_after_species_change(self, updated_upns):
        """Called by SpeciesInfoDialog after a commit: repaint affected cards
        and mark the dialog so closeEvent can offer to re-run Read & Summarize."""
        for card in self._cards:
            if card.upn_key in updated_upns:
                card.refresh()
        if updated_upns:
            self._species_changed = True

    # ── Multi-select ────────────────────────────────────────────────────────

    def _update_selection_display(self):
        for c in self._cards:
            c.set_selected(c.upn_key in self._selected_keys)
        n = len(self._selected_keys)
        self._sel_bar.setVisible(n > 0)
        if n > 0:
            self._sel_label.setText(f"{n} picture{'s' if n != 1 else ''} selected")

    def _selection_from_brackets(self) -> set:
        order = [c.upn_key for c in self._cards]
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

    def _open_species_dialog_for_selection(self):
        if self._selected_keys:
            self._open_species_dialog_for_keys(self._selected_keys)

    def _on_card_context_menu(self, key: str):
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        keys_for_action = (self._selected_keys
                           if key in self._selected_keys and self._selected_keys
                           else {key})
        n = len(keys_for_action)
        lbl = f"{n} picture{'s' if n != 1 else ''}"
        menu = QMenu(self)
        act_species = menu.addAction(f"Set species…  ({lbl})")
        act_rate = menu.addAction(f"Set rating…  ({lbl})")
        act_fullres = menu.addAction(f"Full resolution  ({lbl})")
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
        menu.addSeparator()
        # Copy GPS + place from THIS one picture; paste into the selection. Lets a
        # no-GPS shot inherit a neighbour's location when the sv2 backfill can't
        # reach it (no GPS frame within its time/distance window).
        try:
            item = self._collection.item_for_key(key)
        except (ValueError, KeyError):
            item = None
        act_copy_gps = menu.addAction("Copy GPS + place  (this picture)")
        act_copy_gps.setEnabled(bool(item and getattr(item, "has_gps", False)))
        act_paste_gps = menu.addAction(f"Paste GPS + place  ({lbl})")
        act_paste_gps.setEnabled(getattr(self, "_gps_clipboard", None) is not None)
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
            master_handlers = self._build_master_menu(menu, key)
        chosen = menu.exec(QCursor.pos())
        if chosen in master_handlers:
            master_handlers[chosen]()
        elif chosen == act_species:
            self._open_species_dialog_for_keys(keys_for_action)
        elif chosen == act_rate:
            self._open_batch_rate_dialog(keys_for_action)
        elif chosen == act_fullres:
            self._cull_full_res(keys_for_action)
        elif chosen == act_geo:
            self._cull_geolocate(keys_for_action)
        elif chosen == act_gps_from_loc:
            self._gps_from_location(keys_for_action)
        elif chosen == act_editloc:
            self._edit_location(keys_for_action)
        elif chosen == act_copy_gps:
            self._copy_gps(key)
        elif chosen == act_paste_gps:
            self._paste_gps(keys_for_action)
        elif act_album_pic is not None and chosen == act_album_pic:
            self._set_album_picture(key)
        elif act_delete is not None and chosen == act_delete:
            self._delete_keys_ui(keys_for_action)

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

    def _open_culling_species_dialog(self, keys: set):
        """Smart species assignment for the sidecar/culling viewer (no fpr):
        write the chosen name to each selected picture's XMP:Subject."""
        keys = {k for k in keys if k}
        if not keys:
            return
        item0 = self._collection.item_for_key(next(iter(keys)))
        if not getattr(item0, "can_edit_species", False):
            return
        from gui.culling_species import CullingSpeciesDialog
        current = item0.species if len(keys) == 1 else ""
        dlg = CullingSpeciesDialog(self._culling_sc(), current, len(keys), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = dlg.chosen_name()
            for k in keys:
                try:
                    self._collection.item_for_key(k).set_species(name)
                except Exception as e:
                    logger.error(f"set_species failed for {k}: {e}")
            self.refresh_after_species_change(set(keys))
            self._clear_selection()

    def _open_species_dialog_for_keys(self, keys: set):
        if not keys:
            return
        if not self.fpr:
            self._open_culling_species_dialog(set(keys))
            return
        from gui.species_editor import SpeciesInfoPanel
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        file_paths, species_names, error_msgs = [], [], []
        for k in keys:
            path = self._collection.rep_full_path(k)
            if path:
                file_paths.append(path)
                species_names.append(self.fpr.getSpeciesName(path.name) or "")
                error_msgs.append(self.fpr.getErrorMsg(path.name) or "")
        if not file_paths:
            return
        dlg = QDialog(self)
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

    def _open_batch_rate_dialog(self, keys: set):
        if not keys or self._collection is None:
            return
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox
        from gui.rating_editor import StarWidget, ColorSelector
        n = len(keys)
        dlg = QDialog(self)
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
            progress = QProgressDialog("Updating…", None, 0, len(key_list), self)
            progress.setWindowTitle("Updating pictures")
            progress.setMinimumDuration(0)
            progress.show()
            for i, k in enumerate(key_list):
                progress.setValue(i)
                progress.setLabelText(f"Updating {i + 1} of {len(key_list)}…")
                QApplication.processEvents()
                try:
                    item = self._collection.item(self._collection.index_of(k))
                    item.set_rating(stars)
                    if color:
                        item.set_color(color)
                    card = self._cards_by_key.get(k)
                    if card:
                        card.refresh()
                    self._notify_large_view(k)
                except (ValueError, IndexError):
                    pass
            progress.setValue(len(key_list))

    def _cull_set_cursor(self, key):
        prev = self._cards_by_key.get(self._last_clicked_key) if self._last_clicked_key else None
        if prev:
            prev.set_cursor_focus(False)
        self._last_clicked_key = key
        c = self._cards_by_key.get(key)
        if c:
            c.set_cursor_focus(True)
            self.scroll_area.ensureWidgetVisible(c)

    def _cull_refresh_key(self, key):
        c = self._cards_by_key.get(key)
        if c:
            c.refresh()
        self._notify_large_view(key)

    def refresh_from_disk(self):
        """Re-read sidecar metadata an external tool changed (PhotoLab / LR colour,
        rating, …) and repaint just the affected cards — keeps scroll + selection.
        Cheap: only files whose mtime changed are re-read. No-op for collections
        without on-disk sidecars."""
        coll = self._collection
        if coll is None or not getattr(coll, "supports_external_refresh", False):
            return
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
            card = self._cards_by_key.get(k)
            if card:
                card.refresh()
                if k in image_changed:
                    card.reload_thumbnail()   # re-decode: new render / re-crop
            self._notify_large_view(k, reload_image=(k in image_changed))
        logger.info(f"Refreshed {len(changed)} picture(s) from disk (external edits).")

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
        self._fs_debounce.timeout.connect(self.refresh_from_disk)
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

    def _cull_open_species(self):
        keys = self._cull_keys_target()
        if keys:
            self._open_species_dialog_for_keys(keys)

    def _cull_open_rating(self):
        keys = self._cull_keys_target()
        if keys:
            self._open_batch_rate_dialog(keys)

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

    def _cull_geolocate(self, keys=None):
        """Reverse-geocode the selection (else focused picture) in display order
        and write the location into each picture's XMP. Dedup-by-distance + the
        Nominatim throttle live in run_geolocate_batch."""
        keys = keys if keys else self._cull_keys_target()
        order = [c.upn_key for c in self._cards]
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
            card = self._cards_by_key.get(k)
            if card:
                card.refresh()
            self._notify_large_view(k)
        # A picture that just gained GPS leaves the "missing GPS" filter.
        if self.no_gps_only:
            self._apply_filters()
        self._refresh_open_map()     # the map shows them now, not on the next re-open

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
        for k, src in src_by_key.items():
            fields = read.get(os.path.normcase(os.path.normpath(str(src))), {})
            if fields:
                self._collection.note_location_written(k, fields)
            card = self._cards_by_key.get(k)
            if card:
                card.refresh()
            self._notify_large_view(k)

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
        for k in keys:
            self._collection.note_location_written(k, changed)
            card = self._cards_by_key.get(k)
            if card:
                card.refresh()
            self._notify_large_view(k)
        place = changed.get("Location") or changed.get("City") or ""
        logger.info(f"Edit location: wrote {place!r} to {n} file(s) "
                    f"across {len(keys)} picture(s).")

    # ── Copy / paste GPS + place ──────────────────────────────────────────────
    _PLACE_FIELDS = ("Location", "City", "State", "Country", "CountryCode", "Municipality")

    def _copy_gps(self, key):
        """Copy GPS coordinates + location text from one picture to a clipboard,
        so it can be pasted onto isolated no-GPS shots nearby."""
        latlon = self._collection.gps(key)
        if not latlon:
            logger.info("Copy GPS: this picture has no GPS to copy.")
            return
        try:
            fields = self._collection.read_location(key) or {}
        except Exception:
            fields = {}
        self._gps_clipboard = {"lat": latlon[0], "lon": latlon[1], "fields": fields}
        try:
            name = self._collection.item_for_key(key).display_name or key
        except (ValueError, KeyError):
            name = key
        place = fields.get("Location") or fields.get("City") or ""
        logger.info(f"Copied GPS {latlon[0]:.6f}, {latlon[1]:.6f}"
                    f"{(' + ' + place) if place else ''} from {name} — "
                    f"right-click another picture to Paste GPS + place.")

    def _paste_gps(self, keys):
        """Write the copied GPS + place into each target picture's XMP (RAW
        untouched), refresh the cards, and clear their missing-GPS flag."""
        clip = getattr(self, "_gps_clipboard", None)
        if not clip:
            logger.info("Paste GPS: nothing copied yet — Copy GPS from a picture first.")
            return
        from gui.geolocate import write_gps, write_location_batch
        fields = clip.get("fields") or {}
        has_place = any(fields.get(f) for f in self._PLACE_FIELDS)
        n = 0
        for k in keys:
            files = self._collection.location_targets(k)
            if not files:
                continue
            write_gps(files, clip["lat"], clip["lon"])
            if has_place:
                write_location_batch(files, fields, overwrite=True)
                self._collection.note_location_written(k, fields)
            self._collection.note_gps_written(k)
            card = self._cards_by_key.get(k)
            if card:
                card.refresh()
            self._notify_large_view(k)
            n += 1
        logger.info(f"Pasted GPS {clip['lat']:.6f}, {clip['lon']:.6f}"
                    f"{' + place' if has_place else ''} into {n} picture(s).")

    def keyPressEvent(self, event):
        from gui.viewer_keys import handle_cull_key, handle_grid_arrows, text_input_focused
        if event.key() == Qt.Key.Key_F5:
            self.refresh_from_disk()
            event.accept()
            return
        if handle_cull_key(event, order=[c.upn_key for c in self._cards],
                           current_key=self._last_clicked_key,
                           item_for_key=self._collection.item_for_key,
                           set_cursor=self._cull_set_cursor,
                           refresh_key=self._cull_refresh_key,
                           open_species=self._cull_open_species,
                           open_rating=self._cull_open_rating):
            event.accept()
            return
        if handle_grid_arrows(event, order=[c.upn_key for c in self._cards],
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
        if (event.key() == Qt.Key.Key_A
                and (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                and not text_input_focused()):
            self._selected_keys = {c.upn_key for c in self._cards}
            self._update_selection_display()
            event.accept()
            return
        if (event.key() == Qt.Key.Key_D
                and (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                and not text_input_focused()):
            self._clear_selection()
            event.accept()
            return
        k = event.key()
        if k == Qt.Key.Key_BracketLeft:          # [ — set left bracket at cursor
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
        visible_keys = [c.upn_key for c in self._cards]
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
            result = subprocess.run(
                ["exiftool", "-G", "-json", "-q",
                 "-Composite:GPSPosition",
                 "-EXIF:GPSLatitude", "-EXIF:GPSLongitude",
                 "-XMP:GPSLatitude", "-XMP:GPSLongitude"]
                + [str(p) for p in all_probes],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
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
        for card in self._cards:
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
        if state == Qt.ApplicationState.ApplicationActive:
            self._refresh_no_gps_from_disk()
            self.refresh_from_disk()   # culling viewer: pick up PhotoLab/LR colour edits

    # ── Batch delete by colour ───────────────────────────────────────────────
    def _color_delete_trash_root(self):
        """Where blue/purple deletes are trashed. Culling mode: beside the source
        folder; fpr mode: trip .trash/<day>. Subclasses (the Add-to-Master review
        window, whose trip_root is a throwaway scratch) override this so deletes
        go to a persistent, recoverable location instead."""
        if self.fpr is not None and self.trip_root_dir:
            return Path(self.trip_root_dir) / ".trash" / Path(self.image_dir).name
        return Path(self.image_dir) / ".trash"

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
        if allow_jpg_keys:
            _total, _prot, deleted = self._collection.delete_by_color(
                color, trash_root, allow_jpg_keys=frozenset(allow_jpg_keys))
        else:
            _total, _prot, deleted = self._collection.delete_by_color(color, trash_root)
        logger.info(f"Batch delete {color}: {deleted} picture(s) moved to trash "
                    f"({len(blogged)} kept — in a blog).")
        self._reapply_filter()

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
        # Stay on the filter the user was using (e.g. Species), minus the removed.
        self._reapply_filter()
        return set(deletable)   # the keys the large viewer should drop

    def _reapply_filter(self):
        """Rebuild the grid for the current combined filter (after a delete the
        displayed set must reflect the same radio + colour + find, minus the removed
        pictures). Remembers the scroll position so the grid keeps the user's place
        instead of snapping to the top (restored in _finalize_load)."""
        self._restore_scroll = self.scroll_area.verticalScrollBar().value()
        self._apply_filters()

    # ── Close ───────────────────────────────────────────────────────────────
    def closeEvent(self, event):
        save_window(self, self._geo_key, cols=self.columns)
        self._collection.cancel_pending_loads()
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
        # If a species name was committed, the XMP on disk changed → offer to
        # re-run Read & Summarize for this day (real day folder only). NOT in
        # culling/sidecar mode (fpr is None): culling is deliberately isolated
        # from the pipeline, so editing a sidecar must never trigger a read.
        if (getattr(self, "_species_changed", False) and self.main_window
                and self.image_dir and self._file_list is None
                and self.fpr is not None
                and not getattr(self, "_suppress_rerun", False)):
            day_name = Path(self.image_dir).name
            rerun = getattr(self.main_window, "rerun_read_summarize_day", None)
            if rerun:
                QTimer.singleShot(0, lambda: rerun(
                    day_name, "A species name was changed for one or more pictures."))
        super().closeEvent(event)
