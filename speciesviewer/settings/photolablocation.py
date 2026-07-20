"""Where DxO PhotoLab is installed.

This used to be a field at the bottom of Global Settings, which put the DxO
options in three unrelated places (Global, PhotoLab Denoise, Sidecar Behaviour).
They now live together under File ▸ Settings ▸ Interfaces to Lightroom and
PhotoLab, and this is the "where is it" half of that group.

The key itself is unchanged — [Global] dxo_path — so nothing that reads it has to
move with it (large_image_view.dxo_executable is the reader).
"""

import os

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QDialogButtonBox, QFrame,
)

from utils.config import _config, persist_config

_DEFAULT = r"C:\Program Files\DxO\DxO PhotoLab 9\DxO.PhotoLab.exe"


class PhotoLabLocationDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PhotoLab Location")
        self.setMinimumWidth(620)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        intro = QLabel(
            "Where DxO PhotoLab is installed. SpeciesViewer uses this to open a "
            "picture — or a whole day — in PhotoLab.")
        intro.setWordWrap(True)
        root.addWidget(intro)

        row = QHBoxLayout()
        row.addWidget(QLabel("PhotoLab program"))
        self.field = QLineEdit(
            _config.get("Global", "dxo_path", fallback=_DEFAULT))
        row.addWidget(self.field)
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        root.addLayout(row)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("font-size: 11px;")
        root.addWidget(self.hint)
        self.field.textChanged.connect(self._check)
        self._check(self.field.text())

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _check(self, text):
        """Say straight away whether the path is real — an install that has moved
        (a PhotoLab upgrade renames the folder: 'DxO PhotoLab 9' → '10') otherwise
        only shows up much later, as a failure to open a picture."""
        path = (text or "").strip()
        if not path:
            self.hint.setText("No path set — the default install location will be tried.")
            self.hint.setStyleSheet("color: #888; font-size: 11px;")
        elif os.path.isfile(path):
            self.hint.setText("✅ Found.")
            self.hint.setStyleSheet("color: #2e7d32; font-size: 11px;")
        else:
            self.hint.setText("⚠ Nothing at that path — opening a picture in PhotoLab will fail.")
            self.hint.setStyleSheet("color: #c0392b; font-size: 11px;")

    def _browse(self):
        start = os.path.dirname(self.field.text().strip()) or "C:/Program Files"
        path, _ = QFileDialog.getOpenFileName(
            self, "Select the DxO PhotoLab program", start,
            "Programs (*.exe);;All files (*)")
        if path:
            self.field.setText(path)

    def _save(self):
        if not _config.has_section("Global"):
            _config.add_section("Global")
        _config.set("Global", "dxo_path", self.field.text().strip())
        persist_config()
        self.accept()
