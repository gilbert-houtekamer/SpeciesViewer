"""Shared helpers for reading and enriching an AviBase/BirdingBreaks species list."""

import csv
import os
import re
import logging

logger = logging.getLogger("speciesviewer")

DEFAULT_SOURCE_LIST = "avibase.csv"

# What a column in a Birding Breaks list can hold. Anything not marked is
# ignored — that is how the day boxes and spacer columns stay out of the way.
ROLE_KEYS = ("seq", "en", "nl", "sc", "st")
ROLE_LABELS = {
    "seq": "sequence number",
    "en":  "English name",
    "nl":  "Dutch name",
    "sc":  "scientific name",
    "st":  "status or note",
}


def resolve_source_list(triproot, configured=""):
    """Full path of the species list to read for this trip.

    A list picked in the dialog wins; without one, the historical default
    `avibase.csv` in the trip's own data folder. The path is returned whether
    or not it exists, so the caller can name it in an error message.
    """
    configured = (configured or "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return os.path.join(triproot, DEFAULT_SOURCE_LIST)


def read_avibase(triproot, filename):
    """Read AviBase CSV and return a dict keyed by enname (column 0).

    CSV columns: English/key ; Scientific ; Dutch ; Status
    Section-header rows (no scientific name) get number=-1.
    """
    file_path = os.path.join(triproot, filename)
    number = 1
    avibase_dict = {}

    if not os.path.isfile(file_path):
        # Named in full, because the folder the list is expected in is not the
        # folder the pictures live in — that mix-up is the usual cause.
        raise FileNotFoundError(
            f"No species list found at '{file_path}'. Use Browse… to pick the "
            f"list, or put {DEFAULT_SOURCE_LIST} in the trip data folder "
            f"'{triproot}'."
        )

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f.read().split("\n"):
            tokens = line.split(";")
            try:
                if tokens[0] in ("﻿Column1", "﻿Engels", ""):
                    continue
                if tokens[0].strip() == "":
                    continue
                if len(tokens) > 1 and tokens[1] not in ("Wet", ""):
                    sp = {
                        "enname":       tokens[0],
                        "scname":       tokens[1],
                        "nlname":       tokens[2] if len(tokens) > 2 else "",
                        "status":       tokens[3] if len(tokens) > 3 else "",
                        "english":      "",
                        "number":       number,
                        "gezien":       0,
                        "foto":         0,
                        "knownspecies": False,
                    }
                    number += 1
                    avibase_dict[sp["enname"]] = sp
                elif len(tokens) > 1 and tokens[1] == "Wet":
                    sp = {
                        "enname":       tokens[0],
                        "scname":       tokens[1],
                        "nlname":       tokens[2] if len(tokens) > 2 else "",
                        "status":       tokens[3] if len(tokens) > 3 else "",
                        "english":      "",
                        "number":       "Wet",
                        "gezien":       0,
                        "foto":         0,
                        "knownspecies": False,
                    }
                    avibase_dict[sp["enname"]] = sp
                else:
                    # Section header row — no scientific name
                    avibase_dict[tokens[0]] = {
                        "enname":  tokens[0],
                        "scname":  "",
                        "nlname":  "",
                        "status":  "",
                        "english": "",
                        "number":  -1,
                    }
            except Exception:
                pass

    if not any(entry.get("number") != -1 for entry in avibase_dict.values()):
        # Every line became a section heading, which means the file is not
        # semicolon-separated at all. Saying so beats writing a checklist that
        # holds nothing but grey group rows.
        found = sniff_delimiter(open(file_path, encoding="utf-8-sig",
                                     errors="replace").read().splitlines()[:50])
        named = {"\t": "tabs", ";": "semicolons", ",": "commas",
                 "|": "bars"}.get(found, found)
        raise ValueError(
            f"No species found in '{os.path.basename(file_path)}' with the fixed "
            f"AviBase layout, which expects semicolons between the names. This "
            f"file is separated by {named}. Choose “Columns I marked "
            f"myself” and mark the columns instead."
        )

    return avibase_dict


def add_species_info(avibase_dict, sc, sp):
    """Enrich avibase_dict entries with canonical scientific names and English names.

    sc  — SpeciesClassification instance (has english_dict, dutch_dict, sc_dict)
    sp  — Species instance (has species_dict for known-species check)
    Mutates avibase_dict in place.
    """
    for enname, entry in avibase_dict.items():
        if not entry.get("scname"):
            continue

        original_scname = entry["scname"]

        if enname.lower() in sc.english_dict:
            entry["scname"] = sc.english_dict[enname.lower()]
            if entry["scname"] in sp.species_dict:
                entry["knownspecies"] = True
        else:
            if not entry["nlname"] and entry["scname"].lower() in sc.sc_dict:
                entry["nlname"] = sc.sc_dict[entry["scname"].lower()].get("DutchName", "")
            if entry["nlname"].lower() in sc.dutch_dict:
                entry["scname"] = sc.dutch_dict[entry["nlname"].lower()]
                if entry["scname"] in sp.species_dict:
                    entry["knownspecies"] = True

        # Fill English name from classification when missing
        if not entry.get("english"):
            sc_entry = sc.sc_dict.get(entry["scname"].lower())
            if sc_entry and sc_entry.get("EnglishName"):
                entry["english"] = sc_entry["EnglishName"]

        if (original_scname and entry["scname"] and
                entry["scname"].lower() != original_scname.lower()):
            label = entry.get("nlname") or entry.get("english") or enname
            logger.info(
                f"Scientific name updated for '{label}': "
                f"'{original_scname}' → '{entry['scname']}'"
            )

        if entry["nlname"] == '':
            sc_entry = sc.sc_dict.get(entry["scname"].lower())
            if sc_entry and sc_entry.get("DutchName"):
                entry["nlname"] = sc_entry["DutchName"]



# ── Birding Breaks lists ────────────────────────────────────────────────────
# What arrives from the tour operator is a spreadsheet with title rows above the
# real header and a box per trip day beside each species. Rather than insist on
# one shape, the user marks what each column holds (gui/avibase_columns.py) and
# the reader below follows that map.

def format_roles(roles):
    """Column map as one config line: "0=seq,2=en,3=sc"."""
    return ",".join(f"{col}={role}"
                    for col, role in sorted((roles or {}).items())
                    if role in ROLE_KEYS)


def parse_roles(spec):
    """Column map back from its config line. Unreadable entries are dropped."""
    roles = {}
    for part in (spec or "").split(","):
        if "=" not in part:
            continue
        col, _, role = part.partition("=")
        role = role.strip()
        try:
            col = int(col.strip())
        except ValueError:
            continue
        if role in ROLE_KEYS:
            roles[col] = role
    return roles


def encode_delimiter(delimiter):
    """A delimiter as a config-safe word. An .ini value cannot carry a bare tab
    — it would be written out and read back as nothing at all."""
    if not delimiter:
        return ""
    return "tab" if delimiter == "\t" else delimiter


def decode_delimiter(text):
    """The delimiter back from config. Empty means: work it out from the file."""
    text = (text or "").strip()
    if not text:
        return None
    return "\t" if text.lower() == "tab" else text[0]


def sniff_delimiter(sample_lines):
    """Which delimiter a text list uses. A candidate that appears the same
    number of times on every line beats one that is frequent on a single line."""
    best, best_score = ";", -1
    for cand in (";", ",", "	", "|"):
        counts = [ln.count(cand) for ln in sample_lines if ln.strip()]
        if not counts or max(counts) == 0:
            continue
        score = min(counts) * 10 + sum(counts) / len(counts)
        if score > best_score:
            best, best_score = cand, score
    return best


def _cell_text(value):
    """Spreadsheet cell as the text the user sees — an integer stays "12",
    not "12.0", because that column is usually the sequence number."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _load_spreadsheet_rows(path, limit=None):
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = wb.worksheets
        ws = sheets[0]
        if len(sheets) > 1:
            logger.warning(
                f"'{os.path.basename(path)}' holds {len(sheets)} sheets; reading "
                f"the first one, '{ws.title}'."
            )
        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append([_cell_text(c) for c in row])
            if limit and len(rows) >= limit:
                break
        return rows
    finally:
        wb.close()


def rows_from_text(text, delimiter=None, limit=None):
    """Rows out of delimited text — a file's contents or a paste, same thing."""
    lines = (text or "").splitlines()
    if not delimiter:
        delimiter = sniff_delimiter(lines[:50])
    rows = [[cell.strip() for cell in row]
            for row in csv.reader(lines, delimiter=delimiter)]
    return (rows[:limit] if limit else rows), delimiter


def _load_text_rows(path, delimiter=None, limit=None):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return rows_from_text(f.read(), delimiter, limit)


def load_source_rows(path, delimiter=None, limit=None):
    """A species list as rows of plain text, spreadsheet or delimited file alike.

    Returns (rows, delimiter) — the delimiter is None for a spreadsheet, and for
    a text file it is the one sniffed, so the dialog can show what it settled on.
    `limit` reads only the first rows, which is all a preview needs.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"No species list found at '{path}'. Use Browse… to pick the list."
        )
    if os.path.splitext(path)[1].lower() in (".xlsx", ".xlsm"):
        return _load_spreadsheet_rows(path, limit), None
    return _load_text_rows(path, delimiter, limit)


def read_birdingbreaks(path, roles, first_data_row=1, delimiter=None):
    """Read a list from disk using the columns the user marked.

    Returns the same dict shape as read_avibase, so everything downstream —
    name enrichment and the Excel writer — is untouched. A list without Dutch
    names is fine: add_species_info fills those from the classification.
    """
    rows, _ = load_source_rows(path, delimiter)
    return parse_rows(rows, roles, first_data_row, os.path.basename(path))


def column_title(index):
    """0 -> A, 25 -> Z, 26 -> AA — the column names the user reads in Excel."""
    title = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        title = chr(ord("A") + rest) + title
    return title


def describe_roles(roles, first_data_row):
    """The map in one line for the step dialog: "A sequence number, C English…"."""
    if not roles:
        return "Not set yet — open Columns… to mark the columns."
    parts = [f"{column_title(col)} {ROLE_LABELS[role]}"
             for col, role in sorted(roles.items()) if role in ROLE_KEYS]
    return f"{', '.join(parts)} — species from row {first_data_row}"


# ── guessing what each column holds ─────────────────────────────────────────
# Words a header row uses for each column, per source we have seen. Matched on
# the whole cell for the sequence number (a plain "no" would hit "Notes") and on
# a fragment for the names ("Engelse naam", "Wetenschappelijke naam").
_EXACT_HINTS = {
    "seq": ("no", "no.", "nr", "nr.", "nummer", "#", "seq", "sequence"),
}
_FRAGMENT_HINTS = {
    "en": ("engels", "english"),
    "nl": ("nederlands", "dutch", "hollands"),
    "sc": ("wetensch", "scientific", "latijn", "latin", "wet."),
}


# ── guessing ────────────────────────────────────────────────────────────
def guess_columns(rows, source="the list"):
    """A first stab at the map, so a familiar list needs one look and OK.

    Two clues, in order: the words in a header row above the species
    (Birding Breaks writes “Engelse naam” / “Wetenschappelijke
    naam”), and failing that the shape of the cells — digits are a
    sequence number, “Genus species” is a scientific name, other
    text is a name column. Dutch and English cannot be told apart by shape,
    so a text column is only called Dutch when the header says so.
    """
    roles, first_row, from_header = {}, 1, False

    for r, row in enumerate(rows[:12], start=1):
        found = {}
        for col, cell in enumerate(row):
            text = (cell or "").strip().lower()
            if not text:
                continue
            for key, words in _EXACT_HINTS.items():
                if text in words:
                    found.setdefault(col, key)
            for key, words in _FRAGMENT_HINTS.items():
                if any(word in text for word in words):
                    found.setdefault(col, key)
        if len(found) >= 2:            # a real header row, not one stray word
            roles = dict(found)
            first_row = r + 1
            from_header = True
            break

    # Rows with something in them. A list with a heading and a blank line
    # between every family (AviBase prints them that way) leaves a column
    # half empty, which used to be read as "not a column at all".
    data = [row for row in rows[first_row - 1:first_row + 39]
            if any((c or "").strip() for c in row)]
    taken = set(roles.values())
    text_columns = []
    for col in range(_column_count(rows)):
        if col in roles:
            continue
        cells = [(row[col] if col < len(row) else "").strip() for row in data]
        filled = [c for c in cells if c]
        if len(filled) < 3:            # a stray value is not a column
            continue
        if all(c.replace(".", "").isdigit() for c in filled):
            key = "seq"
        elif sum(1 for c in filled if _looks_scientific(c)) >= len(filled) * 0.6:
            key = "sc"
        else:
            text_columns.append(col)
            continue
        if key not in taken:
            roles[col] = key
            taken.add(key)

    # The first text column is the English name. A second one is the second
    # language the list was asked for — Dutch, in every list here. Shape
    # cannot tell those two apart, so order decides, and the preview shows
    # the result for correcting.
    for col in text_columns:
        key = "en" if "en" not in taken else ("nl" if "nl" not in taken else None)
        if key is None:
            break
        roles[col] = key
        taken.add(key)

    if not from_header:
        # Some lists arrive with no header row at all — only day numbers above
        # the species. Then the species themselves say where they start.
        first_row = _first_species_row(rows, roles) or first_row

    if roles:
        logger.info(f"Column guess for '{source}': "
                    f"{describe_roles(roles, first_row)}")
    return roles, first_row

def _cell(row, roles, role):
    for col, marked in roles.items():
        if marked == role:
            return (row[col] if col < len(row) else "").strip()
    return ""

def _first_species_row(rows, roles):
    """Which row the species start on, read off the rows themselves.

    The first row that reads like a species, then back up over the section
    headings sitting directly above it — "RHEIFORMES: Rheidae" is part of the
    list and belongs inside it, while the workbook's own title, separated by a
    blank row, stays outside. Returns None when nothing looks like a species.
    """
    species_at = None
    for r, row in enumerate(rows, start=1):
        sc = _cell(row, roles, "sc")
        seq = _cell(row, roles, "seq")
        if _looks_scientific(sc) or (sc and any(c.isdigit() for c in seq)):
            species_at = r
            break
    if species_at is None:
        return None

    # Walk up from there. A section heading above the species belongs to the
    # list and comes with it; a header row (two marked cells side by side)
    # does not, and neither does anything beyond a gap of two blank rows —
    # which is what separates a workbook's title from its contents.
    first, blanks = species_at, 0
    for r in range(species_at - 1, 0, -1):
        above = [_cell(rows[r - 1], roles, role)
                 for role in ROLE_KEYS]
        filled = [c for c in above if c]
        if len(filled) >= 2:
            break
        if not filled:
            blanks += 1
            if blanks >= 2:
                break
            continue                  # one blank line inside a list is normal
        if filled[0].replace(".", "").isdigit():
            break                     # a stray number, not a heading
        first, blanks = r, 0
    return first



def _column_count(rows):
    return max((len(r) for r in rows), default=0)


def _looks_scientific(text):
    """“Rhea americana” — two words, genus capitalised, species lower case."""
    parts = text.split()
    if len(parts) < 2 or len(parts) > 3:
        return False
    genus, species = parts[0], parts[1]
    return (genus[:1].isupper() and genus[1:].islower()
            and species.islower() and species.isalpha())


# A trip can hold several lists — one per leg or region — and they do not share
# a shape, so the column map is remembered per file rather than once globally.
COLUMN_MAP_SECTION = "avibase_columns"
LIST_SUFFIXES = (".csv", ".xlsx", ".xlsm", ".txt")


def format_column_map(roles, first_data_row=1, delimiter=None):
    """One config line holding everything needed to read a particular file."""
    return (f"{format_roles(roles)}|{int(first_data_row or 1)}|"
            f"{encode_delimiter(delimiter)}")


def parse_column_map(spec):
    parts = (spec or "").split("|")
    roles = parse_roles(parts[0] if parts else "")
    try:
        first = int(parts[1]) if len(parts) > 1 and parts[1].strip() else 1
    except ValueError:
        first = 1
    return roles, first, decode_delimiter(parts[2] if len(parts) > 2 else "")


def column_map_for(config, path):
    """The map remembered for this file, or None. Keyed by file name, so the
    same list carried to another trip is still understood."""
    if not config.has_section(COLUMN_MAP_SECTION):
        return None
    spec = config.get(COLUMN_MAP_SECTION, os.path.basename(path), fallback="")
    if not spec.strip():
        return None
    roles, first, delimiter = parse_column_map(spec)
    return (roles, first, delimiter) if roles else None


def trip_list_files(triproot, trip_slug=""):
    """Every file in the trip folder that could be a species list.

    What this app writes there is excluded — a checklist is named after the trip
    and soorten-temp.csv is scratch — so a run can never feed on its own output.
    """
    slug = (trip_slug or "").strip().lower()
    found = []
    try:
        names = sorted(os.listdir(triproot))
    except OSError:
        return found
    for name in names:
        low = name.lower()
        if os.path.splitext(low)[1] not in LIST_SUFFIXES:
            continue
        if low.startswith("soorten-temp") or (slug and low.startswith(slug)):
            continue
        full = os.path.join(triproot, name)
        if os.path.isfile(full):
            found.append(full)
    return found


def read_list_file(path, config):
    """One list, read with the map remembered for it, or with a guess.

    Guessing rather than refusing matters here: keyword files cover a whole
    trip, and a list nobody happened to open Columns… on should still bring its
    names along instead of quietly dropping out of the set.
    """
    saved = column_map_for(config, path)
    rows, _ = load_source_rows(path, saved[2] if saved else None)
    if saved:
        roles, first_row = saved[0], saved[1]
    else:
        roles, first_row = guess_columns(rows[:50], os.path.basename(path))
        logger.info(f"No columns marked for '{os.path.basename(path)}' — "
                    f"read as {describe_roles(roles, first_row)}.")
    return parse_rows(rows, roles, first_row, os.path.basename(path))


def selected_list_files(triproot, config, trip_slug=""):
    """The lists chosen for this trip, defaulting to all of them."""
    found = trip_list_files(triproot, trip_slug)
    chosen = [n.strip().lower()
              for n in config.get("avibase", "pm_lists", fallback="").split("|")
              if n.strip()]
    if not chosen:
        return found
    keep = [p for p in found if os.path.basename(p).lower() in chosen]
    return keep or found          # a stale selection must not empty the set


def configured_list_label(config):
    """What to call the outputs made from this list, or "" for the old default.

    A trip used to have one list, so naming a checklist after the trip was
    unambiguous. A trip can now have several — one per region or per leg — and
    without this they would all write the same file, each run quietly replacing
    the last. Empty for plain avibase.csv, so trips that never picked a list keep
    the names they have.
    """
    source = (config.get("avibase", "source_file", fallback="") or "").strip()
    if not source:
        return ""
    stem = os.path.splitext(os.path.basename(source))[0].strip()
    if stem.lower() in ("", "avibase"):
        return ""
    for bad in '<>:"/\\|?*':
        stem = stem.replace(bad, "-")
    # The operator's own file names carry words that say nothing here: the
    # output is already a checklist, and already sits in a trip with a year.
    # "Checklist Fotoreis Brazilië 2026" is worth keeping as "Fotoreis Brazilië".
    stem = re.sub(r"\b(checklist|soortenlijst|list)\b", " ", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\b(19|20)\d{2}\b", " ", stem)
    stem = re.sub(r"[\s_]+", " ", stem).strip(" .-")
    if len(stem) > 24:                     # a file name, not a sentence
        cut = stem[:24].rsplit(" ", 1)[0]
        stem = (cut or stem[:24]).strip(" .-")
    return stem


def read_configured_list(triproot, config):
    """The trip's species list, read the way the settings say to read it.

    ONE JOB, for every step that needs the list. The checklist and the
    PhotoMechanic keyword files must work from the same species in the same
    order — a keyword file describing a list the checklist never saw is worse
    than no keyword file, because nothing about it looks wrong.
    """
    source_format = config.get("avibase", "source_format", fallback="avibase")
    source_list = resolve_source_list(
        triproot, config.get("avibase", "source_file", fallback=""))
    roles = parse_roles(config.get("avibase", "columns", fallback=""))
    marked = source_format == "birdingbreaks" and bool(roles)

    logger.info(
        f"Species list {source_list}, read with "
        f"{'the columns marked for it' if marked else 'the fixed AviBase layout'}."
    )
    if marked:
        # The map remembered for this very file wins over the last one marked:
        # switching the picked list must not read it through another list's shape.
        saved = column_map_for(config, source_list)
        if saved:
            roles, first_row, delimiter = saved
        else:
            first_row = config.getint("avibase", "data_start_row", fallback=1)
            delimiter = decode_delimiter(config.get("avibase", "delimiter", fallback=""))
        return read_birdingbreaks(source_list, roles, first_row, delimiter)
    return read_avibase(triproot, source_list)


def parse_rows(rows, roles, first_data_row=1, source="the list"):
    """The marked columns turned into species, wherever the rows came from.

    Split from reading a file so that a pasted list can be counted with exactly
    the code that will later read it — a preview that lies is worse than none.
    """
    by_role = {}
    for col, role in (roles or {}).items():
        if role in ROLE_KEYS:
            by_role[role] = int(col)
    if not any(role in by_role for role in ("en", "nl", "sc")):
        raise ValueError(
            "No name column marked in the species list — open Columns… and say "
            "which column holds the English, Dutch or scientific name."
        )

    def cell(row, role):
        idx = by_role.get(role)
        if idx is None or idx >= len(row):
            return ""
        return (row[idx] or "").strip()

    avibase_dict = {}
    number = 1
    species = headings = duplicates = 0

    for row in rows[max(0, first_data_row - 1):]:
        en, nl, sc = cell(row, "en"), cell(row, "nl"), cell(row, "sc")
        status = cell(row, "st")
        if not (en or nl or sc):
            continue
        key = en or sc or nl
        digits = re.sub(r"\D", "", cell(row, "seq"))

        if not sc and not digits:
            # A family or region printed on its own line. Kept, because the
            # checklist uses these to group the species below them.
            avibase_dict[key] = {
                "enname": key, "scname": "", "nlname": "",
                "status": "", "english": "", "number": -1,
            }
            headings += 1
            continue

        if key in avibase_dict:
            duplicates += 1
        seq = int(digits) if digits else number
        avibase_dict[key] = {
            "enname":       key,
            "scname":       sc,
            "nlname":       nl,
            "status":       status,
            "english":      en,
            "number":       seq,
            "gezien":       0,
            "foto":         0,
            "knownspecies": False,
        }
        number = seq + 1
        species += 1

    logger.info(f"Read {species} species and {headings} section headings "
                f"from '{source}', starting at row {first_data_row}.")
    if duplicates:
        logger.warning(f"{duplicates} rows repeat a name already in the list; "
                       f"the last of each is kept.")
    if not species:
        raise ValueError(
            f"No species read from '{source}' — check in Columns… which row the "
            f"species start on and which columns hold the names."
        )
    return avibase_dict
