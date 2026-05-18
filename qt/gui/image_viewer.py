import json
import os
import logging
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout, QSpinBox, QPushButton, QSizePolicy,
    QLabel, QMessageBox, QSlider, QHBoxLayout, QProgressDialog, QToolTip, QStyle, QTextEdit)
from PyQt6.QtGui import QPixmap, QImage, QFontMetrics, QIcon, QPalette, QGuiApplication, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QTimer, QSize
from widgets.trip_widgets import DirectoryFieldQt
from engine.filepicture import FilePicture
from gui.species_editor import SpeciesInfoDialog

logger = logging.getLogger("speciesviewer")

def ensure_path(obj):
    return Path(obj) if isinstance(obj, str) else obj


class SingleImageViewer(QDialog):
    """Full-screen-like viewer for a single image, opened by clicking a thumbnail."""

    def __init__(self, parent, img_path: Path, pixmap: QPixmap, species_name: str = ""):
        super().__init__(parent)
        self.setWindowTitle(img_path.name)
        self._pixmap = pixmap
        self._img_path = img_path
        self._parent_ref = parent  # ImagePreviewDialog, holds .fpr

        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(int(screen.width() * 0.92), int(screen.height() * 0.92))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(4)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._image_label, stretch=1)

        # Path on the left, species on the right — wrapped in a fixed-width
        # widget that's resized in _fit_pixmap to match the picture's actual
        # displayed width, so both edges align with the picture's edges.
        self._info_widget = QWidget()
        info_row = QHBoxLayout(self._info_widget)
        info_row.setContentsMargins(0, 0, 0, 0)
        if pixmap and not pixmap.isNull():
            path_text = f"{img_path}  ({pixmap.width()}x{pixmap.height()})"
        else:
            path_text = str(img_path)
        path_label = QLabel(path_text)
        path_label.setStyleSheet("font-size: 10px; color: #333;")
        info_row.addWidget(path_label)
        info_row.addStretch(1)
        if species_name:
            species_label = QLabel(species_name)
            species_label.setStyleSheet("font-size: 10px; color: #333;")
            info_row.addWidget(species_label)
        info_outer = QHBoxLayout()
        info_outer.setContentsMargins(0, 0, 0, 0)
        info_outer.addStretch(1)
        info_outer.addWidget(self._info_widget)
        info_outer.addStretch(1)
        layout.addLayout(info_outer)

        # Description field (XMP:Description). Ctrl+S to save explicitly,
        # auto-save on close if the text changed. Capped at 2000px wide and
        # centered so the line length stays readable on ultra-wide screens.
        desc_label = QLabel("Enter a story about this picture to be shown on the website (optional)")
        desc_label.setStyleSheet("font-size: 11px; font-weight: bold;")
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc_label)

        self._desc_edit = QTextEdit()
        fm = self._desc_edit.fontMetrics()
        self._desc_edit.setFixedHeight(int(fm.lineSpacing() * 3.5) + 8)
        # Width is set dynamically in resizeEvent — target 1800px, fall back
        # to (window - 100) when the window is narrower.
        self._desc_edit.setAcceptRichText(False)
        fpr = getattr(parent, "fpr", None) if parent else None
        self._original_description = fpr.getDescription(img_path.name) if fpr else ""
        self._desc_edit.setPlainText(self._original_description)
        if fpr is None:
            self._desc_edit.setReadOnly(True)
            self._desc_edit.setToolTip("No metadata loaded — descriptions can't be saved.")

        desc_row = QHBoxLayout()
        desc_row.addStretch(1)
        desc_row.addWidget(self._desc_edit)
        desc_row.addStretch(1)
        layout.addLayout(desc_row)

        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self._save_description)

    def _save_description(self):
        fpr = getattr(self._parent_ref, "fpr", None) if self._parent_ref else None
        if fpr is None:
            return
        new_text = self._desc_edit.toPlainText()
        if new_text == self._original_description:
            return
        try:
            files_written = fpr.updateDescription(self._img_path.name, new_text)
            self._original_description = new_text
            logger.info(
                f"Description: wrote {files_written} on-disk file(s) for "
                f"{self._img_path.name}."
            )
        except Exception as e:
            logger.warning(
                f"Description: save failed for {self._img_path.name}: {e}"
            )

    def closeEvent(self, event):
        self._save_description()
        super().closeEvent(event)

    def _fit_pixmap(self):
        if not self._pixmap or self._pixmap.isNull():
            return
        size = self._image_label.size()
        if size.width() <= 1 or size.height() <= 1:
            return
        # Never upscale — a 1200x900 JPG blown up to 3500px wide just looks
        # blurry. If the picture's native resolution fits in the available
        # area, use it as-is; otherwise scale down preserving aspect ratio.
        src_w = self._pixmap.width()
        src_h = self._pixmap.height()
        if src_w <= size.width() and src_h <= size.height():
            scaled = self._pixmap
        else:
            scaled = self._pixmap.scaled(
                size, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self._image_label.setPixmap(scaled)
        # Match the info row + description box to the picture's actual width
        # so their edges line up with the picture's edges, regardless of how
        # much the aspect ratio leaves as letterbox margin.
        pic_w = max(200, scaled.width())
        if hasattr(self, "_info_widget"):
            self._info_widget.setFixedWidth(pic_w)
        if hasattr(self, "_desc_edit"):
            self._desc_edit.setFixedWidth(pic_w)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_pixmap()

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
    # Persistent in-memory pixmap cache shared across all dialog instances
    # key: Path, value: (QPixmap, source_mtime_or_None)
    _pixmap_cache: dict = {}

    def __init__(self, parent=None, image_dir="", trip_root_dir="", mode="jpg",
                 file_list=None, cache_root=None, source_label=None):
        super().__init__(parent)
        self.resize(800, 600)
        self.mode = mode
        self.trip_root_dir = trip_root_dir
        self.main_window = parent
        self._file_list = [Path(p) if isinstance(p, str) else p for p in file_list] \
                          if file_list is not None else None
        self._viewer_label = (f"{len(self._file_list)} files"
                              if self._file_list is not None
                              else Path(image_dir).name)
        self.setWindowTitle(f"Species Image Preview ({self._viewer_label})")

        layout = QVBoxLayout(self)

        # open dialog to ask for time
        self.wait_dialog = QProgressDialog("Preparing preview...", None, 0, 0, self)
        self.wait_dialog.setWindowTitle("Please Wait")
        self.wait_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.wait_dialog.setCancelButton(None)
        self.wait_dialog.setMinimumDuration(0)
        self.wait_dialog.show()        
        self.dialog_active = True        
 
        self.columns = 3
        self.image_dir = image_dir
        self.image_widgets = []
        # Paths whose GPS was added during this dialog session (via Lightroom
        # edit picked up by _refresh_no_gps_from_disk). Used to render a green
        # 📍 badge on the card without removing it from the no-GPS view.
        self._gps_fixed_paths = set()
        self.cache_dir = Path(cache_root) if cache_root else Path(image_dir) / ".preview_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        dir_layout = QHBoxLayout()
        self.dir_field = None
        if file_list is not None:
            path_label = QLabel(source_label or "Virtual trip — files from master database")
            path_label.setStyleSheet("color: #444; font-style: italic;")
            dir_layout.addWidget(path_label)
        else:
            self.dir_field = DirectoryFieldQt("Image Directory", initial_path=image_dir)
            self.dir_field.field.setReadOnly(True)
            dir_layout.addWidget(self.dir_field)
        self.cancelled = False
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        dir_layout.addWidget(self.cancel_button)
        self.cancel_button.clicked.connect(self.cancel_scan)

        layout.addLayout(dir_layout)

        # self.dir_field.field.textChanged.connect(self.on_directory_changed)        

        # Create a horizontal layout for the spinner and the color filter bar
        controls_layout = QHBoxLayout()

        # Filter row — three mutually-exclusive toggles managed by a
        # QButtonGroup. Buttons get disabled (greyed) when their filter would
        # be empty, so a day with no warnings and no missing-GPS files shows
        # two greyed icons at a glance — a clear "clean day" signal.
        self.warnings_only = False
        self.no_gps_only = False
        self.show_all = False

        self.warnings_btn = QPushButton("⚠️")
        self.warnings_btn.setFixedSize(24, 24)
        self.warnings_btn.setCheckable(True)
        self.warnings_btn.setToolTip("Show only files with incorrect species information")
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
        self.no_gps_btn.setToolTip("Show only files without GPS coordinates")
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
        self.all_btn.setToolTip("Show all images")
        self.all_btn.setStyleSheet("""
            QPushButton { border: 2px solid #3498DB; border-radius: 4px; background: transparent; }
            QPushButton:checked { background: #AED6F1; }
        """)
        self.all_btn.clicked.connect(self.on_all_toggled)
        controls_layout.addWidget(self.all_btn)

        # Radio-style: exactly one of the three is active. Clicking the active
        # button does nothing; clicking another switches.
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        self._filter_group.addButton(self.warnings_btn)
        self._filter_group.addButton(self.no_gps_btn)
        self._filter_group.addButton(self.all_btn)

        # ColorFilterBar instance (assuming you have one)
        self.color_filter_bar = ColorFilterBar(on_filter_changed=self.on_filter_changed)
        controls_layout.addWidget(self.color_filter_bar)

        # Column spinner setup
        self.column_spinner = QSpinBox()
        self.column_spinner.setRange(1, 10)
        self.column_spinner.setValue(self.columns)
        self.column_spinner.setPrefix("Images per row: ")
        controls_layout.addWidget(self.column_spinner)

        self.column_spinner.valueChanged.connect(self.on_column_changed)

        # Add the combined controls layout to the main layout
        layout.addLayout(controls_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        layout.addWidget(self.scroll_area)

        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.scroll_area.setWidget(self.grid_widget)
        # read metadata, if present
        projectname = Path(self.image_dir).name        
        if os.path.exists(os.path.join(self.trip_root_dir,"metadata", "review", projectname)):
            # files for edit
            self.fpr = FilePicture(None)
            
            self.fpr.readMaster(self.trip_root_dir, projectname)

            rc = self.fpr.readTripDay(self.trip_root_dir, projectname)
        else:
            self.fpr = None
        # make a list of all images based on file type (not yet filtered)
        self.all_image_paths = self._file_list if self._file_list is not None \
                               else self.discover_images(image_dir)

        # Pre-extract thumbnails for all non-cached RAW files in one batch call
        self.batch_extract_raw_thumbnails(self.all_image_paths)

        # Cascade: warnings if any → no-GPS if any → all. Disable buttons
        # whose filter has no hits so the user can see at a glance whether
        # this day has any open issues.
        warnings_paths = self.get_warnings_paths() if self.fpr else []
        no_gps_paths = self.get_no_gps_paths() if self.fpr else []

        if self.fpr and not warnings_paths:
            self.warnings_btn.setEnabled(False)
        if self.fpr and not no_gps_paths:
            self.no_gps_btn.setEnabled(False)
        if not self.fpr:
            # Without metadata, the filter distinctions don't apply.
            self.warnings_btn.setEnabled(False)
            self.no_gps_btn.setEnabled(False)

        if self.fpr and warnings_paths:
            self._active_filter = "warnings"
            self.warnings_btn.setChecked(True)
            self.warnings_only = True
            initial_paths = warnings_paths
        elif self.fpr and no_gps_paths:
            self._active_filter = "no_gps"
            self.no_gps_btn.setChecked(True)
            self.no_gps_only = True
            initial_paths = no_gps_paths
        elif self.fpr:
            # Clean day — both filters greyed. Don't auto-load (340 images
            # would be hard to cancel); require an explicit "All" click.
            self._active_filter = None
            initial_paths = None
        else:
            # No metadata — filter buttons don't apply, just show everything.
            self._active_filter = "all"
            self.all_btn.setChecked(True)
            self.show_all = True
            initial_paths = self.all_image_paths

        self._update_title_for_filter()
        if initial_paths is not None:
            QTimer.singleShot(0, lambda: self.load_images(initial_paths))
        else:
            # Nothing to load yet — close the modal "preparing preview" dialog
            # so the user can click the All button.
            self.wait_dialog.close()

        QApplication.instance().applicationStateChanged.connect(
            self._on_application_state_changed
        )

    def discover_images(self, image_dir):
        path = Path(image_dir)
        raw_exts = {".nef", ".orf", ".cr2", ".arw", ".rw2", ".heic"}
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
        # Sort by EXIF:DateTimeOriginal (lifted from the CSV) so iPhone HEIC /
        # JPG pictures interleave with Olympus ORFs in capture-time order —
        # essential for "borrow GPS from a temporally-adjacent neighbour".
        # Files without a parseable capture time fall back to filename order.
        return sorted(files, key=self._capture_time_key)

    def _capture_time_key(self, path):
        if self.fpr:
            row = self.fpr.fi.files_dict.get(path.name, {})
            dt = row.get("EXIF:DateTimeOriginal", "")
            if dt:
                return (0, dt, path.name)
        return (1, "", path.name)

    def batch_extract_raw_thumbnails(self, paths):
        """Extract embedded JPEG previews for all non-cached RAW files in two ExifTool calls."""
        raw_exts = {".nef", ".orf", ".cr2", ".arw", ".rw2"}
        to_extract = [
            p for p in paths
            if p.suffix.lower() in raw_exts
            and not (self.cache_dir / (p.stem + ".jpg")).exists()
        ]
        if not to_extract:
            return

        # First pass: PreviewImage (larger, preferred for ORF)
        # -w writes each result as <stem>.jpg into the cache dir
        for tag in ["-PreviewImage", "-ThumbnailImage"]:
            still_needed = [
                p for p in to_extract
                if not (self.cache_dir / (p.stem + ".jpg")).exists()
            ]
            if not still_needed:
                break
            try:
                subprocess.run(
                    ["exiftool", "-b", tag, "-w!", str(self.cache_dir / "%f.jpg")]
                    + [str(p) for p in still_needed],
                    capture_output=True, timeout=120
                )
            except Exception as e:
                print(f"Batch ExifTool ({tag}) failed: {e}")

    def get_warnings_paths(self):
        if not self.fpr:
            return self.all_image_paths
        return [p for p in self.all_image_paths if self.fpr.getErrorMsg(p.name)]

    def on_warnings_toggled(self):
        if not self.warnings_btn.isChecked():
            return  # exclusive group prevents this; defensive
        self._active_filter = "warnings"
        self.warnings_only = True
        self.no_gps_only = False
        self.show_all = False
        self._update_title_for_filter()
        self.load_images(self.get_warnings_paths())

    def _row_has_gps(self, filename):
        if not self.fpr:
            return True
        row = self.fpr.fi.files_dict.get(filename, {})
        return bool(row.get("Composite:GPSPosition") or row.get("EXIF:GPSLatitude"))

    def get_no_gps_paths(self):
        if not self.fpr:
            return self.all_image_paths
        return [p for p in self.all_image_paths if not self._row_has_gps(p.name)]

    def on_no_gps_toggled(self):
        if not self.no_gps_btn.isChecked():
            return
        self._active_filter = "no_gps"
        self.warnings_only = False
        self.no_gps_only = True
        self.show_all = False
        self._update_title_for_filter()
        self.load_images(self.get_no_gps_paths())

    def on_all_toggled(self):
        if not self.all_btn.isChecked():
            return
        self._active_filter = "all"
        self.warnings_only = False
        self.no_gps_only = False
        self.show_all = True
        self._update_title_for_filter()
        self.load_images(self.all_image_paths)

    def _update_title_for_filter(self):
        base = f"Species Image Preview ({self._viewer_label})"
        if not self.fpr:
            self.setWindowTitle(base)
            return
        warnings_count = len(self.get_warnings_paths())
        no_gps_count = len(self.get_no_gps_paths())
        total = len(self.all_image_paths)
        if self._active_filter == "warnings":
            suffix = f"{warnings_count} warning{'s' if warnings_count != 1 else ''}"
        elif self._active_filter == "no_gps":
            suffix = f"{no_gps_count} missing GPS"
        elif self._active_filter == "all":
            suffix = f"all {total} pictures"
        else:
            # No filter active — clean day waiting for explicit All click.
            suffix = f"clean day — click All to view {total} pictures"
        self.setWindowTitle(f"{base} — {suffix}")

    def _refresh_no_gps_from_disk(self):
        """Re-read GPS for currently-visible no-GPS images and refresh the
        filter. Triggered on ApplicationActive when the no-GPS filter is on —
        catches GPS that Lightroom (or another tool) wrote to the XMP sidecar
        while the user was away. The RAW file itself doesn't change, so we
        also probe the .xmp sibling explicitly."""
        if not self.fpr:
            logger.info("No-GPS refresh: skipped (no fpr).")
            return
        if not self.no_gps_only:
            logger.info("No-GPS refresh: skipped (filter not active).")
            return
        if not self.isVisible():
            logger.info("No-GPS refresh: skipped (dialog not visible).")
            return
        visible_paths = [p for (_, _, p) in self.image_widgets]
        if not visible_paths:
            logger.info("No-GPS refresh: skipped (no visible images).")
            return

        # Per visible image, probe both the file itself and its xmp sibling.
        probes_by_image = {}  # raw_path -> list of paths to query
        all_probes = []
        xmp_hits = 0
        xmp_misses = 0
        for raw_path in visible_paths:
            probes = [raw_path]
            xmp_lower = raw_path.with_suffix(".xmp")
            xmp_upper = raw_path.with_suffix(".XMP")
            if xmp_lower.exists():
                probes.append(xmp_lower)
                xmp_hits += 1
            elif xmp_upper.exists():
                probes.append(xmp_upper)
                xmp_hits += 1
            else:
                xmp_misses += 1
            probes_by_image[raw_path] = probes
            all_probes.extend(probes)
        logger.info(
            f"No-GPS refresh: probing {len(visible_paths)} image(s); "
            f"xmp siblings found {xmp_hits}, missing {xmp_misses}."
        )

        try:
            result = subprocess.run(
                ["exiftool", "-G", "-json", "-q",
                 "-Composite:GPSPosition",
                 "-EXIF:GPSLatitude", "-EXIF:GPSLongitude",
                 "-XMP:GPSLatitude", "-XMP:GPSLongitude",
                 "-File:FileModifyDate"]
                + [str(p) for p in all_probes],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
        except Exception as e:
            logger.warning(f"No-GPS refresh: exiftool call failed: {e}")
            return
        if not result.stdout:
            logger.warning(
                f"No-GPS refresh: exiftool returned no stdout "
                f"(rc={result.returncode}, stderr={result.stderr.strip()!r})."
            )
            return
        try:
            entries = json.loads(result.stdout)
        except ValueError as e:
            logger.warning(f"No-GPS refresh: failed to parse exiftool JSON: {e}")
            return

        # Map SourceFile (string, as exiftool returns it) -> (gps_pos, gps_lat)
        gps_by_source = {}
        entries_with_gps = 0
        xmp_modify_sample = None
        for entry in entries:
            src = entry.get("SourceFile", "")
            gps_pos = entry.get("Composite:GPSPosition", "")
            gps_lat = (entry.get("EXIF:GPSLatitude", "")
                       or entry.get("XMP:GPSLatitude", ""))
            if gps_pos or gps_lat:
                gps_by_source[src] = (gps_pos, gps_lat)
                entries_with_gps += 1
            if xmp_modify_sample is None and src.lower().endswith(".xmp"):
                xmp_modify_sample = (src, entry.get("File:FileModifyDate", "?"))
        logger.info(
            f"No-GPS refresh: exiftool returned {len(entries)} entries, "
            f"{entries_with_gps} with GPS."
        )
        if entries:
            sample_src = entries[0].get("SourceFile", "?")
            logger.info(f"No-GPS refresh: sample SourceFile from exiftool: {sample_src!r}")
        if all_probes:
            logger.info(f"No-GPS refresh: sample probe path we sent: {str(all_probes[0])!r}")
        if xmp_modify_sample:
            logger.info(
                f"No-GPS refresh: sample XMP mtime {xmp_modify_sample[1]!r} "
                f"({Path(xmp_modify_sample[0]).name})"
            )

        updated_paths = set()
        sibling_propagations = 0
        unmatched = 0
        for raw_path, probes in probes_by_image.items():
            gps_pos = ""
            gps_lat = ""
            for p in probes:
                # exiftool typically returns paths with forward slashes
                hit = gps_by_source.get(str(p)) or gps_by_source.get(str(p).replace("\\", "/"))
                if hit:
                    gps_pos = gps_pos or hit[0]
                    gps_lat = gps_lat or hit[1]
            if not (gps_pos or gps_lat):
                unmatched += 1
                continue
            row = self.fpr.fi.files_dict.get(raw_path.name)
            if row is None:
                logger.info(f"No-GPS refresh: no CSV row for {raw_path.name}.")
                continue
            if gps_pos:
                row["Composite:GPSPosition"] = gps_pos
            if gps_lat:
                row["EXIF:GPSLatitude"] = gps_lat
            updated_paths.add(raw_path)
            # Propagate GPS to UPN-siblings (XMP twin, DxO JPG derivative, …)
            # so they also clear the no-GPS filter. Same idea as the
            # sv2ReadDirExif Pass 0 sibling fill.
            upn_name = row.get("UniquePictureName")
            if upn_name:
                for sib_filename, sib_row in self.fpr.fi.files_dict.items():
                    if sib_row is row:
                        continue
                    if sib_row.get("UniquePictureName") != upn_name:
                        continue
                    if gps_pos and not sib_row.get("Composite:GPSPosition"):
                        sib_row["Composite:GPSPosition"] = gps_pos
                        sibling_propagations += 1
                    if gps_lat and not sib_row.get("EXIF:GPSLatitude"):
                        sib_row["EXIF:GPSLatitude"] = gps_lat
                    # If a visible card matches this sibling, mark fixed too.
                    for _il, _c, img_path in self.image_widgets:
                        if img_path.name == sib_filename:
                            updated_paths.add(img_path)
                            break

        logger.info(
            f"No-GPS refresh: updated {len(updated_paths)} (incl. "
            f"{sibling_propagations} sibling row(s)), still no GPS {unmatched}."
        )
        if updated_paths:
            self._gps_fixed_paths.update(updated_paths)
            logger.info(
                f"No-GPS refresh: {len(updated_paths)} file(s) now have GPS after Lightroom edit."
            )
            # Refresh the badge on each fixed card in place — don't reload the
            # grid (which would silently remove fixed files from the no-GPS view).
            for _img_label, container, img_path in self.image_widgets:
                if img_path in updated_paths:
                    self.refresh_image_status(container, img_path)

            # Mirror what species-name commit does (FilePicture.updateSubject):
            # update the UPN dict GPSPosition then persist both CSVs so the fix
            # survives dialog close and is visible to downstream pipeline steps.
            updated_upns = set()
            for raw_path in updated_paths:
                row = self.fpr.fi.files_dict.get(raw_path.name)
                if not row:
                    continue
                upn_name = row.get("UniquePictureName")
                upn_obj = self.fpr.upn.UniquePictureName_dict.get(upn_name)
                if upn_obj is None:
                    continue
                gps_pos = row.get("Composite:GPSPosition", "")
                if gps_pos:
                    upn_obj["GPSPosition"] = gps_pos
                    updated_upns.add(upn_name)
            try:
                self.fpr.fi.write_project_files()
                if updated_upns:
                    self.fpr.upn.write_project_summary()
                logger.info(
                    f"No-GPS refresh: persisted GPS to data_files.csv and "
                    f"data_pictures.csv ({len(updated_upns)} UPN(s))."
                )
            except Exception as e:
                logger.warning(f"No-GPS refresh: failed to persist CSV(s): {e}")

    def _on_application_state_changed(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
            logger.info("No-GPS refresh: ApplicationActive received.")
            self._refresh_no_gps_from_disk()

    def compute_max_chars(self, label: QLabel, max_width: int) -> int:
        font_metrics = QFontMetrics(label.font())
        avg_char_width = font_metrics.averageCharWidth()
        return max_width // avg_char_width    


    def cancel_scan(self):
        self.cancelled = True
        self.cancel_button.setEnabled(False)        

    def closeEvent(self, event):
        print(f"🧨 ImagePreviewDialog is being closed! parent {self.parent()}")
        try:
            QApplication.instance().applicationStateChanged.disconnect(
                self._on_application_state_changed
            )
        except (TypeError, RuntimeError):
            pass
        super().closeEvent(event)
        # self.dialog_active = False
        # self.cancelled = True
        # event.accept()

    def __del__(self):
        print("🧹 ImagePreviewDialog was garbage collected")    

    def truncate_filename(self, filename, max_chars):
        if len(filename) <= max_chars:
            return filename
        return "..." + filename[-(max_chars - 3):]        

    def _apply_column_stretches(self):
        # Ensure every active column claims an equal share of the viewport even
        # when it has no widget — otherwise a single visible card stretches to
        # fill the whole row (column spinner max is 10).
        for c in range(10):
            self.grid_layout.setColumnStretch(c, 0)
        for c in range(self.columns):
            self.grid_layout.setColumnStretch(c, 1)

    def rebuild_grid(self):
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

        for i, (_, container, _) in enumerate(self.image_widgets):
            row, col = divmod(i, self.columns)
            self.grid_layout.addWidget(container, row, col)
        self._apply_column_stretches()
 

    def on_column_changed(self, value):
        self.columns = value
        self._update_title_for_filter()
        self.rebuild_grid()
        # Defer one event-loop tick so the scroll-area viewport reports its
        # post-rebuild width — otherwise the first spinner change uses stale
        # dimensions and cards come out the wrong size.
        QTimer.singleShot(0, self.update_image_sizes)

    def on_slider_hover(self):
        value = self.zoom_slider.value()
        if value == 1:
            tip = "1 = large images"
        elif value == 10:
            tip = "10 = small images"
        else:
            tip = f"{value} images per row"
        self.zoom_slider.setToolTip(tip)          

    def get_pixmap_for_path(self, path):
        mtime = path.stat().st_mtime if path.exists() else None

        # Check persistent in-memory cache (mtime-validated for JPG, unconditional for RAW)
        if path in ImagePreviewDialog._pixmap_cache:
            cached_px, cached_mtime = ImagePreviewDialog._pixmap_cache[path]
            if cached_mtime is None or cached_mtime == mtime:
                return cached_px
            # JPG has changed — fall through to reload

        if path.suffix.lower() == ".jpg":
            px = QPixmap(str(path))
            ImagePreviewDialog._pixmap_cache[path] = (px, mtime)
            return px

        # RAW or HEIC — disk cache in .preview_cache/
        cached_path = self.cache_dir / (path.stem + ".jpg")
        if cached_path.exists():
            px = QPixmap(str(cached_path))
            if not px.isNull():
                ImagePreviewDialog._pixmap_cache[path] = (px, None)  # RAW never expires
                return px
            cached_path.unlink(missing_ok=True)

        # HEIC: decode via pillow-heif
        if path.suffix.lower() == ".heic":
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
                from PIL import Image
                img = Image.open(str(path))
                img.thumbnail((800, 800))
                img.save(str(cached_path), "JPEG")
                px = QPixmap(str(cached_path))
                ImagePreviewDialog._pixmap_cache[path] = (px, None)
                return px
            except Exception as e:
                print(f"Failed to decode HEIC {path.name}: {e}")
            return None

        # Other RAW (ORF, NEF, CR2, etc.) — per-file ExifTool fallback if batch missed it
        for tag in ["-PreviewImage", "-ThumbnailImage"]:
            try:
                result = subprocess.run(
                    ["exiftool", "-b", tag, str(path)],
                    capture_output=True, timeout=10
                )
                if result.returncode == 0 and result.stdout:
                    cached_path.write_bytes(result.stdout)
                    px = QPixmap(str(cached_path))
                    if not px.isNull():
                        ImagePreviewDialog._pixmap_cache[path] = (px, None)
                        return px
            except Exception as e:
                print(f"Failed to extract {tag} for {path.name}: {e}")
        return None
        

    def create_image_widget(self, img_path):
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        # Use the same dimension formula as update_image_sizes so cards paint
        # at the correct width from the first batch — and cap the container
        # so a later, slightly-different viewport doesn't widen it.
        column_width, image_width = self._compute_card_dimensions()
        container.setMaximumWidth(column_width)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Image preview — scale immediately to avoid full-size flash
        pixmap = self.get_pixmap_for_path(img_path) or QPixmap("qt/assets/spinner.gif")
        if pixmap and not pixmap.isNull():
            pixmap = pixmap.scaledToWidth(image_width, Qt.TransformationMode.SmoothTransformation)
        image_label = QLabel()
        image_label.setPixmap(pixmap)
        image_label.setStyleSheet("background-color: transparent;")
        image_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        # Border wrapper
        border_wrapper = QWidget()
        border_layout = QVBoxLayout(border_wrapper)
        border_layout.setContentsMargins(3, 3, 3, 3)
        border_layout.addWidget(image_label)

        if self.fpr:
            color = self.fpr.getColor(img_path)
            if color:
                border_wrapper.setStyleSheet(f"border: 3px solid {color}; border-radius: 6px; background-color: transparent;")

        def _open_fullscreen(_event, p=img_path):
            px = self.get_pixmap_for_path(p)
            if px and not px.isNull():
                sp = self.fpr.getSpeciesName(p.name) if self.fpr else ""
                SingleImageViewer(self, p, px, sp).exec()
                # Description may have changed; refresh the 📝 badge on this
                # card and any sibling cards sharing the same UPN.
                if self.fpr:
                    self._refresh_cards_for_upn(p)

        border_wrapper.mousePressEvent = _open_fullscreen
        border_wrapper.setCursor(Qt.CursorShape.PointingHandCursor)

        layout.addWidget(border_wrapper)

        # Info row
        info_row = QWidget()
        info_row.setObjectName("info_row")

        info_layout = QHBoxLayout(info_row)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(0)

        # Status icon
        status_icon = QPushButton()
        status_icon.setObjectName("status_icon")
        status_icon.setIconSize(QSize(16, 16))
        status_icon.setFixedSize(20, 20)
        status_icon.setFlat(True)
        status_icon.setStyleSheet("""
                background-color: white;
                color: #333;
                border: 1px solid #ccc;
                padding: 4px;
            """)      

        error_msg = self.fpr.getErrorMsg(img_path.name) if self.fpr else ""
        species_name = self.fpr.getSpeciesName(img_path.name) if self.fpr else ""

        if error_msg:
            icon = QIcon(QPixmap("qt/assets/warning_icon.png").scaled(16, 16))
            status_icon.setIcon(icon)
            status_icon.setText("")
            status_icon.setToolTip(error_msg)
        elif species_name:
            icon = QIcon(QPixmap("qt/assets/ok_icon.png").scaled(16, 16))
            status_icon.setIcon(icon)
            status_icon.setText("")
            status_icon.setToolTip("OK")
        elif self.fpr:
            # No species set — picture will be skipped by the pipeline.
            status_icon.setIcon(QIcon())
            status_icon.setText("❔")
            status_icon.setToolTip("No species set — picture will be skipped by the pipeline.")


        def show_species_info():
            self.species_dialog = SpeciesInfoDialog(img_path, species_name, error_msg, self)
            self.species_dialog.accepted.connect(self.load_next_image)
            self.species_dialog.rejected.connect(lambda: print("Species info dialog canceled"))
            self.species_dialog.show()

        status_icon.clicked.connect(show_species_info)
        info_layout.addWidget(status_icon)

        # Text label
        text_label = QLabel()
        max_width = container.width() - 80
        max_chars = self.compute_max_chars(text_label, max_width)
        display_text = species_name or self.truncate_filename(img_path.name, max_chars)
        text_label.setText(display_text)
        text_label.setToolTip(img_path.name)
        text_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        text_label.setFixedHeight(20)

        info_layout.addWidget(text_label)

        # Description badge — 📝 when the file has a non-empty XMP:Description
        # ("website-ready"), empty otherwise. Set entirely by _apply_desc_badge.
        desc_badge = QLabel()
        desc_badge.setObjectName("desc_badge")
        desc_badge.setFixedSize(20, 20)
        desc_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_desc_badge(desc_badge, img_path)
        info_layout.addWidget(desc_badge)

        # GPS badge — 📍 (has GPS) / ❓ (missing GPS). Set by _apply_gps_badge.
        gps_badge = QLabel()
        gps_badge.setObjectName("gps_badge")
        gps_badge.setFixedSize(20, 20)
        gps_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_gps_badge(gps_badge, img_path)
        info_layout.addWidget(gps_badge)

        layout.addWidget(info_row)

        return container, image_label, img_path
    
    def _apply_desc_badge(self, desc_badge: QLabel, img_path: Path):
        # 📝 = file has a non-empty XMP:Description ("website-ready").
        # The description itself goes in the tooltip so the user can preview
        # it without opening the full-screen viewer.
        desc_badge.setStyleSheet("font-size: 14px;")
        if not self.fpr:
            desc_badge.setText("")
            desc_badge.setToolTip("")
            return
        desc = self.fpr.getDescription(img_path.name)
        if desc:
            desc_badge.setText("📝")
            desc_badge.setToolTip(desc)
        else:
            desc_badge.setText("")
            desc_badge.setToolTip("")

    def _refresh_cards_for_upn(self, source_path: Path):
        """Refresh badge state on all visible cards sharing source_path's UPN.
        Called after the full-screen viewer closes — description edits affect
        every sibling row in the CSV, so the on-disk JPG twin's card should
        also reflect the new 📝."""
        if not self.fpr:
            return
        source_upn = self.fpr.fi.files_dict.get(source_path.name, {}).get("UniquePictureName")
        if not source_upn:
            return
        for _img_label, container, img_path in self.image_widgets:
            file_upn = self.fpr.fi.files_dict.get(img_path.name, {}).get("UniquePictureName")
            if file_upn == source_upn:
                self.refresh_image_status(container, img_path)

    def _apply_gps_badge(self, gps_badge: QLabel, img_path: Path):
        # 📍 = file has GPS (general indicator, like ✅ for species).
        # ❓ = file lacks GPS.
        # Tooltip distinguishes "just fixed in this session" from "already had GPS".
        gps_badge.setStyleSheet("font-size: 14px;")
        if not self.fpr:
            gps_badge.setText("")
            gps_badge.setToolTip("")
            return
        if self._row_has_gps(img_path.name):
            gps_badge.setText("📍")
            if img_path in self._gps_fixed_paths:
                gps_badge.setToolTip(
                    "GPS added since dialog opened — will be persisted to CSV on next Read Directory."
                )
            else:
                gps_badge.setToolTip("GPS coordinates present.")
        else:
            gps_badge.setText("❓")
            gps_badge.setToolTip("Missing GPS coordinates — fix in Lightroom.")

    def refresh_image_status(self, container: QWidget, img_path: Path):
        # Find the info row inside the container
        info_row = container.findChild(QWidget, "info_row")
        if not info_row:
            print(f"⚠️ Could not find info row for {img_path.name}")
            return

        # Find the status icon and label
        buttons = info_row.findChildren(QPushButton)
        labels = info_row.findChildren(QLabel)

        if not buttons or not labels:
            print(f"⚠️ Could not find status icon or label for {img_path.name}")
            return

        status_icon = buttons[0]
        text_label = labels[0]
        gps_badge = info_row.findChild(QLabel, "gps_badge")
        desc_badge = info_row.findChild(QLabel, "desc_badge")

        # Recompute status
        error_msg = self.fpr.getErrorMsg(img_path.name) if self.fpr else ""
        species_name = self.fpr.getSpeciesName(img_path.name) if self.fpr else ""

        if error_msg:
            icon = QIcon(QPixmap("qt/assets/warning_icon.png").scaled(16, 16))
            status_icon.setIcon(icon)
            status_icon.setText("")
            status_icon.setToolTip(error_msg)
        elif species_name:
            icon = QIcon(QPixmap("qt/assets/ok_icon.png").scaled(16, 16))
            status_icon.setIcon(icon)
            status_icon.setText("")
            status_icon.setToolTip("OK")
        elif self.fpr:
            status_icon.setIcon(QIcon())
            status_icon.setText("❔")
            status_icon.setToolTip("No species set — picture will be skipped by the pipeline.")
        else:
            status_icon.setIcon(QIcon())
            status_icon.setText("")
            status_icon.setToolTip("")

        # Update label text
        max_width = container.width() - 80
        max_chars = self.compute_max_chars(text_label, max_width)
        display_text = species_name or self.truncate_filename(img_path.name, max_chars)
        text_label.setText(display_text)

        if desc_badge is not None:
            self._apply_desc_badge(desc_badge, img_path)
        if gps_badge is not None:
            self._apply_gps_badge(gps_badge, img_path)

    
    def add_image_to_grid(self, container, image_label, img_path, row, col):
        self.grid_layout.addWidget(container, row, col)
        self.image_widgets.append((image_label, container, img_path))
        print(f"Loaded image {self.current_index + 1} of {len(self.image_paths)}: {img_path.name}")


    def load_next_image(self):
        BATCH = 10  # images per timer tick — keeps UI responsive without 50ms per image
        for _ in range(BATCH):
            if not self.dialog_active or self.cancelled or self.current_index >= len(self.image_paths):
                self._finalize_load()
                return

            img_path = Path(self.image_paths[self.current_index])
            container, image_label, img_path = self.create_image_widget(img_path)
            row, col = divmod(self.current_index, self.columns)
            self.add_image_to_grid(container, image_label, img_path, row, col)
            self.current_index += 1

        if self.current_index < len(self.image_paths):
            QTimer.singleShot(50, self.load_next_image)
        else:
            self._finalize_load()

    def _finalize_load(self):
        self.cancel_button.setEnabled(False)
        self.wait_dialog.close()
        # Two passes: immediate (catches the common case) and one after a
        # short delay so the scroll-area's layout has definitely settled.
        # The wait-dialog closing also kicks Qt's layout system, so the
        # initial paint otherwise reads stale dimensions.
        QTimer.singleShot(0, self.update_image_sizes)
        QTimer.singleShot(80, self.update_image_sizes)

    def load_images(self, image_paths):
        self.image_paths = [Path(p) if isinstance(p, str) else p for p in image_paths]

        # Clear existing widgets
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

        self._apply_column_stretches()
        self.image_widgets.clear()
        self.cancelled = False
        self.cancel_button.setEnabled(True)

        if not image_paths:
            self.cancel_button.setEnabled(False)
            self.wait_dialog.close()
            if self.warnings_only:
                QMessageBox.information(self, "No Warnings", "All provided species information is correct.")
            elif self.no_gps_only:
                QMessageBox.information(self, "No Missing GPS", "All pictures have GPS coordinates.")
            else:
                QMessageBox.warning(self, "No Images", "No image files found, or none selected, in this directory.")
            return

        self.image_paths = image_paths
        self.current_index = 0
        self.load_next_image()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Defer so the scroll-area's children have caught up with the new
        # dialog size — running update_image_sizes synchronously here uses
        # stale widths and leaves the initial paint wrong until a second
        # resize. A small delay (vs singleShot(0)) covers cases where Qt's
        # layout pass is queued behind our event.
        QTimer.singleShot(30, self.update_image_sizes)
    
    def _compute_card_dimensions(self):
        """Return (column_width, image_width) for the current scroll-area size
        and column count. Shared by create_image_widget (so cards paint at the
        correct size from the first batch) and update_image_sizes (so column
        changes / resizes recompute the same way)."""
        sb_extent = self.scroll_area.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        frame = 2 * self.scroll_area.frameWidth()
        spacing = self.grid_layout.spacing()
        sa_width = self.scroll_area.width()
        # Reserve scrollbar + frame + one extra "spacing" right-edge gutter.
        # Use scroll_area.width() (outer) rather than viewport().width()
        # because the latter shifts when the scrollbar appears mid-load.
        available_width = max(1, sa_width - sb_extent - frame - spacing)
        cols = max(1, self.columns)
        column_width = max(1, (available_width - spacing * (cols - 1)) // cols)
        image_width = max(1, column_width - 20)
        return column_width, image_width

    def update_image_sizes(self):
        if not self.image_widgets:
            return

        column_width, image_width = self._compute_card_dimensions()

        for image_label, container, img_path in self.image_widgets:
            pixmap = self.get_pixmap_for_path(img_path)
            if pixmap and not pixmap.isNull():
                image_label.setPixmap(pixmap.scaledToWidth(
                    image_width,
                    Qt.TransformationMode.SmoothTransformation
                ))
            # Constrain the card itself, otherwise the grid expands the
            # container to its allocated column width while the border-wrapper
            # inside stays at the image's natural width — leaving the
            # whitespace-on-the-right artifact.
            container.setMaximumWidth(column_width)

    def show_all_images(self):
        self.load_images(self.all_image_paths)

    def show_images(self, filtered):
        self.load_images(filtered)            

    def filter_images_by_color(self, selected_colors):
        if (selected_colors is None) or (self.fpr is None):
            self.show_all_images()
        else:
            filtered = []
            selected_colors_lower = {color.lower() for color in selected_colors}
            for img in self.discover_images(self.image_dir):  
                # for img in self.image_paths:
                filename = img.name
                color = self.fpr.getColor(filename) or "grey"
                if color in selected_colors_lower:
                    filtered.append(img)
            # if filtered != []:
            self.show_images(filtered)   

    def __del__(self):
        print("🧹 ImagePreviewDialog was garbage collected")

    def on_filter_changed(self, selected_color):
        self.filter_images_by_color(selected_color)
        print(f"Color filter changed to: {selected_color}")
