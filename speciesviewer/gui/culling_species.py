"""Smart species assignment for the culling stage.

The pipeline's species-correction panel is fpr-bound (needs the per-day CSVs).
Culling runs before any of that, so this provides a lightweight, fpr-free panel
backed only by the species collection (which loads independently of the trip
CSVs). It accepts a Dutch, English or scientific name with contains-match
auto-complete across all three, shows how the name resolves, and stores the
chosen keyword into XMP:Subject. Placing the species in biological context
(counts, taxonomy) stays the pipeline's job — here a valid NAME is enough.

Regional-list weighting of suggestions is a deliberate later addition.
"""

from __future__ import annotations

import logging
import os

from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QCompleter, QFrame, QWidget, QMenu, QMessageBox)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor

from engine.speciesclassification import SpeciesClassification, Level
from utils.config import _config
from utils.gui_config import get_cache_gui_section, update_cache_gui_section

logger = logging.getLogger("speciesviewer")


def gbif_match(name, timeout=6.0):
    """Look a name up in the GBIF species-match API with a short, explicit
    timeout. Returns one of:
      ("found", canonical_name)  — GBIF recognises it
      ("notfound", "")           — reached GBIF, no match
      ("unreachable", "")        — request failed/timed out (offline, blocked, slow)

    A bare TCP probe is not enough: the connect can succeed while the actual HTTP
    request times out, which previously got mislabelled as 'not in GBIF'."""
    try:
        import requests
        r = requests.get("https://api.gbif.org/v1/species/match",
                         params={"name": name}, timeout=timeout)
        data = r.json()
    except Exception:
        return ("unreachable", "")
    if (data.get("matchType") or "NONE").upper() in ("EXACT", "FUZZY") and data.get("canonicalName"):
        return ("found", data.get("canonicalName") or data.get("scientificName") or name)
    return ("notfound", "")


def load_species_classification():
    """Load a standalone SpeciesClassification from the active trip's (else the
    master's) speciescollection.csv — independent of any fpr / trip CSVs.
    Returns None if no collection file is found."""
    bases = [
        _config.get("trip", "tripbiotadatabase", fallback=""),
        _config.get("master", "masterbiotadatabase", fallback=""),
    ]
    path = next((os.path.join(b, "metadata", "active", "speciescollection.csv")
                 for b in bases
                 if b and os.path.exists(os.path.join(b, "metadata", "active", "speciescollection.csv"))),
                None)
    if not path:
        logger.warning("No speciescollection.csv found (trip or master) for culling species lookup.")
        return None
    sc = SpeciesClassification(None)
    # Fresh per-instance dicts (the class-level ones are shared) so we don't
    # inherit or pollute another load.
    sc.sc_dict, sc.dutch_dict, sc.english_dict, sc.names_not_found_list = {}, {}, {}, []
    try:
        sc.readSpeciesCollection(path)
    except Exception as e:
        logger.warning(f"Could not read species collection {path}: {e}")
        return None
    sc._source_path = path  # so culling can persist newly-added species back
    return sc


def species_name_candidates(sc) -> list[str]:
    """Every species-level name across the three languages, for auto-complete."""
    names = set()
    for sciname, props in sc.sc_dict.items():
        if props.get("Level") != Level.SPECIES:
            continue
        names.add(sciname)
        if props.get("DutchName"):
            names.add(props["DutchName"])
        if props.get("EnglishName"):
            names.add(props["EnglishName"])
    return sorted(names)


# Recently-applied species names, most-recent first, persisted in the GUI cache so
# the common "shot a gull, then a heron, now back to the gull" loop is one click —
# and so it survives an app restart (you often shoot the same species across days).
_RECENT_SECTION = "culling_species"
_RECENT_KEY = "recent_names"
_RECENT_MAX = 20


def load_recent_species() -> list[str]:
    val = get_cache_gui_section(_RECENT_SECTION, _RECENT_KEY)
    return [s for s in val if isinstance(s, str) and s.strip()] if isinstance(val, list) else []


def remember_recent_species(name: str):
    """LRU bump: move `name` to the front of the recents (case-insensitive dedup),
    capped at _RECENT_MAX — so a re-used species rises to the top and the
    least-recently-used drops off the end."""
    name = (name or "").strip()
    if not name:
        return
    recent = [r for r in load_recent_species() if r.lower() != name.lower()]
    recent.insert(0, name)
    update_cache_gui_section(_RECENT_SECTION, _RECENT_KEY, recent[:_RECENT_MAX])


def forget_recent_species(name: str):
    """Drop `name` from the recents (e.g. a wrong/untested name typed by mistake)."""
    name = (name or "").strip()
    if not name:
        return
    recent = [r for r in load_recent_species() if r.lower() != name.lower()]
    update_cache_gui_section(_RECENT_SECTION, _RECENT_KEY, recent[:_RECENT_MAX])


# ── species qualifiers (sex / age / plumage) ──────────────────────────────────
# A qualifier is a token stored alongside the species name in XMP:Subject, e.g.
# "blauwborst, female". The pipeline's specieslib.getBirdInfo is the parser-of-
# record; this table mirrors its vocabulary for the picker and MUST stay in sync
# with it. Each option: (button label, canonical token WRITTEN, {aliases ACCEPTED
# when parsing an existing subject}). One value max per group (they map to the
# single-valued malefemale / ageintext / summerwinter picture fields).
QUALIFIER_GROUPS = [
    ("Sex", [
        ("♂ male",   "male",   {"male", "man"}),
        ("♀ female", "female", {"female", "vrouw"}),
        ("pair",          "couple", {"couple", "paar", "paartje"}),
    ]),
    ("Age", [
        ("juvenile", "juvenile", {"juvenile", "juv", "juv.", "jong"}),
        ("immature", "immature", {"immature", "imm", "imm.", "onvolwassen"}),
        ("adult",    "adult",    {"adult", "ad", "ad.", "volwassen"}),
    ]),
    # Plumage: only "breeding" (br.) is marked — non-breeding is the default, so
    # it needs no token. "summer/winter" were hemisphere-specific (wrong for e.g.
    # Gambia's wet/dry seasons), so they map into "breeding" for back-compat.
    ("Plumage", [
        ("breeding", "breeding", {"breeding", "br.", "br", "summer", "zomer"}),
    ]),
]

# Which qualifier groups actually MEAN anything for a given taxon.
#
# We offered sex / age / plumage on everything, so the picker cheerfully invited
# "breeding plumage" on a scarlet pimpernel. A UI that offers a nonsense value is not
# neutral — it is a suggestion, and sooner or later someone takes it.
#
#   Plumage -> Chordata. Feathers and breeding coats. Nothing else has one.
#   Age     -> Chordata. juvenile/immature/adult are VERTEBRATE life stages. An insect
#              has instars and a larva/pupa/imago; a plant has seedling/rosette/flowering.
#              Those are real vocabularies and they are NOT these words, so offering these
#              words would just be wrong in a way that looks right.
#   Sex     -> Animalia. Many plants do have sexes, but the useful photographic
#              distinction is not male/female-the-individual, and "pair" is meaningless
#              for them. Skip it for plants until there is a vocabulary worth having.
#
# An UNKNOWN taxon shows everything: we hide what we know is wrong, never what we merely
# cannot resolve. More life-cycle vocabularies (instar, larva, …) can be added over time.
_GROUP_SEX, _GROUP_AGE, _GROUP_PLUMAGE = 0, 1, 2
_GROUP_REQUIRES = {
    _GROUP_SEX:     ("kingdom", "animalia"),
    _GROUP_AGE:     ("phylum",  "chordata"),
    _GROUP_PLUMAGE: ("phylum",  "chordata"),
}


def taxon_lineage(sc, sciname: str) -> dict:
    """{'kingdom': 'animalia', 'phylum': 'chordata', 'class': 'aves', …} by walking Up
    from the species to the root. {} when the name is not in the collection."""
    if not sc or not sciname:
        return {}
    out: dict[str, str] = {}
    cur = sciname
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        rec = sc.sc_dict.get(cur)
        if rec is None:
            break
        lvl = str(rec.get("Level", "")).rsplit(".", 1)[-1].lower()   # Level.PHYLUM -> phylum
        if lvl and lvl not in out:
            out[lvl] = str(rec.get("ScientificName", "") or "").lower()
        cur = rec.get("Up", "")
    return out


def groups_for_lineage(lin: dict) -> set:
    """The indices of QUALIFIER_GROUPS that make sense for this lineage.

    Takes the LINEAGE, not a species name and a lookup table — because the two viewers
    resolve a taxon from different places (the culling picker from speciescollection.csv,
    the master from its own species tree) and there must be exactly one copy of the rule
    they both obey. Unknown taxon → all groups: hide what is wrong, never what is merely
    unresolved."""
    if not lin:
        return set(range(len(QUALIFIER_GROUPS)))
    out = set()
    for gi in range(len(QUALIFIER_GROUPS)):
        rank, want = _GROUP_REQUIRES.get(gi, (None, None))
        if rank is None or lin.get(rank, "") == want:
            out.add(gi)
    return out


def applicable_qualifier_groups(sc, sciname: str) -> set:
    """groups_for_lineage, for a caller holding a SpeciesClassification."""
    return groups_for_lineage(taxon_lineage(sc, sciname))


def qualifier_fields(subject: str) -> tuple:
    """(malefemale, ageintext, summerwinter) for a composed subject.

    Delegates to specieslib.getBirdInfo — the PARSER OF RECORD. The picture columns must
    be exactly what a full master re-read would produce, and the one way to guarantee that
    is to run the same code, not a second copy of its vocabulary. (getBirdInfo touches no
    instance state; calling it unbound is deliberate, not a trick.)"""
    from engine.specieslib import Species
    return Species.getBirdInfo(None, subject or "")


class QualifierDialog(QDialog):
    """Sex / age / plumage for a picture whose species is already settled.

    The qualifier is a PROPERTY, not a determination — it exists so the website can show
    the different forms of a species (a juvenile gull beside an adult), which is exactly
    why it belongs on a master picture too. So this dialog deliberately does NOT offer the
    species field: changing what a picture IS and changing which form of it you photographed
    are different acts, and merging them into one dialog invites the wrong one."""

    def __init__(self, lineage: dict, preset: dict, n_pictures: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sex / age / plumage")
        layout = QVBoxLayout(self)

        what = "this picture" if n_pictures == 1 else f"these {n_pictures} pictures"
        layout.addWidget(QLabel(f"Which form of the species is in {what}?"))
        hint = QLabel("The website shows one best photo per form, so this changes what "
                      "gets published.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        layout.addWidget(hint)

        applicable = groups_for_lineage(lineage)
        self._buttons: dict[int, list] = {}
        shown = 0
        for gi, (cat, opts) in enumerate(QUALIFIER_GROUPS):
            has_value = gi in preset
            # Not applicable AND not already set → do not offer it (no plumage on a
            # plant). Not applicable but ALREADY SET → show it, so the value the picture
            # carries can be seen and removed rather than silently dropped.
            if gi not in applicable and not has_value:
                self._buttons[gi] = []
                continue
            row = QHBoxLayout()
            lbl = QLabel(cat + ":")
            lbl.setMinimumWidth(60)
            row.addWidget(lbl)
            self._buttons[gi] = []
            for label, canon, _aliases in opts:
                b = QPushButton(label)
                b.setCheckable(True)
                b.setChecked(canon == preset.get(gi))
                b.clicked.connect(
                    lambda _c, g=gi, bb=b: self._single_select(g, bb))
                row.addWidget(b)
                self._buttons[gi].append((b, canon))
            row.addStretch()
            layout.addLayout(row)
            shown += 1

        if not shown:
            none_lbl = QLabel("None of sex, age or plumage applies to this taxon.")
            none_lbl.setStyleSheet("color: #666;")
            layout.addWidget(none_lbl)

        btns = QHBoxLayout()
        btns.addStretch()
        clear = QPushButton("Clear all")
        clear.clicked.connect(self._clear_all)
        btns.addWidget(clear)
        ok = QPushButton("Apply")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btns.addWidget(ok)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        layout.addLayout(btns)

    def _single_select(self, gi: int, clicked):
        for b, _c in self._buttons.get(gi, []):
            if b is not clicked:
                b.setChecked(False)

    def _clear_all(self):
        for btns in self._buttons.values():
            for b, _c in btns:
                b.setChecked(False)

    def selected(self) -> dict:
        out = {}
        for gi, btns in self._buttons.items():
            for b, canon in btns:
                if b.isChecked():
                    out[gi] = canon
                    break
        return out


# alias (lowercase) -> (group index, canonical token)
_ALIAS_TO_GROUP = {
    alias: (gi, canon)
    for gi, (_cat, opts) in enumerate(QUALIFIER_GROUPS)
    for _label, canon, aliases in opts
    for alias in aliases
}

# Qualifier tokens we RECOGNISE (so they're stripped from the species name) but
# do NOT offer a button for — non-breeding is the default plumage, nothing to show.
_DROP_ALIASES = {"non-breeding", "non-br.", "non-br", "winter"}


def split_species_qualifiers(subject: str):
    """Split a subject like 'blauwborst, female' into ('blauwborst', {group: canon}).
    Recognised qualifier tokens (any group) are pulled out; everything else is
    kept as the species name. Handles the two stored forms: a scalar compound
    ('fuut, juv') and the fpr list-repr ("['fuut', 'juv']")."""
    s = (subject or "").strip()
    try:
        import ast
        parts = ast.literal_eval(s)
        if isinstance(parts, list):
            s = ", ".join(str(p).strip("'\"") for p in parts)
    except Exception:
        pass
    selected: dict[int, str] = {}
    name_parts = []
    for part in s.split(","):
        p = part.strip()
        if not p:
            continue
        hit = _ALIAS_TO_GROUP.get(p.lower())
        if hit:
            selected[hit[0]] = hit[1]
        elif p.lower() in _DROP_ALIASES:
            continue   # recognised default (non-breeding) — strip, no button
        else:
            name_parts.append(p)
    return ", ".join(name_parts), selected


def build_species_subject(species_name: str, selected: dict) -> str:
    """Recombine a species name with the selected qualifier tokens (group order),
    e.g. ('blauwborst', {0: 'female'}) -> 'blauwborst, female'."""
    name = (species_name or "").strip()
    if not name:
        return name
    quals = [selected[gi] for gi in range(len(QUALIFIER_GROUPS)) if gi in selected]
    return ", ".join([name] + quals)


# Compact abbreviations shown after a species name in the viewers (grid + large).
# Keyed by the canonical token the picker writes.
_QUAL_ABBREV = {
    "male": "♂", "female": "♀", "couple": "♂♀",
    "juvenile": "juv.", "immature": "imm.", "adult": "ad.",
    "breeding": "br.",
}
# Keyed by the picture-record field values getBirdInfo produces (malefemale /
# ageintext / summerwinter). non-br. is the default plumage, so it has no mark.
_FIELD_ABBREV = {
    "male": "♂", "female": "♀", "couple": "♂♀",
    "juv.": "juv.", "imm.": "imm.", "ad.": "ad.", "br.": "br.",
}


def qualifier_suffix_from_subject(subject: str) -> str:
    """Compact qualifier suffix to append after a species name — e.g. ' ♀ br.' —
    parsed from a stored subject ('fuut, female, breeding'). '' when none."""
    _name, selected = split_species_qualifiers(subject)
    parts = [_QUAL_ABBREV.get(selected[gi], "") for gi in sorted(selected)]
    parts = [p for p in parts if p]
    return (" " + " ".join(parts)) if parts else ""


def qualifier_suffix_from_fields(malefemale="", ageintext="", summerwinter="") -> str:
    """Same compact suffix, built from the picture record's already-derived
    malefemale / ageintext / summerwinter fields (e.g. master rows)."""
    parts = [_FIELD_ABBREV.get((v or "").strip().lower(), "")
             for v in (malefemale, ageintext, summerwinter)]
    parts = [p for p in parts if p]
    return (" " + " ".join(parts)) if parts else ""


class CullingSpeciesDialog(QDialog):
    """Assign a species name to one or more pictures during culling. Accepts
    NL / EN / scientific input with contains-match auto-complete and live
    resolution feedback; the chosen keyword is written to XMP:Subject."""

    def __init__(self, sc, current_name: str, n_pictures: int, parent=None):
        super().__init__(parent)
        self._sc = sc
        self.setWindowTitle("Set species")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        header = (f"Set species for {n_pictures} pictures"
                  if n_pictures != 1 else "Set species")
        title = QLabel(f"<b>{header}</b>")
        layout.addWidget(title)
        layout.addWidget(QLabel("Type a Dutch, English or scientific name:"))

        # An incoming subject may already carry qualifiers ("blauwborst, female");
        # keep only the species name in the field and pre-select the toggles below.
        name_only, self._preset_qualifiers = split_species_qualifiers(current_name)

        # Typed entry + its own "Set species" button on one row: the button commits
        # exactly what's in the field. "Check online" and the resolution feedback
        # below it also act on this typed name only.
        self.field = QLineEdit(name_only)
        if sc is not None:
            completer = QCompleter(species_name_candidates(sc), self)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            self.field.setCompleter(completer)
            self._completer = completer
        else:
            self._completer = None

        entry_row = QHBoxLayout()
        entry_row.addWidget(self.field, 1)
        self.ok_btn = QPushButton("Set species")
        entry_row.addWidget(self.ok_btn)
        layout.addLayout(entry_row)

        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)

        gbif_row = QHBoxLayout()
        self.gbif_btn = QPushButton("Check online (GBIF)")
        self.gbif_btn.setToolTip("Look the typed name up in GBIF (needs internet)")
        gbif_row.addWidget(self.gbif_btn)
        gbif_row.addStretch()
        layout.addLayout(gbif_row)

        # Optional qualifiers — sex / age / plumage. Stored in the keyword as
        # "<species>, <token>"; the pipeline reads them to caption the picture and
        # to show one best photo per age/sex/plumage on the species page. Single-
        # select within each row; click again to clear.
        self._qual_header = QLabel("Optional — sex / age / plumage:")
        layout.addWidget(self._qual_header)
        self._qual_buttons: dict[int, list[tuple[QPushButton, str]]] = {}
        self._qual_rows: dict[int, QWidget] = {}
        for gi, (cat, opts) in enumerate(QUALIFIER_GROUPS):
            # Each group in its own widget, so a whole row can be hidden when it does not
            # apply to the taxon (no plumage on a plant).
            holder = QWidget()
            qrow = QHBoxLayout(holder)
            qrow.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(cat + ":")
            lbl.setMinimumWidth(60)
            qrow.addWidget(lbl)
            self._qual_buttons[gi] = []
            preset = self._preset_qualifiers.get(gi)
            for label, canon, _aliases in opts:
                b = QPushButton(label)
                b.setCheckable(True)
                b.setChecked(canon == preset)
                b.clicked.connect(
                    lambda _checked, g=gi, bb=b: self._on_qualifier_clicked(g, bb))
                qrow.addWidget(b)
                self._qual_buttons[gi].append((b, canon))
            qrow.addStretch()
            self._qual_rows[gi] = holder
            layout.addWidget(holder)

        # Recent species — left-click re-applies a name in one go; right-click
        # removes a name from the list (e.g. an untested one typed by mistake).
        # Built into a container so it can be rebuilt in place after a removal.
        self._recent_sep = QFrame()
        self._recent_sep.setFrameShape(QFrame.Shape.HLine)
        self._recent_sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(self._recent_sep)
        self._recent_label = QLabel("Recent species — click to apply, right-click to remove:")
        layout.addWidget(self._recent_label)
        self._recent_container = QWidget()
        self._recent_grid = QGridLayout(self._recent_container)
        self._recent_grid.setContentsMargins(0, 0, 0, 0)
        self._recent_grid.setHorizontalSpacing(6)
        self._recent_grid.setVerticalSpacing(4)
        layout.addWidget(self._recent_container)
        self._build_recent_buttons()

        # Close only — "Set species" lives next to the field above. Named "Close"
        # (not "Cancel"): any recents you removed are already gone, so this button
        # doesn't undo them — it just leaves without assigning a species.
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.close_btn = QPushButton("Close")
        self.close_btn.setToolTip("Leave without setting a species (removed recents stay removed)")
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

        self.field.textChanged.connect(lambda: self._resolve(use_gbif=False))
        self.field.returnPressed.connect(self._on_ok)
        self.gbif_btn.clicked.connect(lambda: self._resolve(use_gbif=True))
        self.ok_btn.clicked.connect(self._on_ok)
        self.close_btn.clicked.connect(self.reject)

        self._chosen = ""
        self._is_known = False   # does the current field text resolve to a real species?
        self._resolve(use_gbif=False)
        self.field.setFocus()

    def _build_recent_buttons(self):
        """(Re)fill the recent-species grid from the persisted list. Two columns,
        most-recent first; hidden entirely when there are no recents."""
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
            b.clicked.connect(lambda _=False, nm=name: self._commit(nm))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda _pos, nm=name: self._remove_recent(nm))
            self._recent_grid.addWidget(b, i // 2, i % 2)
        self.adjustSize()   # shrink/grow the dialog to the new row count

    def _remove_recent(self, name: str):
        """Right-click menu on a recent button → forget that name."""
        menu = QMenu(self)
        act = menu.addAction(f"Remove “{name}” from recents")
        if menu.exec(QCursor.pos()) == act:
            forget_recent_species(name)
            self._build_recent_buttons()

    def _resolve(self, use_gbif: bool):
        """Show how the typed name resolves, and offer only the qualifiers that mean
        something for whatever it resolved to.

        The visibility update happens HERE, once, on the name this actually resolved to —
        not at each of the seven exits below, where it would eventually be forgotten at
        one of them and the picker would go on offering breeding plumage on a buttercup."""
        sci = self._resolve_name(use_gbif)
        self._update_qualifier_visibility(sci)

    def _resolve_name(self, use_gbif: bool) -> str:
        """Resolve the typed name against the species collection and report how it went.
        Local-only on each keystroke; GBIF only on explicit request (it blocks).
        Returns the resolved scientific name, or "" when it resolves to nothing."""
        text = self.field.text().strip()
        if not text or self._sc is None:
            self.feedback.setText("")
            self._is_known = False
            return ""
        # Local lookup first (no network) — runs on every keystroke.
        try:
            sci = self._sc.getKnownSpecies(text.lower(), False, LookUpInGBIF=False)
        except Exception:
            sci = ""
        # Found locally — show the resolved taxon and stop (no network).
        if sci and sci in self._sc.sc_dict:
            props = self._sc.sc_dict[sci]
            nl = props.get("DutchName", "") or "—"
            en = props.get("EnglishName", "") or "—"
            self.feedback.setText(f"✓ <b>{sci}</b>  (NL: {nl} / EN: {en})")
            self._is_known = True
            return sci
        if sci:
            self.feedback.setText(f"✓ recognised as <b>{sci}</b>")
            self._is_known = True
            return sci
        # Not local. Only hit GBIF on explicit request, with a real HTTP call so
        # we can tell 'no match' from 'couldn't connect'.
        if not use_gbif:
            self.feedback.setText("⚠️ not in your species list — "
                                  "will be stored as typed  (try Check online)")
            self._is_known = False
            return ""
        self.feedback.setText("⏳ checking GBIF…")
        QApplication.processEvents()
        status, gname = gbif_match(text)
        if status == "found":
            sci = self._add_to_collection(text)
            if sci and sci in self._sc.sc_dict:
                props = self._sc.sc_dict[sci]
                nl = props.get("DutchName", "") or "—"
                en = props.get("EnglishName", "") or "—"
                self.feedback.setText(f"✓ added to your species list: <b>{sci}</b>  "
                                      f"(NL: {nl} / EN: {en})")
            elif sci:
                self.feedback.setText(f"✓ added to your species list: <b>{sci}</b>")
            else:
                self.feedback.setText(f"✓ found in GBIF: <b>{gname}</b> "
                                      f"(couldn't add locally) — will be stored")
            self._is_known = True
            return sci or ""
        if status == "unreachable":
            self.feedback.setText("⚠️ couldn't reach GBIF (no internet?) — "
                                  "will be stored as typed")
            self._is_known = False
            return ""
        self.feedback.setText("⚠️ not a known species (not in your list, "
                              "not in GBIF) — will be stored as typed")
        self._is_known = False
        return ""

    def _add_to_collection(self, name):
        """Resolve `name` via the species infrastructure (GBIF backbone +
        vernacular names) so it is added to the in-memory collection, refresh the
        auto-complete, and persist to speciescollection.csv. Returns the resolved
        scientific name or None. Only called when `name` was not already local."""
        sc = self._sc
        if sc is None:
            return None
        try:
            sci = sc.getSpecies(name)  # local-then-GBIF; addSpecies() on a GBIF hit
        except Exception as e:
            logger.warning(f"GBIF add failed for '{name}': {e}")
            return None
        if not sci:
            return None
        if self._completer is not None:
            try:
                self._completer.model().setStringList(species_name_candidates(sc))
            except Exception:
                pass
        path = getattr(sc, "_source_path", "")
        if path:
            try:
                sc.updateSpeciesCollection(path)
                logger.info(f"Added '{sci}' to species collection {path}.")
            except Exception as e:
                logger.warning(f"Could not save species collection {path}: {e}")
        return sci

    def _on_ok(self):
        name = self.field.text().strip()
        if not name:
            self.feedback.setText("⚠️ Type a name, click a recent one, or Cancel.")
            return
        # Guard against committing a typo / unverified name to the pictures (and
        # into the recents). Only the typed field is checked — recent buttons are
        # already-trusted names and commit straight away.
        if not self._is_known:
            resp = QMessageBox.question(
                self, "Unknown species name",
                f"“{name}” isn’t in your species list and hasn’t been confirmed "
                f"online.\n\n"
                f"Yes — store it as typed.\n"
                f"No — go back and correct the name (or use “Check online (GBIF)”).\n"
                f"Cancel — close without setting a species.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No)
            if resp == QMessageBox.StandardButton.Cancel:
                self.reject()
                return
            if resp != QMessageBox.StandardButton.Yes:   # No → fix the name, stay open
                self.field.setFocus()
                self.field.selectAll()
                return
        self._commit(name)

    def _update_qualifier_visibility(self, sciname: str):
        """Show only the qualifier rows that mean something for this taxon.

        ONE EXCEPTION, and it matters: a row that does NOT apply but ALREADY HAS a value
        selected stays visible. The picture is carrying that token whether we approve of
        it or not, and hiding the row would delete it silently on the next Commit — an
        edit the user never made and could not see. Show it; let them remove it."""
        applicable = applicable_qualifier_groups(self._sc, sciname)
        any_shown = False
        for gi, holder in self._qual_rows.items():
            has_value = any(b.isChecked() for b, _c in self._qual_buttons[gi])
            show = (gi in applicable) or has_value
            holder.setVisible(show)
            any_shown |= show
        self._qual_header.setVisible(any_shown)

    def _on_qualifier_clicked(self, group_index: int, clicked_btn):
        """Single-select within a group: turning one on turns its siblings off.
        (A second click on the active one leaves it unchecked — clears the group.)"""
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

    def _commit(self, name: str):
        """Accept with `name` as the chosen species (from the field or a recent
        button), append any selected qualifiers, and bump the species (without
        qualifiers) to the front of the recents list."""
        name = (name or "").strip()
        if not name:
            return
        self._chosen = build_species_subject(name, self._selected_qualifiers())
        remember_recent_species(name)
        self.accept()

    def chosen_name(self) -> str:
        return self._chosen
