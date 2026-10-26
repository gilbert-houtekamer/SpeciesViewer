"""The alias table, speciesnames.csv (ch.6): other names that point at a species.

ONE JOB: add a name to the table, and merge two tables, without ever losing a row.

INVARIANTS
  - An alias points at a SCIENTIFIC name in the species list, never at another
    common name, so no chains form.
  - A name that is already a species, or already means another species, is
    refused — better unresolved than filed under the wrong bird.
  - A merge is a UNION: a row is never dropped or changed. Two rows that give
    one name to two species may both land; the reader (speciesclassification.
    _readAliases) then refuses that name, which is the safe answer.

Who calls what: the species step adds a local name a guide used (to the master
AND the trip, at once); svr5 merges a trip's table into its master; the trip
export/import ZIP and Install Small Master merge a laptop's table back in.
"""

import csv
import logging
import os
import tempfile

logger = logging.getLogger("speciesviewer")

ALIAS_FILE = "speciesnames.csv"
FIELDS = ["Name", "Language", "PointsTo", "Kind", "Source"]


def alias_path(repo_root):
    """speciesnames.csv of a master, trip or album."""
    return os.path.join(repo_root, "metadata", "active", ALIAS_FILE)


def _key(row):
    return tuple((row.get(f) or "").strip().lower() for f in ("Name", "Language", "PointsTo"))


def read_aliases(path):
    """(header, rows) of a table; (FIELDS, []) when it does not exist."""
    if not path or not os.path.isfile(path):
        return list(FIELDS), []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        rows = list(reader)
        header = list(reader.fieldnames or FIELDS)
    return header, rows


def _write_aliases(path, header, rows):
    """Write via a temp file in the same folder, so a failure never leaves a
    half-written table behind."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".csv", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=header, delimiter=";",
                                    restval="", extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def alias_problem(sc, name, sci):
    """Why `name` cannot become an alias of `sci` in this species list, or "".
    `sc` is a loaded SpeciesClassification."""
    name = (name or "").strip().lower()
    sci = (sci or "").strip().lower()
    if not name:
        return "There is no name to remember."
    if sci not in sc.sc_dict:
        return f"“{sci}” is not in your species list."
    if name == sci:
        return ""
    if name in sc.sc_dict:
        return f"“{name}” is itself a name in your species list."
    for index in (sc.dutch_dict, sc.english_dict, getattr(sc, "alias_dict", {})):
        other = (index.get(name) or "").strip().lower()
        if other and other != sci:
            return f"“{name}” already means {other}."
    if name in getattr(sc, "contested", set()):
        return f"“{name}” is an old name that a split gave to another species."
    return ""


def add_alias(paths, name, sci, *, language="local", kind="local name", source=""):
    """Append one alias row to each table in `paths` (master first), skipping a
    table that already has it. Returns the paths actually written. Raises on a
    write failure — the caller tells the user."""
    row = {"Name": name.strip().lower(), "Language": language,
           "PointsTo": sci.strip().lower(), "Kind": kind, "Source": source}
    written = []
    seen = set()
    for path in paths:
        if not path:
            continue
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen:
            continue
        seen.add(norm)
        header, rows = read_aliases(path)
        if any(_key(r) == _key(row) for r in rows):
            continue
        _write_aliases(path, header, rows + [row])
        written.append(path)
    if written:
        logger.info(f"Remembered “{row['Name']}” as a name for {row['PointsTo']} "
                    f"in {len(written)} alias table(s).")
    return written


def merge_alias_files(src, dest):
    """Fold every row of `src` that `dest` lacks into `dest` (a union; nothing
    in `dest` changes). Returns the number of rows added. A missing `src` adds
    nothing; a missing `dest` becomes a copy of `src`."""
    _, src_rows = read_aliases(src)
    if not src_rows:
        return 0
    header, dest_rows = read_aliases(dest)
    have = {_key(r) for r in dest_rows}
    new = []
    for r in src_rows:
        k = _key(r)
        if k[0] and k[2] and k not in have:
            have.add(k)
            new.append(r)
    if new:
        _write_aliases(dest, header, dest_rows + new)
        logger.info(f"Merged {len(new)} name(s) into {dest}.")
    return len(new)


def merge_alias_text(text, dest):
    """merge_alias_files for a table held in memory (a ZIP member, a backup)."""
    if not text:
        return 0
    fd, tmp = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return merge_alias_files(tmp, dest)
    finally:
        os.unlink(tmp)
