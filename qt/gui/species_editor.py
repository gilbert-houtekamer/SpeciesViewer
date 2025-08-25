import os
import logging
import difflib
import ast


# import sip
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout, QSpinBox, QPushButton, QSizePolicy, QTextEdit, QComboBox, QDialogButtonBox, 
    QLabel, QMessageBox, QSlider, QHBoxLayout, QProgressDialog)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap, QImage, QFontMetrics, QIcon
from PyQt6.QtCore import Qt, QTimer, QSize

from widgets.trip_widgets import LabeledTextFieldQt
from engine.filepicture import FilePicture
from engine.speciesclassification import Level

class DialogContext:
    def __init__(self, fpr):
        self.fpr = fpr

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

def get_species_candidates(name_type, parent):
    sc_dict = parent.fpr.sc.sc_dict

    if name_type == "Dutch":
        return [name for name in parent.fpr.sc.dutch_dict.keys()
                if sc_dict.get(parent.fpr.sc.dutch_dict[name], {}).get("Level") == Level.SPECIES]
    elif name_type == "English":
        return [name for name in parent.fpr.sc.english_dict.keys()
                if sc_dict.get(parent.fpr.sc.english_dict[name], {}).get("Level") == Level.SPECIES]
    else:
        return [name for name, props in sc_dict.items()
                if props.get("Level") == Level.SPECIES]

def get_species_suggestions(input_name, name_type, parent, max_suggestions=5):
    # if name_type == "Dutch":
    #     candidates = list(parent.fpr.sc.dutch_dict.keys())
    # elif name_type == "English":
    #     candidates = list(parent.fpr.sc.english_dict.keys())
    # else:
    #     candidates = list(parent.fpr.sc.sc_dict.keys())
    candidates = get_species_candidates(name_type, parent)
    matches = difflib.get_close_matches(input_name.lower(), candidates, n=max_suggestions, cutoff=0.6)

    scored_matches = []
    for match in matches:
        score = difflib.SequenceMatcher(None, input_name.lower(), match.lower()).ratio()
        scored_matches.append((match, round(score * 100, 1)))  # e.g. ("zeekoet", 92.3)

    return scored_matches

def format_subject_for_xmp(subject_text):
    try:
        parts = ast.literal_eval(subject_text)
        if isinstance(parts, list):
            return ", ".join(p.strip("'\"") for p in parts)
    except Exception:
        pass
    return subject_text.strip("'\"")


class ConfirmSubjectUpdateDialog(QDialog):
    def __init__(self, original_subject, corrected_subject, file_path, fpr, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Update Subject from '{original_subject}' to '{corrected_subject}'")
        self.setMinimumSize(600, 400)
        self.parent = parent
        self.fpr = fpr
        if not self.parent:
            print("⚠️ ConfirmSubjectUpdateDialog received no parent!")        
        self.original_subject = original_subject
        self.corrected_subject = corrected_subject
        self.file_path = Path(file_path)

        layout = QVBoxLayout(self)

        # Scope selector
        self.scope_selector = QComboBox()
        layout.addWidget(QLabel("Apply to:"))
        layout.addWidget(self.scope_selector)

        # File preview table
        self.file_table = QTextEdit()
        self.file_table.setReadOnly(True)
        layout.addWidget(self.file_table)

        # Buttons
        button_row = QHBoxLayout()
        commit_button = QPushButton("Commit")
        commit_button.clicked.connect(self.on_commit)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(commit_button)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

        self.populate_scope_options()
        self.scope_selector.currentIndexChanged.connect(self.update_file_preview)
        self.update_file_preview()

    def populate_scope_options(self):
        folder = self.file_path.parent
        trip_root = folder.parent

        self.scope_options = {
            "Just this file": [self.file_path],
            "All files in this folder": list(folder.glob("*.NEF")),
            "All files in this trip": list(trip_root.rglob("*.NEF")),
        }

        for label, files in self.scope_options.items():
            count = len(files)
            self.scope_selector.addItem(f"{label} ({count} files)", label)

    def update_file_preview(self):
        label = self.scope_selector.currentData()
        files = self.scope_options.get(label, [])
        matching_files = [f for f in files if self.fpr.getSubject(f.name) == self.original_subject]

        preview_text = "\n".join(str(f.name) for f in matching_files)
        self.file_table.setPlainText(preview_text)
        self.selected_files = matching_files

    def on_commit(self):
        for f in self.selected_files:
            self.fpr.updateSubject(f.name, self.original_subject,  self.corrected_subject)
            print(f"""self.fpr.updateSubject({f.name}, "{self.original_subject}", "{self.corrected_subject}")""")
        
        self.accept()        


class SpeciesInfoDialog(QDialog):
    def __init__(self, full_path, species_name, error_msg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Species Info")
        self.setMinimumWidth(400)
        self.main_window = parent
        self.image_viewer = parent
        self.full_path = full_path
        filename = full_path.name

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

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.on_close)
        button_row.addWidget(close_button)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        layout.addLayout(button_row)

    def on_suggest_species(self):
        if not hasattr(self, "subject_field") or not self.subject_field:
            print("⚠️ subject_field is missing or deleted.")
            return        
        
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
            try:
                self.suggestion_dropdown.clear()
                for name, score in suggestions:
                    self.suggestion_dropdown.addItem(f"{name} ({score}%)", name)
                self.suggestion_dropdown.show()
            except RuntimeError:
                print("Suggestion dropdown was deleted.")
                return                
        else:
            QMessageBox.warning(self, "No Suggestion", "No close match found.")
            self.suggestion_dropdown.hide()


    def on_suggestion_selected(self, index):
        if not hasattr(self, "subject_field") or not self.subject_field:
            print("⚠️ subject_field is missing or deleted.")
            return

        selected_name = self.suggestion_dropdown.itemData(index)
        if not selected_name or selected_name.lower() == "none":
            return  # Don't overwrite with invalid suggestion

        raw_subject = self.subject_field.get_text()

        try:
            parts = ast.literal_eval(raw_subject)
            if not isinstance(parts, list):
                raise ValueError
        except Exception:
            # Fallback: treat raw string as a single part
            parts = [raw_subject.strip()]

        selected_part = self.subject_part_selector.currentText()
        selected_name = selected_name.lower()
        updated_parts = [selected_name if part == selected_part else part for part in parts]

        # Rebuild as a proper quoted list
        updated_subject = "[" + ", ".join(f"'{p}'" for p in updated_parts) + "]"
        self.subject_field.set_text(updated_subject)

    def on_commit(self):
        print(f"img full path: { self.full_path}")
        print("Passing parent:", self.main_window)        
        print("Subject field exists:", hasattr(self, "subject_field"))
        print("Field widget:", self.subject_field.field)        
        try:
            edited_subject = self.subject_field.get_text()
        except RuntimeError:
            print("Subject field was deleted before commit.")
            return
        
        if self.image_viewer and self.image_viewer.isVisible():
            parent = self.image_viewer
        else:
            print("⚠️ image_viewer is not visible. Forcing visibility.")
            self.image_viewer.show()
            parent = self.image_viewer


        original_subject_value = self.main_window.fpr.getSubject(self.full_path) if self.main_window and self.main_window.fpr else ""
        formatted_original_subject = format_subject_for_xmp(original_subject_value)
        formatted_edited_subject = format_subject_for_xmp(edited_subject)

        # if isinstance(self.main_window, QWidget):
        #     parent = self.main_window
        # else:
        #     print("⚠️ No valid QWidget parent.")
        #     parent = None

        print("Parent deleted?", isinstance(self.main_window, QWidget))
        try:
            print("Parent name:", self.main_window.objectName())
        except RuntimeError:
            print("❌ main_window is already deleted.")  
            print("⚠️ main_window was deleted, falling back to active window.")
            parent = QApplication.activeWindow()
                
        dialog = ConfirmSubjectUpdateDialog(
            formatted_original_subject, 
            formatted_edited_subject, 
            self.full_path, 
            fpr=self.main_window.fpr if self.main_window else None,
            parent=parent)
        
        result = dialog.exec()

        if result == QDialog.DialogCode.Accepted:
            print("Committed subject:", edited_subject)
            self.accept()

    def closeEvent(self, event):
        try:
            self.suggestion_dropdown.currentIndexChanged.disconnect()
        except Exception:
            pass
        event.accept()

    def on_close(self):
        print("Edited subject:", self.subject_field.get_text())        
        self.accept()

