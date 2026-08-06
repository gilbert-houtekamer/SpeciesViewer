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
import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QTextEdit,
    QLineEdit, QSizePolicy, QScrollArea, QSplitter, QFrame, QPushButton, QToolButton,
    QStyle, QMessageBox, QTabWidget,
)
from PyQt6.QtGui import (
    QMovie, QPixmap, QImage, QGuiApplication, QShortcut, QKeySequence, QPainter, QColor,
    QIcon, QFont)
from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal

from gui.rating_editor import StarWidget, ColorSelector, COLOR_HEX
from gui import theme
from gui.image_loader import AsyncImageLoader
from gui.window_state import restore_window, save_window, clamp_to_visible_screen
from utils.open_path import open_path, reveal_group, launch_app_with_file
from utils.config import get_setting, set_setting, get_window_state

logger = logging.getLogger("speciesviewer")

_DISCARD_COLORS = ("Blue", "Purple")
_DISCARD_GREYED_TIP = ("Has a JPG — kept for the website and a future DxO "
                       "re-export, so it can't be a deletion candidate.")

# Where DxO PhotoLab is installed. The path is configurable in Settings →
# Global Settings ([Global] dxo_path); this is the fallback used when unset.
_DEFAULT_DXO_PATH = r"C:\Program Files\DxO\DxO PhotoLab 9\DxO.PhotoLab.exe"


def _make_pl_icon(size: int = 18) -> QIcon:
    """A small navy 'PL' badge standing in for DxO PhotoLab — drawn in code so
    there's no binary asset to ship and it renders the same on Win/Mac."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#16223d"))
    p.drawRoundedRect(0, 0, size, size, 4, 4)
    p.setPen(QColor("#e8eefc"))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(max(7, int(size * 0.5)))
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "PL")
    p.end()
    return QIcon(pm)


def dxo_executable() -> str:
    """The configured DxO PhotoLab path, falling back to the default install."""
    return (get_setting("Global", "dxo_path", fallback="") or "").strip() or _DEFAULT_DXO_PATH

# ── Filmstrip geometry ─────────────────────────────────────────────────────────
_THUMB_W   = 144   # px — thumbnail width inside a strip cell
_THUMB_H   = 108   # px — thumbnail height (matches 4:3)
_ITEM_W    = 160   # px — total strip-cell width  (thumb + frame + margins)
_ITEM_H    = 140   # px — total strip-cell height (framed thumb + stars + margins)
_ITEM_GAP  = 4     # px — gap between cells
_PREFETCH  = 4     # cells to load beyond each viewport edge



def color_to_css(color: str) -> str:
    if not color:
        return ""
    return COLOR_HEX.get(color.capitalize(), color)


# ── Panel helpers ──────────────────────────────────────────────────────────────

# Biota vocabulary: mono, letter-spaced uppercase section headers; the display
# face for names/values you read; the mono utility face for data you scan.
_MONO = "'Cascadia Mono','Cascadia Code',Consolas,monospace"


def _section_header(title: str) -> QLabel:
    lbl = QLabel(title.upper())
    lbl.setStyleSheet(
        f"font-family:{_MONO}; font-size:9.5px; font-weight:600;"
        f" letter-spacing:1.4px; color:{theme.INK_SOFT}; padding:6px 0 3px 0;")
    return lbl


def _hsep() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setStyleSheet(f"color:{theme.LINE}; margin:3px 0;")
    return line


def _field_lbl(text: str = "") -> QLabel:
    """A value you read (place, camera, species) — display face, ink."""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"font-size:12.5px; color:{theme.INK};")
    return lbl


def _small_lbl(text: str = "") -> QLabel:
    """Data you scan (exts, GPS, exposure, path) — mono utility face, soft ink."""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"font-family:{_MONO}; font-size:10.5px; color:{theme.INK_SOFT};")
    return lbl


def _clear_layout(layout):
    while layout.count():
        child = layout.takeAt(0)
        if child.widget():
            child.widget().deleteLater()


class _KVRow(QWidget):
    """A right-panel key→value row (the mockup's clean look): mono key on the
    left, value right-aligned. setText sets the value; setVisible hides the whole
    row so an empty field leaves no dangling key."""

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 1, 0, 1)
        h.setSpacing(10)
        k = QLabel(key)
        k.setStyleSheet(f"font-family:{_MONO}; font-size:10.5px; color:{theme.INK_SOFT};")
        self._v = QLabel()
        self._v.setStyleSheet(f"font-family:{_MONO}; font-size:11px; color:{theme.INK};")
        self._v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self._v.setWordWrap(True)
        h.addWidget(k, 0, Qt.AlignmentFlag.AlignTop)
        h.addWidget(self._v, 1)

    def setText(self, t: str):
        self._v.setText(t)

    def text(self) -> str:
        return self._v.text()


class _CollapsibleSection(QWidget):
    """A metadata-panel section with a clickable mono header that collapses its
    body. The picture drives the window height; when the panel is short the user
    collapses the sections they don't need (and scrolls for the rest) rather than
    the text forcing a taller window. Collapsed state persists per section under
    [large_viewer] so a chosen layout survives the next open."""

    def __init__(self, title: str, *, setting_key: str | None = None,
                 start_collapsed: bool = False, parent=None):
        super().__init__(parent)
        self._setting_key = setting_key
        if setting_key is not None:
            start_collapsed = get_setting(
                "large_viewer", f"collapsed_{setting_key}",
                fallback="yes" if start_collapsed else "no").strip().lower() == "yes"
        self._collapsed = start_collapsed

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header row — triangle + title, the whole strip is the click target.
        self._hdr = QWidget()
        self._hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hrow = QHBoxLayout(self._hdr)
        self._hrow.setContentsMargins(0, 6, 0, 3)
        self._hrow.setSpacing(5)
        self._tri = QLabel()
        self._tri.setStyleSheet(f"font-size:8px; color:{theme.INK_SOFT};")
        self._tri.setFixedWidth(11)
        title_lbl = QLabel(title.upper())
        title_lbl.setStyleSheet(
            f"font-family:{_MONO}; font-size:9.5px; font-weight:600;"
            f" letter-spacing:1.4px; color:{theme.INK_SOFT};")
        self._hrow.addWidget(self._tri)
        self._hrow.addWidget(title_lbl)
        self._hrow.addStretch(1)
        self._hdr.mousePressEvent = self._on_header_click
        outer.addWidget(self._hdr)

        # Body — the section's actual rows.
        self._body = QWidget()
        self._body_l = QVBoxLayout(self._body)
        self._body_l.setContentsMargins(0, 0, 0, 3)
        self._body_l.setSpacing(2)
        outer.addWidget(self._body)

        outer.addWidget(_hsep())
        self._apply_state()

    # Body content ─────────────────────────────────────────────────────────────
    def add_widget(self, w):
        self._body_l.addWidget(w)

    def add_layout(self, lay):
        self._body_l.addLayout(lay)

    def add_trailing_widget(self, w):
        """Add a control to the RIGHT of the header (e.g. the FILE row's Explorer
        / PhotoLab icons), so it rides the title strip rather than a value row."""
        self._hrow.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)

    # State ──────────────────────────────────────────────────────────────────────
    def _on_header_click(self, _e):
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, yes: bool):
        self._collapsed = yes
        self._apply_state()
        if self._setting_key is not None:
            set_setting("large_viewer", f"collapsed_{self._setting_key}",
                        "yes" if yes else "no")

    def _apply_state(self):
        self._tri.setText("▶" if self._collapsed else "▼")
        self._body.setVisible(not self._collapsed)


# ── Filmstrip ──────────────────────────────────────────────────────────────────

class _StripItem(QWidget):
    """One thumbnail cell in the filmstrip — compact, colour-bordered."""
    tapped = pyqtSignal(str)         # emits key on left click
    right_clicked = pyqtSignal(str)  # emits key on right click (context menu)

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

        # Framed cell: an olive/colour picture frame with a white mat, matching
        # the grid card. Colour label = frame colour; current cell = white ring.
        self._frame = QFrame()
        self._frame.setObjectName("scell")
        fl = QVBoxLayout(self._frame)
        fl.setContentsMargins(4, 4, 4, 4)
        fl.setSpacing(0)
        self._thumb_lbl = QLabel()
        self._thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_lbl.setFixedSize(_THUMB_W, _THUMB_H)
        self._thumb_lbl.setStyleSheet(
            f"background:{theme.SURFACE_2}; border:2px solid {theme.SURFACE}; border-radius:2px;")
        fl.addWidget(self._thumb_lbl)
        vbox.addWidget(self._frame)

        self._star_lbl = QLabel(self._star_text(item))
        self._star_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(self._star_lbl)

        self._repaint_border()

    def _star_text(self, item) -> str:
        """Stars, plus a ⌗ crop mark (crop is its own signal — no '#' on the
        image, matching the grid/large-view text)."""
        r = max(0, min(5, item.rating))
        s = "★" * r + "☆" * (5 - r)
        if getattr(item, "has_crop", False):
            s += "  ⌗"
        return s

    def _repaint_border(self):
        """Paint the frame: colour label (olive when unlabelled); current cell
        adds a white ring. Selection shows as a blue star-bar."""
        c = color_to_css(self._item.color) or theme.ACCENT
        ring = " border:2px solid #fff;" if self._current else ""
        self._frame.setStyleSheet(
            f"QFrame#scell {{ background:{c};{ring} border-radius:5px; }}")
        if self._selected:
            self._star_lbl.setStyleSheet(
                "font-size:9px; color:#fff; background:#2255cc;"
                " border-radius:2px; padding:0 4px;")
        else:
            self._star_lbl.setStyleSheet(
                f"font-size:9px; color:{theme.INK_SOFT}; background:transparent;")

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
            # Fit inside the white mat (2px each side); crop shows as ⌗ on the
            # star line, so the image itself is left clean (no '#' stamp).
            scaled = pixmap.scaled(
                _THUMB_W - 4, _THUMB_H - 4,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._thumb_lbl.setPixmap(scaled)

    def refresh_from(self, item):
        """Repaint stars and colour frame after an edit."""
        self._item = item
        self._star_lbl.setText(self._star_text(item))
        self._repaint_border()  # also updates the star-bar colour

    def reload_thumbnail(self):
        """Re-decode the thumbnail — used when the image itself changed (a re-crop
        or a new render), unlike refresh_from() which only repaints stars/border."""
        self._requested = True
        self._item.request_thumbnail(_THUMB_W, self._on_pixmap)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.tapped.emit(self._key)
        elif event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit(self._key)
        super().mousePressEvent(event)


class FilmstripWidget(QWidget):
    """Thumbnail strip — one cell per picture, lazy-loaded.

    orientation: Horizontal (default) = bottom strip; Vertical = left strip.
    """
    item_clicked = pyqtSignal(str)        # key — host navigates on click
    item_right_clicked = pyqtSignal(str)  # key — host shows a context menu

    def __init__(self, collection, keys, parent=None,
                 orientation=Qt.Orientation.Horizontal):
        super().__init__(parent)
        self._collection  = collection
        self._keys        = list(keys)
        self._cells: dict[str, _StripItem] = {}
        self._current_key: str | None = None
        self._orientation = orientation
        _vert = (orientation == Qt.Orientation.Vertical)

        if _vert:
            # Vertical strip: fixed minimum width, unlimited height via scroll.
            self.setMinimumWidth(_ITEM_W + 30)
            outer = QVBoxLayout(self)
        else:
            # Horizontal strip: fixed minimum height, unlimited width via scroll.
            self.setMinimumHeight(_ITEM_H + 22)
            outer = QHBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(6)

        # Position counter — "94 of 335"
        self._pos_lbl = QLabel("")
        if _vert:
            self._pos_lbl.setStyleSheet("font-size: 10px; color: #888;")
            self._pos_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            self._pos_lbl.setStyleSheet(
                "font-size: 10px; color: #888; min-width: 58px;")
            self._pos_lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(self._pos_lbl)

        # Scroll area — axis depends on orientation
        self._sa = QScrollArea()
        self._sa.setWidgetResizable(False)
        if _vert:
            self._sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
            self._sa.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        else:
            self._sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
            self._sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._sa.horizontalScrollBar().valueChanged.connect(self._on_scrolled)
        outer.addWidget(self._sa, stretch=1)

        # Content strip — cells laid out along the scroll axis
        self._strip = QWidget()
        if _vert:
            strip_layout = QVBoxLayout(self._strip)
        else:
            strip_layout = QHBoxLayout(self._strip)
        strip_layout.setContentsMargins(2, 0, 2, 0)
        strip_layout.setSpacing(_ITEM_GAP)

        for key in self._keys:
            try:
                item = collection.item_for_key(key)
            except (ValueError, KeyError):
                continue
            cell = _StripItem(item, self._strip)
            cell.tapped.connect(self.item_clicked)
            cell.right_clicked.connect(self.item_right_clicked)
            strip_layout.addWidget(cell)
            self._cells[key] = cell

        n = len(self._cells)
        if _vert:
            total_h = n * _ITEM_H + max(0, n - 1) * _ITEM_GAP + 4
            self._strip.setFixedSize(_ITEM_W, max(total_h, 1))
        else:
            total_w = n * _ITEM_W + max(0, n - 1) * _ITEM_GAP + 4
            self._strip.setFixedSize(max(total_w, 1), _ITEM_H)
        self._sa.setWidget(self._strip)

        # Load initially-visible cells once the layout has settled.
        QTimer.singleShot(0, self._load_visible)

    def remove_keys(self, keys):
        """Drop the cells for `keys` (deleted pictures) and shrink the strip."""
        removed = False
        for key in keys:
            cell = self._cells.pop(key, None)
            if cell is not None:
                self._strip.layout().removeWidget(cell)
                cell.deleteLater()
                removed = True
            if key in self._keys:
                self._keys.remove(key)
            if self._current_key == key:
                self._current_key = None
        if not removed:
            return
        n = len(self._cells)
        if self._orientation == Qt.Orientation.Vertical:
            total_h = n * _ITEM_H + max(0, n - 1) * _ITEM_GAP + 4
            self._strip.setFixedSize(_ITEM_W, max(total_h, 1))
        else:
            total_w = n * _ITEM_W + max(0, n - 1) * _ITEM_GAP + 4
            self._strip.setFixedSize(max(total_w, 1), _ITEM_H)

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

    def refresh_item(self, key: str, reload_image: bool = False):
        """Repaint a cell's stars + colour border after an edit; reload_image=True
        also re-decodes its thumbnail (an external re-crop / new render)."""
        if key not in self._cells:
            return
        try:
            item = self._collection.item_for_key(key)
        except (ValueError, KeyError):
            return
        self._cells[key].refresh_from(item)
        if reload_image:
            self._cells[key].reload_thumbnail()

    def set_selected(self, keys: set):
        """Update selection highlight on all cells."""
        for k, cell in self._cells.items():
            cell.set_selected(k in keys)

    # ── Lazy loading ───────────────────────────────────────────────────────────

    def _on_scrolled(self, _):
        self._load_visible()

    def _load_visible(self):
        if self._orientation == Qt.Orientation.Vertical:
            bar    = self._sa.verticalScrollBar()
            vp_lo  = bar.value()
            vp_hi  = vp_lo + self._sa.viewport().height()
            stride = _ITEM_H + _ITEM_GAP
        else:
            bar    = self._sa.horizontalScrollBar()
            vp_lo  = bar.value()
            vp_hi  = vp_lo + self._sa.viewport().width()
            stride = _ITEM_W + _ITEM_GAP
        first = max(0, vp_lo  // stride - _PREFETCH)
        last  = min(len(self._keys) - 1, vp_hi // stride + _PREFETCH)
        for i in range(first, last + 1):
            key = self._keys[i]
            if key in self._cells:
                self._cells[key].request_load()


class _FullResView(QScrollArea):
    """A viewport onto a full-resolution image. Drag with the left button to pan;
    the viewport-centre position is reported (normalised) so the host can keep the
    same spot when flipping through a group — the sharpest-of-similar comparison.
    Messages (decoding / skipped / no-RAW) render in place of an image.

    Default (the 80% decode tab): shown at 1:1, pan by drag. With ``zoomable=True``
    (the Camera JPEG tab) the image opens FIT to the window and the wheel zooms it
    in to 1:1 — an embedded camera JPEG (e.g. the OM-1's 3200×2400) is smaller than
    the sensor, so 1:1 is already pixel-for-pixel and there is no reason to go past
    it. Double-click toggles fit ⇄ 1:1; a small corner overlay names the source."""
    point_changed = pyqtSignal(float, float)  # normalised centre (fx, fy)

    def __init__(self, parent=None, *, zoomable: bool = False):
        super().__init__(parent)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background:#1e1e1e;")
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color:#bbb;")
        self.setWidget(self._label)
        self._pixmap = QPixmap()
        self._drag = None
        self._label.mousePressEvent = self._on_press
        self._label.mouseMoveEvent = self._on_move
        self._label.mouseReleaseEvent = self._on_release
        # Zoomable (Camera JPEG) mode.
        self._zoomable = bool(zoomable)
        self._scale = 1.0          # display px per image px
        self._fit = False          # currently fit-to-window (track window resizes)
        self._overlay = None       # bottom-left: source + size
        self._hint = None          # top-left: how to zoom/pan
        if self._zoomable:
            self._label.mouseDoubleClickEvent = self._on_double_click
            badge_css = ("QLabel{background:rgba(0,0,0,150);color:#eee;"
                         "padding:2px 7px;border-radius:3px;font-size:11px;}")
            self._overlay = QLabel(self.viewport())
            self._overlay.setStyleSheet(badge_css)
            self._overlay.hide()
            self._hint = QLabel(self.viewport())
            self._hint.setStyleSheet(badge_css)
            self._hint.setText("Scroll to zoom · double-click for 1:1 · drag to pan")
            self._hint.hide()

    def set_message(self, text):
        self._pixmap = QPixmap()
        self._label.setPixmap(QPixmap())
        self._label.setText(text)
        self._label.resize(self.viewport().size())
        if self._overlay is not None:
            self._overlay.hide()
        if self._hint is not None:
            self._hint.hide()

    def set_image(self, pixmap, point, *, overlay: str = ""):
        self._pixmap = pixmap
        self._label.setText("")
        if self._zoomable:
            self._fit = True
            self._scale = self._fit_scale()
            self._render_scaled()
            self._center_on((0.5, 0.5))
            if self._hint is not None:
                self._hint.adjustSize()
                self._hint.show()
                self._hint.raise_()
            if self._overlay is not None:
                if overlay:
                    self._overlay.setText(overlay)
                    self._overlay.adjustSize()
                    self._overlay.show()
                    self._overlay.raise_()
                else:
                    self._overlay.hide()
            self._position_overlay()
        else:
            self._label.setPixmap(pixmap)
            self._label.resize(pixmap.size())
            self._center_on(point)

    # ── zoom (zoomable mode only) ──────────────────────────────────────────
    def _fit_scale(self) -> float:
        """Scale that shows the whole image in the viewport — never above 1:1, so a
        small embed fills at most its own pixels rather than upscaling to blur."""
        if self._pixmap.isNull():
            return 1.0
        vw, vh = self.viewport().width(), self.viewport().height()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw <= 0 or ph <= 0:
            return 1.0
        return min(vw / pw, vh / ph, 1.0)

    def _scaled_size(self):
        if self._zoomable and not self._pixmap.isNull():
            return (self._pixmap.width() * self._scale,
                    self._pixmap.height() * self._scale)
        return (self._pixmap.width(), self._pixmap.height())

    def _render_scaled(self):
        if self._pixmap.isNull():
            return
        w = max(1, int(round(self._pixmap.width() * self._scale)))
        h = max(1, int(round(self._pixmap.height() * self._scale)))
        scaled = self._pixmap.scaled(
            w, h, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._label.setPixmap(scaled)
        self._label.resize(scaled.size())

    def _set_scale(self, new_scale, anchor=None):
        """Zoom to ``new_scale`` (clamped fit…1:1) keeping the image point under
        ``anchor`` (a viewport point) stationary; anchor=None keeps the centre."""
        new_scale = max(self._fit_scale(), min(1.0, new_scale))
        if abs(new_scale - self._scale) < 1e-4:
            return
        if anchor is None:
            ax, ay = self.viewport().width() / 2, self.viewport().height() / 2
        else:
            ax, ay = anchor.x(), anchor.y()
        old_w = max(1.0, self._pixmap.width() * self._scale)
        old_h = max(1.0, self._pixmap.height() * self._scale)
        fx = (self.horizontalScrollBar().value() + ax) / old_w
        fy = (self.verticalScrollBar().value() + ay) / old_h
        self._scale = new_scale
        self._fit = abs(new_scale - self._fit_scale()) < 1e-4
        self._render_scaled()
        new_w = max(1.0, self._pixmap.width() * self._scale)
        new_h = max(1.0, self._pixmap.height() * self._scale)
        self.horizontalScrollBar().setValue(int(fx * new_w - ax))
        self.verticalScrollBar().setValue(int(fy * new_h - ay))
        self._emit_point()

    def wheelEvent(self, event):
        if not self._zoomable or self._pixmap.isNull():
            return super().wheelEvent(event)
        dy = event.angleDelta().y()
        if dy == 0:
            return super().wheelEvent(event)
        self._set_scale(self._scale * (1.25 if dy > 0 else 1 / 1.25),
                        anchor=event.position().toPoint())
        event.accept()

    def _on_double_click(self, e):
        if self._pixmap.isNull():
            return
        if self._fit:
            self._set_scale(1.0, anchor=e.position().toPoint())   # inspect at 1:1
        else:
            self._scale = self._fit_scale()
            self._fit = True
            self._render_scaled()
            self._center_on((0.5, 0.5))
            self._emit_point()

    def _position_overlay(self):
        m = 8
        if self._hint is not None and not self._hint.isHidden():
            self._hint.move(m, m)   # top-left: interaction hint
        if self._overlay is not None and not self._overlay.isHidden():
            self._overlay.move(     # bottom-left: source + size
                m, self.viewport().height() - self._overlay.height() - m)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._zoomable and self._fit and not self._pixmap.isNull():
            self._scale = self._fit_scale()
            self._render_scaled()
            self._center_on((0.5, 0.5))
        self._position_overlay()

    def _center_on(self, point):
        if self._pixmap.isNull():
            return
        fx, fy = point
        sw, sh = self._scaled_size()
        self.horizontalScrollBar().setValue(int(fx * sw) - self.viewport().width() // 2)
        self.verticalScrollBar().setValue(int(fy * sh) - self.viewport().height() // 2)

    def _emit_point(self):
        if self._pixmap.isNull():
            return
        sw, sh = self._scaled_size()
        cx = self.horizontalScrollBar().value() + self.viewport().width() // 2
        cy = self.verticalScrollBar().value() + self.viewport().height() // 2
        self.point_changed.emit(cx / max(1.0, sw), cy / max(1.0, sh))

    def _on_press(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition()
            self._label.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _on_move(self, e):
        if self._drag is not None:
            gp = e.globalPosition()
            d = gp - self._drag
            self._drag = gp
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(d.y()))

    def _on_release(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            self._drag = None
            self._label.unsetCursor()
            self._emit_point()


# ── Main dialog ────────────────────────────────────────────────────────────────

class LargeImageView(QDialog):
    picture_changed   = pyqtSignal(str)  # key — emitted after rating/colour/desc edit
    picture_image_changed = pyqtSignal(str)  # key — the IMAGE changed (a crop) → host re-decodes the card
    picture_navigated = pyqtSignal(str)  # key — emitted when ←/→ moves to a new picture
    species_committed = pyqtSignal(set)  # upns — emitted after a species commit in the panel
    selection_changed = pyqtSignal(set)  # keys — emitted whenever filmstrip selection changes
    delete_requested  = pyqtSignal(set)  # keys — right-click Delete on a filmstrip cell

    def __init__(self, parent, collection, keys, index, *,
                 settings_key="LargeImageView", auto_advance=True,
                 initial_selection: set | None = None, allow_delete: bool = False):
        super().__init__(parent)
        # Whether the filmstrip offers a right-click Delete (the host must handle
        # delete_requested). Off for read-only / non-culling hosts, so there is no
        # menu item that does nothing.
        self._allow_delete = bool(allow_delete)
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
        self._original_headline = ""
        self._panel_first_show = True
        self._selected_keys: set[str] = set(initial_selection) if initial_selection else set()
        self._bracket_left:  str | None = None
        self._bracket_right: str | None = None
        self._crop_mode = False
        self._crop_overlay = None

        # ── Root layout ───────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 6)
        root.setSpacing(0)

        # Filmstrip position is a persistent user preference ([layout] filmstrip_left).
        # Default: left strip (better for 4:3 and 3:2 on widescreen monitors).
        self._strip_left = get_setting("layout", "filmstrip_left", fallback="yes").strip().lower() != "no"

        # ── Title bar — name · file on the left (mockup), tools on the right ────
        top_bar = QWidget()
        top_bar.setObjectName("titleBar")
        top_bar.setFixedHeight(28)
        top_bar.setStyleSheet(
            f"QWidget#titleBar {{ border-bottom:1px solid {theme.LINE}; }}")
        top_bar_row = QHBoxLayout(top_bar)
        top_bar_row.setContentsMargins(8, 0, 4, 0)
        top_bar_row.setSpacing(0)
        self._titlebar_lbl = QLabel("")
        self._titlebar_lbl.setStyleSheet(
            f"font-family:{_MONO}; font-size:12px; color:{theme.INK_SOFT};")
        top_bar_row.addWidget(self._titlebar_lbl)
        top_bar_row.addStretch()
        # Crop tool — draw a suggested crop on the full frame for PhotoLab/LR.
        self._crop_btn = QToolButton()
        self._crop_btn.setAutoRaise(True)
        self._crop_btn.setCheckable(True)
        self._crop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._crop_btn.setStyleSheet("font-size: 10px; padding: 1px 5px;")
        self._crop_btn.clicked.connect(self._toggle_crop_mode)
        top_bar_row.addWidget(self._crop_btn)
        # Snap the drawn crop to the nearest standard long-side size — only useful
        # (and only visible) while cropping.
        self._snap_btn = QToolButton()
        self._snap_btn.setAutoRaise(True)
        self._snap_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._snap_btn.setStyleSheet("font-size: 10px; padding: 1px 5px;")
        self._snap_btn.setText("⤢ Snap size")
        from gui.crop_overlay import _SIZE_PRESETS
        self._snap_btn.setToolTip(
            "Resize the crop so its long side is the nearest standard size "
            f"({', '.join(str(s) for s in _SIZE_PRESETS)} px).")
        self._snap_btn.clicked.connect(self._on_snap_crop)
        self._snap_btn.setVisible(False)
        top_bar_row.addWidget(self._snap_btn)
        # Swap the crop between portrait and landscape — lets an upright photo be
        # cropped horizontally. Only useful (and visible) while cropping.
        self._rotate_btn = QToolButton()
        self._rotate_btn.setAutoRaise(True)
        self._rotate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rotate_btn.setStyleSheet("font-size: 10px; padding: 1px 5px;")
        self._rotate_btn.setText("⟳ Rotate crop")
        self._rotate_btn.setToolTip(
            "Swap the crop between portrait and landscape — e.g. a horizontal "
            "crop from an upright photo.")
        self._rotate_btn.clicked.connect(self._on_rotate_crop)
        self._rotate_btn.setVisible(False)
        top_bar_row.addWidget(self._rotate_btn)
        self._update_crop_btn()
        # Headline / Description — the same shared editor the grid right-click
        # opens, for when the panel's fields are scrolled off / collapsed.
        self._hd_btn = QToolButton()
        self._hd_btn.setAutoRaise(True)
        self._hd_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hd_btn.setStyleSheet("font-size: 10px; padding: 1px 5px;")
        self._hd_btn.setText("📝 Notes")
        self._hd_btn.setToolTip("Edit the headline / description for this picture")
        self._hd_btn.clicked.connect(self._edit_headline_description_current)
        top_bar_row.addWidget(self._hd_btn)
        self._filmstrip_btn = QToolButton()
        self._filmstrip_btn.setAutoRaise(True)
        self._filmstrip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._filmstrip_btn.setStyleSheet("font-size: 10px; padding: 1px 5px;")
        self._filmstrip_btn.clicked.connect(self._on_toggle_filmstrip)
        self._update_filmstrip_btn()
        top_bar_row.addWidget(self._filmstrip_btn)
        root.addWidget(top_bar)

        # ── Horizontal splitter: image (left) + panel (right) ─────────────────
        # Always exists; its parent depends on the filmstrip mode set up below.
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.splitterMoved.connect(lambda *_: self._fit_pixmap())

        # Both splitters always present — toggling hides/shows filmstrip panels
        # live without rebuilding the layout.
        self._outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._outer_splitter.setChildrenCollapsible(True)
        self._outer_splitter.setHandleWidth(6)
        self._outer_splitter.splitterMoved.connect(self._on_outer_splitter_moved)
        root.addWidget(self._outer_splitter, stretch=1)

        self._v_splitter = QSplitter(Qt.Orientation.Vertical)
        self._v_splitter.setChildrenCollapsible(True)
        self._v_splitter.splitterMoved.connect(self._on_v_splitter_moved)
        # (children assembled in the filmstrip block below)

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
        self._image_label.mouseDoubleClickEvent = self._on_fit_double_click
        frame_l.addWidget(self._image_label)
        stage_h.addWidget(self._img_frame)
        stage_h.addStretch(1)
        stage_v.addLayout(stage_h)
        stage_v.addStretch(1)

        # Tabbed centre: "Fit" (the scaled preview) + the full-res tab (decoded
        # from the RAW, lazily, and cached). The full-res tab is labelled with the
        # configured Render scale (e.g. "80%"), since that is the fraction of the
        # full sensor it decodes to. The 1:1 point persists across the group.
        self._fullres_point = (0.5, 0.5)
        self._fr_loader = AsyncImageLoader(self)
        self._fullres_view = _FullResView()
        self._fullres_view.point_changed.connect(self._on_fullres_point_changed)
        # Third tab: the CAMERA's own JPEG, lifted straight out of the RAW.
        #
        # Every Nikon from the D90 to the D500 embeds a FULL-SIZE JPEG (D7100:
        # 6000x4000 against a 6036x4020 sensor) — that is ~34,000 pictures here, half
        # the library. Extracting it takes ~30 ms; decoding the same RAW with rawpy
        # takes SECONDS. It is also a different image, not merely a faster one: the
        # camera's own rendering rather than ours, so it belongs in its own tab where
        # you can compare the two, not silently swapped in behind the "80%" label.
        # (The OM-1 embeds only a 3200px preview, so for those the tab says so and
        # sends you to the decode tab.)
        self._embed_view = _FullResView(zoomable=True)
        self._embed_view.point_changed.connect(self._on_fullres_point_changed)
        self._embed_loader = AsyncImageLoader(self)
        self._embed_key = None          # which picture the tab currently holds
        self._view_tabs = QTabWidget()
        # Tab order: Fit, then the Camera JPEG (the quick zoom — where a Fit
        # double-click lands), then the slower full-sensor decode last. Referenced
        # by name everywhere so the order stays changeable in one place.
        self._TAB_FIT, self._TAB_EMBED, self._TAB_FULLRES = 0, 1, 2
        self._view_tabs.addTab(self._stage, "Fit")
        self._view_tabs.addTab(self._embed_view, "Camera JPEG")
        self._view_tabs.addTab(self._fullres_view, self._fullres_scale_label())
        self._view_tabs.setTabToolTip(
            self._TAB_EMBED,
            "The JPEG the camera itself wrote inside the RAW. On the Nikons that "
               "is the FULL-SIZE image and it appears instantly — no decoding. It is "
               "the camera's rendering, not ours, so it is a fair comparison against "
               "the decoded tab rather than a replacement for it.")
        self._view_tabs.currentChanged.connect(self._on_view_tab_changed)

        self._splitter.addWidget(self._view_tabs)
        self._splitter.setStretchFactor(0, 1)

        # Right — metadata panel
        panel_scroll = QScrollArea()
        self._panel_scroll = panel_scroll  # wheelEvent: don't flip pictures over it
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Floor wide enough for the species editor's qualifier button rows (Sex /
        # Age / Plumage) without clipping (h-scroll is off). No max: the user's
        # dragged width is remembered (hsplit in the window state), so let them
        # size it freely rather than capping their choice.
        panel_scroll.setMinimumWidth(300)

        self._panel_widget = QWidget()
        self._panel_widget.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        pl = QVBoxLayout(self._panel_widget)
        pl.setContentsMargins(8, 6, 8, 6)
        pl.setSpacing(2)
        panel_scroll.setWidget(self._panel_widget)
        self._splitter.addWidget(panel_scroll)
        self._splitter.setStretchFactor(1, 0)

        # Identity header — the name you read (matches the grid card / mockup):
        # bold display name + italic scientific name(+modifiers).
        self._panel_name = QLabel("")
        self._panel_name.setWordWrap(True)
        self._panel_name.setStyleSheet(f"font-size:19px; font-weight:700; color:{theme.INK};")
        pl.addWidget(self._panel_name)
        self._panel_sci = QLabel("")
        self._panel_sci.setWordWrap(True)
        self._panel_sci.setStyleSheet(
            f"font-size:12.5px; font-style:italic; color:{theme.INK_SOFT}; padding-bottom:2px;")
        pl.addWidget(self._panel_sci)

        # Every section below is a _CollapsibleSection: the picture drives the
        # window height, and on a short window the user collapses the sections
        # they don't need (the panel scrolls for the rest) instead of the text
        # forcing the dialog taller. Each section's state persists.

        # FILE section — key→value rows; the explorer / PhotoLab icons ride on
        # the section header (kept per your call, just out of the value column).
        sec_file = _CollapsibleSection("FILE", setting_key="file")
        self._explorer_btn = QToolButton()
        self._explorer_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self._explorer_btn.setIconSize(QSize(18, 18))
        self._explorer_btn.setAutoRaise(True)
        self._explorer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._explorer_btn.setToolTip(
            "Show this picture's files in Explorer (folder + UPN search)")
        self._explorer_btn.clicked.connect(self._open_in_explorer)

        self._dxo_btn = QToolButton()
        self._dxo_btn.setIcon(_make_pl_icon(18))
        self._dxo_btn.setIconSize(QSize(18, 18))
        self._dxo_btn.setAutoRaise(True)
        self._dxo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dxo_btn.setToolTip("Open the RAW file in the associated viewer")
        self._dxo_btn.clicked.connect(self._open_in_dxo)
        sec_file.add_trailing_widget(self._explorer_btn)
        sec_file.add_trailing_widget(self._dxo_btn)

        # name (full path on hover) · files · crop — as clean rows
        self._file_key_lbl  = _KVRow("name")
        self._file_exts_lbl = _KVRow("files")
        self._file_crop_lbl = _KVRow("crop")
        for w in (self._file_key_lbl, self._file_exts_lbl, self._file_crop_lbl):
            sec_file.add_widget(w)
        pl.addWidget(sec_file)

        # LOCATION section (right after FILE) — reverse-geocoded place + the GPS
        # coordinates, read straight from the XMP (shows during culling, before
        # any CSV). Filled/updated by 'l'.
        sec_loc = _CollapsibleSection("LOCATION", setting_key="location")
        self._loc_place_lbl = _field_lbl()   # descriptive place (reserve / lake / town)
        self._loc_admin_lbl = _small_lbl()   # City · State · Country
        self._gps_lbl       = _small_lbl()   # GPS coordinates
        for w in (self._loc_place_lbl, self._loc_admin_lbl, self._gps_lbl):
            sec_loc.add_widget(w)
        pl.addWidget(sec_loc)

        # EXIF section (GPS moved up to LOCATION) — clean key→value rows
        sec_exif = _CollapsibleSection("EXIF", setting_key="exif")
        self._exif_camera_lbl   = _KVRow("camera")
        self._exif_lens_lbl     = _KVRow("lens")
        self._exif_settings_lbl = _KVRow("exposure")
        self._exif_focal_lbl    = _KVRow("focal")
        self._exif_dims_lbl     = _KVRow("size")
        self._exif_date_lbl     = _KVRow("date")
        for w in (self._exif_camera_lbl, self._exif_lens_lbl,
                  self._exif_settings_lbl, self._exif_focal_lbl,
                  self._exif_dims_lbl, self._exif_date_lbl):
            sec_exif.add_widget(w)
        pl.addWidget(sec_exif)

        # SPECIES section (container rebuilt on each picture)
        sec_species = _CollapsibleSection("SPECIES", setting_key="species")
        self._species_container = QWidget()
        self._species_cl = QVBoxLayout(self._species_container)
        self._species_cl.setContentsMargins(0, 0, 0, 0)
        self._species_cl.setSpacing(4)
        sec_species.add_widget(self._species_container)
        pl.addWidget(sec_species)

        # RATING section
        sec_rating = _CollapsibleSection("RATING", setting_key="rating")
        stars_row = QHBoxLayout()
        stars_row.setContentsMargins(0, 2, 0, 2)
        self._stars = StarWidget(0)
        self._stars.rating_changed.connect(self._on_rating)
        stars_row.addWidget(self._stars)
        stars_row.addStretch()
        self._rate_lbl = QLabel("")
        self._rate_lbl.setStyleSheet("font-size: 11px; color: #555;")
        stars_row.addWidget(self._rate_lbl)
        sec_rating.add_layout(stars_row)

        self._color_sel = ColorSelector("")
        self._color_sel.color_changed.connect(self._on_color)
        sec_rating.add_widget(self._color_sel)

        self._apply_all_btn = QPushButton("Apply to selected")
        self._apply_all_btn.setEnabled(False)
        self._apply_all_btn.setFlat(True)
        self._apply_all_btn.setStyleSheet(
            "font-size: 10px; color: #555; padding: 1px 4px; margin-top: 2px;")
        self._apply_all_btn.clicked.connect(self._apply_to_all_selected)
        sec_rating.add_widget(self._apply_all_btn)
        pl.addWidget(sec_rating)

        sec_head = _CollapsibleSection("HEADLINE", setting_key="headline")
        sec_head.add_widget(
            _small_lbl("Title for a landscape / context photo (optional)"))
        self._headline_edit = QLineEdit()
        self._headline_edit.setPlaceholderText("e.g. Sunrise over the marsh")
        self._headline_edit.editingFinished.connect(self._save_headline)
        sec_head.add_widget(self._headline_edit)
        pl.addWidget(sec_head)

        sec_desc = _CollapsibleSection("DESCRIPTION", setting_key="description")
        sec_desc.add_widget(_small_lbl("Story for the website (optional)"))
        self._desc_edit = QTextEdit()
        fm = self._desc_edit.fontMetrics()
        self._desc_edit.setMinimumHeight(int(fm.lineSpacing() * 3) + 8)
        self._desc_edit.setMaximumHeight(int(fm.lineSpacing() * 8) + 8)
        self._desc_edit.setAcceptRichText(False)
        sec_desc.add_widget(self._desc_edit)
        pl.addWidget(sec_desc)
        pl.addStretch(1)

        # ── Selection bar (same markup in both layout modes) ──────────────────
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

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #555; margin: 0;")

        # ── Both filmstrips always exist; toggle just shows/hides them ────────
        self._filmstrip_left = FilmstripWidget(collection, self._keys, self,
                                               orientation=Qt.Orientation.Vertical)
        self._filmstrip_left.setMinimumWidth(0)
        self._filmstrip_left.item_clicked.connect(self._on_filmstrip_click)
        self._filmstrip_left.item_right_clicked.connect(self._on_filmstrip_context)

        self._filmstrip_bottom = FilmstripWidget(collection, self._keys, self)
        self._filmstrip_bottom.setMinimumHeight(0)
        self._filmstrip_bottom.item_clicked.connect(self._on_filmstrip_click)
        self._filmstrip_bottom.item_right_clicked.connect(self._on_filmstrip_context)

        # Content wrapper: image+panel splitter + sel-bar + sep
        # (top pane of _v_splitter in both modes)
        content_wrapper = QWidget()
        wrap_vbox = QVBoxLayout(content_wrapper)
        wrap_vbox.setContentsMargins(0, 0, 0, 0)
        wrap_vbox.setSpacing(0)
        wrap_vbox.addWidget(self._splitter, stretch=1)
        wrap_vbox.addWidget(self._sel_bar)
        wrap_vbox.addWidget(sep)

        # _v_splitter: content (top) | bottom filmstrip (bottom)
        self._v_splitter.addWidget(content_wrapper)
        self._v_splitter.addWidget(self._filmstrip_bottom)

        # right_pane: wraps _v_splitter so it can be the right child of _outer_splitter
        right_pane = QWidget()
        right_vbox = QVBoxLayout(right_pane)
        right_vbox.setContentsMargins(0, 0, 0, 0)
        right_vbox.setSpacing(0)
        right_vbox.addWidget(self._v_splitter, stretch=1)

        # _outer_splitter: left filmstrip | right_pane
        self._outer_splitter.addWidget(self._filmstrip_left)
        self._outer_splitter.setStretchFactor(0, 0)
        self._outer_splitter.addWidget(right_pane)
        self._outer_splitter.setStretchFactor(1, 1)

        # Saved sizes for the inactive mode — restored when toggling back.
        self._last_fsplit: list | None = None
        self._last_vsplit: list | None = None

        # Apply initial visibility (sizes come from showEvent).
        self._filmstrip_left.setVisible(self._strip_left)
        self._filmstrip_bottom.setVisible(not self._strip_left)

        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save_description)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save_headline)

        self._apply_default_or_remembered_geometry()
        self._load_current()
        if self._selected_keys:
            self._update_selection_display()

    # ── Keyboard navigation ───────────────────────────────────────────────────

    def wheelEvent(self, event):
        # Wheel over the image flips through pictures: up → next, down → previous.
        # But NOT when the cursor is over the metadata panel or a filmstrip: those
        # scroll their own content, and once scrolled to an end the wheel stops
        # being consumed there and bubbles up here — which used to leak into
        # picture navigation. Gate on the widget under the pointer so it doesn't.
        under = QApplication.widgetAt(event.globalPosition().toPoint())
        if under is not None and any(
                self._is_descendant(under, host) for host in (
                    self._panel_scroll, self._filmstrip_left, self._filmstrip_bottom)):
            return
        dy = event.angleDelta().y()
        if dy > 0:
            self._next()
            event.accept()
        elif dy < 0:
            self._prev()
            event.accept()
        else:
            super().wheelEvent(event)

    @staticmethod
    def _is_descendant(widget, ancestor) -> bool:
        """True when `widget` is `ancestor` or nested inside it."""
        while widget is not None:
            if widget is ancestor:
                return True
            widget = widget.parentWidget()
        return False

    # ── 100% (full-resolution) view ──────────────────────────────────────────
    def _fullres_cache_dir(self):
        from gui.fullres import fullres_cache_dir
        return fullres_cache_dir()

    def _fit_click_to_point(self, posf):
        """Map a click on the Fit image to a normalised (fx, fy) of the picture."""
        pm = self._image_label.pixmap()
        if pm is None or pm.isNull():
            return (0.5, 0.5)
        ox = (self._image_label.width() - pm.width()) / 2
        oy = (self._image_label.height() - pm.height()) / 2
        fx = (posf.x() - ox) / max(1, pm.width())
        fy = (posf.y() - oy) / max(1, pm.height())
        return (min(1.0, max(0.0, fx)), min(1.0, max(0.0, fy)))

    def _on_fit_double_click(self, e):
        # Double-click the photo → the Camera JPEG tab (opens fit, wheel-zooms to
        # 1:1). Remember the clicked spot so a later 80% visit centres there.
        self._fullres_point = self._fit_click_to_point(e.position())
        self._view_tabs.setCurrentIndex(self._TAB_EMBED)

    def _on_view_tab_changed(self, idx):
        if idx in (self._TAB_EMBED, self._TAB_FULLRES):
            if self._crop_mode:
                self._exit_crop_mode(reload=True)  # crop is a Fit-tab tool
        if idx == self._TAB_FULLRES:
            self._load_fullres()
        elif idx == self._TAB_EMBED:
            self._load_embedded()

    def _load_embedded(self):
        """Show the camera's own embedded JPEG at 1:1 — no decode, no cache.

        The extract is ~30 ms and the JPEG decode a few hundred, so this is far
        cheaper than the rawpy path and there is nothing worth caching: the bytes are
        already sitting in the RAW. The tab label is rewritten with the REAL pixel
        size once we know it, because a tab that claims a size it isn't showing is
        exactly the lie we are trying to remove from the 80% tab."""
        if getattr(self, "_view_tabs", None) is None \
                or self._view_tabs.currentIndex() != self._TAB_EMBED:
            return
        if not self._keys or self._item is None:
            return
        key = self._keys[self._index]
        if self._embed_key == key:
            return                       # already showing this picture
        raw = self._collection.raw_path(key)
        if not raw:
            self._view_tabs.setTabText(self._TAB_EMBED, "Camera JPEG")
            self._embed_view.set_message("No RAW file for this picture.")
            return
        self._embed_view.set_message("Reading the camera's JPEG…")
        self._embed_loader.cancel_all()
        raw_s = str(raw)
        # The RAW's own pixel size, so we can say whether the embed is full-size.
        sensor = getattr(getattr(self._item, "exif", None), "dimensions", "") or ""

        def produce():
            from pathlib import Path
            from gui.preview_extract import extract_preview_bytes
            data = extract_preview_bytes(Path(raw_s))
            if not data:
                return None
            img = QImage.fromData(data)
            return img if not img.isNull() else None

        self._embed_key = key
        self._embed_loader.request(
            produce, lambda px, s=sensor: self._on_embedded_pixmap(px, s))

    def _on_embedded_pixmap(self, px, sensor: str):
        if px is None or px.isNull():
            self._view_tabs.setTabText(self._TAB_EMBED, "Camera JPEG")
            self._embed_view.set_message("This RAW carries no embedded JPEG.")
            return
        w, h = px.width(), px.height()
        self._view_tabs.setTabText(self._TAB_EMBED, f"Camera JPEG {w}×{h}")
        # Always show the camera's JPEG, zoomable: it opens fit-to-window and the
        # wheel zooms it to 1:1. When the embed is smaller than the sensor (the
        # OM-1 writes only a 3200×2400 preview, and the odd Nikon NEF a tiny one),
        # the corner overlay says so and points at the decode tab for full detail —
        # a note beside the picture, not a wall where the picture should be.
        long_edge = max(w, h)
        sensor_long = 0
        try:
            sensor_long = max(int(n) for n in sensor.lower().split("x"))
        except Exception:
            pass
        model = (getattr(getattr(self._item, "exif", None), "model", "") or "").strip()
        overlay = f"{model} · camera JPEG {w}×{h}" if model else f"Camera JPEG {w}×{h}"
        if sensor_long and long_edge < sensor_long * 0.9:
            overlay += f"  ·  preview only — {self._fullres_scale_label()} tab decodes the full sensor"
        self._embed_view.set_image(px, self._fullres_point, overlay=overlay)

    def _on_fullres_point_changed(self, fx, fy):
        self._fullres_point = (fx, fy)

    def _update_filmstrip_btn(self):
        if self._strip_left:
            self._filmstrip_btn.setText("⬇ Strip")
            self._filmstrip_btn.setToolTip("Filmstrip is on the left — click to move it to the bottom.")
        else:
            self._filmstrip_btn.setText("⬅ Strip")
            self._filmstrip_btn.setToolTip("Filmstrip is at the bottom — click to move it to the left.")

    def _on_toggle_filmstrip(self):
        # Save the active splitter's current sizes before switching.
        if self._strip_left:
            self._last_fsplit = self._outer_splitter.sizes()
        else:
            self._last_vsplit = self._v_splitter.sizes()

        self._strip_left = not self._strip_left
        set_setting("layout", "filmstrip_left", "yes" if self._strip_left else "no")

        self._filmstrip_left.setVisible(self._strip_left)
        self._filmstrip_bottom.setVisible(not self._strip_left)

        # Restore the sizes for the newly-active filmstrip.
        if self._strip_left:
            if self._last_fsplit:
                self._outer_splitter.setSizes(self._last_fsplit)
        else:
            if self._last_vsplit:
                self._v_splitter.setSizes(self._last_vsplit)

        self._update_filmstrip_btn()
        QTimer.singleShot(0, self._fit_pixmap)

    def _on_outer_splitter_moved(self, *_):
        if self._strip_left:
            self._last_fsplit = self._outer_splitter.sizes()
        self._fit_pixmap()

    def _on_v_splitter_moved(self, *_):
        if not self._strip_left:
            self._last_vsplit = self._v_splitter.sizes()
        self._fit_pixmap()

    def _refresh_strip_items(self, key: str, reload_image: bool = False):
        self._filmstrip_left.refresh_item(key, reload_image=reload_image)
        self._filmstrip_bottom.refresh_item(key, reload_image=reload_image)

    def refresh_external(self, key: str, reload_image: bool = False):
        """Reflect an edit made elsewhere (e.g. a picture culled from the grid, or
        an external PhotoLab/LR write) while this viewer is open: refresh the
        filmstrips, and — if the edited picture is on screen — its widgets. With
        reload_image=True the image is RE-DECODED and the FILE note rebuilt too (a
        re-crop or a new render changed the picture itself), via _load_current.
        Re-reads the item from the (shared) collection so values are current; does
        NOT re-emit picture_changed, so there's no echo back to the host."""
        self._refresh_strip_items(key, reload_image=reload_image)
        if self._item is None or self._item.key != key:
            return
        if reload_image:
            self._load_current()   # full reload: image + FILE note (JPG) + everything
            return
        try:
            item = self._collection.item_for_key(key)
        except (ValueError, KeyError):
            return
        self._item = item
        self._stars.set_rating(item.rating)
        self._color_sel.set_color(item.color)
        self._rate_lbl.setText(f"Rate: {item.rate}" if item.rate is not None else "")
        self._apply_frame_color(item.color or "")
        self._show_location(key)   # a grid-side Edit location / Paste GPS changed the XMP

    def _fullres_scale_label(self):
        """Label for the full-res tab — the configured Render scale (% of sensor)."""
        try:
            from utils.config import _config
            return f"{_config.getint('fullres', 'scale', fallback=80)}%"
        except Exception:
            return "100%"

    def _load_fullres(self):
        if getattr(self, "_view_tabs", None) is None \
                or self._view_tabs.currentIndex() != self._TAB_FULLRES:
            return
        self._view_tabs.setTabText(
            self._TAB_FULLRES, self._fullres_scale_label())  # reflect a changed setting
        if not self._keys or self._item is None:
            return
        key = self._keys[self._index]
        item = self._item
        if (item.color or "").capitalize() in ("Blue", "Purple"):
            self._fullres_view.set_message("Blue / Purple — already a reject, skipped.")
            return
        raw = self._collection.raw_path(key)
        if not raw:
            self._fullres_view.set_message("No RAW file for this picture.")
            return
        self._fullres_view.set_message("Decoding high resolution…")
        cache_root = str(self._fullres_cache_dir())
        raw_s = str(raw)
        self._fr_loader.cancel_all()  # drop a decode still running for a previous picture

        def produce():
            from gui.fullres import ensure_fullres_jpeg
            p = ensure_fullres_jpeg(raw_s, cache_root)
            if not p:
                return None
            img = QImage(str(p))
            return img if not img.isNull() else None

        self._fr_loader.request(produce, self._on_fullres_pixmap)

    def _on_fullres_pixmap(self, px):
        if px is None or px.isNull():
            self._fullres_view.set_message("Could not decode full resolution.")
            return
        self._fullres_view.set_image(px, self._fullres_point)

    # ── Crop tool ─────────────────────────────────────────────────────────────
    def _update_crop_btn(self):
        if getattr(self, "_crop_mode", False):
            self._crop_btn.setText("✂ Cropping…")
            self._crop_btn.setToolTip("Drawing a crop — Enter applies, Esc cancels.")
        else:
            self._crop_btn.setText("✂ Crop")
            self._crop_btn.setToolTip(
                "Suggest a crop for PhotoLab / Lightroom — draw a rectangle on the "
                "full frame (aspect locked to the camera's), then press Enter.")

    def _toggle_crop_mode(self):
        if self._crop_mode:
            self._exit_crop_mode(reload=True)
        else:
            self._enter_crop_mode()

    def _on_rotate_crop(self):
        if self._crop_mode and self._crop_overlay is not None:
            self._crop_overlay.toggle_orientation()
            self._crop_overlay.setFocus()

    def _on_snap_crop(self):
        if self._crop_mode and self._crop_overlay is not None:
            self._crop_overlay.snap_to_standard()
            self._crop_overlay.setFocus()

    def _enter_crop_mode(self):
        if self._item is None or not getattr(self._item, "can_set_crop", False):
            logger.info("Crop: not available for this picture (no RAW / read-only).")
            self._crop_btn.setChecked(False)
            return
        # Crop is a Fit-tab tool; the 100% tab pans instead.
        self._view_tabs.setCurrentIndex(self._TAB_FIT)
        self._crop_mode = True
        self._crop_btn.setChecked(True)
        self._snap_btn.setVisible(True)
        self._rotate_btn.setVisible(True)
        self._update_crop_btn()
        # Show the UNCROPPED frame so a fresh rectangle can be drawn over it.
        self._load_token += 1
        tok = self._load_token
        self._image_label.setText("Loading full frame…")
        self._item.request_uncropped_pixmap(
            lambda px, t=tok: self._on_uncropped_for_crop(t, px))

    def _on_uncropped_for_crop(self, token, px):
        if token != self._load_token or not self._crop_mode:
            return
        self._pixmap = px if (px is not None and not px.isNull()) else QPixmap()
        if self._pixmap.isNull():
            self._image_label.setText("(no image)")
            return
        self._image_label.setText("")
        self._fit_pixmap()
        self._show_crop_overlay()

    def _show_crop_overlay(self):
        if self._crop_overlay is None:
            from gui.crop_overlay import CropOverlay
            self._crop_overlay = CropOverlay(self._image_label)
            self._crop_overlay.committed.connect(self._on_crop_committed)
            self._crop_overlay.cancelled.connect(
                lambda: self._exit_crop_mode(reload=True))
        self._sync_crop_overlay_geom()
        # Lock to the camera's NOMINAL ratio (4:3 / 3:2) so standard sizes come out
        # round (1200×900, not 1200×899); fall back to the shown frame's ratio.
        fw, fh = self._full_frame_size()
        from gui.crop_overlay import nominal_aspect
        aspect = nominal_aspect(fw, fh)
        if aspect is None:
            pm = self._image_label.pixmap()
            if pm is not None and not pm.isNull() and pm.height() > 0:
                aspect = pm.width() / pm.height()
        if aspect:
            self._crop_overlay.set_aspect(aspect)
        self._crop_overlay.set_full_size(fw, fh)
        self._crop_overlay.set_normalized(self._existing_crop())
        self._crop_overlay.show()
        self._crop_overlay.raise_()
        self._crop_overlay.setFocus()
        # The label may still be re-laying out after the frame swap; re-glue once
        # it settles so the overlay lands exactly on the pixmap.
        QTimer.singleShot(0, self._sync_crop_overlay_geom)

    def _sync_crop_overlay_geom(self):
        """Keep the overlay exactly over the displayed pixmap (centred in the
        label), preserving the drawn rectangle across a resize."""
        if self._crop_overlay is None or not self._crop_mode:
            return
        pm = self._image_label.pixmap()
        if pm is None or pm.isNull():
            return
        lw, lh = self._image_label.width(), self._image_label.height()
        pw, ph = pm.width(), pm.height()
        prev = (self._crop_overlay.normalized()
                if self._crop_overlay.has_crop() else None)
        self._crop_overlay.setGeometry(max(0, (lw - pw) // 2),
                                       max(0, (lh - ph) // 2), pw, ph)
        if prev is not None:
            self._crop_overlay.set_normalized(prev)

    def _full_frame_size(self):
        """Full-frame pixel dimensions (w, h) from the RAW's EXIF, for the crop
        size readout. (0, 0) when unknown."""
        try:
            dims = (self._item.exif.dimensions or "") if self._item else ""
            w, h = dims.lower().split("x")
            return int(w), int(h)
        except (ValueError, AttributeError):
            return 0, 0

    def _existing_crop(self):
        """Any crop already in the sidecars, so a re-crop starts from it. Usually
        None in our flow (the tool targets uncropped shots)."""
        try:
            from gui.picture_provider import crop_for
            raw = self._collection.raw_path(self._keys[self._index])
            return crop_for(raw) if raw else None
        except Exception:
            return None

    def _on_crop_committed(self, crop: dict):
        if self._item is None:
            return
        key = self._item.key
        try:
            res = self._item.set_crop(crop)
        except Exception as e:
            logger.warning(f"Crop: could not write suggestion: {e}")
            res = None
        self._exit_crop_mode(reload=False)
        # Re-decode with the new crop applied + refresh strips and the host grid.
        self.refresh_external(key, reload_image=True)
        self.picture_changed.emit(key)
        self.picture_image_changed.emit(key)   # the grid card must re-decode too
        if res and res.get("baked_jpg"):
            logger.info("Crop baked into a full-res JPG (a HEIC has no develop step).")
        elif res:
            created = [k.upper() for k in ("dop", "xmp") if res.get(f"{k}_created")]
            extra = f"  (created {', '.join(created)})" if created else ""
            logger.info("Crop suggestion written to PhotoLab + Lightroom "
                        f"sidecars.{extra}")

    def _exit_crop_mode(self, reload: bool = True):
        if not self._crop_mode and self._crop_overlay is None:
            return
        self._crop_mode = False
        self._crop_btn.setChecked(False)
        self._snap_btn.setVisible(False)
        self._rotate_btn.setVisible(False)
        self._update_crop_btn()
        if self._crop_overlay is not None:
            self._crop_overlay.hide()
        if reload:
            self._load_current()   # back to the normal cropped cull view
        self.setFocus()

    def _cull_set_cursor(self, key):
        if key in self._keys:
            self._index = self._keys.index(key)
            self._load_current()

    def _show_location(self, key):
        """Fill the LOCATION panel from the picture's XMP (no CSV needed)."""
        loc = self._collection.read_location(key)
        place = loc.get("Location", "")
        admin = "  ·  ".join(p for p in (loc.get("City"), loc.get("Municipality"),
                                         loc.get("State"), loc.get("Country")) if p)
        self._loc_place_lbl.setText(place)
        self._loc_place_lbl.setVisible(bool(place))
        self._loc_admin_lbl.setText(admin)
        self._loc_admin_lbl.setVisible(bool(admin))

    def _geolocate_current(self):
        if not self._keys:
            return
        key = self._keys[self._index]
        src = self._collection.gps_source(key)
        files = self._collection.location_targets(key)
        if not src or not files:
            logger.info("Get location: no GPS / writable files for this picture.")
            return
        from gui.geolocate import run_geolocate_batch
        label = (self._item.display_name if self._item else key) or key
        # on_done runs inside on_finished, before dlg.close(). Updating the
        # panel there means Qt repaints the labels when the dialog closes —
        # the normal post-exec() call site is too late for an immediate repaint.
        run_geolocate_batch(self, [(label, src, files)],
                            on_done=lambda: self._after_geocode(key, src))

    def _after_geocode(self, key, src):
        """Refresh the row + panel after a single Get-location write. read_location
        is row-served, so re-read this one file once (its mtime just changed) and
        push it into the cached row before showing it."""
        from gui.geolocate import read_location_fields
        if src:
            self._collection.note_location_written(key, read_location_fields(src))
        self._show_location(key)

    def _edit_location_current(self):
        """Manually type the location for the picture on screen — for places
        Nominatim can't supply. Writes only changed, non-empty fields to its XMP."""
        if not self._keys:
            return
        key = self._keys[self._index]
        files = self._collection.location_targets(key)
        if not files:
            logger.info("Edit location: no writable files for this picture.")
            return
        try:
            current = self._collection.read_location(key) or {}
        except Exception:
            current = {}
        from gui.location_edit import edit_location_dialog
        edited = edit_location_dialog(self, current, 1)
        if edited is None:
            return
        changed = {k: v for k, v in edited.items() if v and v != (current.get(k) or "")}
        if not changed:
            logger.info("Edit location: nothing changed.")
            return
        from gui.geolocate import write_location_batch
        n = write_location_batch(files, changed, overwrite=True)
        self._collection.note_location_written(key, changed)   # keep cached row fresh
        self._show_location(key)
        place = changed.get("Location") or changed.get("City") or ""
        logger.info(f"Edit location: wrote {place!r} to {n} file(s) for this picture.")

    def contextMenuEvent(self, event):
        """Right-click anywhere in the viewer → location actions ('L' still does
        Get location). Child widgets with their own menu (the description box)
        keep theirs; this only fires for clicks they don't consume."""
        from PyQt6.QtWidgets import QMenu
        if not self._keys:
            return
        menu = QMenu(self)
        key = self._item.key if self._item else None
        host = self.parent()          # the grid dialog — reuse its actions for this key

        # Sex / age / plumage — the same form editor as the side-panel button, in the
        # menu too. (Rating + colour already live in the side panel, so they aren't
        # repeated here; "Full resolution" is moot — this IS the full view.)
        act_qual = None
        item = self._item
        if (key and getattr(item, "from_master", False)
                and hasattr(self._collection, "apply_master_qualifiers")
                and not getattr(self._collection, "master_read_only", True)):
            act_qual = menu.addAction("Sex / age / plumage…")
            menu.addSeparator()

        act_geo = menu.addAction("Get location  (from GPS)")
        # Get GPS from location — the reverse of Get location (place name → coordinate),
        # delegated to the grid host. The one grid action the large viewer lacked.
        act_gps_from = None
        gps_from_fn = getattr(host, "_gps_from_location", None)
        if key and callable(gps_from_fn):
            act_gps_from = menu.addAction("Get GPS from location")
            can_fn = getattr(host, "_can_gps_from_location", None)
            if callable(can_fn):
                try:
                    act_gps_from.setEnabled(bool(can_fn({key})))
                except Exception:
                    pass
        act_edit = menu.addAction("Edit location…  (type a place name)")
        # Headline / Description — the same shared editor as the top-bar button.
        act_hd = None
        if item is not None and (getattr(item, "can_edit_headline", False)
                                 or getattr(item, "can_edit_description", False)):
            menu.addSeparator()
            act_hd = menu.addAction("Edit headline / description…")
        # "Add new files" only where the collection supports it (an editable trip
        # picture) — the culling/album/master viewers won't advertise it.
        act_add = None
        supported = getattr(self._collection, "add_files_supported", None)
        if key and callable(supported):
            try:
                if supported(key):
                    menu.addSeparator()
                    act_add = menu.addAction("Add new files (scan folder)…")
            except Exception:
                act_add = None
        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        if chosen == act_geo:
            self._geolocate_current()
        elif chosen == act_edit:
            self._edit_location_current()
        elif act_qual is not None and chosen == act_qual:
            self._edit_master_qualifiers(key)
        elif act_gps_from is not None and chosen == act_gps_from:
            gps_from_fn({key})
            self._show_location(key)   # reflect the new coordinate in the panel
        elif act_hd is not None and chosen == act_hd:
            self._edit_headline_description_current()
        elif act_add is not None and chosen == act_add:
            self._add_files_current()

    def _add_files_current(self):
        """Scan the current picture's folder for files that belong to its UPN but
        aren't tracked yet (a freshly-rendered DxO JPG + its .dop) and pull them
        in — the large-viewer twin of the grid card's right-click 'Add new files'."""
        if self._item is None:
            return
        key = self._item.key
        fn = getattr(self._collection, "add_files", None)
        if not callable(fn):
            return
        try:
            res = fn(key)
        except Exception as e:
            logger.error(f"Add files failed for {key}: {e}", exc_info=True)
            QMessageBox.warning(self, "Add new files",
                                f"Could not scan for new files:\n{e}")
            return
        added = res.get("added") if isinstance(res, dict) else None
        if not added:
            QMessageBox.information(
                self, "Add new files",
                "No new files found in the picture's folder.")
            return
        # A JPG (and maybe its .dop) joined the picture: refresh this viewer and
        # tell the host to repaint + re-decode the grid card.
        self._refresh_strip_items(key)
        self._load_current()
        self.picture_changed.emit(key)
        self.picture_image_changed.emit(key)
        parts = []
        if res.get("jpg"):
            parts.append(f"{res['jpg']} JPG" + ("s" if res["jpg"] != 1 else ""))
        if res.get("meta"):
            parts.append(f"{res['meta']} metafile" + ("s" if res["meta"] != 1 else ""))
        QMessageBox.information(self, "Add new files",
                                f"Added {' and '.join(parts)} to the picture.")

    def _cull_open_rating(self):
        # 'c' with no selection → act on the picture being viewed.
        if not self._selected_keys and self._item is not None:
            self._selected_keys = {self._item.key}
            self._update_selection_display()
        self._batch_rating()

    def keyPressEvent(self, event):
        # Space = next, routed through _go so it honours the 100%-tab reject-skip
        # (handle_cull_key's generic 'next' would jump straight to the literal next).
        if event.key() == Qt.Key.Key_Space and not self._desc_edit.hasFocus():
            self._next()
            event.accept()
            return
        from gui.viewer_keys import handle_cull_key
        if self._keys and handle_cull_key(
                event, order=list(self._keys),
                current_key=self._keys[self._index],
                item_for_key=self._collection.item_for_key,
                set_cursor=self._cull_set_cursor,
                refresh_key=self._refresh_strip_items,
                open_species=self._batch_species,
                open_rating=self._cull_open_rating):
            event.accept()
            return
        k = event.key()
        desc_focused = self._desc_edit.hasFocus()
        if k == Qt.Key.Key_F and not desc_focused:
            # Toggle Fit ⇄ Camera JPEG (the quick zoom the double-click also uses).
            self._view_tabs.setCurrentIndex(
                self._TAB_FIT if self._view_tabs.currentIndex() == self._TAB_EMBED
                else self._TAB_EMBED)
            event.accept()
            return
        if k == Qt.Key.Key_L and not desc_focused:
            self._geolocate_current()
            event.accept()
            return
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right,
                 Qt.Key.Key_Up,   Qt.Key.Key_Down) and not desc_focused:
            if k in (Qt.Key.Key_Left, Qt.Key.Key_Up):
                self._prev()
            else:
                self._next()
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
        # The Camera-JPEG tab holds one picture at a time; flipping to another must
        # re-read it (and re-label the tab) rather than leave the previous frame up.
        if getattr(self, "_embed_key", None) != key:
            self._embed_key = None
            if getattr(self, "_view_tabs", None) is not None \
                    and self._view_tabs.currentIndex() == self._TAB_EMBED:
                self._load_embedded()

        pos = f"  ({self._index + 1} of {len(self._keys)})"
        name = item.display_name or key
        self.setWindowTitle(name + pos)
        # Title bar (mockup): "name · file", so the picture on screen is named
        # right above it as well as in the OS titlebar.
        self._titlebar_lbl.setText(f"{name}  ·  {key}")
        for _fs in (self._filmstrip_left, self._filmstrip_bottom):
            _fs.set_current(key, self._index, len(self._keys))

        # Identity header: display name (bold) + scientific name(+modifiers).
        # Scientific convention is "Genus species" — capitalise the leading
        # genus letter (the resolver returns lower-case), leaving the rest as-is.
        self._panel_name.setText(item.display_name or key)
        _sci = (getattr(item, "sci_name", "") or "").strip()
        if _sci:
            _sci = _sci[0].upper() + _sci[1:]
        _scitxt = (_sci + (getattr(item, "qualifier_suffix", "") or "")).strip()
        self._panel_sci.setText(_scitxt)
        self._panel_sci.setVisible(bool(_scitxt))

        # FILE
        self._file_key_lbl.setText(item.key)
        exts = item.file_exts
        self._file_exts_lbl.setText("  ·  ".join(exts) if exts else "")
        self._file_exts_lbl.setVisible(bool(exts))
        dir_path = self._collection.file_directory(key)
        # Full filename (dir + UPN + file-types) on hover — the header shows the
        # short UPN, the tooltip gives the whole thing.
        _full = (dir_path + "\\" if dir_path else "") + item.key
        if exts:
            _full += "  (" + " · ".join(exts) + ")"
        self._file_key_lbl.setToolTip(_full)
        _crop = getattr(item, "has_crop", False)
        self._file_crop_lbl.setText("⌗ Crop" if _crop else "")
        self._file_crop_lbl.setVisible(_crop)
        self._explorer_btn.setEnabled(bool(dir_path))
        raw = self._collection.raw_path(key)
        self._dxo_btn.setEnabled(raw is not None)
        self._dxo_btn.setToolTip("Open the RAW file in the associated viewer" if raw
                                 else "No RAW file for this picture")
        self._crop_btn.setEnabled(getattr(item, "can_set_crop", False))

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

        # Show the master/RAW size and, when it differs, the displayed JPG size
        # too: "5600x3728  (JPG 800x533)". Older pictures often have a small JPG.
        dims = exif.dimensions or ""
        jpg_dims = exif.jpg_dimensions or ""
        if jpg_dims and jpg_dims != dims:
            dims = f"{dims}  (JPG {jpg_dims})" if dims else f"JPG {jpg_dims}"
        self._exif_dims_lbl.setText(dims)
        self._exif_dims_lbl.setVisible(bool(dims))
        # Keep the full datetime ("2026:03:16 15:30:45" → "2026-03-16 15:30:45")
        date_t = item.date.replace(":", "-", 2) if item.date else ""
        self._exif_date_lbl.setText(date_t)
        self._exif_date_lbl.setVisible(bool(date_t))
        self._gps_lbl.setText(exif.gps or "")
        self._gps_lbl.setVisible(bool(exif.gps))

        self._show_location(key)

        # SPECIES
        self._rebuild_species_section(item, key)

        # RATING
        self._stars.set_rating(item.rating)
        self._stars.setEnabled(item.can_edit_rating)
        self._color_sel.set_color(item.color)
        if item.can_edit_color:
            self._color_sel.setEnabled(True)
            self._color_sel.set_disabled_colors(
                _DISCARD_COLORS if (item.has_jpg
                                    and not getattr(item, "discard_allowed", False))
                else (), _DISCARD_GREYED_TIP)
        else:
            self._color_sel.setEnabled(False)
        self._rate_lbl.setText(f"Rate: {item.rate}" if item.rate is not None else "")
        self._apply_frame_color(item.color or "")

        # Top-bar Notes button: only when this picture's text is writable.
        self._hd_btn.setVisible(item.can_edit_headline or item.can_edit_description)

        self._original_headline = item.headline or ""
        self._headline_edit.blockSignals(True)
        self._headline_edit.setText(self._original_headline)
        self._headline_edit.setReadOnly(not item.can_edit_headline)
        self._headline_edit.setToolTip("" if item.can_edit_headline
                                        else "Headlines can't be saved for this picture.")
        self._headline_edit.blockSignals(False)

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

        # If the 100% tab is showing, decode the new picture too — keeping the
        # same 1:1 point so a group of similars compares at the same crop.
        if getattr(self, "_view_tabs", None) is not None \
                and self._view_tabs.currentIndex() == self._TAB_FULLRES:
            self._load_fullres()

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

        # Landscape / context "blog extra": no species. Show a greyed Topic block
        # instead of the species editor (whatever the host), with a way back to a
        # species. The Headline field over in the DESCRIPTION column is its title.
        if getattr(item, "is_blog_extra", False):
            self._build_landscape_block(item, key)
            return

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
            # Culling (no fpr context) but the subject IS writable → embed the full
            # inline species editor (name + sex/age/plumage + GBIF + recents), the
            # same widget the 's'-key dialog wraps. Writes via the item's own
            # sidecar set_species. Read-only hosts (album/master) fall through to
            # the read-only display below.
            can_inline = (getattr(item, "can_edit_species", False)
                          and not getattr(item, "from_master", False))
            if not can_inline and item.species:
                # Show the FORM as NAME + abbreviated suffix ("roodborst juv."), never
                # the raw subject: that already spells the qualifiers out as WORDS, so
                # item.species + suffix doubled them ("kanarie, female, juvenile ♀ juv.").
                from gui.culling_species import split_species_qualifiers
                name_only, _ = split_species_qualifiers(item.species)
                suffix = getattr(item, "qualifier_suffix", "") or ""
                lbl = _field_lbl((name_only or item.species) + suffix)
                lbl.setStyleSheet("font-size: 11px; font-weight: bold;")
                self._species_cl.addWidget(lbl)
            if item.headline:
                self._species_cl.addWidget(_small_lbl(item.headline))
            if item.error_msg:
                err = _small_lbl(f"⚠  {item.error_msg}")
                err.setStyleSheet("font-size: 10px; color: #c0392b;")
                self._species_cl.addWidget(err)
            if not can_inline and not item.species and not item.error_msg:
                ph = _small_lbl("(no species)")
                ph.setStyleSheet("font-size: 10px; color: #999; font-style: italic;")
                self._species_cl.addWidget(ph)
            if can_inline:
                from gui.culling_species import CullingSpeciesWidget
                editor = CullingSpeciesWidget(
                    self._inline_species_sc(), item.species or "", 1,
                    parent=self._species_container, embedded=True)
                editor.committed.connect(
                    lambda subject, k=key: self._commit_inline_species(subject, k))
                self._species_cl.addWidget(editor)
            # A master picture has no species EDITOR here (its determination is settled),
            # but its FORM is a property of the photograph and remains editable — which is
            # the whole reason the qualifier exists. Same flow as the card's right-click.
            if (getattr(item, "from_master", False)
                    and hasattr(self._collection, "apply_master_qualifiers")
                    and not getattr(self._collection, "master_read_only", True)):
                qbtn = QPushButton("Sex / age / plumage…")
                qbtn.setToolTip(
                    "Which form of the species this photo shows. The website publishes "
                    "one best photo per form, so this changes what appears.")
                qbtn.clicked.connect(lambda _c, k=key: self._edit_master_qualifiers(k))
                self._species_cl.addWidget(qbtn)

        # Offer marking this photo as a landscape / context photo wherever the
        # subject is writable (fpr editor OR the item's own species writer).
        self._add_mark_landscape_button(item, key)

    def _inline_species_sc(self):
        """The species collection backing the inline culling editor. Reuse the
        host's cached one when it has it (the culling grid loads it once); else
        load and cache here so navigation doesn't re-read the CSV each picture."""
        fn = getattr(self.parent(), "_culling_sc", None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
        if getattr(self, "_inline_sc_cache", "unset") == "unset":
            try:
                from gui.culling_species import load_species_classification
                self._inline_sc_cache = load_species_classification()
            except Exception as e:
                logger.warning(f"inline species editor: no species collection ({e})")
                self._inline_sc_cache = None
        return self._inline_sc_cache

    def _commit_inline_species(self, subject: str, key: str):
        """Write the inline editor's chosen subject to THIS picture via the item's
        own sidecar writer, then refresh the section + strips + host. Scoped to
        the one picture on purpose — batch species-set stays on the 's'-key dialog,
        so a range selected for some other reason isn't silently overwritten."""
        try:
            self._collection.item_for_key(key).set_species(subject)
        except Exception as e:
            logger.error(f"inline set_species failed for {key}: {e}")
            return
        self._on_species_committed({key})

    # ── Landscape / context "blog extra" ──────────────────────────────────────

    def _can_set_landscape(self, item, key: str) -> bool:
        """True when this host can change the subject to/from the landscape marker
        — either the item exposes its own species writer (sidecar / culling) or the
        collection offers the fpr species-editor context (trip / rating editor)."""
        if getattr(item, "can_edit_species", False):
            return True
        return self._collection.species_editor_context(key) is not None

    def _add_mark_landscape_button(self, item, key: str):
        if not self._can_set_landscape(item, key):
            return
        btn = QPushButton("🌄  Mark as landscape / context photo")
        btn.setToolTip(
            "Mark this photo as a landscape / context photo — a 'blog extra' titled "
            "by its Headline, not a species. It appears in the day blog but is kept "
            "out of the species website and the master collection.")
        btn.clicked.connect(lambda _c, k=key: self._mark_as_landscape(k))
        self._species_cl.addWidget(btn)

    def _build_landscape_block(self, item, key: str):
        lbl = _field_lbl("🌄  Landscape / context photo")
        lbl.setStyleSheet("font-size: 11px; font-weight: bold; color: #7a7a7a;")
        self._species_cl.addWidget(lbl)
        hint = _small_lbl("No species — shown in the day blog under its Headline; "
                          "kept out of the species website and the master.")
        hint.setStyleSheet("font-size: 10px; color: #999;")
        hint.setWordWrap(True)
        self._species_cl.addWidget(hint)
        if item.headline:
            h = _small_lbl(item.headline)
            h.setStyleSheet("font-size: 11px; font-weight: bold;")
            self._species_cl.addWidget(h)
        if self._can_set_landscape(item, key):
            btn = QPushButton("Not a landscape → set species")
            btn.setToolTip("Clear the landscape marker so this photo is treated as a "
                           "species again.")
            btn.clicked.connect(lambda _c, k=key: self._unmark_landscape(k))
            self._species_cl.addWidget(btn)

    def _mark_as_landscape(self, key: str):
        self._set_landscape_subject(key, "landscape")

    def _unmark_landscape(self, key: str):
        self._set_landscape_subject(key, "")

    def _set_landscape_subject(self, key: str, subject: str):
        """Set (or clear) the landscape marker via whichever species-write path this
        host supports (the item's own set_species for sidecar/culling, else the fpr
        editor context), then rebuild so the Species section flips modes."""
        item = self._item
        try:
            if item is not None and getattr(item, "can_edit_species", False):
                item.set_species(subject)
                updated = {key}
            else:
                ctx = self._collection.species_editor_context(key)
                if ctx is None:
                    return
                fpr, file_paths = ctx
                upns, _ = fpr.updateSubjects(file_paths, subject)
                updated = set(upns) or {key}
        except Exception as e:
            logger.warning(f"LargeImageView: set landscape marker failed: {e}")
            return
        self._on_species_committed(updated)

    def _edit_master_qualifiers(self, key: str):
        from gui.culling_species import edit_master_qualifiers
        if not edit_master_qualifiers(self, self._collection, key):
            return
        if self._item is not None:
            self._rebuild_species_section(self._item, self._item.key)
        self._refresh_strip_items(key)
        self.picture_changed.emit(key)

    def _on_species_committed(self, upns: set):
        if self._item is not None:
            self._rebuild_species_section(self._item, self._item.key)
            for k in upns:
                self._refresh_strip_items(k)
        self.species_committed.emit(upns)
        for k in upns:
            self.picture_changed.emit(k)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _is_reject(self, key):
        try:
            item = self._collection.item_for_key(key)
        except (ValueError, KeyError):
            return False
        return (item.color or "").capitalize() in ("Blue", "Purple")

    def _go(self, delta: int):
        if not self._keys:
            return
        if self._crop_mode:
            self._exit_crop_mode(reload=False)  # _load_current below restores the view
        self._save_description()
        self._save_headline()
        n = len(self._keys)
        step = 1 if delta >= 0 else -1
        # In the 100% tab, Blue/Purple are rejects and aren't even decoded, so
        # navigation steps over them to the next live candidate (Fit tab / grids
        # keep normal navigation, so a reject is still reachable to re-judge).
        skip = (getattr(self, "_view_tabs", None) is not None
                and self._view_tabs.currentIndex() == self._TAB_FULLRES)
        new = self._index + step
        if skip:
            while 0 <= new < n and self._is_reject(self._keys[new]):
                new += step
        if not (0 <= new < n) or new == self._index:
            return  # hit an end (or only rejects ahead) — stay put
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
        if self._crop_mode:
            self._exit_crop_mode(reload=False)
        self._save_description()
        self._save_headline()
        self._index = new
        self._load_current()
        self.picture_navigated.emit(key)

    def _on_filmstrip_context(self, key: str):
        """Right-click a filmstrip cell → a small menu with Delete. Acts on the
        current selection when the clicked cell is part of it, else on that one
        picture. The host does the actual trashing (same protection as the grid)
        and calls drop_keys() with what it removed."""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        if not self._allow_delete or key not in self._keys:
            return
        keys = (set(self._selected_keys)
                if key in self._selected_keys and self._selected_keys else {key})
        n = len(keys)
        menu = QMenu(self)
        act_del = menu.addAction(f"🗑 Delete  ({n} picture{'s' if n != 1 else ''})")
        if menu.exec(QCursor.pos()) is act_del:
            self.delete_requested.emit(keys)

    def drop_keys(self, deleted):
        """Remove trashed pictures from this viewer: drop them from the key list and
        both filmstrips, land on a surviving neighbour (or close if none remain), and
        re-render. Called by the host after it has actually trashed them."""
        deleted = {k for k in deleted if k in self._keys}
        if not deleted:
            return
        old_order = list(self._keys)
        cur_key = self._keys[self._index] if self._keys else None
        self._keys = [k for k in self._keys if k not in deleted]
        self._selected_keys -= deleted
        for _fs in (self._filmstrip_left, self._filmstrip_bottom):
            _fs.remove_keys(deleted)
        if not self._keys:
            self.close()
            return
        if cur_key in deleted:
            old_i = old_order.index(cur_key)
            nxt = next((k for k in old_order[old_i + 1:] if k in self._keys), None)
            if nxt is None:
                nxt = next((k for k in reversed(old_order[:old_i]) if k in self._keys), None)
            self._index = self._keys.index(nxt) if nxt in self._keys else 0
        else:
            self._index = self._keys.index(cur_key)
        self._load_current()
        self.picture_navigated.emit(self._keys[self._index])

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
        for _fs in (self._filmstrip_left, self._filmstrip_bottom):
            _fs.set_selected(self._selected_keys)
        n = len(self._selected_keys)
        self._sel_bar.setVisible(n > 0)
        if n > 0:
            self._sel_label.setText(
                f"{n} picture{'s' if n != 1 else ''} selected")
            self._apply_all_btn.setText(f"Apply to {n} selected")
            self._apply_all_btn.setEnabled(True)
            self._apply_all_btn.setStyleSheet(
                "font-size: 10px; color: #aad4ff; padding: 1px 4px; margin-top: 2px;")
        else:
            self._apply_all_btn.setText("Apply to selected")
            self._apply_all_btn.setEnabled(False)
            self._apply_all_btn.setStyleSheet(
                "font-size: 10px; color: #555; padding: 1px 4px; margin-top: 2px;")
        self.selection_changed.emit(set(self._selected_keys))

    def _clear_selection(self):
        self._selected_keys.clear()
        self._bracket_left = None
        self._bracket_right = None
        self._update_selection_display()

    def _apply_to_all_selected(self):
        """Apply the current picture's rating + colour to every selected picture."""
        if not self._selected_keys or not self._item:
            return
        stars = self._item.rating
        color = self._item.color
        key_list = list(self._selected_keys)
        total = len(key_list)
        cur_key = self._item.key

        progress = QDialog(self)
        progress.setWindowTitle("Even geduld…")
        progress.setModal(True)
        progress.setFixedSize(320, 110)
        prog_layout = QVBoxLayout(progress)
        prog_lbl = QLabel(f"Updating 0 of {total}…")
        prog_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prog_layout.addWidget(prog_lbl)
        spin_lbl = QLabel()
        spin_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        movie = QMovie("spinner.gif")
        spin_lbl.setMovie(movie)
        prog_layout.addWidget(spin_lbl)
        movie.start()
        progress.show()
        QApplication.processEvents()

        changed: list[str] = []
        for i, k in enumerate(key_list):
            prog_lbl.setText(f"Updating {i + 1} of {total}…")
            QApplication.processEvents()
            if k == cur_key:
                changed.append(k)
                continue
            try:
                item = self._collection.item_for_key(k)
                item.set_rating(stars)
                item.set_color(color)
                changed.append(k)
            except (ValueError, KeyError):
                pass

        progress.close()

        for k in changed:
            self._refresh_strip_items(k)
        for k in changed:
            self.picture_changed.emit(k)

    def _batch_species(self):
        # Default to the currently-viewed picture when nothing is multi-selected.
        keys = set(self._selected_keys) or ({self._item.key} if self._item else set())
        if not keys:
            return
        fpr = None
        file_paths, species_names, error_msgs = [], [], []
        for k in keys:
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
            # Sidecar / culling mode (no fpr) → smart culling species dialog.
            self._batch_species_culling(keys)
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
            dlg.accept(),
        ))
        layout.addWidget(panel)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(cancel_btn)
        layout.addLayout(row)
        dlg.exec()  # modal — prevents the panel from z-fighting with the main viewer

    def _batch_species_culling(self, keys):
        """fpr-free species assignment for the culling viewer: the smart culling
        dialog writes the chosen name to each picture's XMP:Subject."""
        keys = {k for k in keys if k}
        if not keys:
            return
        item0 = self._collection.item_for_key(next(iter(keys)))
        if not getattr(item0, "can_edit_species", False):
            return
        from gui.culling_species import CullingSpeciesDialog
        host = self.parent()
        sc = host._culling_sc() if hasattr(host, "_culling_sc") else None
        # Pre-fill with the species of the picture being viewed (helps when
        # re-tagging a group that's mostly the same species), else the single
        # selected picture's species.
        current = ((self._item.species if self._item is not None else "")
                   or (item0.species if len(keys) == 1 else ""))
        dlg = CullingSpeciesDialog(sc, current, len(keys), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = dlg.chosen_name()
            for k in keys:
                try:
                    self._collection.item_for_key(k).set_species(name)
                except Exception as e:
                    logger.error(f"set_species failed for {k}: {e}")
            self._on_species_committed(set(keys))

    def _batch_rating(self):
        if not self._selected_keys:
            return
        # The selection bar's "Set rating…" is always visible, but a read-only
        # viewer with no edit carve-out must not batch-write to the master. Edit
        # flags are per-collection, so one representative item answers for all.
        rep = self._collection.item_for_key(next(iter(self._selected_keys)))
        can_rate = getattr(rep, "can_edit_rating", False)
        can_color = getattr(rep, "can_edit_color", False)
        if not (can_rate or can_color):
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

        # Track interaction so stars=0 or color="" can mean "clear", not "no change".
        _star_touched = [False]
        _color_touched = [False]
        star_w.rating_changed.connect(lambda _: _star_touched.__setitem__(0, True))
        color_w.color_changed.connect(lambda _: _color_touched.__setitem__(0, True))

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        stars = star_w.rating
        color = color_w.color
        if not _star_touched[0] and not _color_touched[0]:
            return  # user didn't interact with anything
        key_list = list(self._selected_keys)
        total = len(key_list)

        # Progress dialog — modal so user can't trigger re-sorts mid-write.
        progress = QDialog(self)
        progress.setWindowTitle("Even geduld…")
        progress.setModal(True)
        progress.setFixedSize(320, 110)
        prog_layout = QVBoxLayout(progress)
        prog_lbl = QLabel(f"Updating 0 of {total}…")
        prog_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prog_layout.addWidget(prog_lbl)
        spin_lbl = QLabel()
        spin_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        movie = QMovie("spinner.gif")
        spin_lbl.setMovie(movie)
        prog_layout.addWidget(spin_lbl)
        movie.start()
        progress.show()
        QApplication.processEvents()

        # Apply writes; processEvents() keeps the spinner animated and the
        # progress label updating.  Signals are emitted only after all writes
        # are done so the host can't re-sort and invalidate the key list mid-loop.
        changed: list[str] = []
        for i, k in enumerate(key_list):
            prog_lbl.setText(f"Updating {i + 1} of {total}…")
            QApplication.processEvents()
            try:
                item = self._collection.item_for_key(k)
                if _star_touched[0] and can_rate:
                    item.set_rating(stars)   # stars=0 clears rating
                if _color_touched[0] and can_color:
                    item.set_color(color)    # color="" clears colour
                changed.append(k)
            except (ValueError, KeyError):
                pass

        progress.close()

        # Refresh filmstrip and notify host only after all writes are done.
        for k in changed:
            self._refresh_strip_items(k)
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
        self._refresh_strip_items(self._item.key)
        self.picture_changed.emit(self._item.key)
        if self._auto_advance and n > 0 and not self._selected_keys:
            self._next()

    def _on_color(self, color: str):
        if self._item is None or not self._item.can_edit_color:
            return
        self._item.set_color(color)
        self._apply_frame_color(color)
        self._rate_lbl.setText(f"Rate: {self._item.rate}" if self._item.rate is not None else "")
        self._refresh_strip_items(self._item.key)
        self.picture_changed.emit(self._item.key)
        if self._auto_advance and color and not self._selected_keys:
            self._next()

    def _edit_headline_description_current(self):
        """Open the shared Headline / Description editor for the picture on screen,
        then mirror the written values back into the panel's own fields (which may
        be collapsed / scrolled off — the reason the top-bar button exists)."""
        item = self._item
        if item is None:
            return
        from gui.headline_desc_dialog import (edit_headline_description,
                                              apply_headline_description)
        values = edit_headline_description(
            self, title=item.display_name or item.key,
            headline=item.headline, description=item.description,
            can_edit_headline=item.can_edit_headline,
            can_edit_description=item.can_edit_description)
        if not apply_headline_description(item, values):
            return
        self._original_headline = item.headline
        self._headline_edit.blockSignals(True)
        self._headline_edit.setText(item.headline)
        self._headline_edit.blockSignals(False)
        self._original_description = item.description
        self._desc_edit.blockSignals(True)
        self._desc_edit.setPlainText(item.description)
        self._desc_edit.blockSignals(False)
        self._refresh_strip_items(item.key)
        self.picture_changed.emit(item.key)

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

    def _save_headline(self):
        if self._item is None or not self._item.can_edit_headline:
            return
        text = self._headline_edit.text()
        if text == self._original_headline:
            return
        try:
            self._item.set_headline(text)
            self._original_headline = text
            self.picture_changed.emit(self._item.key)
        except Exception as e:
            logger.warning(f"LargeImageView: headline save failed: {e}")

    # ── External tools (Explorer / DxO PhotoLab) ───────────────────────────────

    def _open_in_explorer(self):
        """Open the picture's folder in Explorer with the UPN typed into the
        search box, so only the group's files show. Equivalent to: open the
        folder, then type the UPN in Explorer's search bar."""
        if self._item is None:
            return
        key = self._item.key
        dir_path = self._collection.file_directory(key)
        if dir_path:
            reveal_group(dir_path, key)

    def _open_in_dxo(self):
        """Open the RAW file in DxO PhotoLab — gives the Customize view with a
        filmstrip of siblings, and the PhotoLibrary tab lands in the right
        species folder.

        On Windows we dispatch via `os.startfile` (which calls ShellExecuteEx
        with the file's shell IDList — the same path Explorer takes on a
        right-click → Open). A plain Popen launch, even with cwd set, gave
        DxO only the single file and a 'Today' filter, no filmstrip; the
        IDList seems to be the missing context. Trade-off: this opens
        whatever the user has registered as the default RAW handler, ignoring
        the configured [Global] dxo_path. On Mac/Linux we fall through to the
        configured app path (no equivalent shell-context plumbing needed
        there as far as we know).
        """
        if self._item is None:
            return
        raw = self._collection.raw_path(self._item.key)
        if not raw:
            return
        if sys.platform.startswith("win"):
            if open_path(raw):
                return
            # fall through to configured-path attempt if startfile failed
        exe = dxo_executable()
        if not Path(exe).exists():
            QMessageBox.warning(
                self, "DxO PhotoLab not found",
                f"Could not find DxO PhotoLab at:\n\n{exe}\n\n"
                "Set the correct path in Settings → Global Settings.")
            return
        if not launch_app_with_file(exe, raw):
            QMessageBox.warning(
                self, "DxO PhotoLab",
                f"Could not launch DxO PhotoLab for:\n{raw}")

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
        # The PICTURE drives the height: the panel is in a scroll area and its
        # sections collapse, so the text never forces the window taller — which
        # means the floor can stay small and you can freely shrink a wide photo's
        # window down to a short, wide one. (A large minimum here was what pinned
        # the window tall.) Keep just a usability floor.
        self.setMinimumSize(720, 400)
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
        if self._crop_mode:
            self._sync_crop_overlay_geom()

    def showEvent(self, event):
        super().showEvent(event)
        clamp_to_visible_screen(self)
        self.setFocus()
        if self._panel_first_show:
            self._panel_first_show = False
            st = get_window_state(self._geo_key)
            # H-splitter: image | panel (both modes)
            if st["hsplit"] and len(st["hsplit"]) == 2:
                self._splitter.setSizes(st["hsplit"])
            else:
                # First-open default — a bit above the 300 floor so the qualifier
                # buttons and recents have breathing room; the user's own dragged
                # width is remembered after that.
                panel_w = 340
                image_w = max(200, self.width() - panel_w - self._splitter.handleWidth())
                self._splitter.setSizes([image_w, panel_w])
            # Load saved sizes for both positions; only apply the active one now.
            # The inactive one is stored in _last_* for when the user toggles.
            strip_w = _ITEM_W + 30
            strip_h = _ITEM_H + 40
            self._last_fsplit = (st["fsplit"] if st["fsplit"] and len(st["fsplit"]) == 2
                                 else [strip_w, max(400, self.width() - strip_w
                                                    - self._outer_splitter.handleWidth())])
            self._last_vsplit = (st["vsplit"] if st["vsplit"] and len(st["vsplit"]) == 2
                                 else [max(200, self.height() - strip_h
                                          - self._v_splitter.handleWidth()), strip_h])
            if self._strip_left:
                self._outer_splitter.setSizes(self._last_fsplit)
            else:
                self._v_splitter.setSizes(self._last_vsplit)
        QTimer.singleShot(0, self._fit_pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_pixmap()

    def closeEvent(self, event):
        self._save_description()
        self._save_headline()
        # Save the ACTIVE splitter's live sizes; use _last_* for the inactive one
        # (querying a splitter with a hidden child returns misleading 0 values).
        save_window(self, self._geo_key,
                    vsplit=self._v_splitter.sizes() if not self._strip_left else self._last_vsplit,
                    hsplit=self._splitter.sizes(),
                    fsplit=self._outer_splitter.sizes() if self._strip_left else self._last_fsplit)
        super().closeEvent(event)
