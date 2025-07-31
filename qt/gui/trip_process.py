from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt
import logging
from gui.batch import call_batch_module  # Assuming batch handler is here
import bin.sv2ReadDirExif  # Replace with correct module path

def show_read_dir_dialog(parent, config):
    print("✅ trip_read_dir triggered")
    logger = logging.getLogger("speciesviewer")

    dialog = QDialog(parent)
    dialog.setWindowTitle("Read Picture Directory")
    dialog.resize(300, 200)

    layout = QVBoxLayout(dialog)

    info_text = QLabel("Scan a directory with photo files and extract metadata (Exif tags) for each image.")
    info_text.setWordWrap(True)
    layout.addWidget(info_text)

    read_btn = QPushButton("Read Directory")
    layout.addWidget(read_btn, alignment=Qt.AlignmentFlag.AlignLeft)

    def on_read():
        call_batch_module(
            config,
            parent,
            logger,
            bin.sv2ReadDirExif.main,
            "Reading picture directory...",
            "✅ Directory scan complete. Review messages above."
        )
        dialog.accept()

    read_btn.clicked.connect(on_read)

    return dialog