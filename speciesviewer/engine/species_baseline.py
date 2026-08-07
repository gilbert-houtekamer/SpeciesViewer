"""The trip's frozen view of what the master already had pictures of.

ONE JOB: capture, at trip creation, the set of species the master could already
show — so that "is this a lifer?" keeps the answer it had when the trip started,
however often the master is updated afterwards.

INVARIANT: the baseline is written ONCE, by sv1MakeReposity, and never touched
again. It lives in <trip>/metadata/initialize/ because that directory is the
"how this trip started" folder and nothing else writes there — in particular
Reset Trip (engine/tripresetlib.py) clears metadata/review/ and rewrites
metadata/active/, but leaves initialize/ alone.

Without it, svr2CountSpecies compares the trip against the master's LIVE
species.csv, so the moment the trip's own pictures are merged into the master
its species stop counting as new and the lifer verdict silently becomes zero.

File format — comment lines carry the provenance, then a plain one-column CSV:

    # SpeciesViewer master species baseline
    # taken_at=2026-08-07 14:22
    # master=E:\\Dropbox\\naturedb
    # species_count=2258
    # zero_picture_rows_skipped=0
    ScientificName
    neriene montana
    ...
"""

import csv
import logging
import os
from datetime import datetime

logger = logging.getLogger("speciesviewer")

BASELINE_NAME = "master_species_baseline.csv"


def baseline_path(trip_repo):
    """Where this trip's baseline lives (whether or not it exists yet)."""
    return os.path.join(trip_repo, "metadata", "initialize", BASELINE_NAME)


def master_species_csv(master_repo):
    return os.path.join(master_repo, "metadata", "active", "species.csv")


def collect_master_species(master_repo):
    """The master's SPECIES-level names that actually have pictures.

    Returns (names, zero_picture_rows). Rows whose picture counter parses to 0
    are left out: a species row can outlive its last picture, and counting it as
    "known" would silently hide a genuine lifer. A row with no usable counter
    column (older masters) is kept — a visible false "already known" is the
    lesser evil there, and the skipped count goes into the file header so the
    number is never invisible.
    """
    names = set()
    zero_picture_rows = 0
    path = master_species_csv(master_repo)
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if (row.get("Level") or "").strip() != "Level.SPECIES":
                continue
            name = (row.get("ScientificName") or "").strip().lower()
            if not name:
                continue
            raw_count = row.get("iPictureCollectionCount")
            if raw_count is not None and str(raw_count).strip().isdigit():
                if int(str(raw_count).strip()) == 0:
                    zero_picture_rows += 1
                    continue
            names.add(name)
    return names, zero_picture_rows


def write_baseline(master_repo, trip_repo):
    """Freeze the master's species list into the trip. Returns the path written,
    or None when nothing was written (and logs why).

    Refuses to write an EMPTY baseline. A zero-row baseline is worse than no
    baseline at all: it would silently promote every species in the trip to
    "new". Falling back to the live master is wrong-but-explainable; a bogus
    frozen file would be wrong forever.
    """
    src = master_species_csv(master_repo)
    if not os.path.exists(src):
        logger.warning(f"Species baseline: master species list not found: {src}. "
                       f"Count Species will fall back to the live master.")
        return None

    try:
        names, zero_picture_rows = collect_master_species(master_repo)
    except Exception as e:
        logger.warning(f"Species baseline: could not read {src}: {e}. "
                       f"Count Species will fall back to the live master.")
        return None

    if not names:
        logger.warning(f"Species baseline: {src} yielded 0 species — refusing to "
                       f"write an empty baseline (it would make every species "
                       f"look new). Count Species will fall back to the live master.")
        return None

    target = baseline_path(trip_repo)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write("# SpeciesViewer master species baseline\n")
        f.write(f"# taken_at={datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"# master={master_repo}\n")
        f.write(f"# species_count={len(names)}\n")
        f.write(f"# zero_picture_rows_skipped={zero_picture_rows}\n")
        f.write("ScientificName\n")
        for name in sorted(names):
            f.write(name + "\n")
    logger.info(f"Species baseline: froze {len(names)} master species into {target}.")
    return target


def read_baseline(path):
    """Returns (names, meta). `meta` holds the '# key=value' provenance lines."""
    names = set()
    meta = {}
    data_lines = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                key, sep, value = line[1:].strip().partition("=")
                if sep:
                    meta[key.strip()] = value.strip()
            else:
                data_lines.append(line)
    for row in csv.DictReader(data_lines, delimiter=";"):
        name = (row.get("ScientificName") or "").strip().lower()
        if name:
            names.add(name)
    return names, meta
