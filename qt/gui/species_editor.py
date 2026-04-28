import os
import logging
import difflib
import ast
import logging
import weakref
from pygbif import species

from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout, QSpinBox, QPushButton, QSizePolicy, QTextEdit, QComboBox, QDialogButtonBox, 
    QLabel, QMessageBox, QSlider, QHBoxLayout, QProgressDialog)
from PyQt6.QtCore import Qt, QTimer, QSize

from widgets.trip_widgets import LabeledTextFieldQt
from engine.filepicture import FilePicture, format_subject_for_xmp
from engine.speciesclassification import Level
from utils.config import _config

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
    candidates = get_species_candidates(name_type, parent)
    matches = difflib.get_close_matches(input_name.lower(), candidates, n=max_suggestions, cutoff=0.6)

    scored_matches = []
    for match in matches:
        score = difflib.SequenceMatcher(None, input_name.lower(), match.lower()).ratio()
        scored_matches.append((match, round(score * 100, 1)))  # e.g. ("zeekoet", 92.3)

    return scored_matches


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

        # Subject field
        self.subject_field = LabeledTextFieldQt("Suggested species name:", value=subject_value)
        layout.addWidget(self.subject_field)

        # Suggestion dropdown
        self.suggestion_dropdown = QComboBox()
        self.suggestion_dropdown.setEditable(False)
        self.suggestion_dropdown.setVisible(False)
        self.suggestion_dropdown.setToolTip("Select a species name to apply manually")
        self.suggestion_dropdown.setObjectName("suggestion_dropdown")
        self.suggestion_dropdown.currentIndexChanged.connect(self.on_suggestion_selected)
        layout.addWidget(QLabel("Suggestions"))
        layout.addWidget(self.suggestion_dropdown)

        # Status label
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        # Button row
        button_row = QHBoxLayout()

        suggest_button = QPushButton("Suggest")
        suggest_button.setToolTip("Find closest matching species names")
        suggest_button.clicked.connect(self.suggest_species)
        button_row.addWidget(suggest_button)

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

    import ast

    def suggest_species(self):
        raw_input = self.subject_field.get_text().strip()
        if not raw_input:
            self.status_label.setText("⚠️ No input for suggestion.")
            self.suggestion_dropdown.setVisible(False)
            return

        main_window = self.main_window_ref() if self.main_window_ref else None
        image_viewer = getattr(main_window, "image_viewer_dialog", None)
        config = getattr(main_window, "config", None)
        fpr = getattr(image_viewer, "fpr", None)

        name_type = config.get("Species", "name_type", fallback="Auto") if config else "Auto"

        try:
            parts = ast.literal_eval(raw_input)
            if not isinstance(parts, list):
                raise ValueError
        except Exception:
            parts = [raw_input]

        input_name = max(parts, key=len)

        if name_type == "Auto":
            suggestions = get_best_species_suggestions(input_name, image_viewer)
        else:
            suggestions = get_species_suggestions(input_name, name_type, image_viewer)

        if suggestions:
            best_name, _ = suggestions[0]
            updated_parts = [best_name if part == input_name else part for part in parts]
            updated_subject = "[" + ", ".join(f"'{p}'" for p in updated_parts) + "]"
            self.subject_field.set_text(updated_subject)
            self.status_label.setText(f"🔍 Suggested: {best_name}")

            # Populate dropdown
            self.suggestion_dropdown.clear()
            for name, score in suggestions:
                self.suggestion_dropdown.addItem(f"{name} ({score}%)", name)
            self.suggestion_dropdown.setVisible(True)
        else:
            validated = fpr.sc.getSpecies(input_name, fuzzy=True) if fpr else ""
            if validated:
                updated_parts = [validated if part == input_name else part for part in parts]
                updated_subject = "[" + ", ".join(f"'{p}'" for p in updated_parts) + "]"
                self.subject_field.set_text(updated_subject)
                self.status_label.setText(f"✅ Suggested scientific species name: {validated}")
                logger.info(f"Validated scientific species name {input_name} → {validated}")
            else:
                self.status_label.setText(f"❌ No suggestions found for {input_name}.")
                self.suggestion_dropdown.setVisible(False)
                logger.info(f"❌ No suggestions found for {input_name}.")

    def on_suggestion_selected(self, index):
        selected_name = self.suggestion_dropdown.itemData(index)
        if not selected_name:
            return

        raw_input = self.subject_field.get_text().strip()
        try:
            parts = ast.literal_eval(raw_input)
            if not isinstance(parts, list):
                raise ValueError
        except Exception:
            parts = [raw_input]

        input_name = max(parts, key=len)
        updated_parts = [selected_name if part == input_name else part for part in parts]
        updated_subject = "[" + ", ".join(f"'{p}'" for p in updated_parts) + "]"
        self.subject_field.set_text(updated_subject)
        self.status_label.setText(f"🔄 Replaced with: {selected_name}")

    def on_validate_clicked(self):
        raw_input = self.subject_field.get_text().strip()
        main_window = self.main_window_ref() if self.main_window_ref else None
        image_viewer = getattr(main_window, "image_viewer_dialog", None)
        fpr = getattr(image_viewer, "fpr", None)

        if not raw_input:
            self.status_label.setText("⚠️ Please enter a species name.")
            return

        try:
            # Try to parse as list
            try:
                parts = ast.literal_eval(raw_input)
                if not isinstance(parts, list):
                    raise ValueError
            except Exception:
                parts = [raw_input]

            # Validate longest part
            input_name = max(parts, key=len)
            validated = fpr.sc.getSpecies(input_name, fuzzy=True) if fpr else ""

            if validated:
                self.status_label.setText(f"✅ Validated species name: {input_name} ({validated})")
                logger.info(f"Validated species name: {input_name} → {validated}")
            else:
                self.status_label.setText(f"❌ Unknown species: {input_name}")
                logger.info(f"❌ Unknown species: {input_name}")
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
        if not fpr:
            self.status_label.setText("❌ Cannot access FPR for update.")
            return

        original_subject = fpr.getSubject(self.full_path.name)
        saved_scope = _config.get("Species", "correction_scope", fallback="All files with same name error in day folder")

        # Determine scope
        scope_options = getCorrectionScopeOptions(self.full_path)  # Assumes this method returns a dict like {label: [Path, Path, ...]}
        selected_files = scope_options.get(saved_scope, [])

        # Filter files that match the original subject; always include the clicked file itself
        matching_files = [f for f in selected_files if fpr.getSubject(f.name) == original_subject]
        if self.full_path not in matching_files:
            matching_files = [self.full_path] + matching_files

        # Apply updates
        self.status_label.setText(f"📦 Committing species name: {format_subject_for_xmp(corrected_subject)} to {len(matching_files)} file{'s' if len(matching_files) > 1 else ''}")
        logger.info(f"📦 Committing species name: {format_subject_for_xmp(corrected_subject)} to {len(matching_files)} file{'s' if len(matching_files) > 1 else ''}")
        updated_upns = set()
        for f in matching_files:
            upn_object, files_written = fpr.updateSubject(f.name, original_subject, corrected_subject)
            if files_written:
                logger.info(
                    f"""✅ Updated {f.name}: rewrote {files_written} file{'s' if files_written != 1 else ''} on disk, """
                    f'''subject "{format_subject_for_xmp(original_subject)}" → "{format_subject_for_xmp(corrected_subject)}".'''
                )
            else:
                logger.warning(
                    f"""⚠️ {f.name}: no XMP files rewritten (on-disk subject did not match CSV); CSV cache updated to "{format_subject_for_xmp(corrected_subject)}"."""
                )
            upn_name = fpr.fi.files_dict.get(f.name, {}).get("UniquePictureName")
            if upn_name:
                updated_upns.add(upn_name)

        # Refresh all visible images that share a UPN with any updated file (catches DxO JPGs etc.)
        if updated_upns:
            for image_label, container, img_path in image_viewer.image_widgets:
                file_upn = fpr.fi.files_dict.get(img_path.name, {}).get("UniquePictureName")
                if file_upn in updated_upns:
                    image_viewer.refresh_image_status(container, img_path)

        self.accept()


    def closeEvent(self, event):
        print("🧨 SpeciesInfoDialog is being closed!")
        event.accept()

    def __del__(self):
        print("🧹 SpeciesInfoDialog was garbage collected")
