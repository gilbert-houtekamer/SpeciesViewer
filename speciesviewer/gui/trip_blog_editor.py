"""
TripBlogEditorDialog — bulk per-day blog ini editor for the active trip.

Ported from gui/virttrip_blog_editor.py (ch.15 Trip/Album alignment, Phase 6),
sharing its _DayTab widget (gui/blog_day_tab.py). One tab per day that
already has a blog.ini — Trip's flat <day>/blog.ini, not Album's
metadata/blog/blog_<date>.ini. Trip's existing single-day editor
(CreateBlogDialog, trip_process.py, reached via the "2 Write Blog" column)
stays untouched and reachable — this dialog is additive, for bulk review
across a whole trip.

"Save All" writes all modified ini files. "Save All & Re-upload All" saves
then calls the same bulk Write-All-Blogs path the Phase 4 toolbar button
uses (MainWindow.handle_column_run_all on the Write Blog column) — not a
second bulk-write implementation.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabWidget,
    QVBoxLayout,
)

from gui.blog_day_tab import (_DayTab, _GENERATED_DEFAULT_SCORE,
                              DRAFT_DOT, REVIEWED_DOT, DISABLED_DOT)
from utils.config import _config
from utils.trip_config import trip_blog_review_state
from gui.widgets import wrapped

logger = logging.getLogger("speciesviewer")


class TripBlogEditorDialog(QDialog):
    """Tabbed editor for all per-day blog ini files of the active trip."""

    def __init__(self, parent, image_days: list, trip_root: Path, excluded_days=None):
        super().__init__(parent)
        self.setWindowTitle("Blog Editor")
        # ISO day labels unticked in the trip overview's "In Blog" column.
        self._excluded = set(excluded_days or ())
        self.setMinimumSize(740, 640)
        self.resize(880, 760)

        # BatchModuleRunner drives its host dialog through these three: it writes
        # progress into status_label, logs through logger, and reaches the status
        # bar via passed_parent. "Save & Upload this day" runs sv4WriteBlog, so
        # this dialog has to look like the other batch-running dialogs.
        self.passed_parent = parent
        self.logger        = getattr(parent, "logger", logger)
        self.status_label  = QLabel("")
        self.trip_root     = Path(trip_root)

        self._tabs: list[_DayTab] = []
        self._saved_since_open = False

        translator = self._make_translator()

        layout = QVBoxLayout(self)

        self.tab_widget = QTabWidget()
        self.tab_widget.setTabPosition(QTabWidget.TabPosition.North)

        for day_num, day_dir in enumerate(image_days, start=1):
            ini_path = day_dir / "blog.ini"
            if not ini_path.exists():
                continue
            # Trip has no trip-wide "minimum_score" override the way Album's
            # Write All Blogs does (sv4WriteBlog always reads the day's own
            # ini) — pass the generated default straight through, a no-op swap.
            tab = _DayTab(ini_path, day_num, translator, _GENERATED_DEFAULT_SCORE,
                          excluded=day_dir.name in self._excluded,
                          review_state_fn=trip_blog_review_state)
            tab.day_name = day_dir.name          # YYYY-MM-DD
            tab.day_dir  = day_dir                # what sv4WriteBlog scopes to
            tab._on_review_change = self._refresh_tab_labels   # live tab-dot update
            tab._on_saved = self._note_saved                   # silent save happened
            tab.upload_btn.clicked.connect(
                lambda _checked=False, t=tab: self._save_and_upload_day(t))
            if tab.excluded:
                # A day excluded from the blog has nothing to publish.
                tab.upload_btn.setEnabled(False)
                tab.upload_btn.setToolTip(
                    "This day is set to 'not in blog', so there is nothing to "
                    "publish. Re-tick 'In Blog' in the trip overview to include it.")
            idx = self.tab_widget.addTab(tab, self._tab_label(tab))
            self.tab_widget.setTabToolTip(idx,
                f"{day_dir.name}\n{tab._data['titel_nl']}")
            self._tabs.append(tab)

        if not self._tabs:
            layout.addWidget(wrapped(
                "No blog ini files found yet.\n"
                "Click 'Write All Blogs', or double-click a day's '2 Write Blog' "
                "column, to create one first."
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
    def _tab_label(tab: _DayTab) -> str:
        # The trip overview already labels days by date, not "Day N" — match it.
        if tab.excluded:
            return f"{DISABLED_DOT} {tab.day_name}"
        return f"{DRAFT_DOT if tab._draft else REVIEWED_DOT} {tab.day_name}"

    def _refresh_tab_labels(self):
        for i, tab in enumerate(self._tabs):
            self.tab_widget.setTabText(i, self._tab_label(tab))

    def _note_saved(self):
        """A tab wrote itself to disk (e.g. a silent save on ticking Reviewed);
        remember to refresh the trip overview when the editor closes."""
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
    # Actions
    # ------------------------------------------------------------------

    def _save_all(self) -> int:
        """Save every tab; returns how many PUBLISHED days are still drafts."""
        drafts = 0
        for tab in self._tabs:
            reviewed = tab.save()
            if not reviewed and not tab.excluded:
                drafts += 1
        self._saved_since_open = True
        self._refresh_tab_labels()
        return drafts

    def closeEvent(self, event):
        """Guard against losing edits — same prompt as Album's editor."""
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
        """Repaint the trip's day table so the amber/green tab dots' effect
        (the reviewed flag) is reflected — without it a day stays stale on
        screen until the table is rebuilt for some other reason."""
        try:
            from gui.trip_overview import populate_trip_overview
            populate_trip_overview(self.passed_parent)
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
        """Save one day and re-publish only that day's blog. Unlike Album's
        svv3WriteBlog (trip-wide, needs an only_day filter), sv4WriteBlog is
        inherently scoped to ONE day via [project] picturedirectory — point it
        at this tab's day and run the same "Write Blog" step the per-day
        column double-click uses."""
        from gui.batch import BatchModuleRunner

        tab.save()
        self._refresh_tab_labels()
        self._refresh_overview()

        spec = self.passed_parent._find_trip_menu_spec("Write Blog")
        if not spec:
            QMessageBox.warning(self, "Write Blog",
                                "Could not find the Write Blog step in the Trip menu.")
            return
        _config.set("project", "picturedirectory", str(tab.day_dir))

        def _on_success():
            self._refresh_overview()
        BatchModuleRunner(self, self.passed_parent).run(spec, on_success=_on_success)

    def _save_and_upload(self):
        """Save every tab, then hand off to the same bulk Write-All-Blogs path
        the toolbar button uses — not a second bulk-write implementation."""
        from gui.trip_overview import STATE_COL_START, STATE_COLUMNS
        self._save_all()
        self._refresh_overview()
        write_blog_col = STATE_COL_START + next(
            (j for j, c in enumerate(STATE_COLUMNS) if c["menu"] == "Write Blog"), 1)
        self.close()
        self.passed_parent.handle_column_run_all(write_blog_col)
