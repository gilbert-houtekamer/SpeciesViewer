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
    QPushButton, QCompleter, QFrame, QWidget, QMenu, QMessageBox, QSizePolicy, QCheckBox)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor

from engine.speciesclassification import SpeciesClassification, Level
from utils.config import _config, get_setting, set_setting
from utils.gui_config import get_cache_gui_section, update_cache_gui_section
from gui import theme

logger = logging.getLogger("speciesviewer")


def species_prefill_for_keys(keys, species_of, is_landscape=None):
    """(current_name, preset_summary, n_landscape) to seed a multi-picture
    Set-species dialog. Shared by the grid and the large viewer so the two
    can't drift.

    - One picture selected → offer its own species.
    - Several, one distinct species among them → offer that name; flag when not
      every picture has it ("★ … — set on N of M; the rest are blank").
    - Several distinct → keep the field blank, list them, so a stray Enter
      can't overwrite the odd one out.

    species_of(key)   -> the picture's current species text ('' if none)
    is_landscape(key) -> bool, optional (naming a species clears the marker)
    """
    keys = list(keys)
    if len(keys) == 1:
        return ((species_of(keys[0]) or "").strip(), "", 0)
    named = [s for s in ((species_of(k) or "").strip() for k in keys) if s]
    distinct = sorted(set(named))
    # Same species, different sex / age / plumage ("woodpecker" next to
    # "woodpecker, male") is ONE species — compare on the name without its
    # qualifiers, and report the qualifiers separately.
    split = [split_species_qualifiers(s) for s in named]
    bases = sorted({b for b, _q in split if b})
    current, summary = "", ""
    if len(distinct) == 1:
        current = distinct[0]
        if len(named) < len(keys):
            summary = (f"★ {distinct[0]} — set on {len(named)} of {len(keys)}; "
                       f"the rest are blank")
    elif len(bases) == 1:
        # Prefill the bare name: Commit writes ONE subject to every picture, so
        # the summary says plainly that the qualifiers will be made uniform.
        current = bases[0]
        counts: dict[str, int] = {}
        for _b, quals in split:
            for canon in quals.values():
                counts[canon] = counts.get(canon, 0) + 1
        marked = ", ".join(f"{n} marked as {canon}" for canon, n in counts.items())
        blank = (f"; {len(keys) - len(named)} blank" if len(named) < len(keys)
                 else "")
        summary = (f"★ {bases[0]} — {marked} (of {len(keys)}){blank}. "
                   f"Commit gives all {len(keys)} the sex / age / plumage "
                   f"chosen below")
    elif len(distinct) > 1:
        summary = ("Mixed — " + ", ".join(distinct)
                   + f"  ({len(named)} of {len(keys)} named)")
    n_landscape = 0
    if is_landscape is not None:
        for k in keys:
            try:
                if is_landscape(k):
                    n_landscape += 1
            except Exception:
                pass
    return (current, summary, n_landscape)


def quick_species_subject(keys, species_of) -> str:
    """The one subject that quick-'a' may copy across `keys` without asking —
    or "" when there is nothing safe to copy and the dialog must open instead.

    'a' is 's' minus the dialog, for the one case that needs no decision: the
    selection already agrees on a species and the rest are blank, so all the
    dialog could do is re-type what is already there. The usual shape is a
    burst where one guide frame was named and the other twenty were not.

    Returns "" — meaning "open the regular Set species dialog" — when:
      - fewer than two pictures: there is nothing to copy ONTO;
      - no picture carries a species: there is nothing to copy FROM;
      - the named pictures disagree.

    Qualifiers are part of the subject string, so they ride along for free —
    "greater ani, male" copies the sex with the name. They also count towards
    disagreement on purpose: "greater ani, male" next to "greater ani, female"
    is NOT one setting, and silently picking a sex for the whole burst would be
    a data change nobody asked for. That pair opens the dialog.

    Shares its "what is this selection already saying?" reading with
    species_prefill_for_keys() above, which seeds the dialog with the same
    agreed name — the two must not drift apart.
    """
    keys = list(keys)
    if len(keys) < 2:
        return ""
    named = {s for s in ((species_of(k) or "").strip() for k in keys) if s}
    return next(iter(named)) if len(named) == 1 else ""


# Biota vocabulary — mono micro-labels + chip-style recents, so this culling
# species popup reads as one system with the large-viewer SpeciesInfoPanel.
# (Mirrors species_editor's _MICRO / _RECENT_CHIP; kept local rather than
# importing private names across the two species editors.)
_MONO = "'Cascadia Mono','Cascadia Code',Consolas,monospace"
_MICRO = (f"font-family:{_MONO}; font-size:9.5px; font-weight:600;"
          f" letter-spacing:1.2px; color:{theme.INK_SOFT};")
_RECENT_CHIP = (
    f"QPushButton {{ font-size:11.5px; color:{theme.INK}; background:{theme.SURFACE};"
    f" border:1px solid {theme.LINE}; border-radius:12px; padding:3px 11px; text-align:left; }}"
    f"QPushButton:hover {{ border-color:{theme.ACCENT}; color:{theme.ACCENT}; }}")

# Sex / age / plumage toggle buttons. Without an explicit :checked rule a
# stylesheet'd QPushButton shows NO difference between on and off — the user
# clicks "male", nothing visibly changes, and it looks stuck. Solid accent
# fill when checked makes the toggle unmistakable (and :checked:hover keeps
# the app-level :hover from washing it out).
QUAL_BTN_QSS = (
    "QPushButton { padding: 2px 6px; }"
    f"QPushButton:checked {{ background:{theme.ACCENT}; color:{theme.ACCENT_INK};"
    f" border:1px solid {theme.ACCENT}; font-weight:600; }}"
    f"QPushButton:checked:hover {{ background:{theme.ACCENT_HOVER};"
    f" border-color:{theme.ACCENT_HOVER}; }}")


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
        logger.warning("No species list (speciescollection.csv) in the trip or the master — "
                       "species names cannot be looked up while culling.")
        return None
    sc = SpeciesClassification(None)
    # Fresh per-instance dicts (the class-level ones are shared) so we don't
    # inherit or pollute another load.
    sc.sc_dict, sc.dutch_dict, sc.english_dict = {}, {}, {}
    import time as _time
    _t0 = _time.perf_counter()
    try:
        sc.readSpeciesCollection(path)
    except Exception as e:
        logger.warning(f"Could not read species collection {path}: {e}")
        return None
    logger.debug("load_species_classification: %d species from %s in %.2f s",
                len(sc.sc_dict), os.path.basename(os.path.dirname(os.path.dirname(path))),
                _time.perf_counter() - _t0)
    # readSpeciesCollection already records this; kept explicit because the
    # path chosen above is the contract this loader advertises.
    sc._source_path = path
    return sc


def flush_learned_species(sc):
    """Append everything this session learned to the master's species list and
    to the local (trip/album) copy. Returns (ok, message); message is "" when
    there was nothing to write.

    The MASTER is written first and is required. A species learned here is
    knowledge, not a picture: if it only ever reaches the local copy it has to
    be looked up again on every other machine and in every later trip, which is
    the whole point of doing this. When the master cannot be reached that is an
    error the user must see, not a silent queue — and because the buffer is
    only cleared on success, closing the viewer again after fixing the drive
    still saves the work.
    """
    if sc is None or not getattr(sc, "learned", None):
        return True, ""

    master_db = _config.get("master", "masterbiotadatabase", fallback="")
    local = getattr(sc, "_source_path", "")
    master = (os.path.join(master_db, "metadata", "active", "speciescollection.csv")
              if master_db else "")

    if master and not os.path.exists(master):
        return False, (f"{len(sc.learned)} newly recognised species could not be saved: "
                       f"the master species list is not reachable at\n{master}\n\n"
                       f"They are kept for now — reconnect the master and close this "
                       f"viewer again, or they will have to be looked up once more.")

    targets = [p for p in (master, local) if p]
    # Same file twice (working straight on the master) — write it once.
    if len(targets) == 2 and os.path.normcase(os.path.normpath(targets[0])) == \
            os.path.normcase(os.path.normpath(targets[1])):
        targets = targets[:1]
    if not targets:
        return True, ""

    try:
        written = sc.flushSpeciesLearned(targets)
    except Exception as e:
        logger.warning(f"Could not save learned species: {e}")
        return False, f"Newly recognised species could not be saved:\n{e}"

    total = max((len(v) for v in written.values()), default=0)
    if total:
        logger.info(f"Saved {total} newly recognised species row(s) to "
                    f"{len(targets)} collection file(s).")
    return True, ""


def remember_local_name(sc, name, sci):
    """Keep `name` (a local name a guide used) as another name for `sci`, in
    the MASTER's alias table and in this trip's, at once (ch.6). Returns
    (ok, message). The master comes first and is required, for the same reason
    as flush_learned_species: a name kept only in the trip is lost with it.
    A laptop master's table reaches the main master through the trip — the
    trip export, svr5 and Install Small Master all merge it."""
    from engine.species_aliases import ALIAS_FILE, add_alias, alias_problem
    problem = alias_problem(sc, name, sci)
    if problem:
        return False, problem
    master_db = _config.get("master", "masterbiotadatabase", fallback="")
    if not master_db or not os.path.isdir(os.path.join(master_db, "metadata", "active")):
        return False, ("The master is not reachable, so the name cannot be kept. "
                       "Reconnect the master and try again.")
    master = os.path.join(master_db, "metadata", "active", ALIAS_FILE)
    local_dir = os.path.dirname(getattr(sc, "_source_path", "") or "")
    local = os.path.join(local_dir, ALIAS_FILE) if local_dir else ""
    trip = os.path.basename(os.path.dirname(os.path.dirname(local_dir))) if local_dir else ""
    try:
        add_alias([master, local], name, sci, source=trip)
    except Exception as e:
        logger.warning(f"Could not keep the name '{name}': {e}")
        return False, f"The name could not be saved:\n{e}"
    sc.alias_dict[name.strip().lower()] = sci.strip().lower()
    return True, ""


class LocalNameDialog(QDialog):
    """Ask which species a local name means. ONE JOB: return a scientific name
    from the species list that the name may point at — OK stays disabled
    until the choice is one alias_problem accepts."""

    def __init__(self, sc, local_name: str, parent=None):
        super().__init__(parent)
        from gui.widgets import wrapped
        self._sc = sc
        self._local = local_name.strip()
        self.sci = ""
        self.chosen = ""
        self.setWindowTitle("A local name")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.addWidget(wrapped(
            f"Which species is “{self._local}”? From now on SpeciesViewer "
            f"recognises “{self._local}” as that species — on this trip, in "
            f"your master and in every later trip.", self))
        self.field = QLineEdit()
        completer = QCompleter(species_name_candidates(sc), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.field.setCompleter(completer)
        layout.addWidget(self.field)
        self.feedback = wrapped("", self)
        layout.addWidget(self.feedback)
        row = QHBoxLayout()
        row.addStretch()
        self.ok_btn = QPushButton("Remember the name")
        cancel = QPushButton("Cancel")
        row.addWidget(self.ok_btn)
        row.addWidget(cancel)
        layout.addLayout(row)
        self.ok_btn.setEnabled(False)
        self.field.textChanged.connect(self._check)
        self.field.returnPressed.connect(self._accept)
        self.ok_btn.clicked.connect(self._accept)
        cancel.clicked.connect(self.reject)
        self.field.setFocus()

    def _check(self):
        from engine.species_aliases import alias_problem
        text = self.field.text().strip()
        try:
            sci = self._sc.getKnownSpecies(text.lower(), False, LookUpInGBIF=False) if text else ""
        except Exception:
            sci = ""
        problem = alias_problem(self._sc, self._local, sci) if sci else ""
        if not text:
            self.feedback.setText("")
        elif not sci:
            self.feedback.setText("⚠️ not in your species list")
        elif problem:
            self.feedback.setText(f"⚠️ {problem}")
        else:
            self.feedback.setText(f"✓ <b>{sci}</b>")
        self.sci = sci if sci and not problem else ""
        self.ok_btn.setEnabled(bool(self.sci))

    def _accept(self):
        if self.sci:
            self.chosen = self.field.text().strip()
            self.accept()


def flush_learned_species_from(scs, parent=None):
    """Flush several classifications at once — a viewer may have touched both
    the standalone culling collection and the pipeline's (fpr.sc). Reports at
    most one warning. Returns True when everything was saved.

    Call this from closeEvent: it is the end of the session the buffer belongs
    to. Never let it stop the window closing — the buffer survives a failure,
    so closing again after fixing the problem still saves the work."""
    problems = []
    for sc in scs:
        if sc is None:
            continue
        try:
            ok, msg = flush_learned_species(sc)
        except Exception as e:
            logger.warning(f"Saving learned species failed: {e}")
            ok, msg = False, str(e)
        if not ok and msg:
            problems.append(msg)
    if problems and parent is not None:
        try:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(parent, "Species list not saved", "\n\n".join(problems))
        except Exception:
            pass
    return not problems


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


def remember_species_from_subject(subject: str):
    """LRU-bump the species carried by a stored subject — qualifiers stripped.

    Every route that commits a species has to do this, or the name stops rising
    in the quick-pick list exactly when it is being used most. The dialogs do it
    themselves; the dialog-free 'a' key has no dialog to do it for it, so it
    calls this. Qualifiers are stripped for the same reason the Set-species
    panel strips them: a token like "juv" must never leak into the recents as
    if it were a species."""
    name, _quals = split_species_qualifiers(subject or "")
    if name.strip():
        remember_recent_species(name.strip())


def forget_recent_species(name: str):
    """Drop `name` from the recents (e.g. a wrong/untested name typed by mistake)."""
    name = (name or "").strip()
    if not name:
        return
    recent = [r for r in load_recent_species() if r.lower() != name.lower()]
    update_cache_gui_section(_RECENT_SECTION, _RECENT_KEY, recent[:_RECENT_MAX])


def recent_display_label(sc, raw_name: str) -> str:
    """Best-effort display label for a recents entry.

    A brand-new species has to be TYPED as its scientific name (GBIF only
    matches scientific names), so that is what gets stored in the recents
    list — but a scientific name is the last thing you want to read off a
    button for every later picture of the same species. This resolves it
    through the species collection to the same NL/EN/Sci display profile the
    grid and large-viewer cards already use, so the button reads "jaguar"
    even though "panthera onca" is what was actually typed (and is still
    what gets committed — see _build_recent_buttons). Falls back to the raw
    name unchanged when it doesn't resolve (collection not loaded, or a name
    typed but never actually looked up)."""
    if sc is None or not raw_name:
        return raw_name
    try:
        sci = sc.getKnownSpecies(raw_name.strip().lower(), False, LookUpInGBIF=False)
    except Exception:
        sci = ""
    props = sc.sc_dict.get(sci) if sci else None
    if not props:
        return raw_name
    from utils.name_profile import effective_profile, name_for
    row = {"en": props.get("EnglishName", ""), "nl": props.get("DutchName", ""), "sci": sci}
    return name_for(row, effective_profile()) or raw_name


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


# ── the taxon index: EVERY species tree we have, concatenated ─────────────────
#
# speciescollection.csv is the RECOGNITION table (name -> name). The species TREES —
# <repo>/metadata/active/species.csv and every <repo>/metadata/review/<day>/species.csv,
# for the trip AND the master — are where the Up chains live, and they are where the
# PIPELINE writes what it learned from GBIF. Crete's orchids and camomiles are not in the
# recognition table and never will be until a full run, but sv2/sv3 already resolved them
# and wrote the whole lineage into the day's species.csv:
#
#   anthemis arvensis -> asteraceae -> asterales -> magnoliopsida -> tracheophyta -> plantae
#
# Reading only the recognition table was why a flower was still offered breeding plumage:
# the answer was already on disk. sv4/sv5 concatenate the master and trip inventories for
# exactly this reason; so does this now.
_TAXON_INDEX: dict = {"key": None, "up": {}, "level": {}, "alias": {}, "checked": 0.0}
_TAXON_RECHECK_SECONDS = 2.0   # do not stat 30 files on every keystroke


def _taxon_sources(include_master: bool = True) -> list:
    from pathlib import Path
    bases = [_config.get("trip", "tripbiotadatabase", fallback="")]
    if include_master:
        bases.append(_config.get("master", "masterbiotadatabase", fallback=""))
    out = []
    for base in bases:
        if not base:
            continue
        md = Path(base) / "metadata"
        p = md / "active" / "species.csv"
        if p.is_file():
            out.append(p)
        rev = md / "review"
        if rev.is_dir():
            out.extend(sorted(rev.glob("*/species.csv")))
    return out


def trip_species_names() -> list[str]:
    """Every distinct NL/EN/Sci name at species level, across every species tree
    THIS TRIP has ever written — metadata/active/species.csv for committed days
    plus metadata/review/<day>/species.csv for days still in progress. This
    accumulates over the whole trip, unlike fpr.upn (which FilePicture.readTripDay
    narrows to whichever single day is currently open) — so this is the right
    source for a genuinely trip-wide species suggestion, e.g. the "look in trip
    names" completer toggle.

    The master is deliberately EXCLUDED — unlike _taxon_index()/taxon_lineage(),
    which want every taxon ever seen anywhere to classify a congener, "look in
    trip names" promises species seen on THIS trip, and a returning trip (say,
    a second visit to Brazil) would otherwise suggest the whole master's life
    list, defeating the point of a short, trip-relevant list."""
    import csv as _csv
    names = set()
    for p in _taxon_sources(include_master=False):
        try:
            with open(p, newline="", encoding="utf-8") as fh:
                for r in _csv.DictReader(fh, delimiter=";"):
                    if str(r.get("Level") or "").rsplit(".", 1)[-1].upper() != "SPECIES":
                        continue
                    for col in ("ScientificName", "DutchName", "EnglishName"):
                        v = (r.get(col) or "").strip()
                        if v:
                            names.add(v)
        except OSError as e:
            logger.warning(f"trip_species_names: cannot read {p}: {e}")
    return sorted(names)


def _taxon_index() -> dict:
    """Name -> (level, up), merged over every species tree, rebuilt when one changes.

    Keyed on the sources' mtimes: the day trees grow AS HE CULLS, so an index built once
    per session would go stale exactly when it started to matter."""
    import csv as _csv
    import time as _time
    now = _time.monotonic()
    if _TAXON_INDEX["key"] is not None and \
            (now - _TAXON_INDEX["checked"]) < _TAXON_RECHECK_SECONDS:
        return _TAXON_INDEX                      # stat'ing 30 files per keystroke is silly
    srcs = _taxon_sources()
    try:
        key = tuple((str(p), p.stat().st_mtime_ns) for p in srcs)
    except OSError:
        key = None
    _TAXON_INDEX["checked"] = now
    if key is not None and _TAXON_INDEX["key"] == key:
        return _TAXON_INDEX
    up, lvl, alias = {}, {}, {}
    for p in srcs:
        try:
            with open(p, newline="", encoding="utf-8") as fh:
                for r in _csv.DictReader(fh, delimiter=";"):
                    n = (r.get("ScientificName") or "").strip().lower()
                    if not n:
                        continue
                    # Trip trees are read before the master's, and a day the user is
                    # working in is the freshest thing we have — so first write wins.
                    up.setdefault(n, (r.get("Up") or "").strip().lower())
                    lvl.setdefault(n, str(r.get("Level") or "").rsplit(".", 1)[-1].lower())
                    # The trees carry the vernacular names too, and the field often holds
                    # one ("valse kamille"), so index them or the lookup misses the very
                    # name the user is looking at.
                    for col in ("DutchName", "EnglishName"):
                        v = (r.get(col) or "").strip().lower()
                        if v:
                            alias.setdefault(v, n)
        except OSError as e:
            logger.warning(f"taxon index: cannot read {p}: {e}")
    _TAXON_INDEX.update(key=key, up=up, level=lvl, alias=alias)
    logger.debug(f"[qualifiers] taxon index rebuilt: {len(up)} taxa "
                f"(+{len(alias)} vernacular names) from {len(srcs)} species tree(s)")
    return _TAXON_INDEX


def _node(sc, name: str):
    """(level, up_name) for a taxon — the collection first, then every species tree."""
    if sc is not None:
        rec = sc.sc_dict.get(name)
        if rec is not None:
            return (str(rec.get("Level", "")).rsplit(".", 1)[-1].lower(),
                    str(rec.get("Up", "") or "").lower())
    idx = _taxon_index()
    if name in idx["up"]:
        return idx["level"].get(name, ""), idx["up"][name]
    return None


def _walk_up(sc, start: str) -> dict:
    out: dict[str, str] = {}
    cur, seen = start, set()
    while cur and cur not in seen:
        seen.add(cur)
        node = _node(sc, cur)
        if node is None:
            break
        lvl, up = node
        if lvl and lvl not in out:
            out[lvl] = cur
        cur = up
    return out


def taxon_lineage(sc, sciname: str) -> dict:
    """{'kingdom': 'animalia', 'phylum': 'chordata', 'class': 'aves', …} by walking Up.

    Consults the recognition table AND every species tree on disk (trip + master, active
    + per-day review) — the trees are where the pipeline records what GBIF told it, so a
    plant identified during this very trip is already fully classified there.

    Falls back to a CONGENER when the species itself is unknown anywhere: two species of
    one genus cannot be in different kingdoms, so if any 'orchis …' is known, this one is
    a plant too. Only the ranks ABOVE genus are borrowed, never the species itself.

    Still {} for a genus nothing has ever seen — then only GBIF (Validate) can say, and
    until it does, hiding a row would be a guess."""
    if not sciname:
        return {}
    sciname = sciname.strip().lower()
    # The field often holds a vernacular name ("valse kamille"); the trees index those too.
    if _node(sc, sciname) is None:
        sciname = _taxon_index()["alias"].get(sciname, sciname)
    out = _walk_up(sc, sciname)
    if out:
        return out
    genus = sciname.split()[0] if " " in sciname else ""
    if not genus:
        return {}
    prefix = genus + " "
    pool = list(sc.sc_dict) if sc is not None else []
    pool += list(_taxon_index()["up"])
    sib = next((k for k in pool if k.startswith(prefix) and k != sciname), None)
    if not sib:
        return {}
    lin = _walk_up(sc, sib)
    lin.pop("species", None)          # the congener is NOT this species
    return lin


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
                b.setStyleSheet(QUAL_BTN_QSS)
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


def edit_master_qualifiers(parent, collection, key: str) -> bool:
    """The master's sex / age / plumage flow, in ONE place.

    Called from both the Master Species Viewer's card menu and the large viewer's species
    panel. Two copies of this would drift — and one of them would be the copy that forgets
    that plants have no plumage.

    Returns True when the picture changed."""
    lineage = collection.master_taxon_lineage(key)
    preset = collection.master_qualifiers(key)

    # Decide BEFORE opening. A dialog that opens only to announce that nothing in it
    # applies has wasted a click and made the user prove a negative.
    if not groups_for_lineage(lineage) and not preset:
        kingdom = lineage.get("kingdom", "")
        what = {"plantae": "Plants", "fungi": "Fungi"}.get(
            kingdom, kingdom.capitalize() if kingdom else "This taxon")
        QMessageBox.information(
            parent, "Sex / age / plumage",
            f"{what} have no sex, age or plumage form to record.\n\n"
            f"These properties exist so the website can show the different forms of an "
            f"animal — a juvenile robin beside an adult. Age and plumage are vertebrate "
            f"life stages, and 'pair' means nothing for a plant.\n\n"
            f"A vocabulary that does fit (seedling, flowering, fruiting) can be added "
            f"later; offering the wrong words would only look right.")
        return False

    dlg = QualifierDialog(lineage, preset, 1, parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    selected = dlg.selected()
    if selected == preset:
        return False
    if not collection.apply_master_qualifiers(key, selected):
        QMessageBox.warning(parent, "Sex / age / plumage",
                            "Could not write the qualifiers — see the log.")
        return False
    return True


class CullingSpeciesWidget(QWidget):
    """Assign a species name to one or more pictures during culling — the editor
    proper, reusable both as a modal dialog (CullingSpeciesDialog) and embedded
    inline (the large image viewer's SPECIES section). Accepts NL / EN /
    scientific input with contains-match auto-complete and live resolution
    feedback; emits the chosen keyword (name + optional qualifiers) via
    `committed`. `cancelled` fires when the user backs out of committing an
    unrecognised name. It does not write anything itself — the host decides which
    pictures the keyword lands on.

    `embedded=True` drops the modal chrome (the bold title) so it sits cleanly
    under the panel's own SPECIES header."""

    committed = pyqtSignal(str)   # the built XMP:Subject keyword
    cancelled = pyqtSignal()      # user abandoned an unrecognised-name commit

    def __init__(self, sc, current_name: str, n_pictures: int = 1, parent=None,
                 *, embedded: bool = False, n_landscape: int = 0,
                 preset_summary: str = ""):
        super().__init__(parent)
        self._sc = sc
        self._embedded = embedded

        layout = QVBoxLayout(self)
        if embedded:
            layout.setContentsMargins(0, 0, 0, 0)
        else:
            header = (f"Set species for {n_pictures} pictures"
                      if n_pictures != 1 else "Set species")
            layout.addWidget(QLabel(f"<b>{header}</b>"))

        # Pictures in this batch that are currently landscapes / context photos.
        # A landscape's subject IS the marker keyword, so naming a species here
        # replaces it and the picture stops being a blog extra — the editor must
        # say so instead of presenting an empty field as if nothing is set.
        if n_landscape:
            if n_landscape != n_pictures:
                what = f"{n_landscape} of these are landscape / context photos"
            elif n_pictures == 1:
                what = "This picture is a landscape / context photo"
            else:
                what = "These are landscape / context photos"
            note = QLabel(f"🌄 {what} — setting a species here replaces that.")
            note.setWordWrap(True)
            note.setStyleSheet(f"color:{theme.WARN};")
            layout.addWidget(note)
        _hint = QLabel("Type a Dutch, English or scientific name")
        _hint.setStyleSheet(f"color:{theme.INK_SOFT}; font-size:11px;")
        layout.addWidget(_hint)

        # What the selection already carries (multi-picture case): "★ name — set
        # on 1 of 16" when one picture is named and the rest blank, or a "Mixed:"
        # list when several names are present. Lets a fast guide name one frame,
        # then select the burst and have that name pre-filled here.
        if preset_summary:
            _psum = QLabel(preset_summary)
            _psum.setWordWrap(True)
            _psum.setStyleSheet(f"color:{theme.WARN}; font-size:11px;")
            layout.addWidget(_psum)

        # An incoming subject may already carry qualifiers ("blauwborst, female");
        # keep only the species name in the field and pre-select the toggles below.
        name_only, self._preset_qualifiers = split_species_qualifiers(current_name)

        # Typed entry + its own "Set species" button on one row: the button commits
        # exactly what's in the field. "Check online" and the resolution feedback
        # below it also act on this typed name only.
        self.field = QLineEdit(name_only)
        self._completer = None

        entry_row = QHBoxLayout()
        entry_row.addWidget(self.field, 1)
        # Shared setting with the pipeline's species editor (species_editor.py) —
        # one user preference, same key, so it doesn't reset between the two
        # dialogs. Narrows the completer to species already on THIS trip instead
        # of the ~30k full reference collection; useful once a trip has a few
        # days behind it, so off by default on a brand-new trip.
        self._trip_only_check = QCheckBox("trip names")
        self._trip_only_check.setToolTip(
            "Only suggest species already recorded on this trip, instead of "
            "the full species reference list. Culling runs before this day is "
            "read, so its own pictures aren't counted yet — every other day "
            "already committed or in review is.")
        self._trip_only_check.setChecked(
            get_setting("Species", "completer_trip_only", fallback="no") == "yes")
        self._trip_only_check.toggled.connect(self._on_trip_only_toggled)
        entry_row.addWidget(self._trip_only_check)
        self.ok_btn = QPushButton("Set species")
        entry_row.addWidget(self.ok_btn)
        layout.addLayout(entry_row)
        self._attach_completer()

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
        self._qual_header = QLabel("SEX / AGE / PLUMAGE — OPTIONAL")
        self._qual_header.setStyleSheet(_MICRO)
        layout.addWidget(self._qual_header)
        self._qual_buttons: dict[int, list[tuple[QPushButton, str]]] = {}
        self._qual_rows: dict[int, QWidget] = {}
        for gi, (cat, opts) in enumerate(QUALIFIER_GROUPS):
            # Each group in its own widget, so a whole row can be hidden when it does not
            # apply to the taxon (no plumage on a plant).
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
                b.setStyleSheet(QUAL_BTN_QSS)
                b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                b.clicked.connect(
                    lambda _checked, g=gi, bb=b: self._on_qualifier_clicked(g, bb))
                qrow.addWidget(b)
                self._qual_buttons[gi].append((b, canon))
            self._qual_rows[gi] = holder
            layout.addWidget(holder)

        # Recent species — left-click re-applies a name in one go; right-click
        # removes a name from the list (e.g. an untested one typed by mistake).
        # Built into a container so it can be rebuilt in place after a removal.
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

        self.field.textChanged.connect(lambda: self._resolve(use_gbif=False))
        self.field.returnPressed.connect(self._on_ok)
        self.gbif_btn.clicked.connect(lambda: self._resolve(use_gbif=True))
        self.ok_btn.clicked.connect(self._on_ok)

        self._is_known = False   # does the current field text resolve to a real species?
        self._resolve(use_gbif=False)
        # In the modal dialog we want to type at once; embedded (large-viewer
        # panel) we must NOT grab focus — the arrow keys drive picture navigation,
        # and a focused field would swallow them on every rebuild.
        if not embedded:
            self.field.setFocus()

    def _attach_completer(self):
        """(Re)build the field's contains-match auto-complete popup — the full
        NL/EN/Sci reference collection, or (checkbox on) just the species
        already known on this trip. No-op when no species collection loaded."""
        sc = self._sc
        if sc is None:
            self._completer = None
            return
        names = trip_species_names() if self._trip_only_check.isChecked() \
            else species_name_candidates(sc)
        completer = QCompleter(names, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.field.setCompleter(completer)
        self._completer = completer

    def _on_trip_only_toggled(self, checked: bool):
        set_setting("Species", "completer_trip_only", "yes" if checked else "no")
        self._attach_completer()

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
            label = recent_display_label(self._sc, name)
            b = QPushButton(label)
            b.setStyleSheet(_RECENT_CHIP)
            tip = f"Click to set “{name}”; right-click to remove from recents"
            if label != name:
                tip = f"Click to set “{name}” (shown as “{label}”); right-click to remove"
            b.setToolTip(tip)
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
        one of them and the picker would go on offering breeding plumage on a buttercup.

        On a MISS we hand taxon_lineage the TYPED name rather than "", so it can still
        reach the taxon through a congener. An unknown species in a known genus is the
        common case on a trip — every new orchid and anemone starts life that way."""
        sci = self._resolve_name(use_gbif)
        self._update_qualifier_visibility(sci or self.field.text().strip().lower())

    def _resolve_name(self, use_gbif: bool) -> str:
        """Resolve the typed name against the species collection and report how it went.
        Local-only on each keystroke; GBIF only on explicit request (it blocks).
        Returns the resolved scientific name, or "" when it resolves to nothing."""
        text = self.field.text().strip()
        if not text:
            self.feedback.setText("")
            self._is_known = False
            return ""
        # 'landscape' / 'landschap' is a DELIBERATE no-species marker (a blog
        # extra: a landscape / context photo titled by its Headline). It resolves
        # to no taxon by design, so it must not be reported as an unrecognised
        # name — and must not trip the unknown-species guard in _on_ok. Same
        # carve-out the pipeline's species editor already makes.
        if SpeciesClassification.isBlogExtra(text):
            self.feedback.setText(
                "🌄 <b>landscape / context photo</b> — no species; the picture is "
                "titled by its Headline")
            self._is_known = True
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
                # Reached only when GBIF's match was too weak even for the
                # fuzzy=True add above (confidence < 90) — genuinely too
                # uncertain to auto-correct, not a failure on our end.
                self.feedback.setText(
                    f"✓ GBIF knows <b>{gname}</b> — type it exactly to add it "
                    f"to your species list (stored as typed for now)")
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
            # fuzzy=True: this is a user-confirmed GBIF hit (the caller already
            # showed "found in GBIF"), not a batch guess, so a confidently
            # corrected typo (FUZZY match, confidence >= 90 — see
            # getKnownSpecies' GUI branch) should add, same as species_editor.py
            # already does for its own species field. Without this, a name GBIF
            # only fuzzy-matched (the common case for a typo) always failed to
            # add here even though the check above had just confirmed it.
            sci = sc.getSpecies(name, fuzzy=True)  # local-then-GBIF; addSpecies() on a GBIF hit
        except Exception as e:
            logger.warning(f"GBIF add failed for '{name}': {e}")
            return None
        if not sci:
            return None
        self._attach_completer()   # refresh so the just-learned name matches too
        # Saved here, but as an APPEND of just this species and its missing
        # ancestors — not the whole 2 MB rewrite this used to do per species,
        # in Dropbox. Naming a species the collection did not know is a
        # decision the user made once, not a keystroke, so the cost is bounded
        # by the number of NEW species in a session.
        #
        # Saved now rather than at viewer close because a pipeline step run
        # while the viewer is still open would otherwise read a collection
        # without it and look it up in GBIF again — and two lookups can pick
        # two different vernacular names, leaving trip and master disagreeing.
        ok, msg = flush_learned_species(sc)
        if ok:
            logger.info(f"Learned and saved '{sci}'.")
        else:
            logger.warning(f"Learned '{sci}' but could not save it yet: {msg}")
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
            box = QMessageBox(QMessageBox.Icon.Question, "Unknown species name",
                f"“{name}” isn’t in your species list and hasn’t been confirmed "
                f"online.\n\n"
                f"Yes — store it as typed.\n"
                f"No — go back and correct the name (or use “Check online (GBIF)”).\n"
                f"Local name — it is another name for a species you have; "
                f"remember it.\n"
                f"Cancel — close without setting a species.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel, self)
            local_btn = box.addButton("Local name…", QMessageBox.ButtonRole.ActionRole)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            box.exec()
            if box.clickedButton() is local_btn:
                self._learn_local_name(name)
                return
            resp = box.standardButton(box.clickedButton())
            if resp == QMessageBox.StandardButton.Cancel:
                self.cancelled.emit()
                return
            if resp != QMessageBox.StandardButton.Yes:   # No → fix the name, stay open
                self.field.setFocus()
                self.field.selectAll()
                return
        self._commit(name)

    def _learn_local_name(self, name: str):
        """"Local name…": ask which species `name` means, keep it as an alias,
        and set the pictures to that species. Cancel leaves the field as typed."""
        dlg = LocalNameDialog(self._sc, name, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self.field.setFocus()
            return
        ok, msg = remember_local_name(self._sc, name, dlg.sci)
        if not ok:
            QMessageBox.warning(self, "Name not kept", msg)
            self.field.setFocus()
            return
        self._attach_completer()
        self.field.setText(dlg.chosen)
        self._commit(dlg.chosen)

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
        """Emit `name` as the chosen species (from the field or a recent button),
        appending any selected qualifiers, and bump the species (without
        qualifiers) to the front of the recents list. The host decides which
        pictures the keyword is written to."""
        name = (name or "").strip()
        if not name:
            return
        subject = build_species_subject(name, self._selected_qualifiers())
        remember_recent_species(name)
        self.committed.emit(subject)


class CullingSpeciesDialog(QDialog):
    """Modal wrapper around CullingSpeciesWidget for one-shot species assignment
    (the 's' key / grid context menu). Keeps the exec() + chosen_name() API its
    callers rely on; the inline large-viewer editor uses the widget directly."""

    def __init__(self, sc, current_name: str, n_pictures: int, parent=None,
                 *, n_landscape: int = 0, preset_summary: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Set species")
        self.setMinimumWidth(560)
        self._chosen = ""

        layout = QVBoxLayout(self)
        self.panel = CullingSpeciesWidget(sc, current_name, n_pictures, self,
                                          n_landscape=n_landscape,
                                          preset_summary=preset_summary)
        self.panel.committed.connect(self._on_committed)
        self.panel.cancelled.connect(self.reject)
        layout.addWidget(self.panel)

        # Close only — "Set species" lives next to the field. Named "Close" (not
        # "Cancel"): any recents you removed are already gone, so it doesn't undo
        # them — it just leaves without assigning a species.
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.close_btn = QPushButton("Close")
        self.close_btn.setToolTip(
            "Leave without setting a species (removed recents stay removed)")
        self.close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

    def _on_committed(self, subject: str):
        self._chosen = subject
        self.accept()

    def chosen_name(self) -> str:
        return self._chosen
