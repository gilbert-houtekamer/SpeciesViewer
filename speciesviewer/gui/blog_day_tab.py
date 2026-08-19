"""
_DayTab — one day's blog ini editor, shared by Trip's and Album's tabbed blog
editors (gui/trip_blog_editor.py, gui/virttrip_blog_editor.py).

Extracted from virttrip_blog_editor.py (ch.15 Trip/Album alignment, Phase 6).
The NL/EN form, translate buttons, min-score/column-width, save/dirty
tracking and the ini schema itself are identical on both sides — the only
thing that differs is HOW to tell "this text is still an untouched
auto/default draft" (Album detects its own auto-generator's markers; Trip has
no auto-generator, so it compares against known placeholder text instead).
That one difference is injected as review_state_fn.
"""
from __future__ import annotations

import configparser
import logging
from pathlib import Path
from typing import Callable

from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)
from utils.ini_io import read_ini

logger = logging.getLogger("speciesviewer")

_GENERATED_DEFAULT_SCORE = 25   # value hardcoded by _generate_blog_ini

DRAFT_DOT    = "🟠"
REVIEWED_DOT = "🟢"
DISABLED_DOT = "⚪"   # a day unticked in "In Blog" — not published, so not a draft to review


def _default_review_state_fn(ini_path: Path) -> bool:
    """Fallback used when no review_state_fn is injected — Album's own check."""
    from utils.virttrip_config import blog_review_state, BLOG_DRAFT
    return blog_review_state(ini_path) == BLOG_DRAFT


class _DayTab(QWidget):
    """Widget for one day's blog ini content."""

    def __init__(self, ini_path: Path, day_num: int,
                 translator, default_min_score: int, excluded: bool = False,
                 review_state_fn: Callable[[Path], bool] = _default_review_state_fn):
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
        self._draft      = review_state_fn(ini_path)
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
        read_ini(cfg, ini_path)

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
        nl_to_en_btn.setStyleSheet("QPushButton { padding: 4px 0px; }")
        nl_to_en_btn.clicked.connect(lambda: self._translate("nl"))
        en_to_nl_btn = QPushButton("↑  EN → NL")
        en_to_nl_btn.setToolTip("Translate English title and message to Dutch")
        en_to_nl_btn.setFixedWidth(130)
        en_to_nl_btn.setStyleSheet("QPushButton { padding: 4px 0px; }")
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
        read_ini(cfg, self.ini_path)

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
