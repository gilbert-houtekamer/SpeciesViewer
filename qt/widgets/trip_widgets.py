from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QLineEdit, QTextEdit, QPushButton,
    QFileDialog, QVBoxLayout, QDateEdit
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QPixmap
from PIL import Image
import os

class ImageFileFieldQt(QWidget):
    def __init__(self, label, expected_size, root_field, icon_path="qt/assets"):
        super().__init__()
        self.expected_size = expected_size
        self.root_field = root_field  # QLineEdit or similar widget with .text()
        self.icon_path = icon_path

        # UI Elements
        self.label_widget = QLabel(label)
        self.field = QLineEdit()
        self.browse_btn = QPushButton("Browse")
        self.status_icon = QLabel()
        self.hint_label = QLabel("")

        # Initial UI Setup
        self.status_icon.setPixmap(self.get_icon("error"))
        self.status_icon.setToolTip("")
        self.hint_label.setStyleSheet("color: red; font-size: 9pt;")
        self.hint_label.setVisible(False)

        layout = QHBoxLayout(self)
        layout.addWidget(self.label_widget)
        layout.addWidget(self.field)
        layout.addWidget(self.browse_btn)
        layout.addWidget(self.status_icon)
        layout.addWidget(self.hint_label)

        # Connections
        self.field.textChanged.connect(self.clear_hint)
        self.field.textChanged.connect(self.validate)
        self.browse_btn.clicked.connect(self.pick_file)

        # Initial validation
        self.validate()

    def get_icon(self, status: str) -> QPixmap:
        icon_file = "circle-check.png" if status == "valid" else "circle-x.png"
        pixmap = QPixmap(os.path.join(self.icon_path, icon_file))
        if pixmap.isNull():
            print(f"❌ Failed to load icon: {icon_file}")
        return pixmap

    def validate(self) -> bool:
        relative_path = self.field.text().strip()
        root_path = self.root_field.text().strip()

        if not relative_path or not root_path:
            return self._invalidate("Missing path input.")

        full_path = os.path.join(root_path, relative_path)

        if not os.path.isfile(full_path) or not full_path.lower().endswith(".jpg"):
            return self._invalidate(f"'{os.path.basename(full_path)}' is not a valid .JPG file")

        try:
            with Image.open(full_path) as img:
                if img.size != self.expected_size:
                    return self._invalidate(
                        f"Size mismatch: expected {self.expected_size}, got {img.size}"
                    )
        except Exception as err:
            return self._invalidate(f"Error reading image: {err}")

        # Valid image
        self.status_icon.setPixmap(self.get_icon("valid"))
        self.status_icon.setToolTip("")
        self.clear_hint()
        return True

    def _invalidate(self, message: str) -> bool:
        self.status_icon.setPixmap(self.get_icon("error"))
        self.status_icon.setToolTip(message)
        self.show_hint(message)
        return False

    def pick_file(self):
        root_path = self.root_field.text().strip()
        if not os.path.isdir(root_path):
            self.status_icon.setToolTip("❌ Select valid Trip root first")
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Select JPG Image", root_path, "JPEG files (*.jpg *.jpeg)"
        )
        if path:
            self.field.setText(path)

    def get_filename(self):
        return os.path.basename(self.field.text().strip())                

    def show_hint(self, message: str):
        self.hint_label.setText(message)
        self.hint_label.setVisible(True)

    def clear_hint(self):
        self.hint_label.setVisible(False)
           

class DirectoryFieldQt(QWidget):
    def __init__(self, label, initial_path="", icon_path="qt/assets"):
        super().__init__()
        self.icon_path = icon_path

        self.label_widget = QLabel(label)  # ✅ Create label first        

        self.field = QLineEdit()
        self.field.setText(initial_path)
        self.browse_btn = QPushButton("Browse")
        self.status_icon = QLabel()
        self.status_icon.setPixmap(self.get_icon(False))

        layout = QHBoxLayout(self)
        layout.addWidget(self.label_widget)
        layout.addWidget(self.field)
        layout.addWidget(self.browse_btn)
        layout.addWidget(self.status_icon)

        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color: red; font-size: 9pt;")
        self.hint_label.setVisible(False)
        layout.addWidget(self.hint_label)      

        self.field.textChanged.connect(self.clear_hint)        

        self.field.textChanged.connect(self.update_status)
        self.browse_btn.clicked.connect(lambda: self.browse(label))
        self.update_status()

    def get_icon(self, valid):
        icon_file = "circle-check.png" if valid else "circle-x.png"
        return QPixmap(os.path.join(self.icon_path, icon_file)) # .scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio)

    def update_status(self):
        path = self.field.text()
        valid = os.path.isdir(path)
        self.status_icon.setPixmap(self.get_icon(valid))
        tip = "Directory exists" if valid else "Directory not found or invalid"
        self.status_icon.setToolTip(tip)
        if valid:
            self.clear_hint()
        else:
            self.show_hint(tip)

    def browse(self, label):
        path = QFileDialog.getExistingDirectory(self, f"Choose folder for {label}", self.field.text())
        if path:
            self.field.setText(path)

    def get_path(self):
        return self.field.text().strip()            

    def show_hint(self, message):
        self.hint_label.setText(message)
        self.hint_label.setVisible(True)

    def clear_hint(self):
        self.hint_label.setVisible(False)        

class LabeledTextFieldQt(QWidget):
    def __init__(self, label, value="", multiline=False):
        super().__init__()
        self.label = QLabel(label)
        self.multiline = multiline

        if multiline:
            self.field = QTextEdit()
            self.field.setPlainText(value)
        else:
            self.field = QLineEdit()
            self.field.setText(value)

        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addWidget(self.field)

    def get_text(self):
        return self.field.toPlainText().strip() if self.multiline else self.field.text().strip()

    def set_text(self, value):
        if self.multiline:
            self.field.setPlainText(value)
        else:
            self.field.setText(value) 

class DateRangeFieldQt(QWidget):
    def __init__(self, from_date=None, to_date=None):
        super().__init__()
        layout = QHBoxLayout(self)

        self.from_date = QDateEdit()
        self.to_date = QDateEdit()

        self.from_date.setCalendarPopup(True)
        self.to_date.setCalendarPopup(True)
        self.from_date.setDisplayFormat("yyyy-MM-dd")
        self.to_date.setDisplayFormat("yyyy-MM-dd")

        layout.addWidget(QLabel("Start Date"))
        layout.addWidget(self.from_date)
        layout.addSpacing(20)
        layout.addWidget(QLabel("End Date"))
        layout.addWidget(self.to_date)

        # Set initial values
        self.from_date.setDate(from_date or QDate.currentDate())
        self.to_date.setDate(to_date or QDate.currentDate().addDays(7))

    def get_dates(self):
        return self.from_date.date().toString("yyyy-MM-dd"), self.to_date.date().toString("yyyy-MM-dd")

    def set_dates(self, from_str, to_str):
        self.from_date.setDate(QDate.fromString(from_str, "yyyy-MM-dd"))
        self.to_date.setDate(QDate.fromString(to_str, "yyyy-MM-dd"))