"""Copy/paste buffers shared by every picture viewer.

`Ctrl+C` / `Ctrl+V` carry a species name; `Ctrl+Shift+C` / `Ctrl+Shift+V` carry
GPS + place. The buffers are MODULE-LEVEL, which is what makes them trip-wide
rather than day-wide: a viewer is built per day and torn down when you close it,
so anything held on the instance dies with the day. Here, a name copied in
2026-09-13 pastes into 2026-09-14 without a detour through a dialog.

They live for the session, not on disk. Copying a name again is cheap, and a
coordinate pasted from a buffer restored weeks after it was filled would be a
data-integrity bug wearing a convenience's clothes — see the GPS rules in
gui/geolocate.py.

What is copied is the SUBJECT, not the display name: "greater ani, male", the
literal string the picture stores, so the sex/age/plumage qualifier travels with
the name exactly as it does for the 'a' key. Pasting a resolved Dutch display
name would write a different string than the source picture carries.

The species WRITE is not here — it differs per viewer (the pipeline's batched
updateSubjects vs. the culling collection's write_tag_many), and each viewer
already owns that path for the 'a' key. This module decides WHAT to write; the
viewer writes it. The GPS write IS here, because the collection API for it
(gps / read_location / location_targets / note_*_written) is uniform across
every provider.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("speciesviewer")

# Location fields a place is made of. Copied wholesale so a paste reproduces the
# place exactly, rather than re-deriving it from the coordinates — a paste is
# "this is the same spot", not "look it up again".
PLACE_FIELDS = ("Location", "City", "State", "Country", "CountryCode", "Municipality")

_species: str = ""          # the stored subject, qualifiers included
_gps: dict | None = None    # {"lat": float, "lon": float, "fields": {...}}
_date: tuple | None = None  # (datetime, picture name it was copied from)


# ── species ──────────────────────────────────────────────────────────────────

def species_buffer() -> str:
    """The copied subject, or "" when nothing has been copied this session."""
    return _species


def copy_species(subject: str) -> str:
    """Put `subject` in the buffer. Returns it, or "" if there was nothing to
    copy (the caller reports that — this does not guess a message)."""
    global _species
    _species = (subject or "").strip()
    return _species


def copy_species_from(keys, subject_of) -> str:
    """Copy the species the given pictures carry, for Ctrl+C.

    One picture → its subject. Several that agree → that subject. Several that
    disagree → "" and the buffer is left ALONE: a copy that silently picked one
    of two names would be worse than one that refuses, because the wrong name
    then travels to every later paste. Blank pictures among named ones do not
    count as disagreement — they have nothing to say.

    Deliberately the same reading as culling_species.quick_species_subject(),
    which is what the 'a' key applies; the difference is only that 'a' writes
    the agreed name back to the selection and this carries it away.
    """
    named = {s for s in ((subject_of(k) or "").strip() for k in keys) if s}
    if len(named) != 1:
        return ""
    return copy_species(next(iter(named)))


# ── GPS + place ──────────────────────────────────────────────────────────────

def gps_buffer() -> dict | None:
    """The copied {"lat", "lon", "fields"}, or None when nothing was copied."""
    return _gps


def copy_gps_from(collection, key) -> str:
    """Copy one picture's GPS + place into the buffer. Returns a message for the
    caller to show/log — empty string means nothing was copied.

    Falls back to gps_cached() when gps() has nothing: a collection that keeps
    coordinates in its rows (the Rating Editor's) implements the cached reader
    and not the file-reading one, and refusing to copy from it would be an
    arbitrary hole rather than a real limit.
    """
    global _gps
    latlon = None
    for reader in ("gps", "gps_cached"):
        fn = getattr(collection, reader, None)
        if fn is None:
            continue
        try:
            latlon = fn(key)
        except Exception:
            latlon = None
        if latlon:
            break
    if not latlon:
        return ""
    try:
        fields = collection.read_location(key) or {}
    except Exception:
        fields = {}
    _gps = {"lat": latlon[0], "lon": latlon[1], "fields": dict(fields)}
    place = fields.get("Location") or fields.get("City") or ""
    msg = (f"Copied GPS {latlon[0]:.6f}, {latlon[1]:.6f}"
           f"{(' + ' + place) if place else ''}")
    logger.info(msg + f" from {key} — Ctrl+Shift+V pastes it onto another picture.")
    return msg


def paste_gps_into(collection, keys, refresh_key=None) -> tuple:
    """Write the buffered GPS + place into each picture's XMP (the RAW is never
    touched) and clear its missing-GPS flag. Returns (n_written, message).

    n_written of 0 with a non-empty message is a real outcome the caller must
    surface, not a no-op to swallow: a read-only collection, or one with no
    location plumbing at all, has nowhere to put this, and a key that appears to
    do nothing is indistinguishable from one that is broken.

    The whole selection is written in ONE pair of exiftool calls, not one pair
    per picture — see the comment below for what that cost.
    """
    if not _gps:
        return (0, "Nothing copied yet — Ctrl+Shift+C copies GPS from a picture first.")
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    from gui.geolocate import write_gps, write_location_batch
    fields = _gps.get("fields") or {}
    has_place = any(fields.get(f) for f in PLACE_FIELDS)

    # Gather every target file FIRST, then write them all in one go. Both
    # writers take a list and collapse it into a single exiftool call, so
    # calling them per picture — which is what this did, and what the
    # context-menu paste did before it — cost two exiftool spawns AND one
    # master-DB open per picture: ~24 spawns for twelve pictures, seconds of
    # nothing happening. Batched it is two spawns and one DB open for the whole
    # selection, whether that is twelve pictures or three hundred.
    targets: dict = {}          # key -> its files
    all_files: list = []
    seen: set = set()
    for k in keys:
        try:
            files = [f for f in (collection.location_targets(k) or []) if f]
        except Exception:
            files = []
        if not files:
            continue
        targets[k] = files
        for f in files:
            if str(f) not in seen:
                seen.add(str(f))
                all_files.append(f)
    if not all_files:
        return (0, "Nowhere to write GPS on these pictures — no writable sidecar.")

    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        write_gps(all_files, _gps["lat"], _gps["lon"])
        if has_place:
            write_location_batch(all_files, fields, overwrite=True)
    finally:
        QApplication.restoreOverrideCursor()

    # Bookkeeping is in-memory and cheap, so it stays per picture: each one has
    # its own cached row to update and its own card to repaint.
    if has_place:
        collection.note_locations_written({k: fields for k in targets})
    for k in targets:
        collection.note_gps_written(k, _gps["lat"], _gps["lon"])
        if refresh_key is not None:
            refresh_key(k)
    n = len(targets)
    place = " + place" if has_place else ""
    msg = (f"GPS{place} set for {n} picture{'s' if n != 1 else ''}")
    logger.info(f"Pasted GPS {_gps['lat']:.6f}, {_gps['lon']:.6f}{place} "
                f"into {n} picture(s).")
    return (n, msg)


def removable_gps_keys(collection, keys) -> list:
    """The pictures whose GPS may be removed: those that HAVE a coordinate a
    person placed (paste, map, place lookup). A camera's own fix is evidence and
    is never offered — gps_is_derived is the same test the map uses to allow a
    move. A collection without that test offers nothing."""
    derived = getattr(collection, "gps_is_derived", None)
    if derived is None:
        return []
    out = []
    for k in keys:
        try:
            if derived(k) and collection.location_targets(k):
                out.append(k)
        except Exception:
            pass
    return out


def remove_gps_from(collection, keys, refresh_key=None) -> tuple:
    """Remove the hand-placed GPS + place from these pictures (the undo of
    paste_gps_into). Only removable_gps_keys are touched. Returns
    (n_removed, message); the caller confirms with the user FIRST."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    from gui.geolocate import remove_gps_and_place
    import os
    keys = removable_gps_keys(collection, keys)
    targets = {k: [str(f) for f in collection.location_targets(k) if f] for k in keys}
    all_files = sorted({f for fs in targets.values() for f in fs})
    if not all_files:
        return (0, "No GPS placed by hand on these pictures — nothing removed.")
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        cleared, still = remove_gps_and_place(all_files)
    finally:
        QApplication.restoreOverrideCursor()
    still_n = {os.path.normcase(os.path.normpath(f)) for f in still}
    done, kept = [], []
    for k, fs in targets.items():
        if any(os.path.normcase(os.path.normpath(f)) in still_n for f in fs):
            kept.append(k)
        else:
            done.append(k)
    note = getattr(collection, "note_gps_removed", None)
    for k in done:
        if note is not None:
            note(k)
        if refresh_key is not None:
            refresh_key(k)
    n = len(done)
    msg = f"GPS + place removed from {n} picture{'s' if n != 1 else ''}"
    if kept:
        names = ", ".join(sorted(os.path.basename(f) for f in still)[:5])
        msg += (f". {len(kept)} picture{'s' if len(kept) != 1 else ''} still "
                f"show{'s' if len(kept) == 1 else ''} GPS ({names}): it could not be "
                f"removed, or it is inside the JPG itself (a PhotoLab export copies it in).")
    logger.info(msg)
    return (n, msg)


# ── capture date ─────────────────────────────────────────────────────────────

def parse_date(text):
    """A datetime from a card's date ("YYYY:MM:DD HH:MM:SS", maybe with an
    offset after it), or None."""
    from datetime import datetime
    try:
        return datetime.strptime(str(text or "").strip()[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def date_buffer():
    """(datetime, source name) copied this session, or None."""
    return _date


def copy_date(when, source: str) -> str:
    global _date
    if when is None:
        return ""
    _date = (when, source)
    msg = f"Copied date/time {when:%Y-%m-%d %H:%M:%S} from {source}"
    logger.info(msg + " — Set date/time… uses it for pictures without a date.")
    return msg


def dateless_keys(collection, keys) -> list:
    """The pictures whose capture date may be set: none yet, or one a person set
    earlier (an estimate) — never a camera's date — and that have a file that
    can receive one."""
    out = []
    for k in keys:
        try:
            if not collection.date_targets(k):
                continue
            if not collection.item_for_key(k).date or collection.date_is_by_hand(k):
                out.append(k)
        except Exception:
            pass
    return out


def set_dates(collection, keys, start, refresh_key=None) -> tuple:
    """Give the dateless pictures among `keys` a capture date: the first gets
    `start`, each next (file-name order) the next second no other picture of
    this collection already has. Returns (n_written, message, {key: datetime}
    for the pictures that got their date)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    from engine.capture_date_write import spread_times, write_capture_dates
    keys = sorted(dateless_keys(collection, keys))
    if not keys:
        return (0, "These pictures already have a capture date — nothing changed.", {})
    taken = []
    redo = set(keys)          # their old (hand-set) times are free again
    for k in collection.all_keys():
        if k in redo:
            continue
        try:
            t = parse_date(collection.item_for_key(k).date)
        except Exception:
            t = None
        if t:
            taken.append(t)
    times = spread_times(start, len(keys), taken)
    plan = {when: collection.date_targets(k) for when, k in zip(times, keys)}
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        written, had_date, failed = write_capture_dates(plan)
    finally:
        QApplication.restoreOverrideCursor()
    done = set(written)
    dated = {}
    for when, k in zip(times, keys):
        if any(f in done for f in plan[when]):
            collection.note_date_written(k, when.strftime("%Y:%m:%d %H:%M:%S"))
            dated[k] = when
            if refresh_key is not None:
                refresh_key(k)
    import os
    n = len(dated)
    msg = (f"Capture date set for {n} picture{'s' if n != 1 else ''}"
           + (f", from {times[0]:%Y-%m-%d %H:%M:%S}" if n else ""))
    if had_date:
        msg += (f". Skipped {len(had_date)} file(s) that already have a date: "
                + ", ".join(os.path.basename(f) for f in had_date[:5]))
    if failed:
        msg += (f". Could NOT write {len(failed)} file(s): "
                + ", ".join(os.path.basename(f) for f in failed[:5]))
    logger.info(msg)
    return (n, msg, dated)
