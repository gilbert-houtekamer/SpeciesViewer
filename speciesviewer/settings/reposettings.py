from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QMessageBox, QFrame, QCheckBox
)
import logging
from utils.config import (_config, persist_config, get_trips_root,
                          get_virttrips_root, get_currentpictures_root)

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
        # readonly_locked = yes means the SmallMaster install has permanently
        # blocked writes; the checkbox is shown but greyed out.
        _ro_locked = _config.get("master", "readonly_locked", fallback="no").strip().lower() == "yes"
        self.master_readonly = QCheckBox("Master Read-Only (block master re-rating)")
        self.master_readonly.setChecked(
            _config.get("master", "readonly", fallback="yes").strip().lower() != "no")
        if _ro_locked:
            self.master_readonly.setEnabled(False)
            self.master_readonly.setToolTip(
                "Locked — this machine holds a SmallMaster subset and cannot write to the master.")
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
        # Per-machine base that holds every trip's source pictures (e.g.
        # D:\CurrentPictures). trip.ini stores its picture_root RELATIVE to this,
        # so the shared trip.ini stays portable across machines. WP8.
        self.currentpictures_root_field = self._labeled_field(
            layout, "CurrentPictures root directory (per machine)", get_currentpictures_root())
        browse_currentpictures_btn = QPushButton("Browse CurrentPictures Root")
        browse_currentpictures_btn.clicked.connect(self.browse_currentpictures_root)
        layout.addWidget(browse_currentpictures_btn)
        layout.addWidget(self._divider())

        # Active Virtual Trip section
        layout.addWidget(self._section_label("Active Album"))
        self.virttrip_field = self._labeled_field(layout, "Database", _config.get("virttrip", "virttripbiotadatabase", fallback=""))
        browse_virttrip_btn = QPushButton("Browse Album Database")
        browse_virttrip_btn.clicked.connect(self.browse_virttrip)
        layout.addWidget(browse_virttrip_btn)
        self.virttrip_web = self._labeled_field(layout, "Web root", _config.get("virttrip", "virttripbiotawebroot", fallback=""))
        self.virttrips_root_field = self._labeled_field(layout, "Albums root directory", get_virttrips_root())
        browse_virttrips_root_btn = QPushButton("Browse Albums Root")
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
        start = self.master_field.text() or ""
        path = QFileDialog.getExistingDirectory(self, "Select Master Database Directory", start)
        if path:
            self.master_field.setText(path)

    def browse_trip(self):
        start = self.trips_root_field.text() or self.trip_field.text() or ""
        path = QFileDialog.getExistingDirectory(self, "Select Trip Database Directory", start)
        if path:
            self.trip_field.setText(path)

    def browse_trips_root(self):
        start = self.trips_root_field.text() or ""
        path = QFileDialog.getExistingDirectory(self, "Select Trips Root Directory", start)
        if path:
            self.trips_root_field.setText(path)

    def browse_currentpictures_root(self):
        start = self.currentpictures_root_field.text() or ""
        path = QFileDialog.getExistingDirectory(self, "Select CurrentPictures Root Directory", start)
        if path:
            self.currentpictures_root_field.setText(path)

    def browse_virttrip(self):
        start = self.virttrips_root_field.text() or self.virttrip_field.text() or ""
        path = QFileDialog.getExistingDirectory(self, "Select Album Database Directory", start)
        if path:
            self.virttrip_field.setText(path)

    def browse_virttrips_root(self):
        start = self.virttrips_root_field.text() or ""
        path = QFileDialog.getExistingDirectory(self, "Select Albums Root Directory", start)
        if path:
            self.virttrips_root_field.setText(path)

    def _warn_if_trip_inside_virttrips(self) -> bool:
        """Return True if the user wants to proceed despite the mismatch."""
        trip = self.trip_field.text().strip()
        vroot = self.virttrips_root_field.text().strip()
        if not trip or not vroot:
            return True
        try:
            Path(trip).relative_to(vroot)
        except ValueError:
            return True  # not inside virttrips root — fine
        answer = QMessageBox.warning(
            self, "Possible configuration error",
            f"The Active Trip path\n  {trip}\n\nis inside the Virtual Trips root\n  {vroot}\n\n"
            "This looks like a virtual trip set as a regular trip by mistake.\n\n"
            "Save anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def save_settings(self):
        if not self._warn_if_trip_inside_virttrips():
            return
        _config.set("master", "masterbiotadatabase", self.master_field.text())
        _config.set("master", "masterbiotawebroot", self.master_web.text())
        if _config.get("master", "readonly_locked", fallback="no").strip().lower() != "yes":
            _config.set("master", "readonly",
                        "yes" if self.master_readonly.isChecked() else "no")
        _config.set("trip", "tripbiotadatabase", self.trip_field.text())
        _config.set("trip", "tripbiotawebroot", self.trip_web.text())
        if self.trips_root_field.text():
            _config.set("trip", "tripsrootdirectory", self.trips_root_field.text())
        if self.currentpictures_root_field.text():
            if not _config.has_section("currentpictures"):
                _config.add_section("currentpictures")
            _config.set("currentpictures", "rootdirectory", self.currentpictures_root_field.text())
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
