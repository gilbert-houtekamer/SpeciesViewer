import logging
import configparser
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPushButton,
    QTabWidget, QWidget, QVBoxLayout, QComboBox, QLineEdit, QFormLayout, QGridLayout, QDateEdit, QTextEdit)
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QMovie

from utils.batch_config import generate_svconfig_ini

class BatchModuleRunner:
    def __init__(self, config, parent_widget, logger):
        self.config = config
        self.parent = parent_widget
        self.logger = logger

    def run(self, module_called, before_msg, after_msg):
        from utils.batch_config import generate_svconfig_ini

        # Spinner dialog
        spinner = QDialog(self.parent)
        spinner.setWindowTitle("Even geduld...")
        spinner.setModal(True)
        spinner.setFixedSize(200, 100)

        layout = QVBoxLayout(spinner)
        label = QLabel(before_msg)
        layout.addWidget(label, alignment=Qt.AlignmentFlag.AlignCenter)

        movie = QMovie("spinner.gif")  # You’ll need a small spinner GIF
        spinner_label = QLabel()
        spinner_label.setMovie(movie)
        layout.addWidget(spinner_label, alignment=Qt.AlignmentFlag.AlignCenter)
        movie.start()

        spinner.show()
        QApplication.processEvents()

        # Run batch
        generate_svconfig_ini(self.config)
        args = None
        module_config = configparser.ConfigParser()
        module_config.read("svconfig.ini")

        self.logger.info(before_msg)
        module_called(args, module_config)
        self.logger.info(after_msg)

        spinner.close()
