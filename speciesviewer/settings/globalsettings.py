from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QGroupBox, QLabel, QLineEdit, QCheckBox,
    QComboBox, QPushButton, QMessageBox, QRadioButton, QButtonGroup
)
from gui.widgets import wrapped
from utils.config import _config, persist_config
from utils.name_profile import (profile_keys, profile_label, global_profile,
                                set_global_profile)
import logging

class GlobalSettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Global Settings")
        # No fixed size: the layout decides, so adding a field can never clip one.
        self.setMinimumWidth(460)

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

        # Where this installation publishes — read by utils/site_config.py.
        # The choice is REQUIRED (docs/plans/website_optional_plan.md): this
        # computer, or a web server. Neither ticked = not chosen yet.
        from utils import site_config
        web = QGroupBox("Website and publishing")
        web_lay = QVBoxLayout(web)
        web_lay.addWidget(wrapped(
            "SpeciesViewer makes a website of your pictures: species pages and "
            "blogs. Choose where it goes.", self))
        self.target_local = QRadioButton(
            "On this computer — view it in your browser; nothing is uploaded")
        self.target_server = QRadioButton(
            "On my web server — fill in what your web host gave you")
        self._target = QButtonGroup(self)
        for b in (self.target_local, self.target_server):
            self._target.addButton(b)
            web_lay.addWidget(b)
        current = site_config.publish_target()
        self.target_local.setChecked(current == site_config.LOCAL)
        self.target_server.setChecked(current == site_config.SERVER)
        form = QFormLayout()
        web_lay.addLayout(form)

        def field(label, key, placeholder="", password=False, fallback=""):
            f = QLineEdit(_config.get("websettings", key, fallback=fallback))
            f.setPlaceholderText(placeholder)
            if password:
                f.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow(label, f)
            return f

        self.base_url_field = field("Website address", "base_url", "https://example.org")
        self.sftp_host_field = field("Upload server", "sftp_host", "ssh.example.org")
        self.sftp_user_field = field("Upload account", "sftp_user")
        self.webpassword_field = field("Upload password", "sftppwd", password=True,
                                       fallback="? still needed")
        self.sftp_root_field = field("Website folder on the server", "sftp_remote_root",
                                     "/var/www/example")
        self._server_fields = (self.base_url_field, self.sftp_host_field,
                               self.sftp_user_field, self.webpassword_field,
                               self.sftp_root_field)
        self.target_server.toggled.connect(self._enable_server_fields)
        self._enable_server_fields(self.target_server.isChecked())
        self.contact_field = field("Contact e-mail", "contact_email", "you@example.org")
        self.contact_field.setToolTip(
            "Sent with every map and place-name request, as OpenStreetMap's usage "
            "policy asks — so they can reach you, not somebody else.")
        layout.addWidget(web)
        # The DxO PhotoLab path moved to File ▸ Settings ▸ Interfaces to Lightroom
        # and PhotoLab ▸ PhotoLab Location, so the three DxO options sit together
        # instead of one being stranded here. The [Global] dxo_path key is unchanged.

        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_settings)
        layout.addWidget(save_button)

    def _enable_server_fields(self, on: bool):
        for f in self._server_fields:
            f.setEnabled(on)

    def save_settings(self):
        _config.set("Global", "splash_enabled", "yes" if self.splash_switch.isChecked() else "no")
        _config.set("Global", "debug_tools", "yes" if self.debug_switch.isChecked() else "no")
        set_global_profile(self.names_combo.currentData())
        if not _config.has_section("websettings"):
            _config.add_section("websettings")
        for key, f in (("base_url", self.base_url_field),
                       ("sftp_host", self.sftp_host_field),
                       ("sftp_user", self.sftp_user_field),
                       ("sftppwd", self.webpassword_field),
                       ("sftp_remote_root", self.sftp_root_field),
                       ("contact_email", self.contact_field)):
            _config.set("websettings", key, f.text().strip())
        from utils import site_config
        if self.target_local.isChecked():
            _config.set("websettings", "publish_target", site_config.LOCAL)
        elif self.target_server.isChecked():
            _config.set("websettings", "publish_target", site_config.SERVER)

        persist_config()
        from utils import local_web
        local_web.ensure_started()            # switching to "this computer" shows it at once
        # A website that was just set up gets its starter pages now, not at the
        # first publish (Gilbert, 2026-10-12). A site that has them: nothing sent.
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication
        from engine.site_init import initialize_site
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            supplied = initialize_site(password=self.webpassword_field.text().strip())
        finally:
            QApplication.restoreOverrideCursor()

        QMessageBox.information(
            self, "Success", "✅ Global settings updated"
            + ("\n\nYour website now has its starting pages." if supplied else ""))
        self.close()

        logger = logging.getLogger("speciesviewer")
        logger.info("Global settings updated.")
