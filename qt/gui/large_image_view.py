"""Merged large-image viewer — image stage on the left, metadata panel on the right.

The panel has four collapsible-style sections (top→bottom):
  File    — UPN key, file-type badges, directory path
  EXIF    — camera/lens, exposure settings, focal/distance, dims, date, GPS
  Species — SpeciesInfoPanel when the engine supports it; read-only labels otherwise
  Rating  — stars, colour, computed Rate, description story field

Both hosts (Species Preview and Rating Editor) share this viewer via the
PictureCollection/PictureItem provider seam — no engine knowledge inside here.

Navigation: ←/→ step through keys; assigning a rating or colour auto-advances
(the cull-with-few-keystrokes flow). picture_changed(key) lets the host refresh
a card and re-sort. species_committed(upns) lets the host handle the
refresh_after_species_change + Read & Summarize offer.
"""

import logging

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QTextEdit,
    QSizePolicy, QScrollArea, QSplitter, QFrame,
)
from PyQt6.QtGui import QPixmap, QGuiApplication, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from gui.rating_editor import StarWidget, ColorSelector, COLOR_HEX
from gui.window_state import restore_window, save_window, clamp_to_visible_screen

logger = logging.getLogger("speciesviewer")

_DISCARD_COLORS = ("Blue", "Purple")
_DISCARD_GREYED_TIP = ("Has a JPG — kept for the website and a future DxO "
                       "re-export, so it can't be a deletion candidate.")


def color_to_css(color: str) -> str:
    if not color:
        return ""
    return COLOR_HEX.get(color.capitalize(), color)


# ── Panel widget helpers ───────────────────────────────────────────────────────

def _section_header(title: str) -> QLabel:
    lbl = QLabel(title)
    lbl.setStyleSheet(
        "font-weight: bold; font-size: 10px; color: #555; "
        "padding: 5px 0 1px 0; letter-spacing: 0.5px;")
    return lbl


def _hsep() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setStyleSheet("color: #ddd; margin: 2px 0;")
    return line


def _field_lbl(text: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("font-size: 11px;")
    return lbl


def _small_lbl(text: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("font-size: 10px; color: #666;")
    return lbl


def _clear_layout(layout):
    while layout.count():
        child = layout.takeAt(0)
        if child.widget():
            child.widget().deleteLater()


# ── Main dialog ────────────────────────────────────────────────────────────────

class LargeImageView(QDialog):
    picture_changed   = pyqtSignal(str)  # key — emitted after rating/colour/desc edit
    picture_navigated = pyqtSignal(str)  # key — emitted when ←/→ moves to a new picture
    species_committed = pyqtSignal(set)  # upns — emitted after a species commit in the panel

    def __init__(self, parent, collection, keys, index, *,
                 settings_key="LargeImageView", auto_advance=True):
        """
        collection     PictureCollection backing the pictures.
        keys           ordered snapshot of keys to navigate (the host's grid order).
        index          start position in keys.
        settings_key   namespace for remembered geometry (.ini).
        auto_advance   True → assigning a rating/colour steps to the next picture.
        """
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._collection = collection
        self._keys = list(keys)
        self._index = max(0, min(index, len(self._keys) - 1)) if self._keys else 0
        self._auto_advance = auto_advance
        self._settings_key = settings_key
        self._geo_key = f"{settings_key}Large"
        self._item = None
        self._pixmap = QPixmap()
        self._load_token = 0
        self._original_description = ""
        self._panel_first_show = True  # deferred splitter sizing

        # ── Main layout: thin margin, then a horizontal splitter ──────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 6)
        root.setSpacing(0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.splitterMoved.connect(lambda *_: self._fit_pixmap())
        root.addWidget(self._splitter, stretch=1)

        # ── Left: image stage ─────────────────────────────────────────────────
        self._stage = QWidget()
        self._stage.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        stage_v = QVBoxLayout(self._stage)
        stage_v.setContentsMargins(0, 0, 0, 0)
        stage_v.addStretch(1)
        stage_h = QHBoxLayout()
        stage_h.setContentsMargins(0, 0, 0, 0)
        stage_h.addStretch(1)
        self._img_frame = QWidget()
        self._img_frame.setObjectName("imgFrame")
        frame_l = QVBoxLayout(self._img_frame)
        frame_l.setContentsMargins(4, 4, 4, 4)
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(1, 1)
        # Click the image to leave the description box so ←/→ resume navigating.
        self._image_label.mousePressEvent = lambda _e: self.setFocus()
        frame_l.addWidget(self._image_label)
        stage_h.addWidget(self._img_frame)
        stage_h.addStretch(1)
        stage_v.addLayout(stage_h)
        stage_v.addStretch(1)
        self._splitter.addWidget(self._stage)
        self._splitter.setStretchFactor(0, 1)  # image grows when dialog resizes

        # ── Right: metadata panel ─────────────────────────────────────────────
        panel_scroll = QScrollArea()
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel_scroll.setMinimumWidth(220)

        self._panel_widget = QWidget()
        self._panel_widget.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        pl = QVBoxLayout(self._panel_widget)
        pl.setContentsMargins(8, 6, 8, 6)
        pl.setSpacing(2)
        panel_scroll.setWidget(self._panel_widget)
        self._splitter.addWidget(panel_scroll)
        self._splitter.setStretchFactor(1, 0)  # panel doesn't stretch

        # ── Section: FILE ─────────────────────────────────────────────────────
        pl.addWidget(_section_header("FILE"))
        self._file_key_lbl = _field_lbl()
        self._file_key_lbl.setStyleSheet(
            "font-weight: bold; font-size: 12px; padding-bottom: 1px;")
        pl.addWidget(self._file_key_lbl)
        self._file_exts_lbl = _small_lbl()
        pl.addWidget(self._file_exts_lbl)
        self._file_dir_lbl = _small_lbl()
        pl.addWidget(self._file_dir_lbl)
        pl.addWidget(_hsep())

        # ── Section: EXIF ─────────────────────────────────────────────────────
        pl.addWidget(_section_header("EXIF"))
        self._exif_camera_lbl  = _field_lbl()
        self._exif_lens_lbl    = _small_lbl()
        self._exif_settings_lbl = _small_lbl()
        self._exif_focal_lbl   = _small_lbl()
        self._exif_dims_lbl    = _small_lbl()
        self._exif_date_lbl    = _small_lbl()
        self._exif_gps_lbl     = _small_lbl()
        for w in (self._exif_camera_lbl, self._exif_lens_lbl,
                  self._exif_settings_lbl, self._exif_focal_lbl,
                  self._exif_dims_lbl, self._exif_date_lbl, self._exif_gps_lbl):
            pl.addWidget(w)
        pl.addWidget(_hsep())

        # ── Section: SPECIES (container rebuilt on each picture) ──────────────
        pl.addWidget(_section_header("SPECIES"))
        self._species_container = QWidget()
        self._species_cl = QVBoxLayout(self._species_container)
        self._species_cl.setContentsMargins(0, 0, 0, 0)
        self._species_cl.setSpacing(4)
        pl.addWidget(self._species_container)
        pl.addWidget(_hsep())

        # ── Section: RATING ───────────────────────────────────────────────────
        pl.addWidget(_section_header("RATING"))

        stars_row = QHBoxLayout()
        stars_row.setContentsMargins(0, 2, 0, 2)
        self._stars = StarWidget(0)
        self._stars.rating_changed.connect(self._on_rating)
        stars_row.addWidget(self._stars)
        stars_row.addStretch()
        self._rate_lbl = QLabel("")
        self._rate_lbl.setStyleSheet("font-size: 11px; color: #555;")
        stars_row.addWidget(self._rate_lbl)
        pl.addLayout(stars_row)

        self._color_sel = ColorSelector("")
        self._color_sel.color_changed.connect(self._on_color)
        pl.addWidget(self._color_sel)

        pl.addWidget(_section_header("DESCRIPTION"))
        desc_hint = _small_lbl("Story for the website (optional)")
        pl.addWidget(desc_hint)
        self._desc_edit = QTextEdit()
        fm = self._desc_edit.fontMetrics()
        self._desc_edit.setMinimumHeight(int(fm.lineSpacing() * 3) + 8)
        self._desc_edit.setMaximumHeight(int(fm.lineSpacing() * 8) + 8)
        self._desc_edit.setAcceptRichText(False)
        pl.addWidget(self._desc_edit)

        pl.addStretch(1)

        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save_description)

        self._apply_default_or_remembered_geometry()
        self._load_current()

    # ── Keyboard navigation ───────────────────────────────────────────────────

    def keyPressEvent(self, event):
        k = event.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right) and not self._desc_edit.hasFocus():
            self._prev() if k == Qt.Key.Key_Left else self._next()
            event.accept()
            return
        super().keyPressEvent(event)

    # ── Load the current picture ──────────────────────────────────────────────

    def _load_current(self):
        if not self._keys:
            return
        key = self._keys[self._index]
        try:
            item = self._collection.item_for_key(key)
        except (ValueError, KeyError):
            return
        self._item = item

        pos = f"  ({self._index + 1} of {len(self._keys)})"
        self.setWindowTitle((item.display_name or key) + pos)

        # File section
        self._file_key_lbl.setText(item.key)
        exts = item.file_exts
        self._file_exts_lbl.setText("  ·  ".join(exts) if exts else "")
        self._file_exts_lbl.setVisible(bool(exts))
        dir_path = self._collection.file_directory(key)
        self._file_dir_lbl.setText(dir_path)
        self._file_dir_lbl.setVisible(bool(dir_path))

        # EXIF section
        exif = item.exif
        self._exif_camera_lbl.setText(exif.model or "")
        self._exif_camera_lbl.setVisible(bool(exif.model))
        self._exif_lens_lbl.setText(exif.lens or "")
        self._exif_lens_lbl.setVisible(bool(exif.lens))

        shot_parts = []
        if exif.shutter:
            shot_parts.append(f"{exif.shutter}s")
        if exif.aperture:
            shot_parts.append(f"f/{exif.aperture}")
        if exif.iso:
            shot_parts.append(f"ISO {exif.iso}")
        if exif.ev_comp:
            shot_parts.append(f"EV {exif.ev_comp}")
        shot_text = "  ·  ".join(shot_parts)
        self._exif_settings_lbl.setText(shot_text)
        self._exif_settings_lbl.setVisible(bool(shot_text))

        focal_parts = []
        if exif.focal_length:
            focal_parts.append(exif.focal_length)
        if exif.distance:
            focal_parts.append(f"@ {exif.distance}")
        focal_text = "  ·  ".join(focal_parts)
        self._exif_focal_lbl.setText(focal_text)
        self._exif_focal_lbl.setVisible(bool(focal_text))

        self._exif_dims_lbl.setText(exif.dimensions or "")
        self._exif_dims_lbl.setVisible(bool(exif.dimensions))

        date_text = str(item.date)[:10].replace(":", "-") if item.date else ""
        self._exif_date_lbl.setText(date_text)
        self._exif_date_lbl.setVisible(bool(date_text))

        self._exif_gps_lbl.setText(exif.gps or "")
        self._exif_gps_lbl.setVisible(bool(exif.gps))

        # Species section
        self._rebuild_species_section(item, key)

        # Rating section
        self._stars.set_rating(item.rating)
        self._stars.setEnabled(item.can_edit_rating)
        self._color_sel.set_color(item.color)
        if item.can_edit_color:
            self._color_sel.setEnabled(True)
            self._color_sel.set_disabled_colors(
                _DISCARD_COLORS if item.has_jpg else (), _DISCARD_GREYED_TIP)
        else:
            self._color_sel.setEnabled(False)
        self._rate_lbl.setText(f"Rate: {item.rate}" if item.rate is not None else "")
        self._apply_frame_color(item.color or "")

        self._original_description = item.description or ""
        self._desc_edit.blockSignals(True)
        self._desc_edit.setPlainText(self._original_description)
        self._desc_edit.setReadOnly(not item.can_edit_description)
        self._desc_edit.setToolTip("" if item.can_edit_description
                                   else "Descriptions can't be saved for this picture.")
        self._desc_edit.blockSignals(False)

        # Async image load — drop deliveries from any previous picture.
        self._load_token += 1
        tok = self._load_token
        self._pixmap = QPixmap()
        self._image_label.setText("Loading…")
        item.request_full_pixmap(lambda px, t=tok: self._on_pixmap(t, px))

        self.setFocus()

    def _on_pixmap(self, token, pixmap):
        if token != self._load_token:
            return
        self._pixmap = pixmap if (pixmap is not None and not pixmap.isNull()) else QPixmap()
        if self._pixmap.isNull():
            self._image_label.setText("(no image)")
        else:
            self._fit_pixmap()

    # ── Species section rebuild ───────────────────────────────────────────────

    def _rebuild_species_section(self, item, key: str):
        _clear_layout(self._species_cl)
        ctx = self._collection.species_editor_context(key)
        if ctx is not None:
            fpr, file_paths = ctx
            from gui.species_editor import SpeciesInfoPanel
            panel = SpeciesInfoPanel(
                fpr,
                file_paths=file_paths,
                species_names=[item.species or ""],
                error_msgs=[item.error_msg or ""],
                parent=self._species_container,
            )
            panel.committed.connect(self._on_species_committed)
            self._species_cl.addWidget(panel)
        else:
            # Read-only display (e.g. Rating Editor — data already validated)
            if item.species:
                lbl = _field_lbl(item.species)
                lbl.setStyleSheet("font-size: 11px; font-weight: bold;")
                self._species_cl.addWidget(lbl)
            if item.headline:
                self._species_cl.addWidget(_small_lbl(item.headline))
            if item.error_msg:
                err = _small_lbl(f"⚠  {item.error_msg}")
                err.setStyleSheet("font-size: 10px; color: #c0392b;")
                self._species_cl.addWidget(err)
            if not item.species and not item.error_msg:
                placeholder = _small_lbl("(no species)")
                placeholder.setStyleSheet("font-size: 10px; color: #999; font-style: italic;")
                self._species_cl.addWidget(placeholder)

    def _on_species_committed(self, upns: set):
        # Rebuild the species section so the new name shows immediately.
        if self._item is not None:
            self._rebuild_species_section(self._item, self._item.key)
        self.species_committed.emit(upns)
        for k in upns:
            self.picture_changed.emit(k)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _go(self, delta: int):
        if not self._keys:
            return
        self._save_description()
        new = max(0, min(self._index + delta, len(self._keys) - 1))
        if new == self._index:
            return
        self._index = new
        self._load_current()
        self.picture_navigated.emit(self._keys[self._index])

    def _prev(self):
        self._go(-1)

    def _next(self):
        self._go(+1)

    # ── Edits ─────────────────────────────────────────────────────────────────

    def _on_rating(self, n: int):
        if self._item is None or not self._item.can_edit_rating:
            return
        self._item.set_rating(n)
        self._rate_lbl.setText(f"Rate: {self._item.rate}" if self._item.rate is not None else "")
        self.picture_changed.emit(self._item.key)
        if self._auto_advance and n > 0:
            self._next()

    def _on_color(self, color: str):
        if self._item is None or not self._item.can_edit_color:
            return
        self._item.set_color(color)
        self._apply_frame_color(color)
        self._rate_lbl.setText(f"Rate: {self._item.rate}" if self._item.rate is not None else "")
        self.picture_changed.emit(self._item.key)
        if self._auto_advance and color:
            self._next()

    def _save_description(self):
        if self._item is None or not self._item.can_edit_description:
            return
        text = self._desc_edit.toPlainText()
        if text == self._original_description:
            return
        try:
            self._item.set_description(text)
            self._original_description = text
            self.picture_changed.emit(self._item.key)
        except Exception as e:
            logger.warning(f"LargeImageView: description save failed: {e}")

    # ── Colour frame ──────────────────────────────────────────────────────────

    def _apply_frame_color(self, color: str):
        css = color_to_css(color)
        if css:
            self._img_frame.setStyleSheet(
                f"QWidget#imgFrame {{ border: 4px solid {css}; border-radius: 6px; }}")
        else:
            self._img_frame.setStyleSheet("")

    # ── Geometry ──────────────────────────────────────────────────────────────

    def _apply_default_or_remembered_geometry(self):
        avail = QGuiApplication.primaryScreen().availableGeometry()
        restore_window(self, self._geo_key,
                       default_size=(int(avail.width() * 0.80),
                                     int(avail.height() * 0.85)))

    # ── Pixmap fit ─────────────────────────────────────────────────────────────

    def _fit_pixmap(self):
        if self._pixmap.isNull():
            return
        size = self._stage.size()
        avail_w = size.width() - 10
        avail_h = size.height() - 10
        if avail_w <= 1 or avail_h <= 1:
            return
        src_w, src_h = self._pixmap.width(), self._pixmap.height()
        if src_w <= avail_w and src_h <= avail_h:
            scaled = self._pixmap
        else:
            scaled = self._pixmap.scaled(avail_w, avail_h,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
        self._image_label.setPixmap(scaled)
        self._image_label.setMaximumSize(scaled.size())

    def showEvent(self, event):
        super().showEvent(event)
        clamp_to_visible_screen(self)
        self.setFocus()
        if self._panel_first_show:
            self._panel_first_show = False
            # Give panel ~300 px on first open; image takes the rest.
            panel_w = 300
            image_w = max(200, self.width() - panel_w - self._splitter.handleWidth())
            self._splitter.setSizes([image_w, panel_w])
        QTimer.singleShot(0, self._fit_pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_pixmap()

    def closeEvent(self, event):
        self._save_description()
        save_window(self, self._geo_key)
        super().closeEvent(event)
