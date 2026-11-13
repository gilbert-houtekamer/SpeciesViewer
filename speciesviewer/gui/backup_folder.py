"""Choose the card (or drive) a backup goes to — never a trip folder on it.

ONE job: hand back a backup destination that is a CARD ROOT. Shared by the
metadata backup (SV INDEX card), the RAW backup and the restore, so all three
follow the same rule: you choose the card, SpeciesViewer adds the trip folder.

Invariant: a folder that `engine.work_backup.is_trip_folder` recognises is
never returned as it is. Choosing H:/2026 Madagascar made the backup write into
H:/2026 Madagascar/2026 Madagascar/, where the restore could not find it.
"""

from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from engine import work_backup as wb


def _ask_use_root(parent, title, chosen, root) -> str:
    """'use' | 'other' | '' (closed)."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Question)
    trip = Path(chosen).name
    box.setText(f"{chosen} is a trip folder on the backup card, not the card itself.")
    box.setInformativeText(
        f"The backup always adds the trip's own folder, so choosing a trip folder "
        f"would put a second “{trip}” inside it. SpeciesViewer will use {root} "
        f"instead, the same place as the other trips on this card.")
    use = box.addButton(f"Use {root}", QMessageBox.ButtonRole.AcceptRole)
    other = box.addButton("Choose another folder…", QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(use)
    box.exec()
    clicked = box.clickedButton()
    return "use" if clicked is use else "other" if clicked is other else ""


def choose_card_folder(parent, title, start) -> str:
    """A folder dialog that ends on a card root. "" when cancelled."""
    while True:
        chosen = QFileDialog.getExistingDirectory(parent, title, start or str(Path.home()))
        if not chosen:
            return ""
        root = wb.card_root(chosen)
        if root == chosen:
            return chosen
        answer = _ask_use_root(parent, title, chosen, root)
        if answer == "use":
            return root
        if answer != "other":
            return ""
        start = root


def checked_location(parent, title, dest, set_location) -> str:
    """The remembered destination, made a card root before a backup writes
    into it. Settings saved before this rule (or on another computer) can still
    name a trip folder. Returns the folder to use, or "" to stop."""
    if not dest:
        return dest
    root = wb.card_root(dest)
    if root == dest:
        return dest
    answer = _ask_use_root(parent, title, dest, root)
    if answer == "use":
        set_location(root)
        return root
    if answer == "other":
        got = choose_card_folder(parent, title, root)
        if got:
            set_location(got)
        return got
    return ""
