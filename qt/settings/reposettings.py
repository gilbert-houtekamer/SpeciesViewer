from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QMessageBox, QFrame
)
import logging

class RepoSettingsDialog(QDialog):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Repositories")
        self.setFixedSize(600, 450)

        layout = QVBoxLayout(self)

        # Master section
        self.master_field = self._labeled_field(layout, "[master] Database", config.get("master", "masterbiotadatabase", fallback=""))
        browse_master_btn = QPushButton("Browse [master] Database")
        browse_master_btn.clicked.connect(self.browse_master)
        layout.addWidget(browse_master_btn)

        self.master_web = self._labeled_field(layout, "[master] Web root", config.get("master", "masterbiotawebroot", fallback=""))
        layout.addWidget(self._divider())

        # Trip section
        self.trip_field = self._labeled_field(layout, "[trip] Database", config.get("trip", "tripbiotadatabase", fallback=""))
        browse_trip_btn = QPushButton("Browse [trip] Database")
        browse_trip_btn.clicked.connect(self.browse_trip)
        layout.addWidget(browse_trip_btn)

        self.trip_web = self._labeled_field(layout, "[trip] Web root", config.get("trip", "tripbiotawebroot", fallback=""))

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.save_settings)
        layout.addWidget(save_btn)

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
        path = QFileDialog.getExistingDirectory(self, "Select [master] Database Directory")
        if path:
            self.master_field.setText(path)

    def browse_trip(self):
        path = QFileDialog.getExistingDirectory(self, "Select [trip] Database Directory")
        if path:
            self.trip_field.setText(path)

    def save_settings(self):
        self.config.set("master", "masterbiotadatabase", self.master_field.text())
        self.config.set("master", "masterbiotawebroot", self.master_web.text())
        self.config.set("trip", "tripbiotadatabase", self.trip_field.text())
        self.config.set("trip", "tripbiotawebroot", self.trip_web.text())

        with open("sv_config.ini", "w", encoding="utf-8") as f:
            self.config.write(f)

        QMessageBox.information(self, "Success", "Repository settings saved")
        self.close()

        logger = logging.getLogger("speciesviewer")
        logger.info("Repository settings updated.")
