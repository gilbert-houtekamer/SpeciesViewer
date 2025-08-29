import os
import logging
# import sip
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout, QSpinBox, QPushButton, QSizePolicy,
    QLabel, QMessageBox, QSlider, QHBoxLayout, QProgressDialog)
from PyQt6.QtGui import QPixmap, QImage, QFontMetrics, QIcon
from PyQt6.QtCore import Qt, QTimer, QSize
from widgets.trip_widgets import DirectoryFieldQt
from engine.filepicture import FilePicture
from gui.species_editor import SpeciesInfoDialog

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
    def __init__(self, parent=None, image_dir="", trip_root_dir="", mode="jpg"):
        super().__init__(parent)
        # self.setWindowTitle("Species Image Preview")
        self.resize(800, 600)
        self.mode = mode
        self.trip_root_dir = trip_root_dir
        self.main_window = parent
        self.setWindowTitle(f"Species Image Preview ({Path(image_dir).name})")

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
        self.cache_dir = Path(image_dir) / ".preview_cache"
        self.cache_dir.mkdir(exist_ok=True)

        dir_layout = QHBoxLayout()
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
        self.all_image_paths = self.discover_images(image_dir)
        print(f"ImagePreviewDialog __init__: parent {self.parent()}")
        # self.destroyed.connect(lambda: print("⚠️ image_viewer destroyed from self.destroyed.connect in ImagePreviewDialog"))

        QTimer.singleShot(0, lambda: self.load_images(self.all_image_paths))

    def discover_images(self, image_dir):
        path = Path(image_dir)
        if self.mode == "jpg":
            return sorted(
                f for f in path.glob("*.jpg")
                if not f.name.lower().endswith("-small.jpg")
            )
        elif self.mode == "raw":
            raw_exts = [".nef", ".orf", ".cr2", ".arw", ".rw2", ".heic"]
            return sorted(
                f for f in path.iterdir()
                if f.suffix.lower() in raw_exts
            )
        return []

    def compute_max_chars(self, label: QLabel, max_width: int) -> int:
        font_metrics = QFontMetrics(label.font())
        avg_char_width = font_metrics.averageCharWidth()
        return max_width // avg_char_width    


    def cancel_scan(self):
        self.cancelled = True
        self.cancel_button.setEnabled(False)        

    def closeEvent(self, event):
        print(f"🧨 ImagePreviewDialog is being closed! parent {self.parent()}")
        super().closeEvent(event)        
        # self.dialog_active = False
        # self.cancelled = True
        # event.accept()

    def __del__(self):
        print("🧹 ImagePreviewDialog was garbage collected")    
    # too hard to get working
    # def on_directory_changed(self):
    #     new_path = self.dir_field.get_path()
    #     if os.path.isdir(new_path):
        
    #         self.image_dir = new_path
    #         self.all_image_paths = self.discover_images(new_path)
    #         self.show_all_images()
    #         self.setWindowTitle(f"Species Image Preview ({Path(new_path).name})")


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
        self.setWindowTitle(f"Species Image Preview ({self.image_dir})")
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
        if self.mode == "jpg":
            return QPixmap(str(path))

        # RAW or HEIC
        cached_path = self.cache_dir / (path.stem + ".jpg")
        if cached_path.exists():
            return QPixmap(str(cached_path))

        try:
            import rawpy
            # import numpy as np
            import imageio.v3 as iio
            with rawpy.imread(str(path)) as raw:
                rgb = raw.postprocess()
            iio.imwrite(str(cached_path), rgb)  # Save to cache
            return QPixmap(str(cached_path))            
        except Exception as e:
            print(f"Failed to load RAW file {path.name}: {e}")
            return None                  
        

    def create_image_widget(self, img_path):
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Image preview
        pixmap = self.get_pixmap_for_path(img_path) or QPixmap("qt/assets/spinner.gif")
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

        layout.addWidget(border_wrapper)

        # Info row
        info_row = QWidget()
        info_layout = QHBoxLayout(info_row)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(0)

        # Status icon
        status_icon = QPushButton()
        status_icon.setIconSize(QSize(16, 16))
        status_icon.setFixedSize(20, 20)
        status_icon.setFlat(True)
        status_icon.setStyleSheet("border: none; background-color: transparent;")

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
            self.species_dialog = SpeciesInfoDialog(img_path, species_name, error_msg, self.main_window)
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
    
    def add_image_to_grid(self, container, image_label, img_path, row, col):
        self.grid_layout.addWidget(container, row, col)
        self.image_widgets.append((image_label, container, img_path))
        print(f"Loaded image {self.current_index + 1} of {len(self.image_paths)}: {img_path.name}")


    def load_next_image(self):
        if not self.dialog_active or self.cancelled or self.current_index >= len(self.image_paths):
            self.cancel_button.setEnabled(False)
            self.wait_dialog.close()
            print("load_next_image: stopping condition met")
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
            self.on_column_changed(self.columns)
            self.wait_dialog.close()
            print("load_next_image: finished loading all images")


    # def load_next_image(self):
    #     if not self.dialog_active or self.cancelled or self.current_index >= len(self.image_paths):
    #         self.cancel_button.setEnabled(False)
    #         self.wait_dialog.close()
    #         print(f"load_next_image: not self.dialog_active or self.cancelled or self.current_index >= len(self.image_paths)")
    #         return

    #     img_path = Path(self.image_paths[self.current_index])
        

    #     if self.cancelled:
    #         pixmap = QPixmap("qt/assets/spinner.gif")  # Your frozen placeholder
    #     else:
    #         pixmap = self.get_pixmap_for_path(img_path)
    #         if pixmap is None:
    #             pixmap = QPixmap("qt/assets/spinner.gif")

    #     # Create container and layout first
    #     container = QWidget()
    #     container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    #     layout = QVBoxLayout(container)
    #     layout.setContentsMargins(6, 6, 6, 6)
    #     layout.setSpacing(4)

    #     # Inner label with image
    #     image_label = QLabel()
    #     image_label.setPixmap(pixmap)
    #     image_label.setStyleSheet("background-color: transparent;")
    #     image_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    #     # Outer frame with border
    #     border_wrapper = QWidget()
    #     border_layout = QVBoxLayout(border_wrapper)
    #     border_layout.setContentsMargins(3, 3, 3, 3)  # 1px gap between image and border
    #     border_layout.addWidget(image_label)
    #     image_label.setStyleSheet("border: none; background-color: transparent;")

    #     if self.fpr:
    #         color = self.fpr.getColor(img_path)
    #         if color:
    #             border_wrapper.setStyleSheet(f"""
    #                 border: 3px solid {color};
    #                 border-radius: 6px;
    #                 background-color: transparent;
    #             """)

    #     # ✅ Now it's safe to add the border_wrapper to the layout
    #     layout.addWidget(border_wrapper)
    #     # Species info row
    #     info_row = QWidget()
    #     info_layout = QHBoxLayout(info_row)
    #     info_layout.setContentsMargins(0, 0, 0, 0)
    #     info_layout.setSpacing(0)

    #     # (1) Status icon
    #     status_icon = QPushButton()
    #     status_icon.setIconSize(QSize(16, 16))
    #     status_icon.setFixedSize(20, 20)
    #     status_icon.setFlat(True)
    #     status_icon.setStyleSheet("border: none; background-color: transparent;")

    #     def show_species_info():
    #         self.species_dialog = SpeciesInfoDialog(img_path, species_name, error_msg, self)
    #         # Connect signals to handle dialog results
    #         self.species_dialog.accepted.connect(self.load_next_image)
    #         self.species_dialog.rejected.connect(lambda: print("Species info dialog canceled"))
    #         self.species_dialog.show()
    #     # def show_species_info():
    #     #     self.species_dialog = SpeciesInfoDialog(img_path, species_name, error_msg, self)
    #     #     self.species_dialog.exec()
 
    #     status_icon.clicked.connect(show_species_info)            

    #     error_msg = self.fpr.getErrorMsg(img_path.name) if self.fpr else ""
    #     species_name = self.fpr.getSpeciesName(img_path.name) if self.fpr else ""

    #     if error_msg:
    #         icon = QIcon(QPixmap("qt/assets/warning_icon.png").scaled(16, 16))
    #         status_icon.setIcon(icon)
    #         status_icon.setToolTip(error_msg)
    #     elif species_name:
    #         icon = QIcon(QPixmap("qt/assets/ok_icon.png").scaled(16, 16))
    #         status_icon.setIcon(icon)
    #         status_icon.setToolTip("OK")
    #     else:
    #         status_icon.setIcon(QIcon())  # Blank

    #     info_layout.addWidget(status_icon)

    #     # (2) Species name or filename
    #     text_label = QLabel()
    #     max_width = container.width() - 40  # Account for icon and padding
    #     max_chars = self.compute_max_chars(text_label, max_width)

    #     if species_name:
    #         display_text = species_name
    #         text_label.setToolTip(img_path.name)
    #     else:
    #         display_text = self.truncate_filename(img_path.name, max_chars)
    #         text_label.setToolTip(img_path.name)

    #     text_label.setText(display_text)
    #     text_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
    #     text_label.setFixedHeight(20)

    #     info_layout.addWidget(text_label)
    #     layout.addWidget(info_row)

    #     row, col = divmod(self.current_index, self.columns)

    #     # if self.grid_layout is None or sip.isdeleted(self.grid_layout):
    #     #     logger.warning("Grid layout was deleted — skipping image load.")
    #     #     return        
    #     # if self.grid_layout is not None and self.grid_layout.parent():
    #     self.grid_layout.addWidget(container, row, col)        
    #     self.image_widgets.append((image_label, container, img_path))

    #     print(f"Loaded image {self.current_index + 1} of {len(self.image_paths)}: {img_path.name}")
    #     print(f"ImagePreviewDialog load_next_image: parent {self.parent()}")        

    #     self.current_index += 1
    #     QTimer.singleShot(50, self.load_next_image)  # Simulate async loading

    #     if self.current_index >= len(self.image_paths):
    #         self.cancel_button.setEnabled(False)
    #         self.on_column_changed(self.columns)
    #         self.wait_dialog.close()    
    #         print(f"load_next_image: close on self.current_index >= len(self.image_paths)")                    
    #         return        

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
            pixmap = QPixmap(str(img_path)).scaledToWidth(
                image_width,
                Qt.TransformationMode.SmoothTransformation
            )
            image_label.setPixmap(pixmap)    

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
