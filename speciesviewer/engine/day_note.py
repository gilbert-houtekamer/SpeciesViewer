"""A one-line answer to "what was this day about?", stored with the pictures.

ONE JOB: remember "witstaartkievit" or "rondom mijn huis" for a folder of
pictures, from the moment it comes off the card until it is filed somewhere that
has its own words for it (a trip's blog title, a species page).

INVARIANT — the note lives INSIDE the day folder, never in a registry:

    <day>/_dayinfo.txt

That is the whole design decision. A registry keyed by path would be correct
exactly until the day moves — and moving is what these days do: an unfiled day
gets filed under a trip, a Day folder gets tidied somewhere else. The note has to
survive that, so it travels in the same directory the pictures travel in, and it
is the same note on every machine that can see the folder.

The file IS the description. No key=value, no header, no encoding games beyond
UTF-8 — so it reads and edits fine in Notepad, and its purpose is obvious to
anyone who finds it in Explorer without this program.

The name starts with an underscore so it sorts above the pictures, and ends in
.txt so no picture scan, counter or ingest step ever looks at it.
"""

from __future__ import annotations

import os
from pathlib import Path

NOTE_NAME = "_dayinfo.txt"

# A note is a label, not an essay: this bounds what a stray file can push into a
# table cell and a tooltip. Longer files are read and truncated, never rewritten —
# the file on disk is the user's, and we do not edit it behind their back.
MAX_CHARS = 2000


def note_path(day_folder) -> str:
    return os.path.join(str(day_folder or ""), NOTE_NAME)


def read_note(day_folder) -> str:
    """The description for this day, or "" when there is none.

    Never raises: a note is decoration on a table row, and a folder on an
    unplugged drive must not take the panel down with it."""
    path = note_path(day_folder)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(MAX_CHARS + 1).strip()[:MAX_CHARS]
    except OSError:
        return ""


def read_line(day_folder) -> str:
    """The first line only — what fits in a table cell. Use read_note() for the
    tooltip, which can afford the rest."""
    note = read_note(day_folder)
    return note.splitlines()[0].strip() if note else ""


def write_note(day_folder, text: str) -> bool:
    """Save the description. Empty text DELETES the file rather than leaving an
    empty one behind — "no note" should look like no note in Explorer too.

    Returns True when the folder now matches `text`."""
    folder = Path(str(day_folder or ""))
    if not folder.is_dir():
        return False
    path = folder / NOTE_NAME
    text = (text or "").strip()[:MAX_CHARS]
    try:
        if not text:
            if path.exists():
                path.unlink()
            return True
        # Written whole and small; no temp-and-replace dance, because losing a
        # label to a mid-write crash costs one retype.
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
        return True
    except OSError:
        return False
