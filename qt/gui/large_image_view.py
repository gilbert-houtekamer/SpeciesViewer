"""Merged large-image viewer — image stage left, metadata panel right, filmstrip bottom.

Layout:
  QVBoxLayout
    QSplitter (horizontal, stretch)
      left  — image stage (colour-framed, async loaded)
      right — metadata panel (QScrollArea, ~300 px) with four sections:
                File / EXIF / Species (SpeciesInfoPanel or read-only) / Rating + Description
    separator line
    FilmstripWidget — one thumbnail per picture, lazy-loaded, colour-bordered

Navigation: ←/→ step through keys; assigning a rating or colour auto-advances
(cull-with-few-keystrokes). Three signals let hosts stay in sync:
  picture_changed(key)   — after any edit, host refreshes/re-sorts the card
  picture_navigated(key) — after ←/→, host moves its grid cursor + scrolls
  species_committed(upns) — after a species commit, host refreshes + offers Read & Summarize
"""

import logging

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QTextEdit,
    QSizePolicy, QScrollArea, QSplitter, QFrame, QPushButton,
)
from PyQt6.QtGui import QPixmap, QGuiApplication, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from gui.rating_editor import StarWidget, ColorSelector, COLOR_HEX
from gui.window_state import restore_window, save_window, clamp_to_visible_screen

logger = logging.getLogger("speciesviewer")

_DISCARD_COLORS = ("Blue", "Purple")
_DISCARD_GREYED_TIP = ("Has a JPG — kept for the website and a future DxO "
                       "re-export, so it can't be a deletion candidate.")

# ── Filmstrip geometry ─────────────────────────────────────────────────────────
_THUMB_W   = 144   # px — thumbnail width inside a strip cell
_THUMB_H   = 108   # px — thumbnail height (matches 4:3)
_ITEM_W    = 160   # px — total strip-cell width  (thumb + 8px margin × 2)
_ITEM_H    = 130   # px — total strip-cell height (thumb + gap + stars + margins)
_ITEM_GAP  = 4     # px — gap between cells
_PREFETCH  = 4     # cells to load beyond each viewport edge


def color_to_css(color: str) -> str:
    if not color:
        return ""
    return COLOR_HEX.get(color.capitalize(), color)


# ── Panel helpers ──────────────────────────────────────────────────────────────

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


# ── Filmstrip ──────────────────────────────────────────────────────────────────

class _StripItem(QWidget):
    """One thumbnail cell in the filmstrip — compact, colour-bordered."""
    tapped = pyqtSignal(str)  # emits key on left click

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self._key    = item.key
        self._item   = item
        self._requested = False   # True once request_load() has fired
        self._current   = False
        self._selected  = False

        self.setFixedSize(_ITEM_W, _ITEM_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(item.display_name or item.key)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(3, 3, 3, 2)
        vbox.setSpacing(2)

        self._thumb_lbl = QLabel()
        self._thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_lbl.setFixedSize(_THUMB_W, _THUMB_H)
        self._thumb_lbl.setStyleSheet("background: #1e1e1e;")
        vbox.addWidget(self._thumb_lbl)

        r = max(0, min(5, item.rating))
        self._star_lbl = QLabel("★" * r + "☆" * (5 - r))
        self._star_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._star_lbl.setStyleSheet(
            "font-size: 9px; color: #aaa; background: transparent;")
        vbox.addWidget(self._star_lbl)

        self._repaint_border()

    def _repaint_border(self):
        """Update cell border (current=white / normal=colour or grey)."""
        if self._current:
            self.setStyleSheet(
                "QWidget { border: 3px solid white; border-radius: 3px; "
                "background: #111; }")
        else:
            c = color_to_css(self._item.color)
            border = f"2px solid {c}" if c else "1px solid #3a3a3a"
            self.setStyleSheet(
                f"QWidget {{ border: {border}; border-radius: 3px; "
                f"background: #2a2a2a; }}")
        # Selection shown as blue star-bar (keeps colour border fully visible)
        if self._selected:
            self._star_lbl.setStyleSheet(
                "font-size: 9px; color: #fff; background: #2255cc; "
                "border-radius: 2px; padding: 0 4px;")
        else:
            self._star_lbl.setStyleSheet(
                "font-size: 9px; color: #aaa; background: transparent;")

    def set_current(self, yes: bool):
        if self._current == yes:
            return
        self._current = yes
        self._repaint_border()

    def set_selected(self, yes: bool):
        if self._selected == yes:
            return
        self._selected = yes
        self._repaint_border()

    def request_load(self):
        """Ask the provider for the thumbnail (idempotent — fires only once)."""
        if self._requested:
            return
        self._requested = True
        self._item.request_thumbnail(_THUMB_W, self._on_pixmap)

    def _on_pixmap(self, pixmap):
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                _THUMB_W, _THUMB_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._thumb_lbl.setPixmap(scaled)

    def refresh_from(self, item):
        """Repaint stars and colour border after an edit."""
        self._item = item
        r = max(0, min(5, item.rating))
        self._star_lbl.setText("★" * r + "☆" * (5 - r))
        self._repaint_border()  # also updates the star-bar colour

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.tapped.emit(self._key)
        super().mousePressEvent(event)


class FilmstripWidget(QWidget):
    """Horizontal thumbnail strip — one cell per picture, lazy-loaded."""
    item_clicked = pyqtSignal(str)  # key — host navigates on click

    def __init__(self, collection, keys, parent=None):
        super().__init__(parent)
        self._collection  = collection
        self._keys        = list(keys)
        self._cells: dict[str, _StripItem] = {}
        self._current_key: str | None = None

        # Minimum height: one row of cells + scrollbar (~17 px) + outer margins
        self.setMinimumHeight(_ITEM_H + 22)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(6)

        # "94 of 335" counter
        self._pos_lbl = QLabel("")
        self._pos_lbl.setStyleSheet(
            "font-size: 10px; color: #888; min-width: 58px;")
        self._pos_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(self._pos_lbl)

        # Horizontal-only scroll area
        self._sa = QScrollArea()
        self._sa.setWidgetResizable(False)
        self._sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._sa.horizontalScrollBar().valueChanged.connect(self._on_scrolled)
        outer.addWidget(self._sa, stretch=1)

        # Content strip
        self._strip = QWidget()
        strip_h = QHBoxLayout(self._strip)
        strip_h.setContentsMargins(2, 0, 2, 0)
        strip_h.setSpacing(_ITEM_GAP)

        for key in self._keys:
            try:
                item = collection.item_for_key(key)
            except (ValueError, KeyError):
                continue
            cell = _StripItem(item, self._strip)
            cell.tapped.connect(self.item_clicked)
            strip_h.addWidget(cell)
            self._cells[key] = cell

        n = len(self._cells)
        total_w = n * _ITEM_W + max(0, n - 1) * _ITEM_GAP + 4
        self._strip.setFixedSize(max(total_w, 1), _ITEM_H)
        self._sa.setWidget(self._strip)

        # Load initially-visible cells once the layout has settled.
        QTimer.singleShot(0, self._load_visible)

    def set_current(self, key: str, index: int, total: int):
        """Highlight `key`, scroll it into view, update the counter."""
        if self._current_key and self._current_key in self._cells:
            self._cells[self._current_key].set_current(False)
        self._current_key = key
        if key in self._cells:
            cell = self._cells[key]
            cell.set_current(True)
            QTimer.singleShot(0, lambda: self._sa.ensureWidgetVisible(cell))
        self._pos_lbl.setText(f"{index + 1} of {total}")

    def refresh_item(self, key: str):
        """Repaint a cell's stars + colour border after an edit."""
        if key not in self._cells:
            return
        try:
            item = self._collection.item_for_key(key)
        except (ValueError, KeyError):
            return
        self._cells[key].refresh_from(item)

    def set_selected(self, keys: set):
        """Update selection highlight on all cells."""
        for k, cell in self._cells.items():
            cell.set_selected(k in keys)

    # ── Lazy loading ───────────────────────────────────────────────────────────

    def _on_scrolled(self, _):
        self._load_visible()

    def _load_visible(self):
        bar    = self._sa.horizontalScrollBar()
        vp_l   = bar.value()
        vp_r   = vp_l + self._sa.viewport().width()
        stride = _ITEM_W + _ITEM_GAP
        first  = max(0, vp_l  // stride - _PREFETCH)
        last   = min(len(self._keys) - 1, vp_r // stride + _PREFETCH)
        for i in range(first, last + 1):
            key = self._keys[i]
            if key in self._cells:
                self._cells[key].request_load()


# ── Main dialog ────────────────────────────────────────────────────────────────

class LargeImageView(QDialog):
    picture_changed   = pyqtSignal(str)  # key — emitted after rating/colour/desc edit
    picture_navigated = pyqtSignal(str)  # key — emitted when ←/→ moves to a new picture
    species_committed = pyqtSignal(set)  # upns — emitted after a species commit in the panel
    selection_changed = pyqtSignal(set)  # keys — emitted whenever filmstrip selection changes

    def __init__(self, parent, collection, keys, index, *,
                 settings_key="LargeImageView", auto_advance=True,
                 initial_selection: set | None = None):
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
        self._panel_first_show = True
        self._selected_keys: set[str] = set(initial_selection) if initial_selection else set()
        self._bracket_left:  str | None = None
        self._bracket_right: str | None = None

        # ── Root layout ───────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 6)
        root.setSpacing(0)

        # ── Vertical splitter: image+panel (top) ↕ filmstrip area (bottom) ───
        self._v_splitter = QSplitter(Qt.Orientation.Vertical)
        self._v_splitter.setChildrenCollapsible(False)
        root.addWidget(self._v_splitter, stretch=1)

        # ── Horizontal splitter: image (left) + panel (right) ────────────────
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.splitterMoved.connect(lambda *_: self._fit_pixmap())
        self._v_splitter.addWidget(self._splitter)

        # Left — image stage
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
        self._image_label.mousePressEvent = lambda _e: self.setFocus()
        frame_l.addWidget(self._image_label)
        stage_h.addWidget(self._img_frame)
        stage_h.addStretch(1)
        stage_v.addLayout(stage_h)
        stage_v.addStretch(1)
        self._splitter.addWidget(self._stage)
        self._splitter.setStretchFactor(0, 1)

        # Right — metadata panel
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
        self._splitter.setStretchFactor(1, 0)

        # FILE section
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

        # EXIF section
        pl.addWidget(_section_header("EXIF"))
        self._exif_camera_lbl   = _field_lbl()
        self._exif_lens_lbl     = _small_lbl()
        self._exif_settings_lbl = _small_lbl()
        self._exif_focal_lbl    = _small_lbl()
        self._exif_dims_lbl     = _small_lbl()
        self._exif_date_lbl     = _small_lbl()
        self._exif_gps_lbl      = _small_lbl()
        for w in (self._exif_camera_lbl, self._exif_lens_lbl,
                  self._exif_settings_lbl, self._exif_focal_lbl,
                  self._exif_dims_lbl, self._exif_date_lbl, self._exif_gps_lbl):
            pl.addWidget(w)
        pl.addWidget(_hsep())

        # SPECIES section (container rebuilt on each picture)
        pl.addWidget(_section_header("SPECIES"))
        self._species_container = QWidget()
        self._species_cl = QVBoxLayout(self._species_container)
        self._species_cl.setContentsMargins(0, 0, 0, 0)
        self._species_cl.setSpacing(4)
        pl.addWidget(self._species_container)
        pl.addWidget(_hsep())

        # RATING section
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
        pl.addWidget(_small_lbl("Story for the website (optional)"))
        self._desc_edit = QTextEdit()
        fm = self._desc_edit.fontMetrics()
        self._desc_edit.setMinimumHeight(int(fm.lineSpacing() * 3) + 8)
        self._desc_edit.setMaximumHeight(int(fm.lineSpacing() * 8) + 8)
        self._desc_edit.setAcceptRichText(False)
        pl.addWidget(self._desc_edit)
        pl.addStretch(1)

        # ── Bottom widget (selection bar + separator + filmstrip) ─────────────
        bottom = QWidget()
        bottom_v = QVBoxLayout(bottom)
        bottom_v.setContentsMargins(0, 0, 0, 0)
        bottom_v.setSpacing(0)
        self._v_splitter.addWidget(bottom)

        # Selection bar (hidden when empty)
        self._sel_bar = QWidget()
        self._sel_bar.setStyleSheet("background: #1e2a3a; border-radius: 3px;")
        sel_row = QHBoxLayout(self._sel_bar)
        sel_row.setContentsMargins(8, 4, 8, 4)
        sel_row.setSpacing(8)
        self._sel_label = QLabel("")
        self._sel_label.setStyleSheet("font-size: 11px; color: #ccc;")
        sel_row.addWidget(self._sel_label)
        sel_row.addStretch()
        for _txt, _slot in (("Set species…", self._batch_species),
                             ("Set rating…",  self._batch_rating),
                             ("Clear",        self._clear_selection)):
            _btn = QPushButton(_txt)
            _btn.setFlat(True)
            _btn.setStyleSheet("font-size: 11px; color: #aad4ff; padding: 2px 6px;")
            _btn.clicked.connect(_slot)
            sel_row.addWidget(_btn)
        self._sel_bar.setVisible(False)
        bottom_v.addWidget(self._sel_bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #555; margin: 0;")
        bottom_v.addWidget(sep)

        self._filmstrip = FilmstripWidget(collection, self._keys, self)
        self._filmstrip.item_clicked.connect(self._on_filmstrip_click)
        bottom_v.addWidget(self._filmstrip, stretch=1)

        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save_description)

        self._apply_default_or_remembered_geometry()
        self._load_current()
        if self._selected_keys:
            self._update_selection_display()

    # ── Keyboard navigation ───────────────────────────────────────────────────

    def keyPressEvent(self, event):
        k = event.key()
        desc_focused = self._desc_edit.hasFocus()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right) and not desc_focused:
            self._prev() if k == Qt.Key.Key_Left else self._next()
            event.accept()
            return
        if k == Qt.Key.Key_BracketLeft and not desc_focused and self._keys:
            self._bracket_left = self._keys[self._index]
            self._selected_keys = self._selection_from_brackets()
            self._update_selection_display()
            event.accept()
            return
        if k == Qt.Key.Key_BracketRight and not desc_focused and self._keys:
            self._bracket_right = self._keys[self._index]
            self._selected_keys = self._selection_from_brackets()
            self._update_selection_display()
            event.accept()
            return
        if k == Qt.Key.Key_Escape and self._selected_keys:
            self._clear_selection()
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
        self._filmstrip.set_current(key, self._index, len(self._keys))

        # FILE
        self._file_key_lbl.setText(item.key)
        exts = item.file_exts
        self._file_exts_lbl.setText("  ·  ".join(exts) if exts else "")
        self._file_exts_lbl.setVisible(bool(exts))
        dir_path = self._collection.file_directory(key)
        self._file_dir_lbl.setText(dir_path)
        self._file_dir_lbl.setVisible(bool(dir_path))

        # EXIF
        exif = item.exif
        self._exif_camera_lbl.setText(exif.model or "")
        self._exif_camera_lbl.setVisible(bool(exif.model))
        self._exif_lens_lbl.setText(exif.lens or "")
        self._exif_lens_lbl.setVisible(bool(exif.lens))

        shot = []
        if exif.shutter:
            shot.append(f"{exif.shutter}s")
        if exif.aperture:
            shot.append(f"f/{exif.aperture}")
        if exif.iso:
            shot.append(f"ISO {exif.iso}")
        if exif.ev_comp:
            shot.append(f"EV {exif.ev_comp}")
        shot_t = "  ·  ".join(shot)
        self._exif_settings_lbl.setText(shot_t)
        self._exif_settings_lbl.setVisible(bool(shot_t))

        focal = []
        if exif.focal_length:
            focal.append(exif.focal_length)
        if exif.distance:
            focal.append(f"@ {exif.distance}")
        focal_t = "  ·  ".join(focal)
        self._exif_focal_lbl.setText(focal_t)
        self._exif_focal_lbl.setVisible(bool(focal_t))

        self._exif_dims_lbl.setText(exif.dimensions or "")
        self._exif_dims_lbl.setVisible(bool(exif.dimensions))
        # Keep the full datetime ("2026:03:16 15:30:45" → "2026-03-16 15:30:45")
        date_t = item.date.replace(":", "-", 2) if item.date else ""
        self._exif_date_lbl.setText(date_t)
        self._exif_date_lbl.setVisible(bool(date_t))
        self._exif_gps_lbl.setText(exif.gps or "")
        self._exif_gps_lbl.setVisible(bool(exif.gps))

        # SPECIES
        self._rebuild_species_section(item, key)

        # RATING
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

        # Async full-size image — drop deliveries from any previous picture.
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
        self._image_label.setText("") if self._pixmap.isNull() else None
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
                collapsible_error=True,
            )
            panel.committed.connect(self._on_species_committed)
            self._species_cl.addWidget(panel)
        else:
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
                ph = _small_lbl("(no species)")
                ph.setStyleSheet("font-size: 10px; color: #999; font-style: italic;")
                self._species_cl.addWidget(ph)

    def _on_species_committed(self, upns: set):
        if self._item is not None:
            self._rebuild_species_section(self._item, self._item.key)
            for k in upns:
                self._filmstrip.refresh_item(k)
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

    def _on_filmstrip_click(self, key: str):
        if key not in self._keys:
            return
        new = self._keys.index(key)
        if new == self._index:
            return
        self._save_description()
        self._index = new
        self._load_current()
        self.picture_navigated.emit(key)

    # ── Selection ─────────────────────────────────────────────────────────────

    def _selection_from_brackets(self) -> set:
        left, right = self._bracket_left, self._bracket_right
        order = self._keys
        if not left and not right:
            return set()
        if not left or not right:
            anchor = left or right
            return {anchor} if anchor in order else set()
        try:
            a, b = order.index(left), order.index(right)
        except ValueError:
            surviving = next((k for k in (left, right) if k in order), None)
            return {surviving} if surviving else set()
        if a > b:
            a, b = b, a
        return set(order[a:b + 1])

    def _update_selection_display(self):
        self._filmstrip.set_selected(self._selected_keys)
        n = len(self._selected_keys)
        self._sel_bar.setVisible(n > 0)
        if n > 0:
            self._sel_label.setText(
                f"{n} picture{'s' if n != 1 else ''} selected")
        self.selection_changed.emit(set(self._selected_keys))

    def _clear_selection(self):
        self._selected_keys.clear()
        self._bracket_left = None
        self._bracket_right = None
        self._update_selection_display()

    def _batch_species(self):
        if not self._selected_keys:
            return
        fpr = None
        file_paths, species_names, error_msgs = [], [], []
        for k in self._selected_keys:
            ctx = self._collection.species_editor_context(k)
            if ctx is None:
                continue
            f, fps = ctx
            if fpr is None:
                fpr = f
            try:
                item = self._collection.item_for_key(k)
            except (ValueError, KeyError):
                continue
            file_paths.extend(fps)
            species_names.extend([item.species or ""] * len(fps))
            error_msgs.extend([item.error_msg or ""] * len(fps))
        if not fpr or not file_paths:
            return
        from gui.species_editor import SpeciesInfoPanel
        n = len(file_paths)
        dlg = QDialog(self)
        dlg.setWindowTitle(
            f"Set species — {n} picture{'s' if n != 1 else ''}")
        dlg.setMinimumWidth(600)
        layout = QVBoxLayout(dlg)
        panel = SpeciesInfoPanel(
            fpr, file_paths, species_names, error_msgs, parent=dlg)
        panel.committed.connect(lambda upns: (
            self._on_species_committed(upns),
            self._clear_selection(),
            dlg.accept(),
        ))
        layout.addWidget(panel)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(cancel_btn)
        layout.addLayout(row)
        dlg.show()

    def _batch_rating(self):
        if not self._selected_keys:
            return
        from PyQt6.QtWidgets import QDialogButtonBox
        n = len(self._selected_keys)
        dlg = QDialog(self)
        dlg.setWindowTitle(
            f"Set rating / color — {n} picture{'s' if n != 1 else ''}")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(
            f"Apply to {n} picture{'s' if n != 1 else ''}:"))
        star_row = QHBoxLayout()
        star_row.addWidget(QLabel("Rating:"))
        star_w = StarWidget(0)
        star_row.addWidget(star_w)
        star_row.addStretch()
        layout.addLayout(star_row)
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Color:"))
        color_w = ColorSelector("")
        color_row.addWidget(color_w)
        color_row.addStretch()
        layout.addLayout(color_row)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        stars = star_w.rating
        color = color_w.color
        if not stars and not color:
            return
        key_list = list(self._selected_keys)
        # Apply writes synchronously — no processEvents() so the host's
        # resort/refresh can't interleave and cause index mismatches.
        changed: list[str] = []
        for k in key_list:
            try:
                item = self._collection.item_for_key(k)
                if stars > 0:
                    item.set_rating(stars)
                if color:
                    item.set_color(color)
                changed.append(k)
            except (ValueError, KeyError):
                pass
        # Refresh filmstrip and notify host only after all writes are done.
        for k in changed:
            self._filmstrip.refresh_item(k)
        for k in changed:
            self.picture_changed.emit(k)
        cur_key = self._keys[self._index] if self._keys else None
        if cur_key and cur_key in self._selected_keys:
            self._load_current()

    # ── Edits ─────────────────────────────────────────────────────────────────

    def _on_rating(self, n: int):
        if self._item is None or not self._item.can_edit_rating:
            return
        self._item.set_rating(n)
        self._rate_lbl.setText(f"Rate: {self._item.rate}" if self._item.rate is not None else "")
        self._filmstrip.refresh_item(self._item.key)
        self.picture_changed.emit(self._item.key)
        if self._auto_advance and n > 0:
            self._next()

    def _on_color(self, color: str):
        if self._item is None or not self._item.can_edit_color:
            return
        self._item.set_color(color)
        self._apply_frame_color(color)
        self._rate_lbl.setText(f"Rate: {self._item.rate}" if self._item.rate is not None else "")
        self._filmstrip.refresh_item(self._item.key)
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
            # H-splitter: image | panel
            panel_w = 300
            image_w = max(200, self.width() - panel_w - self._splitter.handleWidth())
            self._splitter.setSizes([image_w, panel_w])
            # V-splitter: image+panel | filmstrip
            strip_h = _ITEM_H + 40
            top_h = max(200, self.height() - strip_h - self._v_splitter.handleWidth())
            self._v_splitter.setSizes([top_h, strip_h])
        QTimer.singleShot(0, self._fit_pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_pixmap()

    def closeEvent(self, event):
        self._save_description()
        save_window(self, self._geo_key)
        super().closeEvent(event)
