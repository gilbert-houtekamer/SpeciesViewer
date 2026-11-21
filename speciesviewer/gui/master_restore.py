"""Restore the master's three lists from a daily backup.

ONE job: let the user pick one of the daily backups (pictures, files, species —
`db.master_health` makes them) and put it back, then rebuild the read-copy.

INVARIANT: the lists being replaced are kept first (master_health.restore
snapshots them under backup/pre-restore-*), so a restore can be undone. Two
confirmations: which backup, then "anything added since then will be lost".

Lives in Master ▸ Check the Master… (backlog 1.9); it used to be a button in
Settings ▸ Database.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

from utils.disk_space import human_bytes

TITLE = "Restore the Master's Lists from a Backup"


def restore_master_lists(parent, master_root) -> bool:
    """True when a backup was put back (the caller then refreshes)."""
    if not master_root:
        return False
    from db import master_health
    backups = [b for b in master_health.list_backups(master_root)
               if not b["day"].startswith("pre-restore")]
    if not backups:
        QMessageBox.information(parent, TITLE, "There are no backups yet.")
        return False
    items = [f"{b['day']}  —  {human_bytes(b['bytes'])}  "
             f"({(b['manifest'] or {}).get('check', 'not checked')})"
             for b in backups]
    pick, ok = QInputDialog.getItem(
        parent, TITLE,
        "Restoring REPLACES the master's three lists (pictures, files, species)\n"
        "and rebuilds the read-copy. The current lists are kept first, so this\n"
        "can be undone.\n\nRestore from:", items, 0, False)
    if not ok:
        return False
    day = backups[items.index(pick)]["day"]
    if QMessageBox.question(
            parent, TITLE,
            f"Replace the pictures, files and species lists with the backup from "
            f"{day}?\n\nAnything added to the master since then will be lost. The "
            f"current lists are kept (gzipped) under backup/pre-restore-*, so you "
            f"can come back.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
        return False

    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        res = master_health.restore(master_root, day)
        from db.master_db import MasterDB
        db = MasterDB(master_root)
        try:
            db.rebuild()          # the read-copy is stale by construction after a restore
        finally:
            db.close()
    except Exception as e:
        QApplication.restoreOverrideCursor()
        QMessageBox.warning(parent, TITLE, f"The restore failed:\n{e}")
        return False
    finally:
        QApplication.restoreOverrideCursor()
    QMessageBox.information(
        parent, TITLE,
        f"Restored {len(res['restored'])} list(s) from {day} and rebuilt the "
        f"read-copy.\n\nThe lists you replaced are in {res['kept'].name}.\n\n"
        f"Restart SpeciesViewer — it keeps the master in memory.")
    return True
