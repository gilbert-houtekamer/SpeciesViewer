import os
import logging
import difflib
import ast
import weakref

from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout, QSpinBox, QPushButton,
    QSizePolicy, QTextEdit, QComboBox, QDialogButtonBox, QCompleter, QCheckBox,
    QLabel, QMessageBox, QSlider, QHBoxLayout, QProgressDialog, QFrame, QMenu)
from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal
from PyQt6.QtGui import QCursor

from widgets.trip_widgets import LabeledTextFieldQt
from engine.filepicture import FilePicture, format_subject_for_xmp
from engine.speciesclassification import Level, SpeciesClassification
from utils.config import _config, get_setting, set_setting
from gui import theme
from utils.raw_extensions import RAW_OR_HEIC

logger = logging.getLogger("speciesviewer")

# Biota vocabulary — mono utility face for the small uppercase micro-labels, so
# the embedded editor reads as one system with the restyled large-viewer panel.
_MONO = "'Cascadia Mono','Cascadia Code',Consolas,monospace"
_MICRO = (f"font-family:{_MONO}; font-size:9.5px; font-weight:600;"
          f" letter-spacing:1.2px; color:{theme.INK_SOFT};")
_RECENT_CHIP = (
    f"QPushButton {{ font-size:11.5px; color:{theme.INK}; background:{theme.SURFACE};"
    f" border:1px solid {theme.LINE}; border-radius:12px; padding:3px 11px; text-align:left; }}"
    f"QPushButton:hover {{ border-color:{theme.ACCENT}; color:{theme.ACCENT}; }}")


class DialogContext:
    def __init__(self, fpr):
        self.fpr = fpr


def is_widget_alive(widget):
    try:
        _ = widget.objectName()
        return True
    except RuntimeError:
        return False


raw_exts = RAW_OR_HEIC  # canonical table — see utils/raw_extensions.py


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


def _trip_species_candidates() -> list[str]:
    """Every distinct NL/EN/Sci species name already recorded on THIS trip — a
    much smaller, more relevant set than the full species reference collection
    (species_name_candidates / fpr.sc, ~30k entries) once a trip has been
    running a while. Backs the "look in trip names" completer toggle.

    Reads the trip's species TREES (metadata/active + every metadata/review/
    <day>), not fpr.upn: FilePicture.readMaster loads the trip-wide picture
    inventory, but the image_viewer always follows it with readTripDay, which
    overwrites fpr.upn with just the ONE day currently open. Using fpr.upn
    here silently narrowed "look in trip names" down to a single day — it
    found a same-day-earlier species but nothing from any other day, worst
    for exactly the case (going back to an early day, deep into a trip) where
    the wide list matters most."""
    from gui.culling_species import trip_species_names
    return trip_species_names()


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
            # When embedded in a host panel (the large viewer) the filename +
            # current species are already shown above, so skip these redundant
            # lines; the standalone popup keeps them for context.
            if not collapsible_error:
                layout.addWidget(QLabel(f"<b>Filename:</b> {file_paths[0].name}"))
                # Species with its qualifier abbreviations ("fuut ♀ br.").
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
        _preset_summary = ""
        if n == 1:
            initial = fpr.getSubject(file_paths[0].name) if fpr else ""
        else:
            # Same as the grid / culling dialog: if one distinct species is
            # already on the selection (a guide frame naming a burst), offer it
            # here — flagged "★ … — set on N of M" when not every picture has it.
            from gui.culling_species import species_prefill_for_keys
            initial, _preset_summary, _ = species_prefill_for_keys(
                range(n), lambda i: species_names[i] if i < len(species_names) else "")
        # Keep only the species name in the field; any qualifier already stored
        # (juv / male / …) pre-selects the toggle row below.
        name_only, self._preset_qualifiers = _subject_name_and_quals(initial)
        # Kept so the qualifier rows can be filtered by TAXON even when the field is
        # empty: the picture may already have a species the field is not showing.
        self._initial_species_name = name_only
        self.subject_field = LabeledTextFieldQt("Suggested species name:", value=name_only)
        # "look in trip names" rides the same row as the field — the field
        # still gets the lion's share of the width (stretch factor 1), the
        # checkbox just its own compact size. Narrows the as-you-type
        # suggestions to species already recorded on THIS trip instead of the
        # full ~30k reference collection — most useful well into a trip, once
        # most of what you need is already here; a brand-new trip has nothing
        # to narrow to yet, hence off by default. Persists across sessions
        # (utils.config), independent of this one dialog instance.
        field_row = QHBoxLayout()
        # LabeledTextFieldQt's own layout carries Qt's default margins, so its
        # widget extends a few px below its actual field — enough to throw off
        # the bottom-alignment below. Zeroed here rather than in the shared
        # widget class, which other dialogs rely on looking as it does.
        self.subject_field.layout().setContentsMargins(0, 0, 0, 0)
        field_row.addWidget(self.subject_field, 1)
        self._trip_only_check = QCheckBox("look in trip names")
        self._trip_only_check.setToolTip(
            "Only suggest species already recorded on this trip, instead of "
            "the full species reference list — much shorter once the trip "
            "has a while of pictures behind it.")
        self._trip_only_check.setChecked(
            get_setting("Species", "completer_trip_only", fallback="no") == "yes")
        self._trip_only_check.toggled.connect(self._on_trip_only_toggled)
        # subject_field stacks its own label above its field internally, so
        # the row's height is set by that whole block — bottom-align the
        # checkbox so it sits level with the INPUT box, not the "Suggested
        # species name:" caption above it. A few px of trailing space nudges
        # it up further still: the field is drawn inside a bordered box, so
        # its own bottom edge sits below the box's visible border/padding,
        # while the checkbox's flat text has no such padding of its own.
        check_col = QVBoxLayout()
        check_col.setContentsMargins(0, 0, 0, 0)
        check_col.setSpacing(0)
        check_col.addWidget(self._trip_only_check)
        check_col.addSpacing(4)
        field_row.addLayout(check_col)
        field_row.setAlignment(check_col, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(field_row)

        if _preset_summary:
            _psum = QLabel(_preset_summary)
            _psum.setWordWrap(True)
            _psum.setStyleSheet(f"color:{theme.WARN}; font-size:11px;")
            layout.addWidget(_psum)

        # Immediate as-you-type suggestions — a contains-match auto-complete over
        # every NL / EN / scientific species name, mirroring the culling dialog
        # (which felt much quicker than the older click-"Suggest" flow). It only
        # offers names already in the collection; a genuinely new name is still
        # typed freely and confirmed via Validate / GBIF on Commit.
        self._attach_species_completer()

        # ── Suggestion dropdown ───────────────────────────────────────────────
        _sugg_lbl = QLabel("SUGGESTIONS")
        _sugg_lbl.setStyleSheet(_MICRO)
        layout.addWidget(_sugg_lbl)
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
        self._qual_header = QLabel("SEX / AGE / PLUMAGE — OPTIONAL")
        self._qual_header.setStyleSheet(_MICRO)
        layout.addWidget(self._qual_header)
        self._qual_buttons: dict[int, list[tuple[QPushButton, str]]] = {}
        self._qual_rows: dict[int, QWidget] = {}
        self._qual_shown_last = None      # for the debug line: only log when it changes
        for gi, (cat, opts) in enumerate(QUALIFIER_GROUPS):
            # Each row in its own widget so it can be hidden when it does not apply to
            # the taxon (no breeding plumage on an orchid).
            holder = QWidget()
            qrow = QHBoxLayout(holder)
            qrow.setContentsMargins(0, 0, 0, 0)
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
                # Compact so all three fit the narrow embedded right-panel;
                # QUAL_BTN_QSS gives the checked state a solid accent fill so a
                # toggled qualifier is unmistakable (and can be seen to clear).
                from gui.culling_species import QUAL_BTN_QSS
                b.setStyleSheet(QUAL_BTN_QSS)
                b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                b.clicked.connect(
                    lambda _checked, g=gi, bb=b: self._on_qualifier_clicked(g, bb))
                qrow.addWidget(b)
                self._qual_buttons[gi].append((b, canon))
            self._qual_rows[gi] = holder
            layout.addWidget(holder)

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
        self._recent_label = QLabel("RECENT SPECIES — CLICK TO APPLY, RIGHT-CLICK TO REMOVE")
        self._recent_label.setStyleSheet(_MICRO)
        self._recent_label.setWordWrap(True)
        layout.addWidget(self._recent_label)
        self._recent_container = QWidget()
        self._recent_grid = QGridLayout(self._recent_container)
        self._recent_grid.setContentsMargins(0, 0, 0, 0)
        self._recent_grid.setHorizontalSpacing(6)
        self._recent_grid.setVerticalSpacing(4)
        layout.addWidget(self._recent_container)
        self._build_recent_buttons()

        # Re-filter the qualifier rows whenever the name changes (typing, a suggestion,
        # a recent-species click all land in this field), and once now for the picture
        # the panel opened on.
        try:
            # QLineEdit.textChanged carries the text, QTextEdit's carries nothing —
            # accept either, so this cannot break if the field type ever changes.
            self.subject_field.field.textChanged.connect(
                lambda *_: self._update_qualifier_visibility())
        except AttributeError:
            logger.warning("species editor: no field signal — qualifier rows will not "
                           "re-filter as you type.")
        self._update_qualifier_visibility()

    # ── Live auto-complete ──────────────────────────────────────────────────────

    def _attach_species_completer(self):
        """Wire a contains-match auto-complete popup onto the species field so
        suggestions appear on every keystroke — the same behaviour as the culling
        'Set species' dialog. No-op when the species collection isn't loaded on
        this fpr (some hosts leave fpr.sc as the bare {} placeholder); the user
        can still type freely and Commit stores as typed."""
        fpr = self._fpr
        if fpr is None or not _sc_usable(fpr.sc):
            self._completer = None
            return
        try:
            if getattr(self, "_trip_only_check", None) is not None \
                    and self._trip_only_check.isChecked():
                names = _trip_species_candidates()
            else:
                from gui.culling_species import species_name_candidates
                names = species_name_candidates(fpr.sc)
        except Exception as e:
            logger.warning(f"Could not build species auto-complete list: {e}")
            self._completer = None
            return
        completer = QCompleter(names, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        # subject_field.field is a QLineEdit (single-line, not multiline).
        self.subject_field.field.setCompleter(completer)
        self._completer = completer

    def _on_trip_only_toggled(self, checked: bool):
        set_setting("Species", "completer_trip_only", "yes" if checked else "no")
        self._attach_species_completer()

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
                # Show a name you can read, with the scientific one in brackets:
                # "melkslang (lampropeltis triangulum)". Typing the scientific
                # name used to echo it twice — "x (x)" — which said nothing.
                rec = fpr.sc.sc_dict.get(validated, {}) if _sc_usable(fpr.sc) else {}
                common = (rec.get("DutchName") or rec.get("EnglishName") or "").strip()
                shown = f"{common} ({validated})" if common and common != validated else validated
                self.status_label.setText(f"✅ Validated: {shown}")
                logger.info(f"Validated species name: {input_name} → {validated}")
                # Validate is what TEACHES us the taxon: GBIF adds the species to the
                # collection with its whole lineage. Until it runs, a brand-new orchid is
                # just an unknown string and the picker has no honest reason to hide
                # anything. Re-filter now that we finally know what it is.
                self._update_qualifier_visibility()
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

    # ── qualifier visibility (taxon-aware) ────────────────────────────────────

    def _resolve_sci_local(self) -> str:
        """The scientific name the field currently resolves to — LOCAL LOOKUP ONLY.

        NOT sc.getSpecies(), which falls back to a GBIF network call: this runs on every
        keystroke."""
        fpr = self._fpr
        if fpr is None or not _sc_usable(fpr.sc):
            return ""
        text = self.subject_field.get_text().strip()
        if not text:
            text = (self._initial_species_name or "").strip()
        if not text:
            return ""
        try:
            resolved = fpr.sc.getKnownSpecies(text.lower(), False, LookUpInGBIF=False)
        except Exception:
            resolved = ""
        # On a miss, hand back the TYPED name, not "". taxon_lineage can still reach the
        # taxon through a congener ('anemone hortensis ssp heldreichii' is unknown, but
        # 'anemone blanda' is in the table, and two anemones are not in different
        # kingdoms). Returning "" threw that away and was why an orchid and an anemone
        # were still being offered breeding plumage.
        return (resolved or text).lower()

    def _update_qualifier_visibility(self):
        """Offer only the qualifiers that mean something for whatever the field resolves
        to. A row that does NOT apply but ALREADY HAS a value stays visible — the picture
        carries that token whether we approve of it or not, and hiding the row would drop
        it silently on the next Commit."""
        from gui.culling_species import (groups_for_lineage, taxon_lineage,
                                         QUALIFIER_GROUPS)
        sci = self._resolve_sci_local()
        fpr = self._fpr
        sc = fpr.sc if (fpr is not None and _sc_usable(fpr.sc)) else None
        lin = taxon_lineage(sc, sci) if sc else {}
        applicable = groups_for_lineage(lin)

        any_shown = False
        for gi, holder in self._qual_rows.items():
            has_value = any(b.isChecked() for b, _c in self._qual_buttons[gi])
            show = (gi in applicable) or has_value
            holder.setVisible(show)
            any_shown |= show
        self._qual_header.setVisible(any_shown)

        shown = tuple(sorted(QUALIFIER_GROUPS[gi][0] for gi, h in self._qual_rows.items()
                             if h.isVisible()))
        if shown != self._qual_shown_last:
            self._qual_shown_last = shown
            logger.info(
                f"[qualifiers] {self.subject_field.get_text().strip()!r} -> "
                f"species={sci or '(unresolved)'} "
                f"kingdom={lin.get('kingdom', '?')} phylum={lin.get('phylum', '?')} "
                f"-> showing {list(shown) or 'none (not applicable to this taxon)'}")

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

        # 'landscape' / 'landschap' is a DELIBERATE no-species marker (a blog
        # extra: a landscape / context photo titled by its Headline, not a taxon).
        # It must not trip the unknown-species warning — storing it is the whole point.
        is_landscape = SpeciesClassification.isBlogExtra(corrected_subject)

        # Unknown-species guard (skip for clearing, for the landscape marker, and
        # when the species collection isn't loaded on this fpr — then store as typed).
        if not clearing and not is_landscape and _sc_usable(fpr.sc):
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

        # A commit is a decision, not a keystroke: if this named a species the
        # collection did not know, save it NOW rather than at viewer close.
        # Otherwise a pipeline step run while the viewer is still open reads a
        # collection that does not have it yet and looks it up in GBIF again —
        # which is not merely slow: two lookups can pick two different
        # vernacular names, leaving the trip and the master disagreeing.
        if getattr(fpr, "sc", None) is not None and getattr(fpr.sc, "learned", None):
            try:
                from gui.culling_species import flush_learned_species_from
                flush_learned_species_from((fpr.sc,), parent=self)
            except Exception as e:
                logger.warning(f"Saving learned species failed: {e}")

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
            b.setStyleSheet(_RECENT_CHIP)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
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
