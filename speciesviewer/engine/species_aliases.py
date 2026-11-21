"""The alias table, speciesnames.csv (ch.6): other names that point at a species.

ONE JOB: add a name to the table, remove one, and merge two tables, without ever
losing a row.

INVARIANTS
  - An alias points at a SCIENTIFIC name in the species list, never at another
    common name, so no chains form.
  - A name that is already a species, or already means another species, is
    refused — better unresolved than filed under the wrong bird.
  - A merge is a UNION: a row is never dropped. Two rows that give one name to
    two species may both land; the reader (speciesclassification._readAliases)
    then refuses that name, which is the safe answer.
  - REMOVING a name never deletes its row: it gets a Removed date, and every
    reader skips it. Only that way can a removal survive the union merges — a
    deleted row would come back from the next trip or laptop table (backlog 1.1).
    Removed and Changed are the last two columns (new columns go at the END).
    In a merge, of two copies of one row the one with the later Changed wins, so
    a removal travels, and so does adding the name back afterwards.

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
FIELDS = ["Name", "Language", "PointsTo", "Kind", "Source", "Removed", "Changed"]


def _now():
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def is_removed(row) -> bool:
    return bool((row.get("Removed") or "").strip())


def _with_all_fields(header):
    """The file's own header, with any of FIELDS it lacks appended at the end."""
    return list(header) + [f for f in FIELDS if f not in header]


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
    header = _with_all_fields(header)
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
           "PointsTo": sci.strip().lower(), "Kind": kind, "Source": source,
           "Removed": "", "Changed": _now()}
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
        same = [r for r in rows if _key(r) == _key(row)]
        if same and not any(is_removed(r) for r in same):
            continue
        if same:                          # removed before: bring it back
            for r in same:
                r["Removed"], r["Changed"] = "", row["Changed"]
            _write_aliases(path, header, rows)
        else:
            _write_aliases(path, header, rows + [row])
        written.append(path)
    if written:
        logger.info(f"Remembered “{row['Name']}” as a name for {row['PointsTo']} "
                    f"in {len(written)} alias table(s).")
    return written


def remove_aliases(paths, keys) -> int:
    """Mark the rows with these (name, language, points-to) keys as removed in
    each table of `paths` (master first). The rows stay — see the invariant.
    Returns how many rows were marked in the first table. Raises on a write
    failure; the caller tells the user."""
    keys = {tuple((x or "").strip().lower() for x in k) for k in keys}
    stamp = _now()
    first = None
    seen = set()
    for path in paths:
        if not path or not os.path.isfile(path):
            continue
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen:
            continue
        seen.add(norm)
        header, rows = read_aliases(path)
        n = 0
        for r in rows:
            if _key(r) in keys and not is_removed(r):
                r["Removed"], r["Changed"] = stamp[:10], stamp
                n += 1
        if n:
            _write_aliases(path, header, rows)
        if first is None:
            first = n
    if first:
        logger.info(f"Removed {first} other name(s) from the species names table.")
    return first or 0


def merge_alias_files(src, dest):
    """Fold `src` into `dest`: every row `dest` lacks is added (a union), and of
    a row both hold, the copy with the later Changed wins — that is how a
    removal, or a name added back, travels. Returns the number of rows added or
    changed. A missing `src` adds nothing; a missing `dest` becomes a copy."""
    _, src_rows = read_aliases(src)
    if not src_rows:
        return 0
    header, dest_rows = read_aliases(dest)
    at = {_key(r): r for r in dest_rows}
    new, changed = [], 0
    for r in src_rows:
        k = _key(r)
        if not (k[0] and k[2]):
            continue
        mine = at.get(k)
        if mine is None:
            at[k] = r
            new.append(r)
        elif (r.get("Changed") or "") > (mine.get("Changed") or "") \
                and is_removed(r) != is_removed(mine):
            mine["Removed"], mine["Changed"] = r.get("Removed") or "", r.get("Changed")
            changed += 1
    if new or changed:
        _write_aliases(dest, header, dest_rows + new)
        logger.info(f"Merged {len(new) + changed} name(s) into {dest}.")
    return len(new) + changed


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
