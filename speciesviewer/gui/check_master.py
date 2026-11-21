"""Master ▸ Check the Master… — every master REPAIR in one window.

ONE job: run each repair's check as a report, show one line per check with what
it found, and open the existing review-then-apply tool for the ones that found
something. The window decides nothing and changes nothing itself: the fixes are
the tools that already existed (passed in as `fixes` from gui.menuspec), each
with its own review and confirmation.

Why one window (backlog 1.9, docs/plans/maintenance_regroup_plan.md): a repair
only exists because something drifted, so it should not sit in a normal menu
beside the work. Here they are seen together, report first.

INVARIANTS
- Checking writes nothing to the master. The one check that writes report files
  (Lists vs. Files on Disk) writes them to a temporary folder, not admin/.
- The checks run on a worker thread; each opens and closes its own database
  connection there (SQLite connections do not cross threads).
- A check that fails says so on its own line; the others still run.
- After a fix, its row is checked again — the line always shows the state on disk.
"""

import logging
import tempfile

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

from gui import theme as _th
from gui.widgets import wrapped

logger = logging.getLogger("speciesviewer")


# ── the checks: pure, no Qt — (count, plain sentence) ─────────────────────────

def check_lists_vs_disk(master):
    import engine.admin_rebuild as ar
    with tempfile.TemporaryDirectory(prefix="sv_check_") as tmp:
        p = ar.plan_rebuild(master, out_dir=tmp)
    if not p.get("ok"):
        raise RuntimeError(p.get("error", "failed"))
    n = p["remove"] + p["repoint"] + p["species_change"]
    return n, (f"{p['remove']:,} rows for files that are gone, {p['repoint']:,} moved, "
               f"{p['species_change']:,} filed under another species")


def check_species_counters(master):
    import engine.master_recount as mr
    p = mr.plan(master)
    n = p["species"]
    return n, (f"{n:,} species count other numbers than their pictures"
               + (f"; {p['picks']} best pictures no longer exist" if p["picks"] else ""))


def check_gps_in_files(master):
    from db.master_db import MasterDB
    db = MasterDB(master)
    try:
        db.ensure_fresh()
        n = db.consolidate_picture_gps(apply=False)["candidates"]
    finally:
        db.close()
    return n, f"{n:,} pictures have a camera position in their files but none in the list"


def check_orphan_sidecars(master):
    import engine.admin_rebuild as ar
    info = ar.find_orphan_sidecars(master)
    n = info.get("orphan_files", 0)
    by = ", ".join(f"{v} .{k}" for k, v in sorted(info.get("by_ext", {}).items()))
    return n, f"{n:,} .xmp/.dop files whose picture is gone" + (f" ({by})" if by else "")


def check_read_copy(master):
    from db.master_db import MasterDB
    from db import master_health
    db = MasterDB(master)
    try:
        db.ensure_fresh()
        rep = master_health.check(db, master)
    finally:
        db.close()
    findings = list(rep.findings)
    lines = [f"[{'PROBLEM' if f.level == 'error' else 'warning'}] {f.what}"
             + (f" — {f.detail}" if f.detail else "") for f in findings]
    check_read_copy.details = "\n".join(lines) or "No problems found."
    return len(findings), f"The master is {rep.summary()}"


# key, title, what it checks, check function, fix buttons [(label, fixes-key)]
CHECKS = [
    ("disk", "Lists vs. Files on Disk",
     "Rows in the master's lists whose file is gone, has moved, or sits under "
     "another species.", check_lists_vs_disk,
     [("Review…", "refresh_review"), ("Apply…", "refresh_apply")]),
    ("counters", "Species Counters",
     "The picture counts in the species list against the pictures really there.",
     check_species_counters,
     [("Recount…", "recount"), ("Full report…", "audit")]),
    ("gps", "GPS in the Files, Not in the List",
     "A camera position in a picture's files that never reached the picture "
     "itself, so the map does not show it.", check_gps_in_files,
     [("Fill…", "fill_gps")]),
    ("orphans", "Orphan Sidecars",
     "PhotoLab and Lightroom files left behind after their picture was deleted.",
     check_orphan_sidecars, [("Move to trash…", "orphans")]),
    ("readcopy", "Lists vs. Read-Copy",
     "The master's lists against SpeciesViewer's fast read-copy of them, and the "
     "lists against themselves.", check_read_copy, [("Details…", "_details")]),
]


class _CheckThread(QThread):
    result = pyqtSignal(str, int, str)       # key, count, sentence
    failed = pyqtSignal(str, str)            # key, error

    def __init__(self, master, keys, parent=None):
        super().__init__(parent)
        self._master, self._keys = master, keys

    def run(self):
        for key, _t, _w, fn, _f in CHECKS:
            if key not in self._keys:
                continue
            try:
                n, text = fn(self._master)
                self.result.emit(key, n, text)
            except Exception as e:                  # one failed check, not all
                logger.warning(f"Check the Master: {key} failed: {e}", exc_info=True)
                self.failed.emit(key, str(e))


class CheckMasterWindow(QDialog):
    def __init__(self, parent, master_root, fixes: dict):
        super().__init__(parent)
        self.setWindowTitle("Check the Master")
        self.resize(1000, 560)
        self._master, self._fixes = master_root, fixes
        self._thread = None
        self._queue: list[str] = []

        lay = QVBoxLayout(self)
        lay.addWidget(wrapped(
            "<b>Check the Master</b><br>These are repairs: you only need one when "
            "something drifted. Every check below is a report — nothing is changed "
            "until you open a fix, which shows what it would do and asks first."))

        grid = QGridLayout()
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 3)
        self._status: dict[str, QLabel] = {}
        self._buttons: dict[str, list[QPushButton]] = {}
        for r, (key, title, what, _fn, fix_btns) in enumerate(CHECKS):
            grid.addWidget(wrapped(f"<b>{title}</b><br>"
                                   f"<span style='color:{_th.INK_FAINT}'>{what}</span>"),
                           r, 0)
            st = wrapped("")
            self._status[key] = st
            grid.addWidget(st, r, 1)
            row = QHBoxLayout()
            self._buttons[key] = []
            for label, fix_key in fix_btns:
                b = QPushButton(label)
                b.setEnabled(False)
                b.clicked.connect(lambda _=False, k=key, f=fix_key: self._fix(k, f))
                row.addWidget(b)
                self._buttons[key].append(b)
            row.addStretch(1)
            grid.addLayout(row, r, 2)
        lay.addLayout(grid)
        lay.addStretch(1)

        bottom = QHBoxLayout()
        restore = QPushButton("Restore the Master's Lists from a Backup…")
        restore.setToolTip("Put back one of the daily backups of the pictures, files "
                           "and species lists. The current lists are kept first.")
        restore.clicked.connect(self._restore)
        bottom.addWidget(restore)
        bottom.addStretch(1)
        self._again = QPushButton("Check again")
        self._again.clicked.connect(lambda: self.check([c[0] for c in CHECKS]))
        bottom.addWidget(self._again)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        bottom.addWidget(close)
        lay.addLayout(bottom)

        self.check([c[0] for c in CHECKS])

    # ── checking ─────────────────────────────────────────────────────────

    def check(self, keys):
        if self._thread is not None:
            return
        for k in keys:
            self._status[k].setText("checking…")
            self._status[k].setStyleSheet(f"color: {_th.INK_FAINT};")
            for b in self._buttons[k]:
                b.setEnabled(False)
        self._again.setEnabled(False)
        self._thread = _CheckThread(self._master, set(keys), self)
        self._thread.result.connect(self._on_result)
        self._thread.failed.connect(self._on_failed)
        self._thread.finished.connect(self._on_finished)
        self._thread.start()

    def _on_result(self, key, n, text):
        st = self._status[key]
        if n:
            st.setText(f"⚠ {text}")
            st.setStyleSheet(f"color: {_th.WARN};")
        else:
            st.setText("✅ nothing to repair")
            st.setStyleSheet(f"color: {_th.DONE_FG};")
        for b in self._buttons[key]:
            # A report (Details, Full report) is always useful; a fix only
            # when there is something to fix.
            b.setEnabled(bool(n) or b.text() in ("Details…", "Full report…"))

    def _on_failed(self, key, err):
        self._status[key].setText(f"❌ could not check: {err}")
        self._status[key].setStyleSheet(f"color: {_th.WARN};")

    def _on_finished(self):
        self._thread = None
        self._again.setEnabled(True)

    # ── fixing ───────────────────────────────────────────────────────────

    def _fix(self, key, fix_key):
        if self._thread is not None:
            return
        if fix_key == "_details":
            QMessageBox.information(self, "Lists vs. Read-Copy",
                                    getattr(check_read_copy, "details", "") or
                                    "Run the check first.")
            return
        fn = self._fixes.get(fix_key)
        if fn is None:
            return
        fn()                                # its own review + confirmation
        self.check([key])

    def _restore(self):
        from gui.master_restore import restore_master_lists
        if restore_master_lists(self, self._master):
            self.check([c[0] for c in CHECKS])

    def closeEvent(self, e):
        if self._thread is not None:
            QMessageBox.information(self, "Check the Master",
                                    "A check is still running — a moment, please.")
            e.ignore()
            return
        super().closeEvent(e)

    def reject(self):                       # Escape
        if self._thread is None:
            super().reject()


def open_check_master(widget):
    """Open Check the Master from anywhere (Settings ▸ Database too): finds the
    main window and runs its Master-menu item, so the fixes are the same ones."""
    w = widget
    while w is not None and not hasattr(w, "menu_spec"):
        w = w.parent()
    if w is None:
        return
    try:
        w.menu_spec["Master"]["Check the Master…"].callback()
    except Exception as e:
        logger.warning(f"Could not open Check the Master: {e}")
