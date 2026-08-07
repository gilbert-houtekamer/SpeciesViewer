from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QComboBox,
    QPushButton, QMessageBox
)
from PyQt6.QtCore import Qt
import logging
from utils.config import _config, persist_config

class SpeciesSettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Species Settings")
        self.setFixedSize(400, 330)

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

        # Preferred NAME DISPLAY — which language(s) read out under each picture.
        # This is a different axis from the correction "name type" above: that one
        # picks which name a correction WRITES; this only picks what is SHOWN.
        from utils.name_profile import profile_keys, profile_label, global_profile
        layout.addWidget(QLabel("Preferred name display (under each picture):"))
        self.name_display_selector = QComboBox()
        for key in profile_keys():
            self.name_display_selector.addItem(profile_label(key), key)
        idx = self.name_display_selector.findData(global_profile())
        if idx >= 0:
            self.name_display_selector.setCurrentIndex(idx)
        layout.addWidget(self.name_display_selector)

        # Save button
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_settings)
        layout.addWidget(save_button)

    def save_settings(self):
        selected_scope = self.scope_selector.currentText()
        selected_type = self.name_type_selector.currentText()

        _config.set("Species", "correction_scope", selected_scope)
        _config.set("Species", "name_type", selected_type)

        from utils.name_profile import set_global_profile
        display_key = self.name_display_selector.currentData()
        set_global_profile(display_key)

        persist_config()

        # A plain QMessageBox.information() pairs a huge system icon with two words
        # of text — unbalanced. Drop the icon and centre a compact confirmation.
        box = QMessageBox(self)
        box.setWindowTitle("Species Settings")
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("<div align='center' style='min-width:220px;'>"
                    "✅&nbsp;&nbsp;Species settings updated</div>")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()
        self.close()

        logger = logging.getLogger("speciesviewer")
        logger.info(f"Species settings updated: scope='{selected_scope}', "
                    f"name_type='{selected_type}', name_display='{display_key}'")
