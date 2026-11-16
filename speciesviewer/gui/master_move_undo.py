"""Master ▸ Undo Last Species Move… — put the most recent species move back.

ONE job: say which move is the last one (picture, from, to, when, files), and
only on an explicit "Undo" hand it to master_mutate.undo_move.

INVARIANT: moves undo newest first, one at a time. Each undo marks its move as
undone, so the next time this offers the move before it. A move whose picture
has changed since (moved again, deleted) is refused by undo_move, never forced.
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import QMessageBox

from utils.config import _config

logger = logging.getLogger("speciesviewer")
TITLE = "Undo Last Species Move"


def _master_root() -> str:
    return _config.get("master", "masterbiotadatabase", fallback="").strip().strip('"')


def has_move_to_undo() -> bool:
    """Menu gate — reads only the newest snapshots, cheap enough per menu open."""
    root = _master_root()
    if not root:
        return False
    from db.master_mutate import last_move_batch
    try:
        return last_move_batch(root) is not None
    except Exception:
        return False


def undo_last_move(parent) -> bool:
    """Show the last move, confirm, undo. True when a move was undone."""
    root = _master_root()
    from db.master_mutate import last_move_batch, undo_move, MasterMutateError
    last = last_move_batch(root) if root else None
    if last is None:
        QMessageBox.information(parent, TITLE, "There is no species move to undo.")
        return False
    when = last["when"].strftime("%d %b %Y, %H:%M") if last["when"] else "unknown time"
    n = len(last["batches"])
    files = last["files"]
    shown = ", ".join(files[:6]) + (f", and {len(files) - 6} more" if len(files) > 6 else "")
    pics = (f"Picture: {last['upn']}" if n == 1
            else f"{n} pictures, moved together")
    if QMessageBox.question(
            parent, TITLE,
            f"Undo the last species move?\n\n"
            f"{pics}\n"
            f"Moved from {last['from']} to {last['to']}, {when}.\n\n"
            f"The {len(files)} file(s) go back to where they were, and the "
            f"master's species list is put back as it was: {shown}.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
        return False

    from db.master_db import MasterDB
    db = MasterDB(root)
    done, restored, kw_failed, problems = 0, 0, [], []
    try:
        db.ensure_fresh()
        for batch in last["batches"]:            # newest first
            try:
                res = undo_move(db, root, batch)
            except MasterMutateError as e:
                problems.append(str(e))
                continue
            done += 1
            restored += len(res["restored"])
            kw_failed += res.get("keyword_failed") or []
    except Exception as e:
        logger.error(f"Undo move {last['batch']} failed: {e}", exc_info=True)
        QMessageBox.warning(parent, TITLE, f"The undo failed: {e}")
        return False
    finally:
        db.close()

    if not done:
        QMessageBox.warning(parent, TITLE, "\n\n".join(problems[:5]))
        return False
    msg = (f"{done} picture(s) are back under {last['from']} "
           f"({restored} file(s) moved back).\n\n"
           f"A viewer that is open shows it after you open it again.")
    if problems:
        msg += f"\n\n{len(problems)} could not be undone: " + " ".join(problems[:3])
    if kw_failed:
        msg += (f"\n\nThe old name could not be written back into "
                f"{', '.join(kw_failed[:10])}. The master itself is correct.")
    QMessageBox.information(parent, TITLE, msg)
    return done > 0
