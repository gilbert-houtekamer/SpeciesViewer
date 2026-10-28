"""Master ▸ Empty Master Trash… — the one way to make master deletes final.

ONE job: show what is in the master trash in the units you think in (pictures,
species, dates, size), and only on an explicit "Delete permanently" hand it to
master_mutate.purge_trash. Settings ▸ Database's "Empty trash…" opens this same
window, so there is one confirmation and one deletion path.

A master delete (blue/purple delete, the card menu) never destroys anything
itself: it moves the files to <master>/.trash, where Restore can still bring
them back. This is the step that cannot be undone — hence the elephant.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMessageBox

from gui.wait_picture import picture_column, run_waiting
from gui.widgets import wrapped
from utils.config import _config


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _master_root() -> str:
    return _config.get("master", "masterbiotadatabase", fallback="").strip().strip('"')


def master_trash_has_content() -> bool:
    """Menu gate — cheap (no walking), it runs each time the Master menu opens."""
    root = _master_root()
    if not root:
        return False
    from db.master_mutate import trash_has_content
    return trash_has_content(root)


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def empty_master_trash(parent) -> bool:
    """Count, confirm, purge. True when the trash was emptied."""
    title = "Empty Master Trash"
    root = _master_root()
    from db.master_mutate import trash_has_content, trash_summary, purge_trash
    if not root or not trash_has_content(root):
        QMessageBox.information(parent, title, "The master trash is already empty.")
        return False
    s = run_waiting(parent, "Counting what is in the master trash…",
                    lambda: trash_summary(root), picture=None)

    if s["first_day"] == s["last_day"]:
        when = f"deleted on {s['first_day']}"
    else:
        when = f"deleted between {s['first_day']} and {s['last_day']}"
    species = f" of {s['species']:,} species" if s["species"] else ""

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    column = picture_column(dlg, "elephant")
    head = QLabel(f"Permanently delete {_plural(s['pictures'], 'picture')}{species}?")
    head.setStyleSheet("font-size: 15px; font-weight: 600;")
    column.addWidget(head)
    column.addWidget(wrapped(
        f"{_plural(s['files'], 'file')} ({_size(s['bytes'])}), {when}.\n\n"
        "They are already out of the master — no viewer shows them. Until now "
        "the files were only kept so you could restore them (Settings ▸ "
        "Database ▸ Restore images…).\n\n"
        "After this they are gone for good — this cannot be undone."))
    column.addStretch(1)
    buttons = QDialogButtonBox()
    delete_btn = buttons.addButton("Delete permanently",
                                   QDialogButtonBox.ButtonRole.DestructiveRole)
    cancel_btn = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
    cancel_btn.setDefault(True)             # Enter must never be the destructive choice
    delete_btn.setAutoDefault(False)
    delete_btn.clicked.connect(dlg.accept)
    cancel_btn.clicked.connect(dlg.reject)
    column.addWidget(buttons, alignment=Qt.AlignmentFlag.AlignRight)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False

    try:
        run_waiting(parent, "Emptying the master trash…",
                    lambda: purge_trash(root, older_than_days=0), picture="elephant")
    except Exception as e:
        QMessageBox.warning(parent, title, f"Emptying the trash failed:\n{e}")
        return False
    QMessageBox.information(
        parent, title,
        f"The master trash is empty — {_plural(s['pictures'], 'picture')} deleted, "
        f"{_size(s['bytes'])} freed.")
    return True
