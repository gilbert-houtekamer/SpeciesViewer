"""
VirtTripBlogEditorDialog — bulk per-day blog ini editor for virtual trips.

One tab per day that has an existing blog ini file. Each tab shows:
- Dutch and English title + message (with NL↔EN translate buttons)
- Per-day settings: min score, column width, only-new flag

"Save All" writes all modified ini files; "Save All & Re-upload All" also
launches the normal Write-All-Blogs upload process.

_DayTab itself (ch.15 Trip/Album alignment, Phase 6) now lives in
gui/blog_day_tab.py, shared with gui/trip_blog_editor.py — this module only
uses Album's default review-state check, unchanged from before the split.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabWidget,
    QVBoxLayout,
)

from gui.blog_day_tab import _DayTab, _GENERATED_DEFAULT_SCORE, DRAFT_DOT, REVIEWED_DOT, DISABLED_DOT
from utils.config import _config

logger = logging.getLogger("speciesviewer")


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class VirtTripBlogEditorDialog(QDialog):
    """Tabbed editor for all per-day blog ini files of a virtual trip."""

    def __init__(self, parent, blog_dir: Path, day_dirs: list, first_date: date,
                 excluded_days=None):
        super().__init__(parent)
        self.setWindowTitle("Blog Editor")
        # ISO day labels unticked in the album overview's "In Blog" column.
        self._excluded = set(excluded_days or ())
        # Tall enough for the draft banner, both message boxes at their 70 px
        # minimum, the Reviewed/upload row and the status line — the old 560 was
        # sized for a page that had none of those.
        self.setMinimumSize(740, 640)
        self.resize(880, 760)

        # BatchModuleRunner drives its host dialog through these three: it writes
        # progress into status_label, logs through logger, and reaches the status
        # bar via passed_parent. "Save & Upload this day" runs svv3, so this dialog
        # has to look like the other batch-running dialogs.
        self.passed_parent = parent
        self.logger        = getattr(parent, "logger", logger)
        self.status_label  = QLabel("")

        self._tabs: list[_DayTab] = []
        # Set whenever a day is written to disk (Save All, per-day upload, or a
        # silent save from ticking Reviewed) so closing can refresh the album
        # overview once instead of on every keystroke.
        self._saved_since_open = False

        # Shared translator (one cache load for all tabs)
        translator = self._make_translator()

        # Config-level minimum score — the one Write All Blogs actually uses
        config_score = int(_config.get("virttrip", "minimum_score",
                                       fallback=str(_GENERATED_DEFAULT_SCORE)))

        layout = QVBoxLayout(self)

        self.tab_widget = QTabWidget()
        self.tab_widget.setTabPosition(QTabWidget.TabPosition.North)

        for day_dir in day_dirs:
            ini_path = blog_dir / f"blog_{day_dir.name}.ini"
            if not ini_path.exists():
                continue
            day_num = (date.fromisoformat(day_dir.name) - first_date).days + 1
            tab = _DayTab(ini_path, day_num, translator, config_score,
                          excluded=day_dir.name in self._excluded)
            tab.day_name = day_dir.name          # YYYY-MM-DD — what svv3 filters on
            tab._on_review_change = self._refresh_tab_labels   # live tab-dot update
            tab._on_saved = self._note_saved                   # silent save happened
            tab.upload_btn.clicked.connect(
                lambda _checked=False, t=tab: self._save_and_upload_day(t))
            if tab.excluded:
                # svv3 would reject a single-day publish of an excluded day
                # ("nothing to publish"), so don't offer the button at all.
                tab.upload_btn.setEnabled(False)
                tab.upload_btn.setToolTip(
                    "This day is set to 'not in blog', so there is nothing to "
                    "publish. Re-tick 'In Blog' in the album overview to include it.")
            idx = self.tab_widget.addTab(tab, self._tab_label(tab, day_num))
            self.tab_widget.setTabToolTip(idx,
                f"{day_dir.name}\n{tab._data['titel_nl']}")
            self._tabs.append(tab)

        if not self._tabs:
            layout.addWidget(QLabel(
                "No blog ini files found yet.\n"
                "Click 'Write All Blogs' to auto-generate them first."
            ))
            layout.addStretch()
            close_btn = QPushButton("Close")
            close_btn.clicked.connect(self.close)
            layout.addWidget(close_btn)
            return

        layout.addWidget(self.tab_widget)
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        save_btn   = QPushButton("Save All")
        upload_btn = QPushButton("Save All && Re-upload All")
        close_btn  = QPushButton("Close")
        save_btn.clicked.connect(self._save_all_clicked)
        upload_btn.clicked.connect(self._save_and_upload)
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(upload_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tab_label(tab: _DayTab, day_num: int) -> str:
        if tab.excluded:
            return f"{DISABLED_DOT} Day {day_num}"
        return f"{DRAFT_DOT if tab._draft else REVIEWED_DOT} Day {day_num}"

    def _refresh_tab_labels(self):
        for i, tab in enumerate(self._tabs):
            self.tab_widget.setTabText(i, self._tab_label(tab, tab.day_num))

    def _note_saved(self):
        """A tab wrote itself to disk (e.g. a silent save on ticking Reviewed);
        remember to refresh the album overview when the editor closes."""
        self._saved_since_open = True
        self._refresh_tab_labels()

    @staticmethod
    def _make_translator():
        try:
            from engine.translatelib import CachedTranslate
            master_db = _config.get("master", "masterbiotadatabase", fallback="")
            if master_db:
                return CachedTranslate(master_db)
        except Exception as e:
            logger.warning(f"Blog editor: could not create translator: {e}")
        return None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def jump_to_day(self, day_num: int):
        """Switch to the tab for the given calendar day number."""
        for i, tab in enumerate(self._tabs):
            if tab.day_num == day_num:
                self.tab_widget.setCurrentIndex(i)
                return

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _save_all(self) -> int:
        """Save every tab; returns how many PUBLISHED days are still drafts.

        An excluded day ("In Blog" unticked) is not published, so it never counts
        as a draft to review even when its ini is still auto-text — otherwise the
        Saved dialog would nag about a day that will never see the web."""
        drafts = 0
        for tab in self._tabs:
            reviewed = tab.save()
            if not reviewed and not tab.excluded:
                drafts += 1
        self._saved_since_open = True
        self._refresh_tab_labels()
        return drafts

    def closeEvent(self, event):
        """Guard against losing edits. If any day has unsaved changes, offer to
        save them, discard them, or stay in the editor. A day blessed with
        Reviewed was already saved silently, so it never triggers this."""
        dirty = [t for t in self._tabs if t.is_dirty()]
        if dirty:
            n = len(dirty)
            ret = QMessageBox.question(
                self, "Unsaved changes",
                f"You have unsaved edits to {n} day{'s' if n != 1 else ''}.\n\n"
                "Save them before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if ret == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if ret == QMessageBox.StandardButton.Save:
                self._save_all()
        if self._saved_since_open:
            self._refresh_overview()
        super().closeEvent(event)

    def _refresh_overview(self):
        """Repaint the trip's day table so the amber/green bubbles reflect what
        was just saved — without it a day stays amber on screen until the tab is
        rebuilt for some other reason."""
        try:
            from gui.virttrip_overview import populate_virttrip_overview
            populate_virttrip_overview(self.passed_parent)
        except Exception as e:
            logger.warning(f"Blog editor: could not refresh the trip overview: {e}")

    def _save_all_clicked(self):
        drafts = self._save_all()
        self._refresh_overview()
        msg = f"Saved {len(self._tabs)} blog ini file(s)."
        if drafts:
            msg += (f"\n\n{drafts} day(s) are still auto-written drafts "
                    f"({DRAFT_DOT}). They will be published as they are — edit them, "
                    f"or tick 'Reviewed', to clear the flag.")
        QMessageBox.information(self, "Saved", msg)

    def _save_and_upload_day(self, tab: _DayTab):
        """Save one day and re-publish only that day's blog pages."""
        from gui.batch import BatchModuleRunner
        from gui.virttrip_process import _virttrip_spec
        import batch.svv3WriteBlog

        tab.save()
        self._refresh_tab_labels()
        self._refresh_overview()

        menu_spec = _virttrip_spec(
            batch.svv3WriteBlog.main,
            f"Publishing day {tab.day_num} ({tab.day_name})...",
            f"✅ Day {tab.day_num} published — the other days were left untouched.",
        )
        # Only this day is rendered and uploaded; svv3 keeps every other day's
        # entry in blog_admin.csv and the blog index intact.
        menu_spec.batch_parms["extra_config"] = [
            ("blogconfig", "only_day", tab.day_name),
        ]
        def _on_success():
            self._refresh_overview()
            self._open_blog_in_browser()
        BatchModuleRunner(self, self.passed_parent).run(
            menu_spec, on_success=_on_success)

    def _open_blog_in_browser(self):
        """Open the just-published blog index in a browser, the way Write Blogs does.
        svv3 writes the relative URL to last_blog_url.txt on success."""
        import webbrowser
        base = _config.get("websettings", "base_url", fallback="https://houtekamer.com/")
        try:
            with open("last_blog_url.txt", "r") as f:
                rel = f.read().strip()
            webbrowser.open(base.rstrip("/") + rel)
        except (FileNotFoundError, OSError) as e:
            logger.info(f"Blog editor: no blog URL to open ({e}).")

    def _save_and_upload(self):
        self._save_all()
        from gui.virttrip_process import VirttripWriteBlogDialog
        self.close()
        VirttripWriteBlogDialog(self.passed_parent).exec()
