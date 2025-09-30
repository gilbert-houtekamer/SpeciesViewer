from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QCheckBox,
    QPushButton, QMessageBox
)
from utils.config import _config, persist_config
import logging

class GlobalSettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Global Settings")
        self.setFixedSize(400, 300)

        layout = QVBoxLayout(self)

        self.splash_switch = QCheckBox("Enable Splash")
        splash_enabled = _config.get("Global", "splash_enabled", fallback="yes") == "yes"
        self.splash_switch.setChecked(splash_enabled)
        layout.addWidget(self.splash_switch)

        self.width_field = self._labeled_field(layout, "Window width", config.get("Global", "default_window_width", fallback="1600"))
        self.height_field = self._labeled_field(layout, "Window height", config.get("Global", "default_window_height", fallback="1200"))
        self.webpassword_field = self._labeled_field(layout, "Website password", config.get("websettings", "sftppwd", fallback="? still needed"), password=True)
        self.base_url_field = self._labeled_field(layout, "Base URL for web address", config.get("websettings", "base_url", fallback="https://houtekamer.com"))

        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_settings)
        layout.addWidget(save_button)

    def _labeled_field(self, layout, label, value="", password=False):
        layout.addWidget(QLabel(label))
        field = QLineEdit(value)
        if password:
            field.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(field)
        return field

    def save_settings(self):
        _config.set("Global", "splash_enabled", "yes" if self.splash_switch.isChecked() else "no")
        _config.set("Global", "default_window_width", self.width_field.text())
        _config.set("Global", "default_window_height", self.height_field.text())
        _config.set("websettings", "sftppwd", self.webpassword_field.text())
        _config.set("websettings", "base_url", self.base_url_field.text())        

        persist_config()

        QMessageBox.information(self, "Success", "✅ Global settings updated")
        self.close()

        logger = logging.getLogger("speciesviewer")
        logger.info("Global settings updated.")
