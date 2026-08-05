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


def prompt_translation_locked(parent, err) -> str:
    """Ask the user what to do when TransCache.csv is locked (open in Excel / Dropbox
    syncing). Returns "retry" or "skip" — pass this as writeTranslationCache(on_locked=…)
    so a failed save offers a way out instead of crashing."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Translations not saved")
    box.setText("Could not save the translation cache — <b>TransCache.csv</b> is "
                "locked by another process.")
    box.setInformativeText(
        "It is probably open in Excel, or Dropbox is syncing it. Close it, then "
        "<b>Retry</b> — or <b>Skip</b> to carry on (the translations are kept in memory "
        "and will be saved next time).")
    retry_btn = box.addButton("Retry", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return "retry" if box.clickedButton() is retry_btn else "skip"


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

    def __init__(self, parent, trip_root, day_str, cache_root=None, on_saved=None,
                 master_db=None):
        """trip_root supplies a Trip's day review CSVs (metadata/review/<day>).
        cache_root is the translation cache (TransCache) root — for a Trip it equals
        trip_root, but an Album's pictures ARE master pictures, so the album passes
        the master here so a 'verified' tick lands in the one true cache. on_saved,
        when given, owns the post-save overview refresh (and replaces the Trip-only
        render-invalidation); Trips leave it None.

        master_db (album mode): when given, the day's texts are read straight from the
        master and source-text fixes are written to the master — because an Album is a
        window ON the master and its blog/website read the master, NOT the review CSVs
        (which the large viewer's edits no longer touch). Trips leave it None and keep
        the CSV read/write path."""
        super().__init__(parent)
        self.setWindowTitle(f"Translation Validation — {day_str}")
        self.resize(960, 720)
        self.trip_root = str(trip_root)
        self.day_str = day_str
        self.on_saved = on_saved
        self.master_db = master_db
        self.album_master = master_db is not None
        self.logger = logger
        self._cards = []

        self.translator = CachedTranslate(str(cache_root) if cache_root else self.trip_root)
        if self.album_master:
            # Read this day's pictures + files live from the master (mirrors the album
            # day viewer). Headline stays a Trip/blog-extra concept — master pictures
            # carry none — so in practice these are Description cards.
            self._day_pics = master_db.pictures_in_daterange(day_str, day_str).get(day_str, [])
            self._day_files = master_db.files_for_upns(
                [p["UniquePictureName"] for p in self._day_pics])
            self.fpr = None
        else:
            self._day_pics = None
            self._day_files = None
            self.fpr = FilePicture(None)
            self.fpr.readTripDay(self.trip_root, day_str)

        outer = QVBoxLayout(self)
        info = QLabel(
            "Review the machine translations. Fix typos in the <b>source</b> (left) "
            "— that is written back to the photo's XMP — correct the "
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

    def _picture_items(self):
        """(upn_name, picture_dict) for the day — from the master (album) or the day's
        review CSVs (trip). Both dict shapes carry Headline/Description/Files."""
        if self.album_master:
            return [(p["UniquePictureName"], p) for p in self._day_pics]
        return list(self.fpr.upn.UniquePictureName_dict.items())

    def _build_cards(self):
        for upn_name, pic in self._picture_items():
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

    def _write_master_description(self, upn, files, new_desc):
        """Album source-text fix: write the corrected Description to the master's
        xmp/jpg files (RAW stays immutable) and record it in the master DB via
        apply_description — the same door the album day viewer uses, so the blog and
        website (which read the master, not the review CSVs) publish the fix. Mirrors
        rating_provider._apply_master_description."""
        from engine.exif_writer import exiftool_write
        file_updates: dict = {}
        for fname in files:
            frow = self._day_files.get(fname)
            if not frow:
                continue
            if (frow.get("WindowsExtension") or "").lower() not in ("xmp", "jpg", "jpeg"):
                continue
            fpath = Path(frow.get("WindowsFileName", ""))
            if not fpath.exists():
                continue
            try:
                exiftool_write(fpath, [f"-XMP:Description={new_desc}"])
            except Exception as e:
                self.logger.warning(
                    f"master source write-back failed for {fpath.name}: {e}")
                continue
            file_updates[fname] = {"XMP:Description": new_desc}
        try:
            self.master_db.apply_description(upn, new_desc, file_updates=file_updates)
        except Exception as e:
            self.logger.warning(f"master DB description write failed for {upn}: {e}")

    def _on_save(self):
        verified_count = 0
        source_fixes = 0
        for c in self._cards:
            new_raw = c.source_edit.toPlainText().strip()
            new_trans = c.trans_edit.toPlainText().strip()
            if new_raw == "":
                continue
            # 1) source typo fix -> write back to the master (album) or the CSVs (trip)
            if new_raw != c.original_raw:
                if self.album_master:
                    if c.kind == "Description":
                        self._write_master_description(c.upn_name, c.files, new_raw)
                        source_fixes += 1
                    else:
                        # Master pictures carry no Headline (blog extras are excluded
                        # from the master), so there is nowhere to write one back.
                        self.logger.info(
                            f"album headline source-fix skipped (no master headline): "
                            f"{c.upn_name}")
                else:
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

        self.translator.writeTranslationCache(
            on_locked=lambda e: prompt_translation_locked(self, e))
        self.logger.info(
            f"Translation validation ({self.day_str}): {verified_count} verified, "
            f"{source_fixes} source text(s) corrected.")

        # Rebuild the overview so the Translations sphere updates live.
        parent = self.parent()

        # Album mode: the caller owns the refresh (its own tab) and there is no
        # per-day statemachine to invalidate, so we stop here — invalidating the
        # active trip's render day (below) would mark the WRONG trip's day stale.
        if self.on_saved is not None:
            try:
                self.on_saved()
            except Exception as e:
                self.logger.warning(f"overview refresh after validation failed: {e}")
            QMessageBox.information(
                self, "Translation Validation",
                f"Saved: {verified_count} verified, {source_fixes} source correction(s).")
            return

        # Trip mode. populate_trip_overview is a module-level function (not a bound
        # method), so call it directly with the main window — a hasattr() check fails.
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
