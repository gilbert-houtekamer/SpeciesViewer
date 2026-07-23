"""
VirtTripBlogEditorDialog — bulk per-day blog ini editor for virtual trips.

One tab per day that has an existing blog ini file. Each tab shows:
- Dutch and English title + message (with NL↔EN translate buttons)
- Per-day settings: min score, column width, only-new flag

"Save All" writes all modified ini files; "Save All & Re-upload All" also
launches the normal Write-All-Blogs upload process.
"""
from __future__ import annotations

import configparser
import logging
from datetime import date
from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget,
)

from utils.config import _config
from utils.virttrip_config import blog_review_state, BLOG_DRAFT

logger = logging.getLogger("speciesviewer")

_GENERATED_DEFAULT_SCORE = 25   # value hardcoded by _generate_blog_ini

DRAFT_DOT    = "🟠"
REVIEWED_DOT = "🟢"
DISABLED_DOT = "⚪"   # a day unticked in "In Blog" — not published, so not a draft to review


class _DayTab(QWidget):
    """Widget for one day's blog ini content."""

    def __init__(self, ini_path: Path, day_num: int,
                 translator, default_min_score: int, excluded: bool = False):
        super().__init__()
        self.ini_path    = ini_path
        self.day_num     = day_num
        self._translator = translator
        # A day unticked in "In Blog": it is not published, so it is never a draft
        # awaiting review. Its tab shows a grey dot and it drops out of the drafts
        # count — the amber "read this auto-text" banner is suppressed too. The real
        # review state (_draft) is kept truthful so re-ticking "In Blog" later shows
        # the day's true colour again.
        self.excluded    = excluded
        self._data       = self._load(ini_path)
        self._draft      = blog_review_state(ini_path) == BLOG_DRAFT
        # True once any field is edited and not yet saved — drives the
        # "discard your changes?" prompt when the editor is closed.
        self._dirty      = False

        # If the ini still has the generic generated default, swap in the
        # globally-configured value so the editor is in sync with Write All Blogs.
        ini_score = self._data["minimum_score"]
        self._data["minimum_score"] = (
            default_min_score if ini_score == _GENERATED_DEFAULT_SCORE
            else ini_score
        )

        self._build_ui()

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    @staticmethod
    def _load(ini_path: Path) -> dict:
        cfg = configparser.RawConfigParser()
        try:
            cfg.read(str(ini_path), encoding="utf-8")
        except UnicodeDecodeError:
            cfg.read(str(ini_path), encoding="latin-1")

        def _u8(section, key, fallback=""):
            val = cfg.get(section, key, fallback=fallback)
            try:
                val = val.encode("latin1").decode("utf-8")
            except Exception:
                pass
            return val.replace("<female>", "♀").replace("<male>", "♂")

        return {
            "titel_nl":      _u8("blog_text",   "titel_nl"),
            "message_nl":    _u8("blog_text",   "message_nl"),
            "titel_en":      _u8("blog_text",   "titel_en"),
            "message_en":    _u8("blog_text",   "message_en"),
            "minimum_score": cfg.getint    ("blog_config", "minimum_score", fallback=_GENERATED_DEFAULT_SCORE),
            "column_width":  cfg.getint    ("blog_config", "column_width",  fallback=300),
            "only_new":      cfg.getboolean("blog_config", "only_new",      fallback=False),
            "species":       cfg.get       ("blog_selection", "species",    fallback="biota"),
        }

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: bold; margin-top: 6px;")
        return lbl

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 8, 8, 8)

        # Draft banner — an auto-written day says so, in plain words, above the
        # text it wrote. Hidden the moment the day is marked Reviewed (see
        # _on_reviewed_toggled) so it doesn't linger over text you've just blessed.
        self._banner = None
        self._on_review_change = None    # dialog sets this to refresh the tab dot
        self._on_saved = None            # dialog sets this: a silent save happened
        if self._draft and not self.excluded:
            self._banner = QLabel(
                f"{DRAFT_DOT}  This text was written automatically from the day's "
                f"locations and best species. Read it, edit what you want, then tick "
                f"<b>Reviewed</b> below.")
            self._banner.setWordWrap(True)
            self._banner.setStyleSheet(
                "background:#fff3e0; border:1px solid #ffb74d; "
                "border-radius:4px; padding:6px; margin-bottom:4px;")
            layout.addWidget(self._banner)

        # Dutch
        layout.addWidget(self._section_label("Dutch"))
        layout.addWidget(QLabel("Title:"))
        self.titel_nl = QLineEdit(self._data["titel_nl"])
        layout.addWidget(self.titel_nl)
        layout.addWidget(QLabel("Message:"))
        self.message_nl = QTextEdit()
        # Plain-text only: a paste from a web page comes in as plain text, so it
        # can't drag in the source's fonts/italics (which is how "(Fluvicola
        # nengeta)" arrived big and italic). Our own <i>…</i> markup is literal text
        # and is preserved untouched.
        self.message_nl.setAcceptRichText(False)
        self.message_nl.setPlainText(self._data["message_nl"])
        # A minimum, not a fixed height: pinned at 90 these two boxes could not
        # give up a pixel, so once the banner + button row pushed the content past
        # the window height the layout had nothing left to shrink and the widgets
        # overlapped instead. As the stretchy items they now absorb the squeeze
        # (and scroll internally) and grow into any spare room.
        self.message_nl.setMinimumHeight(70)
        layout.addWidget(self.message_nl, stretch=1)

        # Translate buttons (between the two language sections)
        trans_row = QHBoxLayout()
        trans_row.addStretch()
        nl_to_en_btn = QPushButton("↓  NL → EN")
        nl_to_en_btn.setToolTip("Translate Dutch title and message to English")
        nl_to_en_btn.setFixedWidth(130)
        nl_to_en_btn.clicked.connect(lambda: self._translate("nl"))
        en_to_nl_btn = QPushButton("↑  EN → NL")
        en_to_nl_btn.setToolTip("Translate English title and message to Dutch")
        en_to_nl_btn.setFixedWidth(130)
        en_to_nl_btn.clicked.connect(lambda: self._translate("en"))
        trans_row.addWidget(nl_to_en_btn)
        trans_row.addWidget(en_to_nl_btn)
        trans_row.addStretch()
        layout.addLayout(trans_row)

        # English
        layout.addWidget(self._section_label("English"))
        layout.addWidget(QLabel("Title:"))
        self.titel_en = QLineEdit(self._data["titel_en"])
        layout.addWidget(self.titel_en)
        layout.addWidget(QLabel("Message:"))
        self.message_en = QTextEdit()
        self.message_en.setAcceptRichText(False)   # plain-text paste (see message_nl)
        self.message_en.setPlainText(self._data["message_en"])
        self.message_en.setMinimumHeight(70)
        layout.addWidget(self.message_en, stretch=1)

        # Settings
        layout.addWidget(self._section_label("Settings"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Min score:"))
        self.min_score = QSpinBox()
        self.min_score.setRange(0, 100)
        self.min_score.setValue(self._data["minimum_score"])
        self.min_score.setFixedWidth(90)
        row.addWidget(self.min_score)
        row.addSpacing(16)
        row.addWidget(QLabel("Column width:"))
        self.col_width = QSpinBox()
        self.col_width.setRange(100, 600)
        self.col_width.setSingleStep(50)
        self.col_width.setValue(self._data["column_width"])
        self.col_width.setFixedWidth(70)
        row.addWidget(self.col_width)
        row.addStretch()
        layout.addLayout(row)

        review_row = QHBoxLayout()
        self.reviewed_box = QCheckBox("Reviewed — the text is final (green in the trip overview)")
        self.reviewed_box.setChecked(not self._draft)
        self.reviewed_box.setToolTip(
            "Tick this to accept the text as it stands. Editing any of the four text "
            "fields counts as a review on its own, so you only need this to bless an "
            "auto-written day you are happy with unchanged.")
        self.reviewed_box.toggled.connect(self._on_reviewed_toggled)
        review_row.addWidget(self.reviewed_box)
        review_row.addStretch()

        self.upload_btn = QPushButton("Save && Upload this day")
        self.upload_btn.setToolTip(
            "Save this day and re-publish only its blog pages. The other days keep "
            "the pages already on the web — nothing else is re-uploaded.")
        review_row.addWidget(self.upload_btn)
        layout.addLayout(review_row)
        # No trailing addStretch(): the two message boxes are the stretchy items
        # now, so spare height goes into the text you are actually editing.

        # Any edit after the initial values are in place marks the tab dirty, so
        # closing without saving can offer to keep or discard the work. Connected
        # last, after the widgets are seeded, so loading doesn't count as an edit.
        self.titel_nl.textChanged.connect(self._mark_dirty)
        self.message_nl.textChanged.connect(self._mark_dirty)
        self.titel_en.textChanged.connect(self._mark_dirty)
        self.message_en.textChanged.connect(self._mark_dirty)
        self.min_score.valueChanged.connect(self._mark_dirty)
        self.col_width.valueChanged.connect(self._mark_dirty)

    def _mark_dirty(self, *args):
        self._dirty = True

    def is_dirty(self) -> bool:
        """True when there are edits not yet written to the ini."""
        return self._dirty

    # ------------------------------------------------------------------
    # Translate
    # ------------------------------------------------------------------

    def _translate(self, source_lang: str):
        if self._translator is None:
            QMessageBox.warning(self, "Translation unavailable",
                                "No translation service configured.")
            return
        try:
            if source_lang == "nl":
                self.titel_en.setText(
                    self._translator.cachedTranslate(self.titel_nl.text(), language="nl"))
                self.message_en.setPlainText(
                    self._translator.cachedTranslate(self.message_nl.toPlainText(), language="nl"))
            else:
                self.titel_nl.setText(
                    self._translator.cachedTranslate(self.titel_en.text(), language="en"))
                self.message_nl.setPlainText(
                    self._translator.cachedTranslate(self.message_en.toPlainText(), language="en"))
        except Exception as e:
            QMessageBox.warning(self, "Translation failed", str(e))

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _text_edited(self) -> bool:
        """True when the blog TEXT differs from what was loaded. Changing the min
        score or column width is not a review of the words, so it doesn't count."""
        return (self.titel_nl.text()             != self._data["titel_nl"]
                or self.message_nl.toPlainText() != self._data["message_nl"]
                or self.titel_en.text()          != self._data["titel_en"]
                or self.message_en.toPlainText() != self._data["message_en"])

    def _on_reviewed_toggled(self, checked: bool):
        """Ticking Reviewed clears the amber auto-text banner and turns the tab dot
        green at once. It also SAVES the day silently: ticking Reviewed reads as
        "this is final", so the text and the reviewed flag are written to disk right
        away — you no longer have to remember a separate Save afterwards."""
        if self._banner is not None:
            self._banner.setVisible(not checked)
        self._draft = not checked
        if checked:
            # save() re-baselines and clears the dirty flag, so a tick that follows
            # your edits persists both in one silent write.
            self.save()
            if callable(self._on_saved):
                self._on_saved()
        if callable(self._on_review_change):
            self._on_review_change()

    def save(self) -> bool:
        """Write the current field values back to the ini file. Returns the day's
        reviewed state.

        Editing the text counts as reviewing it, so a day you actually worked on
        goes green whether or not you noticed the checkbox. A day you left alone
        stays amber even though Save All wrote it — otherwise one click would
        bless every draft in the trip and the amber flag would mean nothing."""
        cfg = configparser.RawConfigParser()
        try:
            cfg.read(str(self.ini_path), encoding="utf-8")
        except UnicodeDecodeError:
            cfg.read(str(self.ini_path), encoding="latin-1")

        for section in ("blog_config", "blog_selection", "blog_text"):
            if not cfg.has_section(section):
                cfg.add_section(section)

        reviewed = self.reviewed_box.isChecked() or self._text_edited()

        cfg.set("blog_config", "minimum_score", str(self.min_score.value()))
        cfg.set("blog_config", "column_width",  str(self.col_width.value()))
        cfg.set("blog_config", "reviewed",      "true" if reviewed else "false")
        cfg.set("blog_text",   "titel_nl",      self.titel_nl.text())
        cfg.set("blog_text",   "message_nl",    self.message_nl.toPlainText())
        cfg.set("blog_text",   "titel_en",      self.titel_en.text())
        cfg.set("blog_text",   "message_en",    self.message_en.toPlainText())

        with open(self.ini_path, "w", encoding="utf-8") as f:
            cfg.write(f)

        # Re-baseline so a second Save in the same session doesn't re-report the
        # first one's edits as fresh ones.
        self._data.update({
            "titel_nl":   self.titel_nl.text(),
            "message_nl": self.message_nl.toPlainText(),
            "titel_en":   self.titel_en.text(),
            "message_en": self.message_en.toPlainText(),
        })
        self._draft = not reviewed
        self.reviewed_box.setChecked(reviewed)
        self._dirty = False
        logger.info(f"Blog editor: saved {self.ini_path.name} "
                    f"({'reviewed' if reviewed else 'still a draft'})")
        return reviewed


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
