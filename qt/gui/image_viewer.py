import os
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout, QSpinBox, QPushButton,
    QLabel, QMessageBox, QSlider, QHBoxLayout, QProgressDialog
)
from PyQt6.QtGui import QPixmap, QImage
from PyQt6.QtCore import Qt, QTimer
from widgets.trip_widgets import DirectoryFieldQt

class ImagePreviewDialog(QDialog):
    def __init__(self, parent=None, image_dir="", mode="jpg"):
        super().__init__(parent)
        # self.setWindowTitle("Species Image Preview")
        self.resize(800, 600)
        self.mode = mode
        self.setWindowTitle(f"Species Image Preview ({Path(image_dir).name})")

        layout = QVBoxLayout(self)

        # open dialog to ask for time
        self.wait_dialog = QProgressDialog("Preparing preview...", None, 0, 0, self)
        self.wait_dialog.setWindowTitle("Please Wait")
        self.wait_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.wait_dialog.setCancelButton(None)
        self.wait_dialog.setMinimumDuration(0)
        self.wait_dialog.show()        
 
        self.columns = 3
        self.image_dir = image_dir
        self.image_widgets = []
        self.cache_dir = Path(image_dir) / ".preview_cache"
        self.cache_dir.mkdir(exist_ok=True)

        dir_layout = QHBoxLayout()
        self.dir_field = DirectoryFieldQt("Image Directory", initial_path=image_dir)
        dir_layout.addWidget(self.dir_field)
        self.cancelled = False
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)        
        dir_layout.addWidget(self.cancel_button)
        self.cancel_button.clicked.connect(self.cancel_scan)               

        layout.addLayout(dir_layout)           

        self.dir_field.field.textChanged.connect(self.on_directory_changed)        

        self.column_spinner = QSpinBox()
        self.column_spinner.setRange(1, 10)
        self.column_spinner.setValue(self.columns)
        self.column_spinner.setPrefix("Images per row: ")
        layout.addWidget(self.column_spinner)
        self.column_spinner.valueChanged.connect(self.on_column_changed)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        layout.addWidget(self.scroll_area)

        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.scroll_area.setWidget(self.grid_widget)
        QTimer.singleShot(0, lambda: self.load_images(image_dir))

    def cancel_scan(self):
        self.cancelled = True
        self.cancel_button.setEnabled(False)        

    def on_directory_changed(self):
        new_path = self.dir_field.get_path()
        if os.path.isdir(new_path):
            self.load_images(new_path)
            self.setWindowTitle(f"Species Image Preview ({self.columns} images per row)")
            self.setWindowTitle(f"Species Image Preview ({new_path})")

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
        
    def load_next_image(self):
        if self.current_index >= len(self.image_paths):
            self.cancel_button.setEnabled(False)
            self.wait_dialog.close()
            return

        img_path = self.image_paths[self.current_index]
        image_label = QLabel()

        if self.cancelled:
            pixmap = QPixmap("qt/assets/spinner.gif")  # Your frozen placeholder
        else:
            pixmap = self.get_pixmap_for_path(img_path)
            if pixmap is None:
                pixmap = QPixmap("qt/assets/spinner.gif")

        image_label.setPixmap(pixmap)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(image_label)

        max_chars = 30
        truncated_name = self.truncate_filename(img_path.name, max_chars)
        filename_label = QLabel(truncated_name)
        filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        filename_label.setToolTip(img_path.name)
        layout.addWidget(filename_label)

        row, col = divmod(self.current_index, self.columns)
        self.grid_layout.addWidget(container, row, col)
        self.image_widgets.append((image_label, container, img_path))

        self.current_index += 1
        QTimer.singleShot(50, self.load_next_image)  # Simulate async loading

        if self.current_index >= len(self.image_paths):
            self.cancel_button.setEnabled(False)
            self.on_column_changed(self.columns)
            self.wait_dialog.close()            
            return


    def load_images(self, image_dir):
        # Clear existing widgets
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

        self.image_widgets.clear()
        self.cancelled = False
        self.cancel_button.setEnabled(True)
        self.image_dir = image_dir

        path = Path(image_dir)
        if self.mode == "jpg":
            image_paths = sorted(
                f for f in path.glob("*.jpg")
                if not f.name.lower().endswith("-small.jpg")
            )
        elif self.mode == "raw":
            raw_exts = [".nef", ".orf", ".cr2", ".arw", ".rw2", ".heic"]
            image_paths = sorted(
                f for f in path.iterdir()
                if f.suffix.lower() in raw_exts
            )
        else:
            image_paths = []

        if not image_paths:
            QMessageBox.warning(self, "No Images", "No image files found in this directory.")
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
        image_width = available_width // self.columns - 20

        for image_label, _, img_path in self.image_widgets:
            pixmap = QPixmap(str(img_path)).scaledToWidth(
                image_width,
                Qt.TransformationMode.SmoothTransformation
            )
            image_label.setPixmap(pixmap)

 
