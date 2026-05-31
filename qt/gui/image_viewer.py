import json
import os
import logging
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout, QSpinBox, QPushButton, QSizePolicy,
    QLabel, QMessageBox, QHBoxLayout, QProgressDialog, QStyle)
from PyQt6.QtCore import Qt, QTimer
from widgets.trip_widgets import DirectoryFieldQt
from engine.filepicture import FilePicture
from gui.species_editor import SpeciesInfoDialog
from gui.window_state import restore_window, save_window
from gui.image_loader import AsyncImageLoader
from gui.preview_provider import PreviewCollection, FileListPreviewCollection
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


class ImagePreviewDialog(QDialog):
    """Per-picture (UPN) contact sheet, rendered from a picture-provider
    collection (the same seam the Rating Editor uses). Three modes:
      - day folder with metadata  → PreviewCollection (fpr, one card per UPN)
      - virtual-trip file_list     → FileListPreviewCollection (one card per file)
      - metadata-less day folder   → FileListPreviewCollection over discovered files
    Each card is a shared PictureCard; the badge row (status / 📝 / 📍) and the
    warnings / missing-GPS filters only apply in the fpr mode.
    """

    def __init__(self, parent=None, image_dir="", trip_root_dir="", mode="jpg",
                 file_list=None, cache_root=None, source_label=None, collection=None):
        super().__init__(parent)
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
        self.cache_dir = Path(cache_root) if cache_root else Path(image_dir) / ".preview_cache"
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
            # use the editable sidecar-backed culling collection; otherwise the
            # plain (read-only) file-list preview.
            if paths and any(Path(p).with_suffix(".xmp").exists() for p in paths):
                from gui.sidecar_provider import SidecarCollection
                self._collection = SidecarCollection(
                    paths, cache_root=self.cache_dir, loader=self._loader)
            else:
                self._collection = FileListPreviewCollection(
                    paths, cache_root=self.cache_dir, loader=self._loader)

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
            path_label = QLabel(source_label or "Virtual trip — files from master database")
            path_label.setStyleSheet("color: #444; font-style: italic;")
            dir_layout.addWidget(path_label)
        else:
            self.dir_field = DirectoryFieldQt("Image Directory", initial_path=image_dir)
            self.dir_field.field.setReadOnly(True)
            dir_layout.addWidget(self.dir_field)
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

        # Radio-style: exactly one of the three is active.
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        self._filter_group.addButton(self.warnings_btn)
        self._filter_group.addButton(self.no_gps_btn)
        self._filter_group.addButton(self.all_btn)

        self.color_filter_bar = ColorFilterBar(on_filter_changed=self.on_filter_changed)
        controls_layout.addWidget(self.color_filter_bar)

        self.column_spinner = QSpinBox()
        self.column_spinner.setRange(1, 10)
        self.column_spinner.setValue(self.columns)
        self.column_spinner.setPrefix("Images per row: ")
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
        no_gps_keys = [k for k in self._collection.no_gps_keys() if k in base] if self.fpr else []

        if not self.fpr:
            # No metadata — the warnings / no-GPS distinctions don't apply.
            self.warnings_btn.setEnabled(False)
            self.no_gps_btn.setEnabled(False)
        else:
            if not warnings_keys:
                self.warnings_btn.setEnabled(False)
            if not no_gps_keys:
                self.no_gps_btn.setEnabled(False)

        if self.fpr and warnings_keys:
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

        self._update_title_for_filter()
        if initial_keys is not None:
            QTimer.singleShot(0, lambda: self._rebuild_grid_from_keys(initial_keys))
        else:
            self.wait_dialog.close()

        QApplication.instance().applicationStateChanged.connect(
            self._on_application_state_changed)

    # ── Mode / key helpers ─────────────────────────────────────────────────
    def _base_keys(self) -> list[str]:
        """All keys for the current mode. Column 2 ('jpg') restricts to pictures
        that have a JPG; column 1 ('all') and the no-fpr modes show everything
        the collection already discovered."""
        keys = self._collection.all_keys()
        if self.mode == "jpg" and self.fpr is not None:
            jpg = set(self._collection.jpg_keys())
            keys = [k for k in keys if k in jpg]
        return keys

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
    def on_warnings_toggled(self):
        if not self.warnings_btn.isChecked():
            return  # exclusive group; defensive
        self._active_filter = "warnings"
        self.warnings_only, self.no_gps_only, self.show_all = True, False, False
        self._update_title_for_filter()
        base = set(self._base_keys())
        self._rebuild_grid_from_keys([k for k in self._collection.warning_keys() if k in base])

    def on_no_gps_toggled(self):
        if not self.no_gps_btn.isChecked():
            return
        self._active_filter = "no_gps"
        self.warnings_only, self.no_gps_only, self.show_all = False, True, False
        self._update_title_for_filter()
        base = set(self._base_keys())
        self._rebuild_grid_from_keys([k for k in self._collection.no_gps_keys() if k in base])

    def on_all_toggled(self):
        if not self.all_btn.isChecked():
            return
        self._active_filter = "all"
        self.warnings_only, self.no_gps_only, self.show_all = False, False, True
        self._update_title_for_filter()
        self._rebuild_grid_from_keys(self._base_keys())

    def on_filter_changed(self, selected_colors):
        if selected_colors is None or self.fpr is None:
            # Cleared colour filter → fall back to the active filter's set.
            if self._active_filter == "warnings":
                self.on_warnings_toggled()
            elif self._active_filter == "no_gps":
                self.on_no_gps_toggled()
            else:
                self._rebuild_grid_from_keys(self._base_keys())
            return
        base = set(self._base_keys())
        keys = [k for k in self._collection.color_keys(selected_colors) if k in base]
        self._rebuild_grid_from_keys(keys)

    def _warnings_count(self) -> int:
        if not self.fpr:
            return 0
        base = set(self._base_keys())
        return len([k for k in self._collection.warning_keys() if k in base])

    def _no_gps_count(self) -> int:
        if not self.fpr:
            return 0
        base = set(self._base_keys())
        return len([k for k in self._collection.no_gps_keys() if k in base])

    def _update_title_for_filter(self):
        base = f"Species Image Preview ({self._viewer_label})"
        if not self.fpr:
            self.setWindowTitle(base)
            return
        total = len(self._base_keys())
        if self._active_filter == "warnings":
            n = self._warnings_count()
            suffix = f"{n} warning{'s' if n != 1 else ''}"
        elif self._active_filter == "no_gps":
            suffix = f"{self._no_gps_count()} missing GPS"
        elif self._active_filter == "all":
            suffix = f"all {total} pictures"
        else:
            suffix = f"clean day — click All to view {total} pictures"
        self.setWindowTitle(f"{base} — {suffix}")

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
            if self.warnings_only:
                QMessageBox.information(self, "No Warnings", "All provided species information is correct.")
            elif self.no_gps_only:
                QMessageBox.information(self, "No Missing GPS", "All pictures have GPS coordinates.")
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
        show_status = self.fpr is not None
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

    def cancel_scan(self):
        self.cancelled = True
        self.cancel_button.setEnabled(False)

    def on_column_changed(self, value):
        self.columns = value
        self._update_title_for_filter()
        # Rebuild with the currently-displayed keys at the new card width.
        if self._active_filter is not None:
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
                             initial_selection=set(self._selected_keys))
        dlg.picture_changed.connect(self._on_large_view_changed)
        dlg.picture_navigated.connect(self._on_large_view_navigated)
        dlg.selection_changed.connect(self._on_large_view_selection_changed)
        dlg.species_committed.connect(self.refresh_after_species_change)
        self._large_view = dlg
        dlg.finished.connect(lambda _=0: setattr(self, "_large_view", None))
        dlg.show()

    def _on_large_view_changed(self, key):
        # The viewer wrote the edit via the provider; repaint that card's frame +
        # badges (preview keeps capture order, so no re-sort).
        card = self._cards_by_key.get(key)
        if card:
            card.refresh()

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
        chosen = menu.exec(QCursor.pos())
        if chosen == act_species:
            self._open_species_dialog_for_keys(keys_for_action)
        elif chosen == act_rate:
            self._open_batch_rate_dialog(keys_for_action)

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

    def keyPressEvent(self, event):
        from gui.viewer_keys import handle_cull_key
        if handle_cull_key(event, order=[c.upn_key for c in self._cards],
                           current_key=self._last_clicked_key,
                           item_for_key=self._collection.item_for_key,
                           set_cursor=self._cull_set_cursor,
                           refresh_key=self._cull_refresh_key):
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
                and self.fpr is not None):
            day_name = Path(self.image_dir).name
            rerun = getattr(self.main_window, "rerun_read_summarize_day", None)
            if rerun:
                QTimer.singleShot(0, lambda: rerun(
                    day_name, "A species name was changed for one or more pictures."))
        super().closeEvent(event)
