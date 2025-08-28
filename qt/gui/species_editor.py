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

def is_widget_alive(widget):
    try:
        _ = widget.objectName()
        return True
    except RuntimeError:
        return False


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
    def __init__(self, original_subject, corrected_subject, file_path, scope_options, fpr, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Update Subject from '{original_subject}' to '{corrected_subject}'")
        self.setMinimumSize(600, 400)

        self.original_subject = original_subject
        self.corrected_subject = corrected_subject
        self.file_path = Path(file_path)
        self.scope_options = scope_options
        self.fpr = fpr

        layout = QVBoxLayout(self)

        # Scope selector
        self.scope_selector = QComboBox()
        for label, files in self.scope_options.items():
            count = len(files)
            self.scope_selector.addItem(f"{label} ({count} files)", label)
        layout.addWidget(QLabel("Apply to:"))
        layout.addWidget(self.scope_selector)

        # File preview
        self.file_table = QTextEdit()
        self.file_table.setReadOnly(True)
        layout.addWidget(self.file_table)

        # Buttons
        button_row = QHBoxLayout()
        commit_button = QPushButton("Commit")
        commit_button.clicked.connect(self.on_commit_code)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(commit_button)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

        self.scope_selector.currentIndexChanged.connect(self.update_file_preview)
        self.update_file_preview()

    def update_file_preview(self):
        label = self.scope_selector.currentData()
        files = self.scope_options.get(label, [])
        matching_files = [f for f in files if self.fpr.getSubject(f.name) == self.original_subject]
        preview_text = "\n".join(str(f.name) for f in matching_files)
        self.file_table.setPlainText(preview_text)
        self.selected_files = matching_files

    def on_commit_code(self):
        print("ConfirmSubjectUpdateDialog on_commit entered")
        for f in self.selected_files:
            self.fpr.updateSubject(f.name, self.original_subject, self.corrected_subject)
            print(f"""self.fpr.updateSubject({f.name}, "{self.original_subject}", "{self.corrected_subject}")""")
        QTimer.singleShot(0, self.accept)


    def closeEvent(self, event):
        print(f"closeEvent: self.commit_button.clicked.disconnect() and .accept")
        try:
            self.commit_button.clicked.disconnect()
        except Exception:
            pass
        event.accept()

    def __del__(self):
        print("🧹 ConfirmSubjectUpdateDialog was garbage collected")        


import ast
import weakref
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTextEdit, QComboBox,
    QPushButton, QHBoxLayout, QMessageBox, QApplication, QWidget
)
from PyQt6.QtCore import QTimer
from functools import partial

class SpeciesInfoDialog(QDialog):
    def __init__(self, full_path, species_name, error_msg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Species Info")
        self.setMinimumWidth(400)
        self.main_window_ref = weakref.ref(parent) if parent else None
        self.full_path = full_path
        filename = full_path.name

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"<b>Filename:</b> {filename}"))
        layout.addWidget(QLabel(f"<b>Species:</b> {species_name or '(unknown)'}"))
        layout.addWidget(QLabel("<b>Error Message:</b>"))

        error_text = QTextEdit()
        error_text.setReadOnly(True)
        error_text.setPlainText(error_msg or "No error.")
        layout.addWidget(error_text)

        main_window = self.main_window_ref() if self.main_window_ref else None
        subject_value = main_window.fpr.getSubject(filename) if main_window and hasattr(main_window, "fpr") else ""
        self.subject_field = LabeledTextFieldQt("Species name as specified in keyword values:", value=subject_value)

        self.subject_part_selector = QComboBox()
        layout.addWidget(QLabel("Select part to match"))
        layout.addWidget(self.subject_part_selector)
        self.subject_part_selector.hide()
        layout.addWidget(self.subject_field)

        self.name_type_selector = QComboBox()
        self.name_type_selector.addItems(["Auto", "Dutch", "English", "Scientific"])
        layout.addWidget(QLabel("Name Type"))
        layout.addWidget(self.name_type_selector)

        suggest_button = QPushButton("Suggest Species")
        suggest_button.clicked.connect(lambda: self.on_suggest_species() if self.isVisible() else None)
        layout.addWidget(suggest_button)

        self.suggestion_dropdown = QComboBox()
        layout.addWidget(QLabel("Suggestions"))
        layout.addWidget(self.suggestion_dropdown)
        self.suggestion_dropdown.hide()
        self.suggestion_dropdown.currentIndexChanged.connect(
            lambda index: self.on_suggestion_selected(index) if self.isVisible() else None
        )

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

        self.confirm_dialog = None

    def on_suggest_species(self):
        if not hasattr(self, "subject_field") or not self.subject_field:
            print("⚠️ subject_field is missing or deleted.")
            return

        raw_subject = self.subject_field.get_text()
        try:
            parsed = ast.literal_eval(raw_subject)
            parts = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            parts = [raw_subject.strip()]

        normalized_subject = "[" + ", ".join(f"'{p}'" for p in parts) + "]"
        self.subject_field.set_text(normalized_subject)

        self.subject_part_selector.clear()
        for part in parts:
            self.subject_part_selector.addItem(part)
        self.subject_part_selector.show()

        input_name = max(parts, key=len)
        selected_index = self.subject_part_selector.findText(input_name)
        self.subject_part_selector.setCurrentIndex(selected_index)

        name_type = self.name_type_selector.currentText()
        main_window = self.main_window_ref() if self.main_window_ref else None
        if name_type == "Auto":
            suggestions = get_best_species_suggestions(input_name, main_window)
        else:
            suggestions = get_species_suggestions(input_name, name_type, main_window)

        if suggestions:
            try:
                self.suggestion_dropdown.clear()
                for name, score in suggestions:
                    self.suggestion_dropdown.addItem(f"{name} ({score}%)", name)
                self.suggestion_dropdown.show()
            except RuntimeError:
                print("Suggestion dropdown was deleted.")
        else:
            QMessageBox.warning(self, "No Suggestion", "No close match found.")
            self.suggestion_dropdown.hide()

    def on_suggestion_selected(self, index):
        if not hasattr(self, "subject_field") or not self.subject_field:
            print("⚠️ subject_field is missing or deleted.")
            return

        selected_name = self.suggestion_dropdown.itemData(index)
        if not selected_name or selected_name.lower() == "none":
            return

        raw_subject = self.subject_field.get_text()
        try:
            parts = ast.literal_eval(raw_subject)
            if not isinstance(parts, list):
                raise ValueError
        except Exception:
            parts = [raw_subject.strip()]

        selected_part = self.subject_part_selector.currentText()
        selected_name = selected_name.lower()
        updated_parts = [selected_name if part == selected_part else part for part in parts]
        updated_subject = "[" + ", ".join(f"'{p}'" for p in updated_parts) + "]"
        self.subject_field.set_text(updated_subject)

    def on_commit(self):
        print(f"📷 Image full path: {self.full_path}")
        if not hasattr(self, "subject_field") or not self.subject_field.field:
            print("❌ Subject field missing or invalid.")
            return

        try:
            edited_subject = self.subject_field.get_text()
        except RuntimeError:
            print("❌ Subject field was deleted before commit.")
            return

        main_window = self.main_window_ref() if self.main_window_ref else None
        if not main_window or not isinstance(main_window, QWidget) or not main_window.isVisible():
            print("⚠️ main_window is deleted or not usable. Falling back to active window.")
            parent = QApplication.activeWindow()
        else:
            parent = main_window

        original_subject_value = ""
        if main_window and hasattr(main_window, "fpr"):
            original_subject_value = main_window.fpr.getSubject(self.full_path) or ""

        formatted_original_subject = format_subject_for_xmp(original_subject_value)
        formatted_edited_subject = format_subject_for_xmp(edited_subject)

        folder = Path(self.full_path).parent
        trip_root = folder.parent
        scope_options = {
            "Just this file": [self.full_path],
            "All files in this folder": list(folder.glob("*.NEF")),
            "All files in this trip": list(trip_root.rglob("*.NEF")),
        }

        self.confirm_dialog = ConfirmSubjectUpdateDialog(
            formatted_original_subject,
            formatted_edited_subject,
            self.full_path,
            scope_options,
            fpr=main_window.fpr if main_window else None,
            parent=parent
        )
        result = self.confirm_dialog.exec()
        self.confirm_dialog.deleteLater()
        self.confirm_dialog = None

        if result == QDialog.DialogCode.Accepted:
            self._on_confirm_accepted_safe(edited_subject)
        else:
            self._on_confirm_rejected()

    def _on_confirm_accepted_safe(self, edited_subject):
        print("Committed subject:", edited_subject)
        try:
            self.accept()
        except RuntimeError:
            print("❌ ConfirmSubjectUpdateDialog was deleted before accept()")

    def _on_confirm_rejected(self):
        print("Subject commit canceled")

    def on_close(self):
        print("on_close: edited subject:", self.subject_field.get_text())
        self.accept()

    def closeEvent(self, event):
        try:
            self.suggestion_dropdown.currentIndexChanged.disconnect()
        except Exception:
            pass

        if self.confirm_dialog:
            self.confirm_dialog.deleteLater()
            self.confirm_dialog = None

        print("🧨 SpeciesInfoDialog is being closed!")
        event.accept()

    def __del__(self):
        print("🧹 SpeciesInfoDialog was garbage collected")


