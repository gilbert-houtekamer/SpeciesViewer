import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QCheckBox,
    QPushButton, QScrollArea, QWidget, QFrame, QMessageBox
)

from engine.filepicture import FilePicture
from engine.translatelib import CachedTranslate

logger = logging.getLogger("speciesviewer")

OTHER = {'nl': 'en', 'en': 'nl'}


def _cache_text(kind, raw):
    """The text used as the cache key / translation unit — matches the blog
    render: a Description's leading '#' (suppress-names marker) is stripped, a
    Headline is used as-is. The raw text (with '#') is what we display/write back."""
    if kind == "Description" and raw[0:1] == "#":
        return raw[1:]
    return raw


class _Card:
    """One validatable text: a picture's Headline or Description."""
    def __init__(self, upn_name, files, kind, raw, src_lang,
                 source_edit, trans_edit, verified_chk):
        self.upn_name = upn_name
        self.files = files          # the UPN's filenames (for XMP write-back)
        self.kind = kind            # 'Headline' | 'Description'
        self.original_raw = raw     # source as stored (incl. any leading '#')
        self.src_lang = src_lang    # 'nl' | 'en' | ''
        self.source_edit = source_edit
        self.trans_edit = trans_edit
        self.verified_chk = verified_chk


class TranslationValidateDialog(QDialog):
    """Per-day panel to review/correct machine translations of picture
    Headlines/Descriptions, fix source typos (written back to XMP + CSVs), and
    mark translations verified. Reads/writes the per-trip translation cache."""

    def __init__(self, parent, trip_root, day_str):
        super().__init__(parent)
        self.setWindowTitle(f"Translation Validation — {day_str}")
        self.resize(960, 720)
        self.trip_root = str(trip_root)
        self.day_str = day_str
        self.logger = logger
        self._cards = []

        self.translator = CachedTranslate(self.trip_root)
        self.fpr = FilePicture(None)
        self.fpr.readTripDay(self.trip_root, day_str)

        outer = QVBoxLayout(self)
        info = QLabel(
            "Review the machine translations. Fix typos in the <b>source</b> (left) "
            "— that is written back to the photo's XMP and the CSVs — correct the "
            "<b>translation</b> (right), and tick <b>Verified</b> to lock it. "
            "Unverified texts still publish with the machine translation."
        )
        info.setWordWrap(True)
        outer.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self._vbox = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self._build_cards()
        if not self._cards:
            self._vbox.addWidget(QLabel("No headlines or descriptions on this day."))
        self._vbox.addStretch(1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._on_save)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btns.addWidget(save_btn)
        btns.addWidget(close_btn)
        outer.addLayout(btns)

    def _detect_src_lang(self, picture):
        """Anchor on the Description (reliable), fall back to the Headline —
        same heuristic as the render's _pictureSourceLang."""
        desc = (picture.get("Description", "") or "").strip()
        if desc[0:1] == "#":
            desc = desc[1:].strip()
        head = (picture.get("Headline", "") or "").strip()
        anchor = desc if desc else head
        if not anchor:
            return ""
        try:
            d = self.translator.detectLanguage(anchor)
            return d.lang if d else ""
        except Exception:
            return ""

    def _build_cards(self):
        for upn_name, pic in self.fpr.upn.UniquePictureName_dict.items():
            head = pic.get("Headline", "") or ""
            desc = pic.get("Description", "") or ""
            if head == "" and desc == "":
                continue
            src_lang = self._detect_src_lang(pic)
            files = list(pic.get("Files", []))
            for kind, raw in (("Headline", head), ("Description", desc)):
                if raw != "":
                    self._add_card(upn_name, files, kind, raw, src_lang)

    def _add_card(self, upn_name, files, kind, raw, src_lang):
        cache_text = _cache_text(kind, raw)
        try:
            translation = (self.translator.cachedTranslate(cache_text, language=src_lang)
                           if src_lang in ("nl", "en") else cache_text)
        except Exception as e:
            self.logger.warning(f"translate failed for {upn_name} {kind}: {e}")
            translation = ""

        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        v = QVBoxLayout(frame)
        title = QLabel(
            f"<b>{upn_name}</b> — {kind} "
            f"<span style='color:#888'>({src_lang or '?'} &rarr; {OTHER.get(src_lang, '?')})</span>"
        )
        title.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(title)

        row = QHBoxLayout()
        source_edit = QPlainTextEdit(raw)
        source_edit.setFixedHeight(80)
        trans_edit = QPlainTextEdit(translation)
        trans_edit.setFixedHeight(80)
        row.addWidget(source_edit)
        row.addWidget(trans_edit)
        v.addLayout(row)

        verified_chk = QCheckBox("Verified")
        verified_chk.setChecked(self.translator.is_verified(cache_text))
        v.addWidget(verified_chk)

        self._vbox.addWidget(frame)
        self._cards.append(_Card(upn_name, files, kind, raw, src_lang,
                                 source_edit, trans_edit, verified_chk))

    def _on_save(self):
        verified_count = 0
        source_fixes = 0
        for c in self._cards:
            new_raw = c.source_edit.toPlainText().strip()
            new_trans = c.trans_edit.toPlainText().strip()
            if new_raw == "":
                continue
            # 1) source typo fix -> write back to XMP + CSVs
            if new_raw != c.original_raw:
                fname = c.files[0] if c.files else None
                if fname:
                    try:
                        if c.kind == "Headline":
                            self.fpr.updateHeadline(fname, new_raw)
                        else:
                            self.fpr.updateDescription(fname, new_raw)
                        source_fixes += 1
                    except Exception as e:
                        self.logger.warning(
                            f"source write-back failed for {c.upn_name} {c.kind}: {e}")
                c.original_raw = new_raw
            # 2) verify (and persist any translation correction)
            if c.verified_chk.isChecked():
                self.translator.mark_verified(_cache_text(c.kind, new_raw),
                                              corrected=new_trans, source_lang=c.src_lang)
                verified_count += 1

        self.translator.writeTranslationCache()
        self.logger.info(
            f"Translation validation ({self.day_str}): {verified_count} verified, "
            f"{source_fixes} source text(s) corrected.")

        # Rebuild the trip overview so the Translations sphere updates live.
        # populate_trip_overview is a module-level function (not a bound method),
        # so call it directly with the main window — a hasattr() check fails.
        parent = self.parent()
        if parent is not None:
            try:
                from gui.trip_overview import populate_trip_overview
                populate_trip_overview(parent)
            except Exception as e:
                self.logger.warning(f"overview refresh after validation failed: {e}")

        # A verified/corrected translation or a source-text fix changes what the
        # blog/website would publish. We do NOT re-render here — Write Blog/Build
        # Website upload to the web server, which must be avoided on low (lodge)
        # bandwidth, and the local data (CSVs + translation cache) is already
        # current. Instead mark those steps stale so the user re-publishes
        # deliberately when convenient.
        changed = (verified_count > 0 or source_fixes > 0)
        invalidated = []
        if changed and parent is not None and hasattr(parent, "invalidate_render_day"):
            invalidated = parent.invalidate_render_day(
                self.day_str, reason="translation/source text changed")
        msg = f"Saved: {verified_count} verified, {source_fixes} source correction(s)."
        if invalidated:
            joined = " and ".join(invalidated)
            msg += (f"\n\n{joined} for {self.day_str} marked for re-run — "
                    f"re-publish when you have bandwidth.")
        QMessageBox.information(self, "Translation Validation", msg)
