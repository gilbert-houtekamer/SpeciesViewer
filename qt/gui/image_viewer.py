import json
import os
import logging
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout, QSpinBox, QPushButton, QSizePolicy,
    QLabel, QMessageBox, QSlider, QHBoxLayout, QProgressDialog, QToolTip)
from PyQt6.QtGui import QPixmap, QImage, QFontMetrics, QIcon, QPalette, QGuiApplication
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

        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(int(screen.width() * 0.92), int(screen.height() * 0.92))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(4)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._image_label, stretch=1)

        info_parts = [str(img_path)]
        if species_name:
            info_parts.append(species_name)
        info_label = QLabel("   ".join(info_parts))
        info_label.setStyleSheet("font-size: 10px; color: #333;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

    def _fit_pixmap(self):
        if not self._pixmap or self._pixmap.isNull():
            return
        size = self._image_label.size()
        if size.width() <= 1 or size.height() <= 1:
            return
        scaled = self._pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        self._image_label.setPixmap(scaled)

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

        # Warnings-only toggle (default on when metadata is available)
        self.warnings_only = False
        self.warnings_btn = QPushButton("⚠️")
        self.warnings_btn.setFixedSize(24, 24)
        self.warnings_btn.setCheckable(True)
        self.warnings_btn.setToolTip("Show only files with warnings")
        self.warnings_btn.setStyleSheet("""
            QPushButton { border: 2px solid #FFA500; border-radius: 4px; background: transparent; }
            QPushButton:checked { background: #FFA500; }
        """)
        self.warnings_btn.clicked.connect(self.on_warnings_toggled)
        controls_layout.addWidget(self.warnings_btn)

        # No-GPS toggle: show only files whose CSV row lacks GPS coordinates.
        # Useful for finding pictures to fix in Lightroom.
        self.no_gps_only = False
        self.no_gps_btn = QPushButton("📍")
        self.no_gps_btn.setFixedSize(24, 24)
        self.no_gps_btn.setCheckable(True)
        self.no_gps_btn.setToolTip("Show only files without GPS coordinates")
        self.no_gps_btn.setStyleSheet("""
            QPushButton { border: 2px solid #C0392B; border-radius: 4px; background: transparent; }
            QPushButton:checked { background: #F5B7B1; }
        """)
        self.no_gps_btn.clicked.connect(self.on_no_gps_toggled)
        controls_layout.addWidget(self.no_gps_btn)

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

        # Default to warnings-only when metadata is available
        if self.fpr:
            self.warnings_only = True
            self.warnings_btn.setChecked(True)
            initial_paths = self.get_warnings_paths()
        else:
            initial_paths = self.all_image_paths

        QTimer.singleShot(0, lambda: self.load_images(initial_paths))

        QApplication.instance().applicationStateChanged.connect(
            self._on_application_state_changed
        )

    def discover_images(self, image_dir):
        path = Path(image_dir)
        raw_exts = {".nef", ".orf", ".cr2", ".arw", ".rw2", ".heic"}
        if self.mode == "jpg":
            return sorted(
                f for f in path.glob("*.jpg")
                if not f.name.lower().endswith("-small.jpg")
            )
        elif self.mode == "raw":
            return sorted(f for f in path.iterdir() if f.suffix.lower() in raw_exts)
        elif self.mode == "all":
            return sorted(
                f for f in path.iterdir()
                if f.suffix.lower() in raw_exts
                or (f.suffix.lower() == ".jpg" and not f.name.lower().endswith("-small.jpg"))
            )
        return []

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
        self.warnings_only = self.warnings_btn.isChecked()
        if self.warnings_only:
            self.no_gps_btn.setChecked(False)
            self.no_gps_only = False
            self.load_images(self.get_warnings_paths())
        else:
            self.load_images(self.all_image_paths)

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
        self.no_gps_only = self.no_gps_btn.isChecked()
        if self.no_gps_only:
            self.warnings_btn.setChecked(False)
            self.warnings_only = False
            self.load_images(self.get_no_gps_paths())
        else:
            self.load_images(self.all_image_paths)

    def _refresh_no_gps_from_disk(self):
        """Re-read GPS for currently-visible no-GPS images and refresh the
        filter. Triggered on ApplicationActive when the no-GPS filter is on —
        catches GPS that Lightroom (or another tool) wrote to the XMP sidecar
        while the user was away. The RAW file itself doesn't change, so we
        also probe the .xmp sibling explicitly."""
        if not self.fpr or not self.no_gps_only or not self.isVisible():
            return
        visible_paths = [p for (_, _, p) in self.image_widgets]
        if not visible_paths:
            return

        # Per visible image, probe both the file itself and its xmp sibling.
        probes_by_image = {}  # raw_path -> list of paths to query
        all_probes = []
        for raw_path in visible_paths:
            probes = [raw_path]
            xmp = raw_path.with_suffix(".xmp")
            if xmp.exists():
                probes.append(xmp)
            probes_by_image[raw_path] = probes
            all_probes.extend(probes)

        try:
            result = subprocess.run(
                ["exiftool", "-G", "-json", "-q",
                 "-Composite:GPSPosition", "-EXIF:GPSLatitude"]
                + [str(p) for p in all_probes],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
        except Exception as e:
            logger.warning(f"No-GPS refresh: exiftool call failed: {e}")
            return
        if not result.stdout:
            return
        try:
            entries = json.loads(result.stdout)
        except ValueError:
            return

        # Map SourceFile (string, as exiftool returns it) -> (gps_pos, gps_lat)
        gps_by_source = {}
        for entry in entries:
            src = entry.get("SourceFile", "")
            gps_pos = entry.get("Composite:GPSPosition", "")
            gps_lat = entry.get("EXIF:GPSLatitude", "")
            if gps_pos or gps_lat:
                gps_by_source[src] = (gps_pos, gps_lat)

        updated = 0
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
                continue
            row = self.fpr.fi.files_dict.get(raw_path.name)
            if row is None:
                continue
            if gps_pos:
                row["Composite:GPSPosition"] = gps_pos
            if gps_lat:
                row["EXIF:GPSLatitude"] = gps_lat
            updated += 1

        if updated:
            logger.info(f"No-GPS refresh: {updated} file(s) now have GPS after Lightroom edit.")
            self.load_images(self.get_no_gps_paths())

    def _on_application_state_changed(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
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

    def rebuild_grid(self):
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

        for i, (_, container, _) in enumerate(self.image_widgets):
            row, col = divmod(i, self.columns)
            self.grid_layout.addWidget(container, row, col)
 

    def on_column_changed(self, value):
        self.columns = value
        self.setWindowTitle(f"Species Image Preview ({self._viewer_label})")
        self.rebuild_grid()
        self.update_image_sizes()

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
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Image preview — scale immediately to avoid full-size flash
        pixmap = self.get_pixmap_for_path(img_path) or QPixmap("qt/assets/spinner.gif")
        if pixmap and not pixmap.isNull():
            available = self.scroll_area.viewport().width()
            if available > 0:
                image_width = available // max(self.columns, 1) - 20
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
            status_icon.setToolTip(error_msg)
        elif species_name:
            icon = QIcon(QPixmap("qt/assets/ok_icon.png").scaled(16, 16))
            status_icon.setIcon(icon)
            status_icon.setToolTip("OK")
    

        def show_species_info():
            self.species_dialog = SpeciesInfoDialog(img_path, species_name, error_msg, self)
            self.species_dialog.accepted.connect(self.load_next_image)
            self.species_dialog.rejected.connect(lambda: print("Species info dialog canceled"))
            self.species_dialog.show()

        status_icon.clicked.connect(show_species_info)
        info_layout.addWidget(status_icon)

        # Text label
        text_label = QLabel()
        max_width = container.width() - 40
        max_chars = self.compute_max_chars(text_label, max_width)
        display_text = species_name or self.truncate_filename(img_path.name, max_chars)
        text_label.setText(display_text)
        text_label.setToolTip(img_path.name)
        text_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        text_label.setFixedHeight(20)

        info_layout.addWidget(text_label)
        layout.addWidget(info_row)

        return container, image_label, img_path
    
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

        # Recompute status
        error_msg = self.fpr.getErrorMsg(img_path.name) if self.fpr else ""
        species_name = self.fpr.getSpeciesName(img_path.name) if self.fpr else ""

        if error_msg:
            icon = QIcon(QPixmap("qt/assets/warning_icon.png").scaled(16, 16))
            status_icon.setIcon(icon)
            status_icon.setToolTip(error_msg)
        elif species_name:
            icon = QIcon(QPixmap("qt/assets/ok_icon.png").scaled(16, 16))
            status_icon.setIcon(icon)
            status_icon.setToolTip("OK")
        else:
            status_icon.setIcon(QIcon())
            status_icon.setToolTip("")

        # Update label text
        max_width = container.width() - 40
        max_chars = self.compute_max_chars(text_label, max_width)
        display_text = species_name or self.truncate_filename(img_path.name, max_chars)
        text_label.setText(display_text)

    
    def add_image_to_grid(self, container, image_label, img_path, row, col):
        self.grid_layout.addWidget(container, row, col)
        self.image_widgets.append((image_label, container, img_path))
        print(f"Loaded image {self.current_index + 1} of {len(self.image_paths)}: {img_path.name}")


    def load_next_image(self):
        BATCH = 10  # images per timer tick — keeps UI responsive without 50ms per image
        for _ in range(BATCH):
            if not self.dialog_active or self.cancelled or self.current_index >= len(self.image_paths):
                self.cancel_button.setEnabled(False)
                self.wait_dialog.close()
                return

            img_path = Path(self.image_paths[self.current_index])
            container, image_label, img_path = self.create_image_widget(img_path)
            row, col = divmod(self.current_index, self.columns)
            self.add_image_to_grid(container, image_label, img_path, row, col)
            self.current_index += 1

        if self.current_index < len(self.image_paths):
            QTimer.singleShot(50, self.load_next_image)
        else:
            self.cancel_button.setEnabled(False)
            self.wait_dialog.close()
            QTimer.singleShot(0, self.update_image_sizes)

    def load_images(self, image_paths):
        self.image_paths = [Path(p) if isinstance(p, str) else p for p in image_paths]

        # Clear existing widgets
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

        self.image_widgets.clear()
        self.cancelled = False
        self.cancel_button.setEnabled(True)

        if not image_paths:
            self.cancel_button.setEnabled(False)
            self.wait_dialog.close()
            if self.warnings_only:
                QMessageBox.information(self, "No Warnings", "All provided species information is correct.")
            else:
                QMessageBox.warning(self, "No Images", "No image files found, or none selected, in this directory.")
            return

        self.image_paths = image_paths
        self.current_index = 0
        self.load_next_image()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_image_sizes()
    
    def update_image_sizes(self):
        if not self.image_widgets:
            return

        available_width = self.scroll_area.viewport().width()
        actual_columns = min(self.columns, len(self.image_widgets))
        image_width = available_width // actual_columns - 20

        for image_label, _, img_path in self.image_widgets:
            pixmap = self.get_pixmap_for_path(img_path)
            if pixmap and not pixmap.isNull():
                image_label.setPixmap(pixmap.scaledToWidth(
                    image_width,
                    Qt.TransformationMode.SmoothTransformation
                ))

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
