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

logger = logging.getLogger("speciesviewer")

_GENERATED_DEFAULT_SCORE = 25   # value hardcoded by _generate_blog_ini


class _DayTab(QWidget):
    """Widget for one day's blog ini content."""

    def __init__(self, ini_path: Path, day_num: int,
                 translator, default_min_score: int):
        super().__init__()
        self.ini_path    = ini_path
        self._translator = translator
        self._data       = self._load(ini_path)

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

        # Dutch
        layout.addWidget(self._section_label("Dutch"))
        layout.addWidget(QLabel("Title:"))
        self.titel_nl = QLineEdit(self._data["titel_nl"])
        layout.addWidget(self.titel_nl)
        layout.addWidget(QLabel("Message:"))
        self.message_nl = QTextEdit()
        self.message_nl.setPlainText(self._data["message_nl"])
        self.message_nl.setFixedHeight(90)
        layout.addWidget(self.message_nl)

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
        self.message_en.setPlainText(self._data["message_en"])
        self.message_en.setFixedHeight(90)
        layout.addWidget(self.message_en)

        # Settings
        layout.addWidget(self._section_label("Settings"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Min score:"))
        self.min_score = QSpinBox()
        self.min_score.setRange(0, 100)
        self.min_score.setValue(self._data["minimum_score"])
        self.min_score.setFixedWidth(60)
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
        layout.addStretch()

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

    def save(self):
        """Write the current field values back to the ini file."""
        cfg = configparser.RawConfigParser()
        try:
            cfg.read(str(self.ini_path), encoding="utf-8")
        except UnicodeDecodeError:
            cfg.read(str(self.ini_path), encoding="latin-1")

        for section in ("blog_config", "blog_selection", "blog_text"):
            if not cfg.has_section(section):
                cfg.add_section(section)

        cfg.set("blog_config", "minimum_score", str(self.min_score.value()))
        cfg.set("blog_config", "column_width",  str(self.col_width.value()))
        cfg.set("blog_text",   "titel_nl",      self.titel_nl.text())
        cfg.set("blog_text",   "message_nl",    self.message_nl.toPlainText())
        cfg.set("blog_text",   "titel_en",      self.titel_en.text())
        cfg.set("blog_text",   "message_en",    self.message_en.toPlainText())

        with open(self.ini_path, "w", encoding="utf-8") as f:
            cfg.write(f)
        logger.info(f"Blog editor: saved {self.ini_path.name}")


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class VirtTripBlogEditorDialog(QDialog):
    """Tabbed editor for all per-day blog ini files of a virtual trip."""

    def __init__(self, parent, blog_dir: Path, day_dirs: list, first_date: date):
        super().__init__(parent)
        self.setWindowTitle("Blog Editor")
        self.setMinimumSize(740, 560)
        self.resize(860, 620)

        self._tabs: list[_DayTab] = []

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
            tab = _DayTab(ini_path, day_num, translator, config_score)
            idx = self.tab_widget.addTab(tab, f"Day {day_num}")
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

        btn_row = QHBoxLayout()
        save_btn   = QPushButton("Save All")
        upload_btn = QPushButton("Save All && Re-upload All")
        close_btn  = QPushButton("Close")
        save_btn.clicked.connect(self._save_all)
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
        for i in range(self.tab_widget.count()):
            if self.tab_widget.tabText(i) == f"Day {day_num}":
                self.tab_widget.setCurrentIndex(i)
                return

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _save_all(self):
        for tab in self._tabs:
            tab.save()
        QMessageBox.information(self, "Saved",
                                f"Saved {len(self._tabs)} blog ini file(s).")

    def _save_and_upload(self):
        for tab in self._tabs:
            tab.save()
        from gui.virttrip_process import VirttripWriteBlogDialog
        self.close()
        VirttripWriteBlogDialog(self.parent()).exec()
