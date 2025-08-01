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
        self.root_field = root_field  # QLineEdit or any widget with .text()
        self.icon_path = icon_path

        self.label_widget = QLabel(label)  # ✅ Create label first

        self.field = QLineEdit()
        self.browse_btn = QPushButton("Browse")
        self.status_icon = QLabel()
        self.status_icon.setPixmap(self.get_icon("error"))
        self.status_icon.setToolTip("")

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

        self.browse_btn.clicked.connect(self.pick_file)
        self.field.textChanged.connect(lambda: self.validate(self.field.text()))
        self.validate(self.field.text())  # Initial validation



    def get_icon(self, status):
        icon_file = "circle-check.png" if status == "valid" else "circle-x.png"
        return QPixmap(os.path.join(self.icon_path, icon_file)) # .scaled(24, 24)

    def validate(self, path):
        # pixmap = QPixmap("assets/circle-check.png")
        full_path = os.path.abspath("qt/assets/circle-check.png")
        pixmap = QPixmap(full_path)        
        # print("Pixmap loaded?", not pixmap.isNull())
        # print("Validating:", full_path , ' and ', path)
        # print("Exists?", os.path.isfile(path))
        # print("Current working directory:", os.getcwd())
        # print("File exists?", os.path.isfile("assets/circle-check.png"))        
        if pixmap.isNull():
            print("❌ Failed to load icon:", full_path)        
        if not os.path.isfile(path) or not path.lower().endswith(".jpg"):
            self.status_icon.setPixmap(self.get_icon("error"))
            self.status_icon.setToolTip(f"'{os.path.basename(path)}' is not a valid .JPG file") 
            return False

        try:
            with Image.open(path) as img:
                if img.size == self.expected_size:
                    self.status_icon.setPixmap(self.get_icon("valid"))
                    self.status_icon.setToolTip("")
                    self.clear_hint()
                    return True
                else:
                    self.status_icon.setPixmap(self.get_icon("error"))
                    errortext = f"Size mismatch: expected {self.expected_size}, got {img.size}"
                    self.status_icon.setToolTip(errortext)
                    self.show_hint(errortext)
        except Exception as err:
            self.status_icon.setPixmap(self.get_icon("error"))
            self.status_icon.setToolTip(f"Error reading image: {err}")
            self.show_hint(f"Error reading image: {err}")
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

    def show_hint(self, message):
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

    def show_hint(self, message):
        self.hint_label.setText(message)
        self.hint_label.setVisible(True)

    def clear_hint(self):
        self.hint_label.setVisible(False)        

def create_labeled_text(label, value="", multiline=False):
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.addWidget(QLabel(label))
    if multiline:
        text_field = QTextEdit()
        text_field.setPlainText(value)
    else:
        text_field = QLineEdit()
        text_field.setText(value)

    layout.addWidget(text_field)
    return {"widget": container, "field": text_field}

def create_directory_field(label_text, initial_path="", icon_path="qt_assets"):
    container = QWidget()
    layout = QHBoxLayout(container)

    label = QLabel(label_text)
    field = QLineEdit()
    field.setText(initial_path)

    icon = QLabel()
    # Optionally load the icon, e.g.:
    # icon.setPixmap(QPixmap(os.path.join(icon_path, "folder.png")).scaled(16, 16))

    layout.addWidget(label)
    layout.addWidget(field)
    layout.addWidget(icon)

    return {
        "widget": container,
        "field": field,
        "icon": icon
    }

def create_image_file_field(label_text, initial_path="", icon_path="qt_assets"):
    container = QWidget()
    layout = QHBoxLayout(container)

    label = QLabel(label_text)
    field = QLineEdit()
    field.setText(initial_path)

    icon = QLabel()
    # icon.setPixmap(QPixmap(os.path.join(icon_path, "image.png")).scaled(16, 16))

    layout.addWidget(label)
    layout.addWidget(field)
    layout.addWidget(icon)

    return {
        "widget": container,
        "field": field,
        "icon": icon
    }

def create_date_inputs(trip_data=None):
    layout = QHBoxLayout()

    from_date = QDateEdit()
    to_date = QDateEdit()

    from_date.setCalendarPopup(True)
    to_date.setCalendarPopup(True)

    from_date.setDisplayFormat("yyyy-MM-dd")
    to_date.setDisplayFormat("yyyy-MM-dd")

    # Set default or trip_data dates
    if trip_data:
        from_str = trip_data.get("fromdate", "")
        to_str = trip_data.get("todate", "")
        if from_str:
            from_date.setDate(QDate.fromString(from_str, "yyyy-MM-dd"))
        else:
            from_date.setDate(QDate.currentDate())
        if to_str:
            to_date.setDate(QDate.fromString(to_str, "yyyy-MM-dd"))
        else:
            to_date.setDate(QDate.currentDate().addDays(7))
    else:
        from_date.setDate(QDate.currentDate())
        to_date.setDate(QDate.currentDate().addDays(7))

    layout.addWidget(QLabel("Start Date"))
    layout.addWidget(from_date)
    layout.addSpacing(20)
    layout.addWidget(QLabel("End Date"))
    layout.addWidget(to_date)

    return layout, from_date, to_date