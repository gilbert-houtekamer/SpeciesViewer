"""Paste a species list straight from the AviBase page into the trip.

ONE JOB: take pasted text, say what it contains, and save it beside the trip as
a file the checklist step can read. It saves a real file rather than keeping the
text in memory: the list is then next to the trip for the next run, for a rebuild
months later, and for a machine with no connection.

INVARIANT: an existing file is never overwritten without being asked.
"""

import logging
import os

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QPlainTextEdit, QMessageBox,
)

from engine.avibaselib import rows_from_text, parse_rows
from gui.avibase_columns import guess_columns, describe_roles

logger = logging.getLogger("speciesviewer")


class PasteListDialog(QDialog):
    """Returns the path it wrote in `saved_path` once accepted."""

    def __init__(self, parent, trip_root, default_name="birdlist"):
        super().__init__(parent)
        self.setWindowTitle("Paste a species list")
        self.resize(760, 560)

        self.trip_root = str(trip_root)
        self.saved_path = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Select the list on the AviBase page, copy it, and paste it here. "
            "It is saved in the trip data folder, so it stays available for a "
            "later run."
        ))

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Save as"))
        self.name_field = QLineEdit(default_name)
        name_row.addWidget(self.name_field)
        name_row.addWidget(QLabel(".csv  in " + os.path.basename(self.trip_root)))
        name_row.addStretch()
        layout.addLayout(name_row)

        self.text_box = QPlainTextEdit()
        self.text_box.setPlaceholderText(
            "Greater Rhea\tRhea americana\tNandoe\n"
            "Dwarf Tinamou\tTaoniscus nanus\tDwergtinamoe\n…"
        )
        layout.addWidget(self.text_box)

        self.summary = QLabel("Nothing pasted yet.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.save_btn = QPushButton("Save and mark the columns")
        self.save_btn.setEnabled(False)
        self.cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

        self.save_btn.clicked.connect(self._on_save)
        self.cancel_btn.clicked.connect(self.reject)
        self.text_box.textChanged.connect(self._describe)

    # ── what did he just paste? ─────────────────────────────────────────────
    def _describe(self):
        """Count the pasted text with the very code that will read the file, so
        the number here cannot disagree with the number after saving."""
        text = self.text_box.toPlainText()
        if not text.strip():
            self.summary.setText("Nothing pasted yet.")
            self.save_btn.setEnabled(False)
            return
        try:
            rows, delimiter = rows_from_text(text)
            roles, first_row = guess_columns(rows, "the pasted list")
            species = parse_rows(rows, roles, first_row, "the pasted list")
        except Exception:
            # Whatever went wrong, at this point the text simply is not a list —
            # the reader's own advice ("open Columns…") makes no sense yet.
            self.summary.setText(
                "❌ This does not look like a species list yet. Expected one "
                "species per line, with at least a name and a scientific name, "
                "separated by tabs or semicolons — exactly what the AviBase page "
                "gives when you select and copy it."
            )
            self.save_btn.setEnabled(False)
            return

        real = [v for v in species.values() if v["number"] != -1]
        headings = len(species) - len(real)
        named = sum(1 for v in real if v["nlname"])
        shown = {"\t": "tab"}.get(delimiter, delimiter)
        self.summary.setText(
            f"{len(real)} species, {headings} section headings, {named} with a "
            f"second-language name. Columns look like: {describe_roles(roles, first_row)} "
            f"(separator: {shown}). You can correct all of that in the next step."
        )
        self.save_btn.setEnabled(True)

    # ── saving ──────────────────────────────────────────────────────────────
    def _target_path(self):
        name = (self.name_field.text().strip() or "birdlist")
        for bad in '<>:"/\\|?*':
            name = name.replace(bad, "-")
        if not name.lower().endswith(".csv"):
            name += ".csv"
        return os.path.join(self.trip_root, name)

    def _on_save(self):
        path = self._target_path()
        if os.path.isfile(path):
            answer = QMessageBox.question(
                self, "Replace the file?",
                f"{os.path.basename(path)} already exists in the trip data "
                f"folder.\n\nReplace it with what you pasted?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            # UTF-8, so the accented names survive — see utils/ini_io for the
            # same lesson learned the hard way.
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(self.text_box.toPlainText())
        except OSError as err:
            self.summary.setText(f"❌ Could not save: {err}")
            return
        logger.info(f"Pasted species list saved as {path}")
        self.saved_path = path
        self.accept()
