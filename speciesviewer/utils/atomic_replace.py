"""os.replace that waits out a sync client — the ONE copy.

SpeciesViewer writes a file safely by writing `<name>.tmp` and swapping it in
with os.replace. On Windows that swap fails with "Access is denied" (WinError 5)
whenever another process holds the destination open at that instant — Dropbox
or OneDrive reading the file it just saw change, the search indexer, a virus
scanner. It is transient: a moment later the same call succeeds.

The trips and the master live in Dropbox, so a batch that writes many days in a
row sooner or later hits that instant. It took the app down writing a day's
statemachine.ini (Greece, 2026-10-14). Two modules had already grown their own
private retry for the same reason; this is the shared one.
"""
from __future__ import annotations

import os
import time


def replace_with_retry(tmp, dest, attempts: int = 12, delay: float = 0.25) -> None:
    """os.replace(tmp, dest), retried on PermissionError for about three seconds
    (12 × 0.25 s) before giving up with the original error."""
    for attempt in range(attempts):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
