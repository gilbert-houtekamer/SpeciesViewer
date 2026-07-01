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
        self.setFixedSize(400, 420)

        layout = QVBoxLayout(self)

        self.splash_switch = QCheckBox("Enable Splash")
        splash_enabled = _config.get("Global", "splash_enabled", fallback="yes") == "yes"
        self.splash_switch.setChecked(splash_enabled)
        layout.addWidget(self.splash_switch)

        self.debug_switch = QCheckBox("Enable debug functionality")
        self.debug_switch.setToolTip(
            "Shows the diagnostic tools under File ▸ Admin (status consoles, "
            "audits, rebuild, clean orphans, testbed).\nTakes effect after a "
            "restart.")
        self.debug_switch.setChecked(
            _config.get("Global", "debug_tools", fallback="no") == "yes")
        layout.addWidget(self.debug_switch)

        self.width_field = self._labeled_field(layout, "Window width", _config.get("Global", "default_window_width", fallback="1600"))
        self.height_field = self._labeled_field(layout, "Window height", _config.get("Global", "default_window_height", fallback="1200"))
        self.webpassword_field = self._labeled_field(layout, "Website password", _config.get("websettings", "sftppwd", fallback="? still needed"), password=True)
        self.base_url_field = self._labeled_field(layout, "Base URL for web address", _config.get("websettings", "base_url", fallback="https://houtekamer.com"))
        self.dxo_path_field = self._labeled_field(
            layout, "DxO PhotoLab path",
            _config.get("Global", "dxo_path",
                        fallback=r"C:\Program Files\DxO\DxO PhotoLab 9\DxO.PhotoLab.exe"))

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
        _config.set("Global", "debug_tools", "yes" if self.debug_switch.isChecked() else "no")
        _config.set("Global", "default_window_width", self.width_field.text())
        _config.set("Global", "default_window_height", self.height_field.text())
        _config.set("websettings", "sftppwd", self.webpassword_field.text())
        _config.set("websettings", "base_url", self.base_url_field.text())
        _config.set("Global", "dxo_path", self.dxo_path_field.text())

        persist_config()

        QMessageBox.information(self, "Success", "✅ Global settings updated")
        self.close()

        logger = logging.getLogger("speciesviewer")
        logger.info("Global settings updated.")
