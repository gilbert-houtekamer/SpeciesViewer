"""Rebuild the website pages of the species a master change touched.

ONE JOB: after a master change that alters what a species page shows (pictures
with a JPG deleted, or put back from the trash), re-render and upload those
species' pages: the same work as the viewer's ↻ Web, for several species at once.

INVARIANT: never undoes or blocks the change it follows, and never removes
anything from the website (uploads only add). No website set up → silent.
Website unreachable → ONE warning naming every page not updated. A failed species
never stops the others.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog

logger = logging.getLogger("speciesviewer")


def _name(master_db, sci: str) -> str:
    try:
        row = master_db.species_row(sci)
    except Exception:
        row = None
    if not row:
        return sci
    return (row.get("nl") or row.get("en") or sci).strip()


def _listing(names: list[str], limit: int = 12) -> str:
    shown = "\n".join(f"  • {n}" for n in names[:limit])
    if len(names) > limit:
        shown += f"\n  … and {len(names) - limit} more"
    return shown


def rebuild_species_pages(parent, scis, master_db, *, ask: str = "") -> str:
    """Rebuild + upload the page of each species in `scis` (scientific names).

    `ask`: a question to put first, with `{names}` where the list goes ("" =
    just do it: the person already agreed in the dialog that led here).
    Returns one line for the caller's status bar ("" when there was nothing to do).
    """
    scis = sorted({(s or "").strip() for s in scis if (s or "").strip()},
                  key=str.casefold)
    if not scis or master_db is None:
        return ""
    from utils import site_config
    if not site_config.publishing_set_up():
        return ""                       # no website: nothing to keep up to date

    # A species the change emptied has no row left, so no page to render. Its old
    # page stays on the website (nothing is ever removed there).
    gone = [s for s in scis if master_db.species_row(s) is None]
    todo = [s for s in scis if s not in gone]
    names = {s: _name(master_db, s) for s in todo}

    if ask and todo:
        if QMessageBox.question(
                parent, "Update the website",
                ask.format(names=_listing([names[s] for s in todo])),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
            return "The website pages were not updated."

    from engine import connectivity
    if todo and not connectivity.publish_reachable():
        QMessageBox.warning(
            parent, "Update the website",
            "The website could not be reached, so the page of these species still "
            "shows the old pictures:\n\n"
            + _listing([names[s] for s in todo])
            + "\n\nLater, open each species and press ↻ Web to update its page.")
        return "The website could not be reached, so its pages were not updated."

    from engine.regenerate_species import regenerate_species_page
    done, failed, urls = [], [], []
    prog = QProgressDialog("Updating the website…", None, 0, len(todo), parent)
    prog.setWindowTitle("Update the website")
    prog.setWindowModality(Qt.WindowModality.WindowModal)
    prog.setCancelButton(None)
    prog.setMinimumDuration(0)
    prog.setAutoClose(False)
    prog.setAutoReset(False)
    try:
        for i, sci in enumerate(todo):
            label = f"Updating the page of {names[sci]} ({i + 1} of {len(todo)})…"
            prog.setLabelText(label)
            prog.setValue(i)
            QApplication.processEvents()

            def _cb(msg, _label=label):
                prog.setLabelText(f"{_label}\n{msg}")
                QApplication.processEvents()

            try:
                res = regenerate_species_page(sci, master_db, progress_cb=_cb)
            except Exception as e:
                logger.error(f"Page rebuild for {sci} failed: {e}", exc_info=True)
                res = {"ok": False, "error": str(e)}
            if res.get("ok"):
                done.append(names[sci])
                urls += list(res.get("urls") or [])
            else:
                failed.append(f"{names[sci]}: {res.get('error', 'see the log')}")
        prog.setValue(len(todo))
    finally:
        prog.close()

    if done:
        logger.info(f"Website pages updated: {', '.join(done)}")
    if failed or gone:
        msg = []
        if failed:
            msg.append("These pages could not be updated:\n\n" + _listing(failed)
                       + "\n\nLater, open the species and press ↻ Web to try again.")
        if gone:
            msg.append("These species have no pictures left in the master, so their "
                       "page was not rebuilt; the old page stays on the website:\n\n"
                       + _listing(gone))
        QMessageBox.warning(parent, "Update the website", "\n\n".join(msg))
    if done:
        _show_links(parent, done, urls)
    if not done:
        return "The website pages were not updated."
    return (f"Updated the website page of {done[0]}." if len(done) == 1
            else f"Updated the website pages of {len(done)} species.")


def _show_links(parent, done: list[str], urls: list[str]) -> None:
    """The finished pages as clickable links — the same box as ↻ Web, because
    the person checks the result anyway."""
    from urllib.parse import quote
    from utils import site_config
    base = site_config.site_base()
    links = [f'<a href="{base + quote(u, safe="/")}">{base + u}</a>' for u in urls]
    what = (f"the page of {done[0]} was" if len(done) == 1
            else f"the pages of {len(done)} species were")
    box = QMessageBox(parent)
    box.setWindowTitle("Update the website")
    box.setIcon(QMessageBox.Icon.Information)
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(f"Done — {what} rebuilt and uploaded.<br><br>" + "<br>".join(links))
    box.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    box.exec()
