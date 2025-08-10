import os
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout,
    QLabel, QMessageBox, QSlider, QHBoxLayout
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt
from widgets.trip_widgets import DirectoryFieldQt

class ImagePreviewDialog(QDialog):
    def __init__(self, parent=None, image_dir=""):
        super().__init__(parent)
        # self.setWindowTitle("Species Image Preview")
        self.resize(800, 600)

        self.columns = 3
        self.image_dir = image_dir
        self.image_widgets = []

        layout = QVBoxLayout(self)

        self.dir_field = DirectoryFieldQt("Image Directory", initial_path=image_dir)
        layout.addWidget(self.dir_field)

        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setMinimum(1)
        self.zoom_slider.setMaximum(10)
        self.zoom_slider.setValue(self.columns)  # Default: 2 images per row
        self.zoom_slider.setTickInterval(1)
        self.zoom_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        # Create a layout to hold the slider and its tick labels
        slider_layout = QVBoxLayout()

        # Add the slider to this layout
        slider_layout.addWidget(self.zoom_slider)

        # Create tick labels from 1 to 10
        tick_labels = QHBoxLayout()
        for i in range(1, 11):
            label = QLabel(str(i))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tick_labels.addWidget(label)

        # Add tick labels below the slider
        slider_layout.addLayout(tick_labels)

        # Now add the whole slider+labels layout to the main layout
        layout.addLayout(slider_layout)


        self.zoom_slider.valueChanged.connect(self.on_zoom_changed)
        self.zoom_slider.sliderMoved.connect(lambda _: on_slider_hover())
        self.zoom_slider.sliderPressed.connect(self.on_slider_hover)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        layout.addWidget(self.scroll_area)

        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.scroll_area.setWidget(self.grid_widget)
        
        self.load_images(image_dir)
        self.setWindowTitle(f"Species Image Preview ({self.zoom_slider.value()} images per row)")

    def rebuild_grid(self):
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

        for i, (_, container, _) in enumerate(self.image_widgets):
            row, col = divmod(i, self.columns)
            self.grid_layout.addWidget(container, row, col)

    def on_zoom_changed(self, value):
        self.columns = value
        self.setWindowTitle(f"Species Image Preview ({value} images per row)")        
        self.update_image_sizes()       
        # Rebuild the grid layout
        self.rebuild_grid()        

    def on_slider_hover(self):
        value = self.zoom_slider.value()
        if value == 1:
            tip = "1 = large images"
        elif value == 10:
            tip = "10 = small images"
        else:
            tip = f"{value} images per row"
        self.zoom_slider.setToolTip(tip)                    

    def load_images(self, image_dir):
        path = Path(image_dir)
        image_paths = sorted(
            f for f in path.glob("*.jpg")
            if not f.name.lower().endswith("-small.jpg")
        )

        if not image_paths:
            QMessageBox.warning(self, "No Images", "No JPG files found in this directory.")
            return

        self.image_widgets.clear()

        for i, img_path in enumerate(image_paths):
            image_label = QLabel()
            pixmap = QPixmap(str(img_path))
            image_label.setPixmap(pixmap)

            filename_label = QLabel(img_path.name)
            filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

            container = QWidget()
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(image_label)
            layout.addWidget(filename_label)

            row, col = divmod(i, self.columns)
            self.grid_layout.addWidget(container, row, col)

            self.image_widgets.append((image_label, container, img_path))


        self.update_image_sizes()


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

 
