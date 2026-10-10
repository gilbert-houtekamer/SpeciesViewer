from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QCheckBox, QComboBox,
    QPushButton, QMessageBox
)
from utils.config import _config, persist_config
from utils.name_profile import (profile_keys, profile_label, global_profile,
                                set_global_profile)
import logging

class GlobalSettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Global Settings")
        self.setFixedSize(400, 380)

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

        # Preferred names — which of NL/EN/Sci reads out under each picture (a
        # fallback chain). Per-trip overrides live in Create Trip ▸ Images & dates.
        layout.addWidget(QLabel("Preferred names (shown under each picture)"))
        self.names_combo = QComboBox()
        for key in profile_keys():
            self.names_combo.addItem(profile_label(key), key)
        _ni = self.names_combo.findData(global_profile())
        if _ni >= 0:
            self.names_combo.setCurrentIndex(_ni)
        self.names_combo.setToolTip(
            "Which of Dutch / English / Scientific reads out as the label under "
            "each picture, tried in order — so 'English / Scientific' never shows "
            "Dutch. The window size is not set here: the app remembers it.")
        layout.addWidget(self.names_combo)

        self.webpassword_field = self._labeled_field(layout, "Website password", _config.get("websettings", "sftppwd", fallback="? still needed"), password=True)
        self.base_url_field = self._labeled_field(layout, "Base URL for web address", _config.get("websettings", "base_url", fallback=""))
        # The DxO PhotoLab path moved to File ▸ Settings ▸ Interfaces to Lightroom
        # and PhotoLab ▸ PhotoLab Location, so the three DxO options sit together
        # instead of one being stranded here. The [Global] dxo_path key is unchanged.

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
        set_global_profile(self.names_combo.currentData())
        _config.set("websettings", "sftppwd", self.webpassword_field.text())
        _config.set("websettings", "base_url", self.base_url_field.text())

        persist_config()

        QMessageBox.information(self, "Success", "✅ Global settings updated")
        self.close()

        logger = logging.getLogger("speciesviewer")
        logger.info("Global settings updated.")
