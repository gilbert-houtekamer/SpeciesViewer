"""Frozen-day enforcement for the trip viewers.

A trip DAY becomes immutable once it has been Frozen (sv6) or Committed (sv7):
its pictures + file rows are baked into the trip's ``metadata/active/`` CSVs and
its ``biota/`` tree. By design the ONLY supported way to change a committed day
is to Empty the whole trip and rebuild — ``sv6Freeze`` re-emits the existing
repository rows untouched and never prunes ``biota/``, so an in-place re-freeze
cannot remove a picture. Letting a viewer delete or edit a committed day
therefore silently diverges the working layer (CurrentPictures + review CSVs)
from the frozen trip DB: the deleted picture lives on in ``active/`` + ``biota/``
(a zombie), or its ``active/`` row is left pointing at a trashed file (a dangling
row). That divergence is the "blue-delete accounting" gap.

So the trip viewers open a committed day READ-ONLY. A day that is NOT yet frozen
stays fully editable — but every downstream pipeline step must then be re-run so
the edit reaches the eventual freeze; the caller offers a Read & Summarize re-run
on close, whose statemachine cascade invalidates WritePictures → WriteBlog →
BuildWebsite → Freeze.

This only concerns real trip DAYS. The Album viewer (read-only), the Master
viewers (edits route through master_mutate with full accounting) and the ad-hoc
Working-Days lane (no per-day statemachine) have no committed trip day to protect
— ``day_is_frozen`` returns False for them because there is no statemachine.ini.
"""
import logging

from engine.statemachine import StateMachine

logger = logging.getLogger("speciesviewer")

FROZEN_BANNER = "This day is committed — Empty the trip to unlock it."


def day_is_frozen(trip_root, day_folder):
    """Return ``(is_frozen, statemachine)`` for the day whose working folder is
    ``day_folder`` inside trip ``trip_root``.

    ``is_frozen`` is True once the day's Freeze OR WriteCommit step is done.
    Returns ``(False, None)`` when there is no per-day statemachine.ini (the
    Working-Days lane, an album, the master, or a metadata-less folder) — there
    is nothing frozen to protect there.
    """
    if not trip_root or not day_folder:
        return False, None
    try:
        sm = StateMachine(str(trip_root), str(day_folder), readState=True)
    except Exception as e:
        logger.warning(f"day_is_frozen: could not read statemachine for "
                       f"{day_folder!r}: {e}")
        return False, None
    ts = sm.Taskstate
    if not ts:                      # no statemachine.ini → not a pipeline day
        return False, None
    frozen = bool(ts.get("Freeze", [False])[0]
                  or ts.get("WriteCommit", [False])[0])
    return frozen, sm
