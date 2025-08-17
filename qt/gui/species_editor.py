import os
import logging
# import sip
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout, QSpinBox, QPushButton, QSizePolicy, QTextEdit,
    QLabel, QMessageBox, QSlider, QHBoxLayout, QProgressDialog)
from PyQt6.QtGui import QPixmap, QImage, QFontMetrics, QIcon
from PyQt6.QtCore import Qt, QTimer, QSize
from widgets.trip_widgets import DirectoryFieldQt
from engine.filepicture import FilePicture



class SpeciesInfoDialog(QDialog):
    def __init__(self, filename, species_name, error_msg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Species Info")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        # Filename
        filename_label = QLabel(f"<b>Filename:</b> {filename}")
        layout.addWidget(filename_label)

        # Species name
        species_label = QLabel(f"<b>Species:</b> {species_name or '(unknown)'}")
        layout.addWidget(species_label)

        # Error message
        error_label = QLabel("<b>Error Message:</b>")
        layout.addWidget(error_label)

        error_text = QTextEdit()
        error_text.setReadOnly(True)
        error_text.setPlainText(error_msg or "No error.")
        layout.addWidget(error_text)

        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
