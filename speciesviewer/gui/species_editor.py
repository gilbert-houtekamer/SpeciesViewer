import os
import logging
import difflib
import ast
import weakref

from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout, QSpinBox, QPushButton,
    QSizePolicy, QTextEdit, QComboBox, QDialogButtonBox,
    QLabel, QMessageBox, QSlider, QHBoxLayout, QProgressDialog, QFrame, QMenu)
from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal
from PyQt6.QtGui import QCursor

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


def _subject_name_and_quals(raw):
    """Split any stored subject — a list-repr ("['fuut', 'juv']"), a scalar
    compound ("fuut, juv") or a plain name — into (species_name, {group: canon})
    using the shared qualifier vocabulary. Lets the panel keep the species name
    in the field and the qualifiers in the toggle row."""
    from gui.culling_species import split_species_qualifiers
    s = (raw or "").strip()
    try:
        parts = ast.literal_eval(s)
        if isinstance(parts, list):
            s = ", ".join(str(p).strip("'\"") for p in parts)
    except Exception:
        pass
    return split_species_qualifiers(s)


def _sc_usable(sc) -> bool:
    """True when fpr.sc is a real SpeciesClassification (has the lookup API),
    not the empty ``{}`` placeholder a bare FilePicture starts with. Some hosts
    (e.g. the Rating Editor's day/trip load) don't populate the collection, so
    Suggest/Validate/the unknown-species guard must degrade instead of crashing
    on ``dict.getSpecies`` / ``dict.sc_dict``."""
    return hasattr(sc, "getSpecies") and hasattr(sc, "sc_dict")


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
                 parent=None, collapsible_error: bool = False,
                 scope_correction: bool = False):
        """
        collapsible_error  When True the error-message text is hidden behind a
                           ⚠ toggle button — useful when the panel is embedded
                           in a narrow sidebar rather than a standalone dialog.
        scope_correction   When True a single-file commit fans out to every file
                           in the configured correction scope that shares the same
                           (erroneous) species name — the legacy "fix this typo
                           everywhere" behaviour of the ⚠ error popup. When False
                           (the default) a commit touches EXACTLY the selected
                           file(s); an explicit one-picture selection updates only
                           that picture.
        """
        super().__init__(parent)
        self._fpr = fpr
        self._file_paths = list(file_paths)   # list[Path]
        self._scope_correction = scope_correction

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ── Header ───────────────────────────────────────────────────────────
        n = len(file_paths)
        if n == 1:
            layout.addWidget(QLabel(f"<b>Filename:</b> {file_paths[0].name}"))
            # Show the species with its qualifier abbreviations ("fuut ♀ br.").
            from gui.culling_species import qualifier_suffix_from_subject
            _suffix = qualifier_suffix_from_subject(fpr.getSubject(file_paths[0].name)) if fpr else ""
            layout.addWidget(QLabel(f"<b>Species:</b> {(species_names[0] or '(unknown)')}{_suffix}"))
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
        # Keep only the species name in the field; any qualifier already stored
        # (juv / male / …) pre-selects the toggle row below.
        name_only, self._preset_qualifiers = _subject_name_and_quals(initial)
        self.subject_field = LabeledTextFieldQt("Suggested species name:", value=name_only)
        layout.addWidget(self.subject_field)

        # ── Suggestion dropdown ───────────────────────────────────────────────
        layout.addWidget(QLabel("Suggestions"))
        self.suggestion_dropdown = QComboBox()
        self.suggestion_dropdown.setEditable(False)
        self.suggestion_dropdown.setVisible(False)
        self.suggestion_dropdown.setToolTip("Select a species name to apply manually")
        self.suggestion_dropdown.currentIndexChanged.connect(self._on_suggestion_selected)
        layout.addWidget(self.suggestion_dropdown)

        # ── Qualifiers (sex / age / plumage) ──────────────────────────────────
        # Stored in the keyword as "<species>, <token>"; the pipeline reads them
        # to caption the picture and to show one best photo per age/sex/plumage.
        # Single-select within each row; click again to clear.
        from gui.culling_species import QUALIFIER_GROUPS
        layout.addWidget(QLabel("Optional — sex / age / plumage:"))
        self._qual_buttons: dict[int, list[tuple[QPushButton, str]]] = {}
        for gi, (cat, opts) in enumerate(QUALIFIER_GROUPS):
            qrow = QHBoxLayout()
            qrow.setSpacing(4)
            lbl = QLabel(cat + ":")
            lbl.setMinimumWidth(56)
            qrow.addWidget(lbl)
            self._qual_buttons[gi] = []
            preset = self._preset_qualifiers.get(gi)
            for label, canon, _aliases in opts:
                b = QPushButton(label)
                b.setCheckable(True)
                b.setChecked(canon == preset)
                # Compact so all three fit the narrow embedded right-panel.
                b.setStyleSheet("QPushButton { padding: 2px 6px; }")
                b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                b.clicked.connect(
                    lambda _checked, g=gi, bb=b: self._on_qualifier_clicked(g, bb))
                qrow.addWidget(b)
                self._qual_buttons[gi].append((b, canon))
            layout.addLayout(qrow)

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

        # ── Recent species quick-pick ─────────────────────────────────────────
        # Shared LRU with the culling dialog: left-click applies a name to the
        # selected picture(s) in one go, right-click removes it from the list.
        self._recent_sep = QFrame()
        self._recent_sep.setFrameShape(QFrame.Shape.HLine)
        self._recent_sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(self._recent_sep)
        self._recent_label = QLabel("Recent species — click to apply, right-click to remove:")
        self._recent_label.setWordWrap(True)
        layout.addWidget(self._recent_label)
        self._recent_container = QWidget()
        self._recent_grid = QGridLayout(self._recent_container)
        self._recent_grid.setContentsMargins(0, 0, 0, 0)
        self._recent_grid.setHorizontalSpacing(6)
        self._recent_grid.setVerticalSpacing(4)
        layout.addWidget(self._recent_container)
        self._build_recent_buttons()

    # ── Suggest ───────────────────────────────────────────────────────────────

    def _suggest_species(self):
        raw_input = self.subject_field.get_text().strip()
        if not raw_input:
            self.status_label.setText("⚠️ No input for suggestion.")
            self.suggestion_dropdown.setVisible(False)
            return

        fpr = self._fpr
        if fpr is None or not _sc_usable(fpr.sc):
            self.status_label.setText("⚠️ Species list not loaded here; type the name and Commit.")
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
        if fpr is None or not _sc_usable(fpr.sc):
            self.status_label.setText("⚠️ Species list not loaded here; can't validate — Commit stores as typed.")
            return
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

    def _on_qualifier_clicked(self, group_index: int, clicked_btn):
        """Single-select within a group: turning one on turns its siblings off.
        A second click leaves it off — clears the group."""
        for b, _canon in self._qual_buttons[group_index]:
            if b is not clicked_btn:
                b.setChecked(False)

    def _selected_qualifiers(self) -> dict:
        """{group index: canonical token} for every currently-checked qualifier."""
        sel = {}
        for gi, btns in self._qual_buttons.items():
            for b, canon in btns:
                if b.isChecked():
                    sel[gi] = canon
                    break
        return sel

    def _compose_subject(self) -> str:
        """The value to write: the field's species name + the picked qualifiers,
        as a scalar compound ("fuut, juvenile"). Scalar (not a list) so the
        pipeline resolves the species by comma-split, independent of token length
        — 'juvenile' being longer than 'fuut' must not hijack the species name."""
        from gui.culling_species import build_species_subject
        name, field_quals = _subject_name_and_quals(self.subject_field.get_text())
        selected = dict(field_quals)
        selected.update(self._selected_qualifiers())   # toggles win over field text
        return build_species_subject(name, selected)

    def _do_commit(self):
        corrected_subject = self._compose_subject()
        fpr = self._fpr
        if not fpr:
            self._safe_status("❌ Cannot access FPR for update.")
            return

        def _is_empty(s):
            return (not s) or (isinstance(s, str) and s.strip() in ("", "[]"))

        clearing = (corrected_subject == "")

        # Unknown-species guard (skip for clearing, and skip when the species
        # collection isn't loaded on this fpr — then we just store as typed).
        if not clearing and _sc_usable(fpr.sc):
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
        if self._scope_correction and len(self._file_paths) == 1 and not clearing:
            # Legacy scope-based batch — ONLY the ⚠ error-correction popup opts in.
            # Fixes all files in the configured scope that share the same erroneous
            # name. NB: an explicit one-picture selection does NOT reach this branch,
            # so it updates only that picture (see scope_correction docstring).
            full_path = self._file_paths[0]
            original_subject = fpr.getSubject(full_path.name)
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
            # Default: commit to EXACTLY the selected picture(s).
            if clearing:
                n = len(self._file_paths)
                if n == 1:
                    if _is_empty(fpr.getSubject(self._file_paths[0].name)):
                        self.status_label.setText("⚠️ This picture has no species to clear.")
                        return
                    title = "Clear species name"
                    prompt = (f"Remove the species name from this picture only?"
                              f"\n\n{self._file_paths[0].name}")
                else:
                    title = "Clear species names"
                    prompt = f"Remove the species name from all {n} selected pictures?"
                reply = QMessageBox.question(
                    self, title, prompt,
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

        # Batched write: the whole selection gets the same subject in two
        # exiftool calls (one guard-read, one write) + one CSV/summary rewrite,
        # instead of read+write+CSV+summary per file (was ~0.6s/file → 23s@37).
        self._safe_status(f"📦 {action} {n} file{'s' if n != 1 else ''}…")
        QApplication.processEvents()
        updated_upns, files_written = fpr.updateSubjects(matching_files, corrected_subject)
        logger.info(f"✅ {files_written} file(s) rewritten on disk across "
                    f"{len(updated_upns)} picture(s).")

        self._safe_status(f"✅ Done — {n} file{'s' if n != 1 else ''} updated.")
        QApplication.processEvents()

        # Bump the committed SPECIES (without qualifiers) to the front of the
        # shared recents — so qualifier tokens like "juv" never leak into the
        # quick-pick list shared with the culling dialog.
        if not clearing:
            _name, _ = _subject_name_and_quals(corrected_subject)
            if _name.strip():
                from gui.culling_species import remember_recent_species
                remember_recent_species(_name.strip())

        self.committed.emit(updated_upns)

    # ── Recent species quick-pick ───────────────────────────────────────────────

    def _build_recent_buttons(self):
        """(Re)fill the recent-species grid from the shared persisted list. Two
        columns, most-recent first; hidden entirely when there are no recents."""
        from gui.culling_species import load_recent_species
        while self._recent_grid.count():
            item = self._recent_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        recent = load_recent_species()
        for widget in (self._recent_sep, self._recent_label, self._recent_container):
            widget.setVisible(bool(recent))
        for i, name in enumerate(recent):
            b = QPushButton(name)
            b.setToolTip(f"Click to set “{name}”; right-click to remove from recents")
            b.clicked.connect(lambda _=False, nm=name: self._apply_recent(nm))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda _pos, nm=name: self._remove_recent(nm))
            self._recent_grid.addWidget(b, i // 2, i % 2)

    def _remove_recent(self, name: str):
        """Right-click a recent button → forget that name (e.g. a mistyped one)."""
        from gui.culling_species import forget_recent_species
        menu = QMenu(self)
        act = menu.addAction(f"Remove “{name}” from recents")
        if menu.exec(QCursor.pos()) == act:
            forget_recent_species(name)
            self._build_recent_buttons()

    def _apply_recent(self, name: str):
        """Left-click a recent button → set it as the species and commit in one go
        (applies to exactly the selected picture(s), like a typed Commit)."""
        self.subject_field.set_text(f"['{name}']")
        self._on_commit_clicked()


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
            scope_correction=True,   # ⚠ popup: fix this name error across the folder
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
