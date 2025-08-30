import os
import logging
import difflib
import ast
import logging
import weakref

from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout, QSpinBox, QPushButton, QSizePolicy, QTextEdit, QComboBox, QDialogButtonBox, 
    QLabel, QMessageBox, QSlider, QHBoxLayout, QProgressDialog)
from PyQt6.QtCore import Qt, QTimer, QSize

from widgets.trip_widgets import LabeledTextFieldQt
from engine.filepicture import FilePicture
from engine.speciesclassification import Level

logger = logging.getLogger("speciesviewer")

class DialogContext:
    def __init__(self, fpr):
        self.fpr = fpr

def is_widget_alive(widget):
    try:
        _ = widget.objectName()
        return True
    except RuntimeError:
        return False

raw_exts = [".nef", ".orf", ".cr2", ".arw", ".rw2", ".heic"]

def getCorrectionScopeOptions(file_path: Path) -> dict[str, list[Path]]:
    folder = file_path.parent
    trip_root = folder.parent

    def is_raw(p: Path) -> bool:
        return p.suffix.lower() in raw_exts

    return {
        "Only the selected file": [file_path],
        "All files with same name error in day folder": [p for p in folder.iterdir() if is_raw(p)],
        "All files with same name error in all trip day folders": [p for p in trip_root.rglob("*") if is_raw(p)],
    }

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


# class ConfirmSubjectUpdateDialog(QDialog):
#     def __init__(self, original_subject, corrected_subject, file_path, scope_options, fpr, parent=None):
#         super().__init__(parent)
#         self.setWindowTitle(f"Update Subject from '{original_subject}' to '{corrected_subject}'")
#         self.setMinimumSize(600, 400)

#         self.original_subject = original_subject
#         self.corrected_subject = corrected_subject
#         self.file_path = Path(file_path)
#         self.scope_options = scope_options
#         self.fpr = fpr

#         layout = QVBoxLayout(self)

#         # Scope selector
#         self.scope_selector = QComboBox()
#         for label, files in self.scope_options.items():
#             count = len(files)
#             self.scope_selector.addItem(f"{label} ({count} files)", label)
#         layout.addWidget(QLabel("Apply to:"))
#         layout.addWidget(self.scope_selector)

#         # File preview
#         self.file_table = QTextEdit()
#         self.file_table.setReadOnly(True)
#         layout.addWidget(self.file_table)

#         # Buttons
#         button_row = QHBoxLayout()
#         commit_button = QPushButton("Commit")
#         commit_button.clicked.connect(self.on_commit_code)
#         cancel_button = QPushButton("Cancel")
#         cancel_button.clicked.connect(self.reject)
#         button_row.addWidget(commit_button)
#         button_row.addWidget(cancel_button)
#         layout.addLayout(button_row)

#         self.scope_selector.currentIndexChanged.connect(self.update_file_preview)
#         self.update_file_preview()

#     def update_file_preview(self):
#         label = self.scope_selector.currentData()
#         files = self.scope_options.get(label, [])
#         matching_files = [f for f in files if self.fpr.getSubject(f.name) == self.original_subject]
#         preview_text = "\n".join(str(f.name) for f in matching_files)
#         self.file_table.setPlainText(preview_text)
#         self.selected_files = matching_files

#     def on_commit_code(self):
#         print("ConfirmSubjectUpdateDialog on_commit entered")
#         for f in self.selected_files:
#             self.fpr.updateSubject(f.name, self.original_subject, self.corrected_subject)
#             print(f"""self.fpr.updateSubject({f.name}, "{self.original_subject}", "{self.corrected_subject}")""")
#         QTimer.singleShot(0, self.accept)


#     def closeEvent(self, event):
#         print(f"closeEvent: self.commit_button.clicked.disconnect() and .accept")
#         try:
#             self.commit_button.clicked.disconnect()
#         except Exception:
#             pass
#         event.accept()

#     def __del__(self):
#         print("🧹 ConfirmSubjectUpdateDialog was garbage collected")        

class SpeciesInfoDialog(QDialog):
    def __init__(self, full_path, species_name, error_msg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Species Info")
        self.setMinimumWidth(600)
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

        # Subject field
        main_window = self.main_window_ref() if self.main_window_ref else None
        image_viewer = getattr(main_window, "image_viewer_dialog", None)
        fpr = getattr(image_viewer, "fpr", None)

        subject_value = fpr.getSubject(filename) if fpr else ""
        self.subject_field = LabeledTextFieldQt("Suggested species name:", value=subject_value)
        layout.addWidget(self.subject_field)

        # Status label for validation feedback
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        # Suggest species immediately
        self.suggest_species()

        # Button row
        button_row = QHBoxLayout()

        validate_button = QPushButton("Validate")
        validate_button.clicked.connect(self.on_validate_clicked)
        button_row.addWidget(validate_button)

        commit_button = QPushButton("Commit")
        commit_button.clicked.connect(self.on_commit_clicked)
        button_row.addWidget(commit_button)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        layout.addLayout(button_row)

    def suggest_species(self):
        input_name = self.subject_field.get_text().strip()
        if not input_name:
            self.status_label.setText("⚠️ No input for suggestion.")
            return

        main_window = self.main_window_ref() if self.main_window_ref else None
        image_viewer = getattr(main_window, "image_viewer_dialog", None)
        config = getattr(main_window, "config", None)

        name_type = config.get("Species", "name_type", fallback="Auto") if config else "Auto"

        if name_type == "Auto":
            suggestions = get_best_species_suggestions(input_name, image_viewer)
        else:
            suggestions = get_species_suggestions(input_name, name_type, image_viewer)

        if suggestions:
            best_name, _ = suggestions[0]
            self.subject_field.set_text(best_name)
            self.status_label.setText(f"🔍 Suggested: {best_name}")
        else:
            self.status_label.setText("❌ No suggestions found.")

    def on_validate_clicked(self):
        name = self.subject_field.get_text().strip()
        main_window = self.main_window_ref() if self.main_window_ref else None
        image_viewer = getattr(main_window, "image_viewer_dialog", None)
        fpr = getattr(image_viewer, "fpr", None)

        if not name:
            self.status_label.setText("⚠️ Please enter a species name.")
            return

        try:
            validated = fpr.sc.getSpecies(name) if fpr else ""
            if validated:
                self.status_label.setText(f"✅ Validated species name: {name} ({validated})")
                logger.info(f"Validated species name: {name} → {validated}")
            else:
                self.status_label.setText(f"❌ Unknown species: {name}")
                logger.info(f"❌ Unknown species: {name}")  
        except Exception as e:
            self.status_label.setText(f"⚠️ Validation error: {str(e)}")
            logger.info(f"⚠️ Validation error: {str(e)}")

    def on_commit_clicked(self):
        corrected_subject = self.subject_field.get_text().strip()
        if not corrected_subject:
            self.status_label.setText("⚠️ No species name to commit.")
            return

        main_window = self.main_window_ref() if self.main_window_ref else None
        image_viewer = getattr(main_window, "image_viewer_dialog", None)
        fpr = getattr(image_viewer, "fpr", None)
        config = getattr(main_window, "config", None)

        if not fpr:
            self.status_label.setText("❌ Cannot access FPR for update.")
            return

        original_subject = fpr.getSubject(self.full_path.name)
        saved_scope = config.get("Species", "correction_scope", fallback="All files with same name error in day folder") if config else "Only the selected file"

        # Determine scope
        scope_options = getCorrectionScopeOptions(self.full_path)  # Assumes this method returns a dict like {label: [Path, Path, ...]}
        selected_files = scope_options.get(saved_scope, [])

        # Filter files that match the original subject
        matching_files = [f for f in selected_files if fpr.getSubject(f.name) == original_subject]

        # Apply updates
        self.status_label.setText(f"📦 Committing species name: {corrected_subject} to {len(matching_files)} file{'s' if len(matching_files) > 1 else ''}")
        logger.info(f"📦 Committing species name: {corrected_subject} to {len(matching_files)} file{'s' if len(matching_files) > 1 else ''}")
        for f in matching_files:
            fpr.updateSubject(f.name, original_subject, corrected_subject)
            logger.info(f"""✅ Updated {f.name} with corrected subject from "{original_subject}" to "{corrected_subject}".""")

        self.accept()


    def closeEvent(self, event):
        print("🧨 SpeciesInfoDialog is being closed!")
        event.accept()

    def __del__(self):
        print("🧹 SpeciesInfoDialog was garbage collected")
