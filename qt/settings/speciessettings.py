from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QComboBox,
    QPushButton, QMessageBox
)
import logging

class SpeciesSettingsDialog(QDialog):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Species Settings")
        self.setFixedSize(400, 200)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("The scope of species name corrections is:"))

        self.scope_selector = QComboBox()
        self.scope_selector.addItems([
            "Only the selected file",
            "All files with same name error in day folder",
            "All files with same name error in all trip day folders"
        ])

        # Load saved value from config
        saved_scope = config.get("Species", "correction_scope", fallback="All files with same name error in day folder")
        index = self.scope_selector.findText(saved_scope)
        if index >= 0:
            self.scope_selector.setCurrentIndex(index)

        layout.addWidget(self.scope_selector)

        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_settings)
        layout.addWidget(save_button)

    def save_settings(self):
        selected_scope = self.scope_selector.currentText()
        self.config.set("Species", "correction_scope", selected_scope)

        with open("sv_config.ini", "w", encoding="utf-8") as f:
            self.config.write(f)

        QMessageBox.information(self, "Success", "✅ Species settings updated")
        self.close()

        logger = logging.getLogger("speciesviewer")
        logger.info(f"Species correction scope set to: {selected_scope}")
