"""Phase 2 — read-only dry-run scan for the topics/landscape ingest.

Given a trip's already-frozen CSVs (data_pictures.csv + data_files.csv — the
species classifier has already run, so WetSoortNaam=="" is the clean, no-GBIF
signal that a picture resolved to NO species), report every blog-extra
candidate: where it WOULD land in the master (topics/<topic>/<leaf>/), its
authoritative rating+colour read from the .dop (NOT the .xmp — the trap that
silently loses PhotoLab's colour work), whether it has a rendered JPG (a topic
photo with no JPG is authored but cannot render/ingest), and any conflicts.

This writes NOTHING to the master. It is the review artifact that must be read
and confirmed before Phase 3 turns the freeze exclusion into an ingest (see
feedback_review_over_autofix). The per-candidate describe logic is deliberately
independent of the CSV loader so a later backfill scan (loose files, no trip
repo) can reuse it.
"""

import os
import logging
from dataclasses import dataclass

from engine.picturecsv import Pictures
from engine.filecsv import Files
from engine.speciesclassification import SpeciesClassification
from engine.specieslib import Species
from engine import topics, sidecarlib
from utils.raw_extensions import RAW, bare

logger = logging.getLogger("speciesviewer")

_RAW_EXTS = bare(RAW)  # canonical table — see utils/raw_extensions.py

# Candidate status codes.
READY = "ready"                     # resolves to a target and has a JPG
NO_JPG = "no-jpg"                   # resolves, but no rendered JPG yet (won't ingest)
NO_LEAF = "no-leaf"                 # topic matched but no leaf (e.g. missing date)
IN_MASTER = "already-in-master"     # UPN already present in the master DB


@dataclass
class TopicCandidate:
    upn: str
    subject: str
    topic: str            # rule.key, or "" when unresolved
    leaf: str             # leaf key (e.g. year), or ""
    target_path: str      # topics/<topic>/<leaf>/, or ""
    has_jpg: bool
    best_jpg: str
    dop_rating: object    # int | None
    dop_color: object     # str | None
    csv_rate: str         # the picture's computed Rate from the CSV
    in_master: object     # bool | None (None = no master consulted)
    status: str
    note: str = ""


def _raw_path_for(picture, files_dict):
    """Full path of the picture's RAW file (for locating its .dop), or ""."""
    for fname in picture.get("Files", []):
        frow = files_dict.get(fname)
        if frow is None:
            continue
        if frow.get("WindowsExtension", "").lower() in _RAW_EXTS:
            return frow.get("WindowsFileName", "")
    return ""


def _dop_meta_for(picture, files_dict):
    """Authoritative colour/rating from the picture's .dop (RAW's first, then any
    tracked file's). Returns {'color', 'rating'} with None when unavailable."""
    raw = _raw_path_for(picture, files_dict)
    candidates = []
    if raw:
        candidates.append(raw)
    # fall back to any tracked file that has a .dop beside it (e.g. a _DxO.jpg)
    for fname in picture.get("Files", []):
        frow = files_dict.get(fname)
        if frow is None:
            continue
        wfn = frow.get("WindowsFileName", "")
        if wfn and wfn not in candidates:
            candidates.append(wfn)
    for path in candidates:
        dop = sidecarlib.ensure_dop(path)  # returns existing .dop or None
        if dop:
            meta = sidecarlib.read_dop_metadata(dop)
            if meta.get("color") is not None or meta.get("rating") is not None:
                return meta
    return {"color": None, "rating": None}


def describe_candidate(picture, files_dict, sc, sp, registry, master_upns=None):
    """Build a TopicCandidate for one blog-extra picture. Callers must have
    already established this is a blog extra (no species + a topic marker)."""
    upn = picture.get("UniquePictureName", "")
    subject = picture.get("subject", "")
    best_jpg = picture.get("BestJPG", "")
    has_jpg = bool(best_jpg)
    csv_rate = str(picture.get("Rate", ""))

    rule = registry.match(subject)
    topic = rule.key if rule else ""
    leaf_info = topics.resolve_leaf(rule, picture) if rule else None

    target_path = ""
    status = NO_LEAF
    note = ""
    if rule and leaf_info:
        # Inject into a throwaway view of the tree to derive the exact path the
        # ingest would produce — proves the target rather than string-building it.
        try:
            topics.inject_chain(sc, rule, leaf_info)
            sp.getSpecies(leaf_info.key)
            target_path = sp.getPathToSpecies(leaf_info.key)
            status = READY if has_jpg else NO_JPG
        except topics.TopicCollisionError as e:
            status = NO_LEAF
            note = str(e)
    elif rule and not leaf_info:
        note = f"topic '{topic}' matched but no leaf (missing/invalid date)"

    in_master = None
    if master_upns is not None:
        in_master = upn in master_upns
        if in_master:
            status = IN_MASTER

    dop = _dop_meta_for(picture, files_dict)

    return TopicCandidate(
        upn=upn, subject=subject, topic=topic,
        leaf=leaf_info.key if leaf_info else "",
        target_path=target_path, has_jpg=has_jpg, best_jpg=best_jpg,
        dop_rating=dop.get("rating"), dop_color=dop.get("color"),
        csv_rate=csv_rate, in_master=in_master, status=status, note=note,
    )


def scan_trip_csv(pictures_csv, files_csv, master_upns=None, registry=None):
    """Load a trip's CSVs and return (candidates, summary). READ-ONLY.

    master_upns: optional set of UPNs already in the master (to flag conflicts).
    """
    if registry is None:
        registry = topics.TopicRegistry.default()

    pics = Pictures(None)
    pics.read_pictures(pictures_csv)
    files = Files(None)
    files.read_file(files_csv)
    files_dict = files.files_dict

    sc = SpeciesClassification(None)
    sp = Species(None)
    sp.register_sc_with_sp(sc)

    candidates = []
    mixed = 0  # topic keyword present but a species DID resolve -> stays a species
    for picture in pics.UniquePictureName_dict.values():
        subject = picture.get("subject", "")
        if not SpeciesClassification.isBlogExtra(subject):
            continue
        if picture.get("WetSoortNaam", ""):
            # Marker keyword AND a resolved species (e.g. ['robin','landscape']):
            # the pipeline files it as the species, not a topic. Not a candidate.
            mixed += 1
            continue
        candidates.append(
            describe_candidate(picture, files_dict, sc, sp, registry, master_upns))

    summary = {
        "candidates": len(candidates),
        "ready": sum(1 for c in candidates if c.status == READY),
        "no_jpg": sum(1 for c in candidates if c.status == NO_JPG),
        "no_leaf": sum(1 for c in candidates if c.status == NO_LEAF),
        "in_master": sum(1 for c in candidates if c.status == IN_MASTER),
        "mixed_kept_as_species": mixed,
        "pictures_scanned": len(pics.UniquePictureName_dict),
    }
    return candidates, summary


def format_report(candidates, summary):
    """Human-readable dry-run report."""
    lines = []
    lines.append("=== Topics / landscape ingest - DRY RUN (writes nothing) ===")
    lines.append(
        f"{summary['pictures_scanned']} pictures scanned; "
        f"{summary['candidates']} topic candidate(s): "
        f"{summary['ready']} ready, {summary['no_jpg']} no-JPG, "
        f"{summary['no_leaf']} no-leaf, {summary['in_master']} already-in-master; "
        f"{summary['mixed_kept_as_species']} marked-landscape-but-a-species (kept as species).")
    lines.append("")
    for c in candidates:
        rating = "-" if c.dop_rating is None else c.dop_rating
        color = "-" if c.dop_color in (None, "") else c.dop_color
        target = c.target_path or "(unresolved)"
        jpg_note = ("yes - " + c.best_jpg) if c.has_jpg else \
            "NONE (won't render/ingest until PhotoLab makes one)"
        lines.append(f"[{c.status:<18}] {c.upn}")
        lines.append(f"    subject : {c.subject}")
        lines.append(f"    target  : {target}")
        lines.append(f"    .dop    : rating={rating}  colour={color}   (csv Rate={c.csv_rate})")
        lines.append(f"    jpg     : {jpg_note}")
        if c.note:
            lines.append(f"    note    : {c.note}")
        lines.append("")
    return "\n".join(lines)
