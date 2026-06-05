from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QMessageBox, QFrame, QCheckBox
)
import logging
from utils.config import _config, persist_config, get_trips_root, get_virttrips_root

class RepoSettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Repositories")
        self.setMinimumSize(600, 700)

        layout = QVBoxLayout(self)

        # Master section (shared by trips and virtual trips)
        layout.addWidget(self._section_label("Master (shared)"))
        self.master_field = self._labeled_field(layout, "Database", _config.get("master", "masterbiotadatabase", fallback=""))
        browse_master_btn = QPushButton("Browse Master Database")
        browse_master_btn.clicked.connect(self.browse_master)
        layout.addWidget(browse_master_btn)
        self.master_web = self._labeled_field(layout, "Web root", _config.get("master", "masterbiotawebroot", fallback=""))
        # Master Read-Only: when checked, the Rating Editor cannot write back to
        # the master (re-rating master pictures). Default ON (safe) — needed on a
        # machine that does not hold the complete master (e.g. the laptop).
        self.master_readonly = QCheckBox("Master Read-Only (block master re-rating)")
        self.master_readonly.setChecked(
            _config.get("master", "readonly", fallback="yes").strip().lower() != "no")
        layout.addWidget(self.master_readonly)
        layout.addWidget(self._divider())

        # Active Trip section
        layout.addWidget(self._section_label("Active Trip"))
        self.trip_field = self._labeled_field(layout, "Database", _config.get("trip", "tripbiotadatabase", fallback=""))
        browse_trip_btn = QPushButton("Browse Trip Database")
        browse_trip_btn.clicked.connect(self.browse_trip)
        layout.addWidget(browse_trip_btn)
        self.trip_web = self._labeled_field(layout, "Web root", _config.get("trip", "tripbiotawebroot", fallback=""))
        self.trips_root_field = self._labeled_field(layout, "Trips root directory", get_trips_root())
        browse_trips_root_btn = QPushButton("Browse Trips Root")
        browse_trips_root_btn.clicked.connect(self.browse_trips_root)
        layout.addWidget(browse_trips_root_btn)
        layout.addWidget(self._divider())

        # Active Virtual Trip section
        layout.addWidget(self._section_label("Active Virtual Trip"))
        self.virttrip_field = self._labeled_field(layout, "Database", _config.get("virttrip", "virttripbiotadatabase", fallback=""))
        browse_virttrip_btn = QPushButton("Browse Virtual Trip Database")
        browse_virttrip_btn.clicked.connect(self.browse_virttrip)
        layout.addWidget(browse_virttrip_btn)
        self.virttrip_web = self._labeled_field(layout, "Web root", _config.get("virttrip", "virttripbiotawebroot", fallback=""))
        self.virttrips_root_field = self._labeled_field(layout, "Virtual trips root directory", get_virttrips_root())
        browse_virttrips_root_btn = QPushButton("Browse Virtual Trips Root")
        browse_virttrips_root_btn.clicked.connect(self.browse_virttrips_root)
        layout.addWidget(browse_virttrips_root_btn)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.save_settings)
        layout.addWidget(save_btn)

    def _section_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: bold; margin-top: 4px;")
        return lbl

    def _labeled_field(self, layout, label, value=""):
        layout.addWidget(QLabel(label))
        field = QLineEdit(value)
        layout.addWidget(field)
        return field

    def _divider(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def browse_master(self):
        path = QFileDialog.getExistingDirectory(self, "Select Master Database Directory")
        if path:
            self.master_field.setText(path)

    def browse_trip(self):
        path = QFileDialog.getExistingDirectory(self, "Select Trip Database Directory")
        if path:
            self.trip_field.setText(path)

    def browse_trips_root(self):
        path = QFileDialog.getExistingDirectory(self, "Select Trips Root Directory")
        if path:
            self.trips_root_field.setText(path)

    def browse_virttrip(self):
        path = QFileDialog.getExistingDirectory(self, "Select Virtual Trip Database Directory")
        if path:
            self.virttrip_field.setText(path)

    def browse_virttrips_root(self):
        path = QFileDialog.getExistingDirectory(self, "Select Virtual Trips Root Directory")
        if path:
            self.virttrips_root_field.setText(path)

    def save_settings(self):
        _config.set("master", "masterbiotadatabase", self.master_field.text())
        _config.set("master", "masterbiotawebroot", self.master_web.text())
        _config.set("master", "readonly",
                    "yes" if self.master_readonly.isChecked() else "no")
        _config.set("trip", "tripbiotadatabase", self.trip_field.text())
        _config.set("trip", "tripbiotawebroot", self.trip_web.text())
        if self.trips_root_field.text():
            _config.set("trip", "tripsrootdirectory", self.trips_root_field.text())
        if not _config.has_section("virttrip"):
            _config.add_section("virttrip")
        _config.set("virttrip", "virttripbiotadatabase", self.virttrip_field.text())
        _config.set("virttrip", "virttripbiotawebroot", self.virttrip_web.text())
        if self.virttrips_root_field.text():
            _config.set("virttrip", "virttriprootdirectory", self.virttrips_root_field.text())

        persist_config()

        QMessageBox.information(self, "Success", "Repository settings saved")
        self.close()

        logger = logging.getLogger("speciesviewer")
        logger.info("Repository settings updated.")
