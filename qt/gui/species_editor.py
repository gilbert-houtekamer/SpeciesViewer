import os
import logging
import difflib
import ast

# import sip
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout, QSpinBox, QPushButton, QSizePolicy, QTextEdit, QComboBox, 
    QLabel, QMessageBox, QSlider, QHBoxLayout, QProgressDialog)
from PyQt6.QtGui import QPixmap, QImage, QFontMetrics, QIcon
from PyQt6.QtCore import Qt, QTimer, QSize

from widgets.trip_widgets import LabeledTextFieldQt
from engine.filepicture import FilePicture

def get_best_species_suggestions(input_name, parent, max_suggestions=5):
    all_types = ["Scientific", "English", "Dutch"]
    best_match = []
    best_score = 0

    for name_type in all_types:
        suggestions = get_species_suggestions(input_name, name_type, parent, max_suggestions)
        if suggestions and suggestions[0][1] > best_score:
            best_match = suggestions
            best_score = suggestions[0][1]

    return best_match


def get_species_suggestions(input_name, name_type, parent, max_suggestions=5):
    if name_type == "Dutch":
        candidates = list(parent.fpr.sc.dutch_dict.keys())
    elif name_type == "English":
        candidates = list(parent.fpr.sc.english_dict.keys())
    else:
        candidates = list(parent.fpr.sc.sc_dict.keys())

    matches = difflib.get_close_matches(input_name.lower(), candidates, n=max_suggestions, cutoff=0.6)

    scored_matches = []
    for match in matches:
        score = difflib.SequenceMatcher(None, input_name.lower(), match.lower()).ratio()
        scored_matches.append((match, round(score * 100, 1)))  # e.g. ("zeekoet", 92.3)

    return scored_matches

class SpeciesInfoDialog(QDialog):
    def __init__(self, filename, species_name, error_msg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Species Info")
        self.setMinimumWidth(400)
        self.main_window = parent

        layout = QVBoxLayout(self)

        # Filename
        filename_label = QLabel(f"<b>Filename:</b> {filename}")
        layout.addWidget(filename_label)

        # Species name
        species_label = QLabel(f"<b>Species:</b> {species_name or '(unknown)'}")
        layout.addWidget(species_label)

        # Error message
        error_label = QLabel("<b>Error Message:</b>")
        layout.addWidget(error_label)

        error_text = QTextEdit()
        error_text.setReadOnly(True)
        error_text.setPlainText(error_msg or "No error.")
        layout.addWidget(error_text)

        # Subject field (editable)
        subject_value = self.main_window.fpr.getSubject(filename) if self.main_window and self.main_window.fpr else ""
        self.subject_field = LabeledTextFieldQt("Species name as specified in keyword values:", value=subject_value)
        self.subject_part_selector = QComboBox()
        layout.addWidget(QLabel("Select part to match"))
        layout.addWidget(self.subject_part_selector)
        self.subject_part_selector.hide()  # Hide until populated
        layout.addWidget(self.subject_field)

        self.name_type_selector = QComboBox()
        self.name_type_selector.addItems(["Auto", "Dutch", "English", "Scientific"])
        layout.addWidget(QLabel("Name Type"))
        layout.addWidget(self.name_type_selector)

        suggest_button = QPushButton("Suggest Species")
        suggest_button.clicked.connect(self.on_suggest_species)
        layout.addWidget(suggest_button)

        self.suggestion_dropdown = QComboBox()
        layout.addWidget(QLabel("Suggestions"))
        layout.addWidget(self.suggestion_dropdown)
        self.suggestion_dropdown.hide()  # Hide until needed
        self.suggestion_dropdown.currentIndexChanged.connect(self.on_suggestion_selected)

        button_row = QHBoxLayout()

        commit_button = QPushButton("Commit")
        commit_button.clicked.connect(self.on_commit)
        button_row.addWidget(commit_button)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        layout.addLayout(button_row)

    def on_suggest_species(self):
        raw_subject = self.subject_field.get_text()

        try:
            parsed = ast.literal_eval(raw_subject)
            if isinstance(parsed, list):
                parts = parsed
            elif isinstance(parsed, str):
                parts = [parsed]
            else:
                raise ValueError
        except Exception:
            # Fallback: treat raw string as a single part
            parts = [raw_subject.strip()]

        # ✅ Normalize and update subject field for consistency
        normalized_subject = "[" + ", ".join(f"'{p}'" for p in parts) + "]"
        self.subject_field.set_text(normalized_subject)

        # Populate dropdown
        self.subject_part_selector.clear()
        for part in parts:
            self.subject_part_selector.addItem(part)
        self.subject_part_selector.show()

        # Default to longest part
        input_name = max(parts, key=len)
        selected_index = self.subject_part_selector.findText(input_name)
        self.subject_part_selector.setCurrentIndex(selected_index)

        name_type = self.name_type_selector.currentText()
        if name_type == "Auto":
            suggestions = get_best_species_suggestions(input_name, self.main_window)
        else:
            suggestions = get_species_suggestions(input_name, name_type, self.main_window)

        if suggestions:
            self.suggestion_dropdown.clear()
            for name, score in suggestions:
                self.suggestion_dropdown.addItem(f"{name} ({score}%)", name)
            self.suggestion_dropdown.show()
        else:
            QMessageBox.warning(self, "No Suggestion", "No close match found.")
            self.suggestion_dropdown.hide()


    def on_suggestion_selected(self, index):
        selected_name = self.suggestion_dropdown.itemData(index)
        raw_subject = self.subject_field.get_text()

        try:
            parts = ast.literal_eval(raw_subject)
            if not isinstance(parts, list):
                raise ValueError
        except Exception:
            # Fallback: treat raw string as a single part
            parts = [raw_subject.strip()]

        selected_part = self.subject_part_selector.currentText()
        updated_parts = [selected_name if part == selected_part else part for part in parts]

        # Rebuild as a proper quoted list
        updated_subject = "[" + ", ".join(f"'{p}'" for p in updated_parts) + "]"
        self.subject_field.set_text(updated_subject)

    def on_commit(self):
        edited_subject = self.subject_field.get_text()
        print("Committed subject:", edited_subject)
        self.accept()



