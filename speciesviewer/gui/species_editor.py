import os
import logging
import difflib
import ast
import weakref

from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout, QSpinBox, QPushButton,
    QSizePolicy, QTextEdit, QComboBox, QDialogButtonBox,
    QLabel, QMessageBox, QSlider, QHBoxLayout, QProgressDialog)
from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal

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


def get_best_species_suggestions(input_name, fpr, max_suggestions=5):
    all_types = ["Scientific", "English", "Dutch"]
    best_match = []
    best_score = 0

    for name_type in all_types:
        suggestions = get_species_suggestions(input_name, name_type, fpr, max_suggestions)
        if suggestions and suggestions[0][1] > best_score:
            best_match = suggestions
            best_score = suggestions[0][1]

    return best_match


def get_species_candidates(name_type, fpr):
    sc_dict = fpr.sc.sc_dict

    if name_type == "Dutch":
        return [name for name in fpr.sc.dutch_dict.keys()
                if sc_dict.get(fpr.sc.dutch_dict[name], {}).get("Level") == Level.SPECIES]
    elif name_type == "English":
        return [name for name in fpr.sc.english_dict.keys()
                if sc_dict.get(fpr.sc.english_dict[name], {}).get("Level") == Level.SPECIES]
    else:
        return [name for name, props in sc_dict.items()
                if props.get("Level") == Level.SPECIES]


def get_species_suggestions(input_name, name_type, fpr, max_suggestions=5):
    candidates = get_species_candidates(name_type, fpr)
    matches = difflib.get_close_matches(input_name.lower(), candidates, n=max_suggestions, cutoff=0.6)

    scored_matches = []
    for match in matches:
        score = difflib.SequenceMatcher(None, input_name.lower(), match.lower()).ratio()
        scored_matches.append((match, round(score * 100, 1)))

    return scored_matches


# ── Embeddable panel ────────────────────────────────────────────────────────────

class SpeciesInfoPanel(QWidget):
    """Species info + commit widget — embeds in a dialog or the right-panel stack.

    For a single picture: shows filename / species / error and the legacy
    scope-based batch commit (finds all files in the folder with the same
    erroneous name).  For N > 1 pictures: shows "N selected" header and commits
    to exactly the provided file_paths — the explicit multi-select replaces the
    scope scan.

    Emits ``committed(upns)`` after a successful write.  The dialog wrapper
    connects that signal to its own accept() + refresh call.
    """

    committed = pyqtSignal(set)   # set of updated UPN keys

    def __init__(self, fpr, file_paths: list, species_names: list, error_msgs: list, *,
                 parent=None, collapsible_error: bool = False):
        """
        collapsible_error  When True the error-message text is hidden behind a
                           ⚠ toggle button — useful when the panel is embedded
                           in a narrow sidebar rather than a standalone dialog.
        """
        super().__init__(parent)
        self._fpr = fpr
        self._file_paths = list(file_paths)   # list[Path]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ── Header ───────────────────────────────────────────────────────────
        n = len(file_paths)
        if n == 1:
            layout.addWidget(QLabel(f"<b>Filename:</b> {file_paths[0].name}"))
            layout.addWidget(QLabel(f"<b>Species:</b> {species_names[0] or '(unknown)'}"))
            if error_msgs[0]:
                if collapsible_error:
                    # Compact toggle: ⚠ button reveals the error text on demand.
                    err_btn = QPushButton("⚠  Show error  ▸")
                    err_btn.setCheckable(True)
                    err_btn.setFlat(True)
                    err_btn.setStyleSheet(
                        "QPushButton { color: #c0392b; "
                        "text-align: left; padding: 2px 0; border: none; }"
                        "QPushButton:checked { font-weight: bold; }")
                    err_body = QTextEdit()
                    err_body.setReadOnly(True)
                    err_body.setMaximumHeight(80)
                    err_body.setPlainText(error_msgs[0])
                    err_body.setVisible(False)
                    def _toggle(checked, w=err_body, b=err_btn):
                        w.setVisible(checked)
                        b.setText("⚠  Hide error  ▾" if checked else "⚠  Show error  ▸")
                    err_btn.toggled.connect(_toggle)
                    layout.addWidget(err_btn)
                    layout.addWidget(err_body)
                else:
                    layout.addWidget(QLabel("<b>Error Message:</b>"))
                    error_text = QTextEdit()
                    error_text.setReadOnly(True)
                    error_text.setMaximumHeight(70)
                    error_text.setPlainText(error_msgs[0])
                    layout.addWidget(error_text)
        else:
            preview = ", ".join(p.name for p in file_paths[:4])
            if n > 4:
                preview += f"  … (+{n - 4} more)"
            layout.addWidget(QLabel(f"<b>{n} pictures selected:</b> {preview}"))

        # ── Subject field ─────────────────────────────────────────────────────
        if n == 1:
            initial = fpr.getSubject(file_paths[0].name) if fpr else ""
        else:
            initial = ""
        self.subject_field = LabeledTextFieldQt("Suggested species name:", value=initial)
        layout.addWidget(self.subject_field)

        # ── Suggestion dropdown ───────────────────────────────────────────────
        layout.addWidget(QLabel("Suggestions"))
        self.suggestion_dropdown = QComboBox()
        self.suggestion_dropdown.setEditable(False)
        self.suggestion_dropdown.setVisible(False)
        self.suggestion_dropdown.setToolTip("Select a species name to apply manually")
        self.suggestion_dropdown.currentIndexChanged.connect(self._on_suggestion_selected)
        layout.addWidget(self.suggestion_dropdown)

        # ── Status ────────────────────────────────────────────────────────────
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        # ── Buttons ───────────────────────────────────────────────────────────
        button_row = QHBoxLayout()
        suggest_btn = QPushButton("Suggest")
        suggest_btn.setToolTip("Find closest matching species names")
        suggest_btn.clicked.connect(self._suggest_species)
        button_row.addWidget(suggest_btn)

        validate_btn = QPushButton("Validate")
        validate_btn.clicked.connect(self._on_validate_clicked)
        button_row.addWidget(validate_btn)

        commit_btn = QPushButton("Commit")
        commit_btn.clicked.connect(self._on_commit_clicked)
        button_row.addWidget(commit_btn)

        layout.addLayout(button_row)

    # ── Suggest ───────────────────────────────────────────────────────────────

    def _suggest_species(self):
        raw_input = self.subject_field.get_text().strip()
        if not raw_input:
            self.status_label.setText("⚠️ No input for suggestion.")
            self.suggestion_dropdown.setVisible(False)
            return

        fpr = self._fpr
        if fpr is None:
            self.status_label.setText("⚠️ Species data not loaded; cannot suggest.")
            self.suggestion_dropdown.setVisible(False)
            return

        name_type = _config.get("Species", "name_type", fallback="Auto")

        try:
            parts = ast.literal_eval(raw_input)
            if not isinstance(parts, list):
                raise ValueError
        except Exception:
            parts = [raw_input]

        input_name = max(parts, key=len)

        if name_type == "Auto":
            suggestions = get_best_species_suggestions(input_name, fpr)
        else:
            suggestions = get_species_suggestions(input_name, name_type, fpr)

        if suggestions:
            best_name, _ = suggestions[0]
            updated_parts = [best_name if part == input_name else part for part in parts]
            updated_subject = "[" + ", ".join(f"'{p}'" for p in updated_parts) + "]"
            self.subject_field.set_text(updated_subject)
            self.status_label.setText(f"🔍 Suggested: {best_name}")
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

    def _on_suggestion_selected(self, index):
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

    # ── Validate ──────────────────────────────────────────────────────────────

    def _on_validate_clicked(self):
        raw_input = self.subject_field.get_text().strip()
        if not raw_input:
            self.status_label.setText("⚠️ Please enter a species name.")
            return
        fpr = self._fpr
        try:
            try:
                parts = ast.literal_eval(raw_input)
                if not isinstance(parts, list):
                    raise ValueError
            except Exception:
                parts = [raw_input]
            input_name = max(parts, key=len)
            validated = fpr.sc.getSpecies(input_name, fuzzy=True) if fpr else ""
            if validated:
                self.status_label.setText(f"✅ Validated: {input_name} ({validated})")
                logger.info(f"Validated species name: {input_name} → {validated}")
            else:
                self.status_label.setText(f"❌ Unknown species: {input_name}")
                logger.info(f"❌ Unknown species: {input_name}")
        except Exception as e:
            self.status_label.setText(f"⚠️ Validation error: {e}")
            logger.info(f"⚠️ Validation error: {e}")

    # ── Commit ────────────────────────────────────────────────────────────────

    def _safe_status(self, text):
        # The per-file processEvents() can rebuild/close this panel, deleting the
        # label; setting it then raises "wrapped C/C++ object deleted".
        try:
            self.status_label.setText(text)
        except RuntimeError:
            pass

    def _on_commit_clicked(self):
        # Re-entrancy guard: the per-file QApplication.processEvents() below can
        # deliver a queued SECOND Commit click, which ran a nested commit and
        # deleted this panel's status_label mid-loop (RuntimeError crash).
        if getattr(self, "_committing", False):
            return
        self._committing = True
        try:
            self._do_commit()
        finally:
            self._committing = False

    def _do_commit(self):
        corrected_subject = self.subject_field.get_text().strip()
        fpr = self._fpr
        if not fpr:
            self._safe_status("❌ Cannot access FPR for update.")
            return

        def _is_empty(s):
            return (not s) or (isinstance(s, str) and s.strip() in ("", "[]"))

        clearing = (corrected_subject == "")

        # Unknown-species guard (skip for clearing)
        if not clearing:
            try:
                _parts = ast.literal_eval(corrected_subject)
                _lookup = max(_parts, key=len) if isinstance(_parts, list) and _parts else corrected_subject
            except Exception:
                _lookup = corrected_subject
            if not fpr.sc.getSpecies(_lookup):
                reply = QMessageBox.question(
                    self, "Unknown species",
                    f"'{_lookup}' is not a known species (not in your list and not "
                    f"found in GBIF).\n\nStore it anyway as a placeholder or a new species?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    self.status_label.setText("Commit cancelled — name not stored.")
                    logger.info(f"Commit cancelled: '{_lookup}' not stored (unknown species).")
                    return

        # Build the list of files to update
        if len(self._file_paths) == 1:
            # Single-file: keep the legacy scope-based batch (fixes all files
            # in the folder that share the same erroneous name).
            full_path = self._file_paths[0]
            original_subject = fpr.getSubject(full_path.name)
            if clearing:
                if _is_empty(original_subject):
                    self.status_label.setText("⚠️ This picture has no species to clear.")
                    return
                reply = QMessageBox.question(
                    self, "Clear species name",
                    f"Remove the species name from this picture only?\n\n{full_path.name}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    self.status_label.setText("Clear cancelled.")
                    return
                matching_files = [full_path]
            else:
                if _is_empty(original_subject):
                    matching_files = [full_path]
                else:
                    saved_scope = _config.get(
                        "Species", "correction_scope",
                        fallback="All files with same name error in day folder")
                    selected_files = getCorrectionScopeOptions(full_path).get(saved_scope, [])
                    matching_files = [f for f in selected_files
                                      if fpr.getSubject(f.name) == original_subject]
                    if full_path not in matching_files:
                        matching_files = [full_path] + matching_files
        else:
            # Multi-file: commit to exactly the selected pictures.
            if clearing:
                reply = QMessageBox.question(
                    self, "Clear species names",
                    f"Remove the species name from all {len(self._file_paths)} selected pictures?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    self.status_label.setText("Clear cancelled.")
                    return
            matching_files = self._file_paths

        # Apply
        n = len(matching_files)
        action = ("Clearing species from" if clearing
                  else f"Committing {format_subject_for_xmp(corrected_subject)} to")
        logger.info(f"📦 {action} {n} file{'s' if n != 1 else ''}")

        updated_upns: set[str] = set()
        for i, f in enumerate(matching_files):
            self._safe_status(f"📦 {action} … {i + 1} of {n}")
            QApplication.processEvents()
            orig = fpr.getSubject(f.name)
            upn_object, files_written = fpr.updateSubject(f.name, orig, corrected_subject)
            if files_written:
                logger.info(f"✅ Updated {f.name}: {files_written} file(s) on disk.")
            else:
                logger.warning(f"⚠️ {f.name}: no XMP files rewritten (CSV cache updated).")
            upn_name = fpr.fi.files_dict.get(f.name, {}).get("UniquePictureName")
            if upn_name:
                updated_upns.add(upn_name)

        self._safe_status(f"✅ Done — {n} file{'s' if n != 1 else ''} updated.")
        QApplication.processEvents()
        self.committed.emit(updated_upns)


# ── Dialog wrapper (unchanged call-site API) ────────────────────────────────────

class SpeciesInfoDialog(QDialog):
    """Thin wrapper around SpeciesInfoPanel for the single-picture popup."""

    def __init__(self, full_path, species_name, error_msg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Species Info")
        self.setMinimumWidth(600)

        image_viewer = parent
        fpr = getattr(image_viewer, "fpr", None)

        layout = QVBoxLayout(self)

        self._panel = SpeciesInfoPanel(
            fpr,
            file_paths=[full_path],
            species_names=[species_name or ""],
            error_msgs=[error_msg or ""],
            parent=self,
        )
        self._panel.committed.connect(lambda upns: self._on_committed(upns))
        layout.addWidget(self._panel)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._image_viewer = image_viewer

    def _on_committed(self, upns: set):
        if self._image_viewer is not None and hasattr(self._image_viewer, "refresh_after_species_change"):
            self._image_viewer.refresh_after_species_change(upns)
        self.accept()
