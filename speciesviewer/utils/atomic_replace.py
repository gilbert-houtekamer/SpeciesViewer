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


def copy_with_retry(src, dest, attempts: int = 12, delay: float = 0.25):
    """shutil.copy(src, dest), retried for about three seconds when a sync client
    holds dest. Copying a JPG into an album's Dropbox folder while Dropbox is
    reading the previous copy failed with "[Errno 22] Invalid argument"
    (Tanzania, rebuilding all album websites 2026-10-17). A missing source is
    not transient and is raised at once."""
    import shutil
    for attempt in range(attempts):
        try:
            return shutil.copy(src, dest)
        except FileNotFoundError:
            raise
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
