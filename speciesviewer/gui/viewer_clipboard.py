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
    for k in targets:
        if has_place:
            collection.note_location_written(k, fields)
        collection.note_gps_written(k, _gps["lat"], _gps["lon"])
        if refresh_key is not None:
            refresh_key(k)
    n = len(targets)
    place = " + place" if has_place else ""
    msg = (f"GPS{place} set for {n} picture{'s' if n != 1 else ''}")
    logger.info(f"Pasted GPS {_gps['lat']:.6f}, {_gps['lon']:.6f}{place} "
                f"into {n} picture(s).")
    return (n, msg)
