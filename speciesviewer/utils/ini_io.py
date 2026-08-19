"""Read an .ini without letting its text encoding decide whether it opens.

ONE JOB: hand a ConfigParser the contents of a config file, decoded correctly.

The app writes .ini files as UTF-8. Reading them back in the machine's locale
encoding is what turned a trip path like "Brazilië" into "BraziliÄ«" on Windows
and sent batch steps looking for files that do not exist — while the same code
worked on macOS, whose locale encoding is already UTF-8.

INVARIANT: a config file that could be read before is still readable. Files
written before the app settled on UTF-8 exist (a cp1252 blog.ini from 2024), so
a decode failure falls back rather than raising; the warning says which file to
re-save, and the next write puts it back as UTF-8.
"""

import io
import os
import logging

logger = logging.getLogger("speciesviewer")

# What Windows wrote before UTF-8 was settled. latin-1 is the last resort
# because it can decode any byte at all — a wrong character beats a crash in a
# config file that is mostly ASCII keys.
LEGACY_ENCODINGS = ("cp1252", "latin-1")


def read_ini(parser, path):
    """Read one .ini (or a list of them) into `parser`. Returns what was read.

    Mirrors ConfigParser.read: a missing file is skipped silently, and the
    return value lists the files actually parsed.
    """
    paths = [path] if isinstance(path, (str, os.PathLike)) else list(path)
    parsed = []
    for one in paths:
        text = read_ini_text(one)
        if text is None:
            continue
        parser.read_string(text, source=str(one))
        parsed.append(str(one))
    return parsed


def read_ini_text(path):
    """The decoded text of an .ini, or None when there is no such file.

    Decoding happens in one go rather than through ConfigParser's own file
    handling, so a bad byte halfway down cannot leave the parser holding half a
    config.
    """
    if not os.path.isfile(path):
        return None
    raw = io.open(path, "rb").read()
    try:
        return raw.decode("utf-8-sig")          # -sig also drops a stray BOM
    except UnicodeDecodeError:
        pass
    for encoding in LEGACY_ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        logger.warning(
            f"'{path}' is not UTF-8; read as {encoding}. It will be written "
            f"back as UTF-8 the next time it is saved."
        )
        return text
    # Unreachable with latin-1 in the list, but never return a half-decoded file.
    logger.error(f"'{path}' cannot be decoded; treated as empty.")
    return None
