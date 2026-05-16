from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QComboBox,
    QPushButton, QMessageBox
)
import logging
from utils.config import _config, persist_config

class SpeciesSettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Species Settings")
        self.setFixedSize(400, 250)

        layout = QVBoxLayout(self)

        # Scope selector
        layout.addWidget(QLabel("The scope of species name corrections is:"))

        self.scope_selector = QComboBox()
        self.scope_selector.addItems([
            "Only the selected file",
            "All files with same name error in day folder",
            "All files with same name error in all trip day folders"
        ])
        saved_scope = _config.get("Species", "correction_scope", fallback="All files with same name error in day folder")
        index = self.scope_selector.findText(saved_scope)
        if index >= 0:
            self.scope_selector.setCurrentIndex(index)
        layout.addWidget(self.scope_selector)

        # Name type selector
        layout.addWidget(QLabel("Preferred species name type:"))

        self.name_type_selector = QComboBox()
        self.name_type_selector.addItems(["Auto", "Dutch", "English", "Scientific"])
        saved_type = _config.get("Species", "name_type", fallback="Auto")
        index = self.name_type_selector.findText(saved_type)
        if index >= 0:
            self.name_type_selector.setCurrentIndex(index)
        layout.addWidget(self.name_type_selector)

        # Save button
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_settings)
        layout.addWidget(save_button)

    def save_settings(self):
        selected_scope = self.scope_selector.currentText()
        selected_type = self.name_type_selector.currentText()

        _config.set("Species", "correction_scope", selected_scope)
        _config.set("Species", "name_type", selected_type)

        persist_config()

        QMessageBox.information(self, "Success", "✅ Species settings updated")
        self.close()

        logger = logging.getLogger("speciesviewer")
        logger.info(f"Species settings updated: scope='{selected_scope}', name_type='{selected_type}'")
