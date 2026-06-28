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
    QPushButton, QCompleter, QFrame)
from PyQt6.QtCore import Qt

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

        # Typed entry + its own "Set species" button on one row: the button commits
        # exactly what's in the field. "Check online" and the resolution feedback
        # below it also act on this typed name only.
        self.field = QLineEdit(current_name)
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

        # Recent species — one click re-applies a recently used name to the
        # selected pictures (no typing). Two columns, most-recent first.
        recent = load_recent_species()
        if recent:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setFrameShadow(QFrame.Shadow.Sunken)
            layout.addWidget(sep)
            layout.addWidget(QLabel("Recent species — click to apply:"))
            grid = QGridLayout()
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(4)
            for i, name in enumerate(recent):
                b = QPushButton(name)
                b.setToolTip(f"Set “{name}” for these picture(s)")
                b.clicked.connect(lambda _=False, nm=name: self._commit(nm))
                grid.addWidget(b, i // 2, i % 2)
            layout.addLayout(grid)

        # Cancel only — "Set species" now lives next to the field above.
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

        self.field.textChanged.connect(lambda: self._resolve(use_gbif=False))
        self.field.returnPressed.connect(self._on_ok)
        self.gbif_btn.clicked.connect(lambda: self._resolve(use_gbif=True))
        self.ok_btn.clicked.connect(self._on_ok)
        self.cancel_btn.clicked.connect(self.reject)

        self._chosen = ""
        self._resolve(use_gbif=False)
        self.field.setFocus()

    def _resolve(self, use_gbif: bool):
        """Show how the typed name resolves against the species collection.
        Local-only on each keystroke; GBIF only on explicit request (it blocks)."""
        text = self.field.text().strip()
        if not text or self._sc is None:
            self.feedback.setText("")
            return
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
            return
        if sci:
            self.feedback.setText(f"✓ recognised as <b>{sci}</b>")
            return
        # Not local. Only hit GBIF on explicit request, with a real HTTP call so
        # we can tell 'no match' from 'couldn't connect'.
        if not use_gbif:
            self.feedback.setText("⚠️ not in your species list — "
                                  "will be stored as typed  (try Check online)")
            return
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
        elif status == "unreachable":
            self.feedback.setText("⚠️ couldn't reach GBIF (no internet?) — "
                                  "will be stored as typed")
        else:
            self.feedback.setText("⚠️ not a known species (not in your list, "
                                  "not in GBIF) — will be stored as typed")

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
        self._commit(name)

    def _commit(self, name: str):
        """Accept with `name` as the chosen species (from the field or a recent
        button) and bump it to the front of the recents list."""
        name = (name or "").strip()
        if not name:
            return
        self._chosen = name
        remember_recent_species(name)
        self.accept()

    def chosen_name(self) -> str:
        return self._chosen
