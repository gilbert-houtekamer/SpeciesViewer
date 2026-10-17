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
import re
import shutil
import sys
from datetime import datetime

from engine import topics

logger = logging.getLogger("speciesviewer")

BASELINE_NAME = "master_species_baseline.csv"


def baseline_path(trip_repo):
    """Where this trip's baseline lives (whether or not it exists yet)."""
    return os.path.join(trip_repo, "metadata", "initialize", BASELINE_NAME)


def master_species_csv(master_repo):
    return os.path.join(master_repo, "metadata", "active", "species.csv")


def collect_species_from_csv(path):
    """The SPECIES-level names that actually have pictures, from any species.csv
    in the master's format — the live one or a dated snapshot of it.

    Returns (names, zero_picture_rows, topic_rows). Rows whose picture counter
    parses to 0 are left out: a species row can outlive its last picture, and
    counting it as "known" would silently hide a genuine lifer. A row with no
    usable counter column (older masters) is kept — a visible false "already
    known" is the lesser evil there, and the skipped count goes into the file
    header so the number is never invisible.

    Landscapes/topics are dropped. They are stored as Level.SPECIES rows
    (`2025`, `2026` under topics/landscape/) and so look exactly like species
    here, but they are years, not organisms, and have no business in a list of
    "species I have already photographed".
    """
    names = set()
    zero_picture_rows = 0
    topic_rows = 0
    with open(path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    # is_topic_node walks the Up chain, so it needs every row, not just species.
    records = {(r.get("ScientificName") or "").strip(): {"Up": (r.get("Up") or "").strip()}
               for r in rows}
    for row in rows:
        if (row.get("Level") or "").strip() != "Level.SPECIES":
            continue
        raw_name = (row.get("ScientificName") or "").strip()
        name = raw_name.lower()
        if not name:
            continue
        if topics.is_topic_node(records, raw_name):
            topic_rows += 1
            continue
        raw_count = row.get("iPictureCollectionCount")
        if raw_count is not None and str(raw_count).strip().isdigit():
            if int(str(raw_count).strip()) == 0:
                zero_picture_rows += 1
                continue
        names.add(name)
    return names, zero_picture_rows, topic_rows


def collect_master_species(master_repo):
    """The live master's species — see collect_species_from_csv."""
    return collect_species_from_csv(master_species_csv(master_repo))


# ----------------------------------------------------------------------
# The date rule — the baseline that does not need freezing.
#
# "Already known" = the master holds a picture of it taken BEFORE the trip
# started. Derived from picture dates, so it does not care when a trip was
# set up or in what order trips were merged: a species shot on an earlier
# trip counts as known even if that trip reaches the master months later.
# The trip's own pictures fall inside its date range, so they never enter
# its own baseline and the answer survives the trip's own merge.
#
# This replaced an earlier rule that froze the master's species list at trip
# creation. That rule reported a shield bug as new on a May trip because the
# March trip carrying it was merged after the May trip's repo was created.
# ----------------------------------------------------------------------
def master_pictures_csv(master_repo):
    return os.path.join(master_repo, "metadata", "active", "data_pictures.csv")


def collect_species_before(master_repo, before_date, sc_records=None):
    """Species the master has a picture of from before `before_date`
    ('YYYY-MM-DD'). Returns (names, topic_rows), or (None, 0) when the picture
    index cannot be read — the caller must then fall back, never treat an
    unreadable index as "nothing was known".
    """
    path = master_pictures_csv(master_repo)
    day = (before_date or "")[:10].replace("-", ":")
    if not re.match(r"^\d{4}:\d{2}:\d{2}$", day) or not os.path.exists(path):
        return None, 0
    names = set()
    topic_rows = 0
    try:
        csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter=";"):
                name = (row.get("WetSoortNaam") or "").strip()
                taken = (row.get("DateTimeOriginal") or "")[:10]
                if not name or not taken or taken >= day:
                    continue
                if sc_records and topics.is_topic_node(sc_records, name):
                    topic_rows += 1
                    continue
                names.add(name.lower())
    except Exception as e:
        logger.warning(f"Species baseline: could not read {path}: {e}")
        return None, 0
    return names, topic_rows


# ----------------------------------------------------------------------
# Reconstructing a baseline for a trip that predates this feature.
#
# sv7WriteCommit copies the master's active/species.csv to
# active/backup/<date>_species.csv BEFORE overwriting it, and only for the
# first commit of that day. So a snapshot dated D holds the master as it was
# up to the first commit on D — which makes the master's state at time T the
# snapshot of the EARLIEST commit day after T, not the latest one before it.
#
# Getting that backwards matters. Too new a baseline hides real lifers
# silently; too old only invents visible false ones. This rule picks the one
# that is actually correct, and says so when it cannot.
# ----------------------------------------------------------------------
_SNAPSHOT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_species\.csv$")


def snapshot_dir(master_repo):
    return os.path.join(master_repo, "metadata", "active", "backup")


def snapshots(master_repo):
    """[(date_string, path), ...] oldest first."""
    d = snapshot_dir(master_repo)
    if not os.path.isdir(d):
        return []
    found = []
    for name in os.listdir(d):
        m = _SNAPSHOT_RE.match(name)
        if m:
            found.append((m.group(1), os.path.join(d, name)))
    return sorted(found)


def source_for_date(master_repo, created):
    """Which species.csv shows the master as it stood on `created`
    (a 'YYYY-MM-DD' or longer date string).

    Returns (path, snapshot_date, exactness) where exactness is:
      'same-day'  — a snapshot exists from `created` itself. A snapshot dated D
                    predates the first commit ON D, and we cannot know whether
                    that commit came before or after the trip was created, so
                    this is either exact or slightly OLD. We take it anyway:
                    see the asymmetry below.
      'snapshot'  — the earliest snapshot after `created`. It predates every
                    commit that followed, so it is the master as the trip found
                    it, up to the nearest commit.
      'current'   — nothing has been committed since, so the live master is
                    still the state at `created`.
    Returns (None, None, None) when there is nothing usable.

    THE ASYMMETRY that decides the same-day case: too OLD a baseline lists
    fewer species than the master really had, so a few already-known species
    show up as new — wrong, but visible and checkable. Too NEW a baseline
    contains species the trip itself contributed and hides real lifers with no
    trace. So when in doubt, choose the older candidate.

    CAVEAT the caller must not lose: only sv7 commits leave dated snapshots.
    Master edits made through the database (Add Pictures to Master, deletes,
    move-to-species) change species.csv without one, so a reconstruction is
    good to the nearest commit, not to the minute.
    """
    day = (created or "")[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        return None, None, None
    all_snaps = snapshots(master_repo)
    same = [(d, p) for d, p in all_snaps if d == day]
    if same:
        return same[0][1], same[0][0], "same-day"
    after = [(d, p) for d, p in all_snaps if d > day]
    if after:
        return after[0][1], after[0][0], "snapshot"
    live = master_species_csv(master_repo)
    if os.path.exists(live):
        return live, None, "current"
    return None, None, None


def _master_has_no_pictures(master_repo) -> bool:
    """True only when the master's picture list exists and has no rows — a
    brand-new master. Missing or unreadable is NOT "empty"."""
    path = os.path.join(master_repo, "metadata", "active", "data_pictures.csv")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip()) <= 1    # header only
    except OSError:
        return False


def write_baseline(master_repo, trip_repo, source_csv=None, taken_at=None,
                   reconstructed_from=""):
    """Freeze the master's species list into the trip. Returns the path written,
    or None when nothing was written (and logs why).

    `source_csv` overrides which species.csv to read (a dated snapshot, when
    backfilling a trip created before this feature existed); `taken_at` records
    the moment the baseline is meant to represent rather than now.

    Refuses to write an EMPTY baseline. A zero-row baseline is worse than no
    baseline at all: it would silently promote every species in the trip to
    "new". Falling back to the live master is wrong-but-explainable; a bogus
    frozen file would be wrong forever.
    """
    src = source_csv or master_species_csv(master_repo)
    if not os.path.exists(src):
        logger.warning(f"Species baseline: master species list not found: {src}. "
                       f"Count Species will fall back to the live master.")
        return None

    try:
        names, zero_picture_rows, topic_rows = collect_species_from_csv(src)
    except Exception as e:
        logger.warning(f"Species baseline: could not read {src}: {e}. "
                       f"Count Species will fall back to the live master.")
        return None

    if not names:
        if source_csv is None and _master_has_no_pictures(master_repo):
            # A new master really knows no species: every species of this trip
            # IS new, and an empty baseline says exactly that (Gilbert,
            # 2026-10-12). The guard below is for a master WITH pictures whose
            # list reads as empty — a broken read, not an empty master.
            logger.info("Species baseline: the master has no pictures yet, so "
                        "every species of this trip will count as new.")
        else:
            logger.warning(f"Species baseline: {src} yielded 0 species — refusing to "
                           f"write an empty baseline (it would make every species "
                           f"look new). Count Species will fall back to the live master.")
            return None

    target = baseline_path(trip_repo)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write("# SpeciesViewer master species baseline\n")
        f.write(f"# taken_at={taken_at or datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"# master={master_repo}\n")
        f.write(f"# species_count={len(names)}\n")
        f.write(f"# zero_picture_rows_skipped={zero_picture_rows}\n")
        f.write(f"# topic_rows_skipped={topic_rows}\n")
        if reconstructed_from:
            # Backfilled after the fact: say so, and say from what. A
            # reconstructed baseline is good to the nearest master commit,
            # and the report repeats that wording rather than claiming the
            # trip froze it itself.
            f.write(f"# reconstructed_from={reconstructed_from}\n")
        f.write("ScientificName\n")
        for name in sorted(names):
            f.write(name + "\n")
    logger.info(f"Species baseline: froze {len(names)} master species into {target}.")
    return target


def translate_baseline(trip_repo, master_repo):
    """After the master's species list was converted (ch.6), put the trip's frozen
    baseline into the new names: 'bubulcus ibis' -> 'ardea ibis', through the
    master's alias table (speciesnames.csv). NOT re-taken from today's master — by
    now that holds the trip's own species and would hide its lifers. A name that
    has no single new name stays as it is. Returns the number of names changed."""
    path = baseline_path(trip_repo)
    aliases_csv = os.path.join(master_repo, "metadata", "active", "speciesnames.csv")
    if not os.path.exists(path) or not os.path.exists(aliases_csv):
        return 0
    targets = {}
    with open(aliases_csv, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if (row.get("Language") or "") == "sci":
                name = (row.get("Name") or "").strip().lower()
                targets.setdefault(name, set()).add((row.get("PointsTo") or "").strip().lower())
    names, meta = read_baseline(path)
    new_names, changed = set(), 0
    for n in names:
        to = targets.get(n)
        if to and len(to) == 1:
            new_names.add(next(iter(to)))
            changed += 1
        else:
            new_names.add(n)
    if not changed:
        return 0
    shutil.copy2(path, path + ".pre-conversion")
    with open(path, "w", encoding="utf-8", newline="") as f:
        for k, v in meta.items():
            f.write(f"# {k}={v}\n")
        f.write(f"# translated_to_converted_list={datetime.now().strftime('%Y-%m-%d %H:%M')}"
                f" ({changed} names)\n")
        f.write("ScientificName\n")
        for name in sorted(new_names):
            f.write(name + "\n")
    logger.info(f"Species baseline: {changed} names put into the master's new species list.")
    return changed


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
