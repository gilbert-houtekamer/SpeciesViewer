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
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QCompleter)
from PyQt6.QtCore import Qt

from engine.speciesclassification import SpeciesClassification, Level
from utils.config import _config

logger = logging.getLogger("speciesviewer")


def _internet_available(host="api.gbif.org", port=443, timeout=3.0):
    """Quick reachability probe so we can tell 'not a known species' from
    'couldn't reach GBIF' — and avoid a long blocking GBIF timeout when offline."""
    import socket
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


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

        self.field = QLineEdit(current_name)
        if sc is not None:
            completer = QCompleter(species_name_candidates(sc), self)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            self.field.setCompleter(completer)
        layout.addWidget(self.field)

        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)

        btn_row = QHBoxLayout()
        self.gbif_btn = QPushButton("Check online (GBIF)")
        self.gbif_btn.setToolTip("Look the name up in GBIF (needs internet)")
        btn_row.addWidget(self.gbif_btn)
        btn_row.addStretch()
        self.ok_btn = QPushButton("Set species")
        self.cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(self.ok_btn)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

        self.field.textChanged.connect(lambda: self._resolve(use_gbif=False))
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
        # GBIF only on explicit request, and only if reachable — otherwise the
        # call blocks on a long timeout and we can't tell offline from not-found.
        if not sci and use_gbif:
            if not _internet_available():
                self.feedback.setText("⚠️ couldn't reach GBIF (no internet?) — "
                                      "will be stored as typed")
                return
            try:
                sci = self._sc.getSpecies(text)
            except Exception:
                sci = ""
        if sci and sci in self._sc.sc_dict:
            props = self._sc.sc_dict[sci]
            nl = props.get("DutchName", "") or "—"
            en = props.get("EnglishName", "") or "—"
            self.feedback.setText(f"✓ <b>{sci}</b>  (NL: {nl} / EN: {en})")
        elif sci:
            self.feedback.setText(f"✓ recognised as <b>{sci}</b>")
        elif use_gbif:
            self.feedback.setText("⚠️ not a known species (not in your list, "
                                  "not in GBIF) — will be stored as typed")
        else:
            self.feedback.setText("⚠️ not in your species list — "
                                  "will be stored as typed  (try Check online)")

    def _on_ok(self):
        name = self.field.text().strip()
        if not name:
            self.feedback.setText("⚠️ Enter a name, or Cancel.")
            return
        self._chosen = name
        self.accept()

    def chosen_name(self) -> str:
        return self._chosen
