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
import os
import sys
import time
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QTextEdit,
    QLineEdit, QSizePolicy, QScrollArea, QSplitter, QFrame, QPushButton, QToolButton,
    QStyle, QMessageBox, QTabWidget, QCheckBox,
)
from PyQt6.QtGui import (
    QMovie, QPixmap, QImage, QGuiApplication, QShortcut, QKeySequence, QPainter, QColor,
    QIcon, QFont)
from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal

from gui.rating_editor import (StarWidget, ColorSelector, COLOR_HEX, _FramedPhoto,
                               _corner_badge, stamp_play_marker)
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


def _handover_delay_s() -> float:
    """How long a running PhotoLab gets to re-read the folder's .xmp files
    (colours, ratings) after we point it there, before we write the held .dop
    edits. [Global] photolab_handover_delay_s, default 5.

    Measured in test T7 (2026-09-29, 6 pictures): PhotoLab had caught up within
    1 s of being pointed at the folder, both when it came from another folder and
    when it already showed this one. The default keeps a margin for a full day
    and a slower machine; a hand-edited value is clamped to 1-60 s."""
    try:
        v = float(get_setting("Global", "photolab_handover_delay_s", fallback="5"))
    except (TypeError, ValueError):
        v = 5.0
    return min(60.0, max(1.0, v))


# After the button, pictures whose .dop PhotoLab has not created yet are
# watched this long, and handed over the moment their recipe appears.
_WAIT_POLL_MS = 3000
_WAIT_MAX_S = 90
_followers = []          # keeps the running follow-up timers alive


def handover_around_launch(raw, name_for=None) -> None:
    """Bring the folder's .dop files up to date with the crop/exposure edits SV
    held back while PhotoLab was running (sidecarlib.handover_to_photolab), timed
    so the .dop write cannot wipe a colour PhotoLab has not read yet (T6,
    docs/plans/photolab_sidecar_fight.md):

    * PhotoLab closed  -> write the .dop files now, BEFORE it starts.
    * PhotoLab running -> the launch that follows points it at the folder, which
      makes it re-read our .xmp files (T4); write the .dop files only after that.

    A picture PhotoLab has not processed yet has no .dop to write to — it makes
    one only when it reaches that thumbnail. Those are followed for _WAIT_MAX_S
    and handed over as soon as the recipe appears (seen live 2026-09-29: crops
    on a freshly read day that "did not always arrive").

    Everything is reported as ONE summary in the log, per picture and with its
    species name when `name_for(raw) -> str` is given.
    Scope: every RAW in the picture's folder that has an .xmp."""
    from engine.sidecarlib import handover_to_photolab
    from utils.photolab_process import photolab_running
    from utils.raw_extensions import is_raw
    folder = Path(str(raw)).parent
    raws = [str(p) for p in folder.iterdir()
            if is_raw(p) and (p.with_suffix(".xmp").exists() or p.with_suffix(".XMP").exists())]
    clicked = time.time()

    def _run(note):
        res = handover_to_photolab(raws)
        _log_handover_summary(folder.name, res, name_for, note)
        if res["waiting"]:
            _follow_waiting(res["waiting"], name_for, clicked)

    if not photolab_running():
        _run("PhotoLab was closed")
        return
    delay = _handover_delay_s()
    before = _dop_mtimes(raws)
    logger.info(f"Open in PhotoLab: pointing it at {folder.name}; handing over "
                f"waiting crops/exposure in {delay:g} s")

    def _later():
        after = _dop_mtimes(raws)
        touched = [t for r, t in after.items() if t != before.get(r)]
        note = (f"PhotoLab re-read {len(touched)} picture(s) in "
                f"{max(touched) - clicked:.1f} s" if touched else
                "PhotoLab re-read nothing new")
        _run(note)

    QTimer.singleShot(int(delay * 1000), _later)


def _pic_label(raw, name_for) -> str:
    name = ""
    if name_for:
        try:
            name = name_for(raw) or ""
        except Exception:
            name = ""
    stem = Path(str(raw)).stem
    return f"{stem:24} {name}".rstrip()


def _log_handover_summary(folder_name, res, name_for, note):
    done, waiting = res["done"], res["waiting"]
    if not done and not waiting:
        logger.info(f"PhotoLab handover {folder_name}: nothing waiting ({note})")
        return
    logger.info(f"PhotoLab handover {folder_name}: {len(done)} handed over, "
                f"{len(waiting)} waiting for PhotoLab ({note})")
    for r, fields in done:
        logger.info(f"   ✓ {_pic_label(r, name_for)} — {', '.join(fields)}")
    for r, fields in waiting:
        logger.info(f"   … {_pic_label(r, name_for)} — {', '.join(fields)}: PhotoLab "
                    f"has no recipe for it yet; handed over as soon as it has")
    if waiting:
        logger.info("   Tip: in PhotoLab, select all (Ctrl-A) and Write sidecars once per "
                    "day — then every picture has its recipe and nothing has to wait.")


def _follow_waiting(waiting, name_for, clicked):
    """Poll the waiting pictures for their .dop; hand each over when it appears."""
    from engine.sidecarlib import handover_to_photolab
    left = {r: f for r, f in waiting}
    timer = QTimer()
    timer.setInterval(_WAIT_POLL_MS)

    def _tick():
        ready = [r for r in left if os.path.exists(r + ".dop")]
        if ready:
            res = handover_to_photolab(ready)
            for r, fields in res["done"]:
                logger.info(f"   ✓ {_pic_label(r, name_for)} — {', '.join(fields)} "
                            f"(PhotoLab made its recipe after {time.time() - clicked:.0f} s)")
            for r in ready:
                left.pop(r, None)
        if not left or time.time() - clicked > _WAIT_MAX_S:
            timer.stop()
            if timer in _followers:
                _followers.remove(timer)
            for r, fields in left.items():
                logger.warning(f"   ✗ {_pic_label(r, name_for)} — {', '.join(fields)} "
                               f"still waiting after {_WAIT_MAX_S} s: in PhotoLab, select "
                               f"it and Write sidecars, then press Open in PhotoLab again")

    timer.timeout.connect(_tick)
    _followers.append(timer)
    timer.start()


def _dop_mtimes(raws) -> dict:
    out = {}
    for r in raws:
        try:
            out[r] = os.path.getmtime(r + ".dop")
        except OSError:
            pass
    return out


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


def _cropped_size_text(item) -> str:
    """Pixel size of the cropped frame, e.g. "3132x2543" — the crop rectangle
    applied to the picture's full dimensions. "" when either is unknown (a baked
    render keeps no readable rectangle), so the crop row falls back to the bare
    ⌗ marker."""
    rect = getattr(item, "crop_rect", None)
    if not rect:
        return ""
    dims = (getattr(item, "exif", None).dimensions or "") if getattr(item, "exif", None) else ""
    try:
        w, h = (int(v) for v in dims.lower().split("x")[:2])
    except Exception:
        return ""
    cw = round(float(rect.get("width", 1.0)) * w)
    ch = round(float(rect.get("height", 1.0)) * h)
    return f"{cw}x{ch}" if cw > 0 and ch > 0 else ""


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

class _StripPlaceholder(QWidget):
    """Cheap stand-in for a not-yet-materialized _StripItem — same fixed size
    so the strip's scroll geometry is correct from the start, but none of the
    frame/thumbnail/corner-badge sub-widgets that made building one per
    picture up front cost seconds on a big day. FilmstripWidget._materialize
    swaps it for a real _StripItem the first time its picture actually needs
    to be shown (current picture, or scrolled into view)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(_ITEM_W, _ITEM_H)


class _StripItem(QWidget):
    """One thumbnail cell in the filmstrip — compact, colour-bordered."""
    tapped = pyqtSignal(str)         # emits key on left click
    right_clicked = pyqtSignal(str)  # emits key on right click (context menu)

    def __init__(self, item, parent=None, viewer_key="strip"):
        super().__init__(parent)
        self._key    = item.key
        self._item   = item
        self._viewer_key = viewer_key
        self._requested = False   # True once request_load() has fired
        self._current   = False
        self._selected  = False

        from gui.card_layout import corner_config
        self._cfg = corner_config(viewer_key)

        self.setFixedSize(_ITEM_W, _ITEM_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(item.display_name or item.key)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(3, 3, 3, 2)
        vbox.setSpacing(2)

        # Framed cell: an olive/colour picture frame with a white mat, matching
        # the grid card. Frame colour + corner badges come from this viewer's set.
        self._frame = QFrame()
        self._frame.setObjectName("scell")
        fl = QVBoxLayout(self._frame)
        fl.setContentsMargins(4, 4, 4, 4)
        fl.setSpacing(0)
        self._thumb_lbl = _FramedPhoto()     # hosts the corner badges
        self._thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_lbl.setFixedSize(_THUMB_W, _THUMB_H)
        self._thumb_lbl.setStyleSheet(
            f"background:{theme.SURFACE_2}; border:2px solid {theme.SURFACE}; border-radius:2px;")
        fl.addWidget(self._thumb_lbl)
        vbox.addWidget(self._frame)
        vbox.addStretch(1)

        self._build_corners()
        self._repaint_border()

    def _build_corners(self):
        """(Re)build the configured corner badges over the thumbnail. Colour is
        the frame; lock isn't meaningful on a strip cell, so both are skipped."""
        from gui.card_layout import render_datum
        for w, _pos in list(getattr(self._thumb_lbl, "_badges", [])):
            w.setParent(None)
            w.deleteLater()
        self._thumb_lbl._badges = []
        for pos in ("tl", "tr", "bl", "br"):
            datum = self._cfg.get(pos, "none")
            if datum in ("none", "colour", "lock"):
                continue
            txt = render_datum(datum, self._item)
            if txt:
                self._thumb_lbl.add_badge(_corner_badge(txt, bold=(datum == "format")), pos)
        self._thumb_lbl.reposition()

    def _repaint_border(self):
        """Paint the frame: colour label (grey when unlabelled, or always olive
        per the viewer's frame setting); current cell = CURRENT_RING, selected =
        SELECT_MULTI (brown) ring."""
        if self._cfg.get("frame") == "olive":
            c = theme.ACCENT
        else:
            c = color_to_css(self._item.color) or theme.FRAME_UNRATED
        if self._current:
            ring = f"border:2px solid {theme.CURRENT_RING};"   # same as the grids' cursor
        elif self._selected:
            ring = f"border:2px solid {theme.SELECT_MULTI};"
        else:
            ring = ""
        self._frame.setStyleSheet(f"QFrame#scell {{ background:{c}; {ring} border-radius:5px; }}")

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
            if getattr(self._item, "is_video", False):
                scaled = stamp_play_marker(scaled)   # same cue as the grid card
            self._thumb_lbl.setPixmap(scaled)

    def refresh_from(self, item):
        """Rebuild corners + repaint the colour frame after an edit."""
        self._item = item
        self._build_corners()
        self._repaint_border()

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
                 orientation=Qt.Orientation.Horizontal, viewer_key="strip"):
        super().__init__(parent)
        self._collection  = collection
        self._keys        = list(keys)
        self._cells: dict[str, _StripItem] = {}
        self._current_key: str | None = None
        self._orientation = orientation
        self._viewer_key = viewer_key
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

        # Placeholders for every key — cheap enough that building thousands of
        # them costs nothing worth measuring, unlike the real _StripItem this
        # used to build for every one of them (frame + thumbnail slot + corner
        # badges each), which is what made opening a 2000+ picture day's
        # filmstrip cost seconds before the window could even paint. Swapped
        # for the real thing on demand — see _materialize.
        self._selected_keys: set[str] = set()
        for key in self._keys:
            cell = _StripPlaceholder(self._strip)
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

    def _materialize(self, key: str):
        """Ensure `key` has a real _StripItem, upgrading its placeholder in
        place if this is the first time it actually needs to be shown.
        `layout.replaceWidget` swaps it in at the exact same layout slot the
        placeholder held — same fixed size, so no repositioning is needed —
        which is what lets this stay a plain QBoxLayout instead of the
        absolute-positioning a full virtualized view would need. Returns the
        cell (real or, if the key can't be resolved, the untouched
        placeholder) or None if `key` isn't in this strip at all."""
        cell = self._cells.get(key)
        if cell is None or isinstance(cell, _StripItem):
            return cell
        try:
            item = self._collection.item_for_key(key)
        except (ValueError, KeyError):
            return cell
        real = _StripItem(item, self._strip, viewer_key=self._viewer_key)
        real.tapped.connect(self.item_clicked)
        real.right_clicked.connect(self.item_right_clicked)
        self._strip.layout().replaceWidget(cell, real)
        cell.deleteLater()
        self._cells[key] = real
        if key == self._current_key:
            real.set_current(True)
        if key in self._selected_keys:
            real.set_selected(True)
        return real

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

    def set_order(self, keys):
        """Show the SAME pictures in a new order (Editor re-sort / time order).
        Moves the existing cells — loaded thumbnails stay loaded."""
        if set(keys) != set(self._keys):
            return
        lay = self._strip.layout()
        for key in self._keys:
            lay.removeWidget(self._cells[key])
        self._keys = list(keys)
        for key in self._keys:
            lay.addWidget(self._cells[key])
        QTimer.singleShot(0, self._load_visible)

    def set_current(self, key: str, index: int, total: int):
        """Highlight `key`, scroll it into view, update the counter."""
        old = self._cells.get(self._current_key) if self._current_key else None
        if isinstance(old, _StripItem):
            old.set_current(False)
        self._current_key = key
        cell = self._materialize(key)
        if isinstance(cell, _StripItem):
            cell.set_current(True)
            QTimer.singleShot(0, lambda: self._sa.ensureWidgetVisible(cell))
        self._pos_lbl.setText(f"{index + 1} of {total}")

    def refresh_item(self, key: str, reload_image: bool = False):
        """Repaint a cell's stars + colour border after an edit; reload_image=True
        also re-decodes its thumbnail (an external re-crop / new render). A
        cell still showing its placeholder has nothing to repaint — it picks
        up fresh data the moment it's materialized anyway."""
        cell = self._cells.get(key)
        if not isinstance(cell, _StripItem):
            return
        try:
            item = self._collection.item_for_key(key)
        except (ValueError, KeyError):
            return
        cell.refresh_from(item)
        if reload_image:
            cell.reload_thumbnail()

    def set_selected(self, keys: set):
        """Update selection highlight on all materialized cells, and remember
        the set so a cell materialized later (scrolled into view) starts with
        the right highlight instead of none."""
        self._selected_keys = set(keys)
        for k, cell in self._cells.items():
            if isinstance(cell, _StripItem):
                cell.set_selected(k in self._selected_keys)

    # ── Lazy loading ───────────────────────────────────────────────────────────

    def _on_scrolled(self, _):
        self._load_visible()

    def resizeEvent(self, event):
        """A resize changes how many cells fit, so re-run the visible sweep.
        Without this the strip stays stuck with whatever fitted at build time:
        the one-shot sweep runs before the window has been laid out, when the
        viewport is still effectively zero-sized, so only the _PREFETCH margin
        (5 cells) ever appeared until the user scrolled."""
        super().resizeEvent(event)
        self._load_visible()

    def showEvent(self, event):
        """Same reason, for the strip that starts hidden (the other
        orientation): it may already have its final size when it is shown, so
        no resize follows to trigger the sweep."""
        super().showEvent(event)
        self._load_visible()

    def _load_visible(self):
        # A resize can reach us while the constructor is still running (the
        # minimum size is set before the scroll area exists) — nothing to sweep
        # yet, the one-shot sweep at the end of __init__ covers it.
        if not hasattr(self, "_sa"):
            return
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
            cell = self._materialize(self._keys[i])
            if isinstance(cell, _StripItem):
                cell.request_load()


# The corner-badge look used over an image: the interaction hint (top-left) and
# the source/size overlay (bottom-left) here, and the Quick view's transient
# message (top-right). Module-level so the three cannot drift apart — they sit
# over the same picture and a second style would read as a second kind of thing.
BADGE_CSS = ("QLabel{background:rgba(0,0,0,150);color:#eee;"
             "padding:2px 7px;border-radius:3px;font-size:11px;}")


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
        self._zoom_locked = False  # user zoomed past fit → carry that zoom to the
                                   # next picture (culling a burst at 1:1)
        self._overlay = None       # bottom-left: source + size
        self._hint = None          # top-left: how to zoom/pan
        if self._zoomable:
            self._label.mouseDoubleClickEvent = self._on_double_click
            self._overlay = QLabel(self.viewport())
            self._overlay.setStyleSheet(BADGE_CSS)
            self._overlay.hide()
            self._hint = QLabel(self.viewport())
            self._hint.setStyleSheet(BADGE_CSS)
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
            fit = self._fit_scale()
            if self._zoom_locked and self._scale > fit + 1e-4:
                # Keep the zoom the user set on the previous picture and land on
                # the same spot (point), so a burst can be checked at 1:1 without
                # re-zooming after every colour label.
                self._scale = min(1.0, self._scale)
                self._fit = False
                self._render_scaled()
                self._center_on(point)
            else:
                self._fit = True
                self._scale = fit
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
        self._zoom_locked = not self._fit
        self._render_scaled()
        new_w = max(1.0, self._pixmap.width() * self._scale)
        new_h = max(1.0, self._pixmap.height() * self._scale)
        self.horizontalScrollBar().setValue(int(fx * new_w - ax))
        self.verticalScrollBar().setValue(int(fy * new_h - ay))
        self._emit_point()

    def restore_view(self, scale: float, hscroll: int, vscroll: int):
        """Restore an EXACT prior view (scale + scroll position), bypassing
        set_image's normal fit / carry-zoom-and-recenter logic. For a caller
        that remembers a per-picture view state (see QuickViewer) — set_image
        alone has no memory of where a specific picture was framed, only
        "current zoom, recentred on the new image", which is right for
        stepping through a burst but wrong for returning to a picture you
        deliberately panned."""
        if self._pixmap.isNull():
            return
        self._scale = max(self._fit_scale(), min(1.0, scale))
        self._fit = abs(self._scale - self._fit_scale()) < 1e-4
        self._zoom_locked = not self._fit
        self._render_scaled()
        self.horizontalScrollBar().setValue(hscroll)
        self.verticalScrollBar().setValue(vscroll)
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
            self._zoom_locked = False
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
    dxo_launched      = pyqtSignal(str)  # raw path — opened in DxO; host watches its folder for the render
    # keys, bracket_left, bracket_right — emitted whenever filmstrip selection
    # changes; the brackets travel too so a [ / ] range can be opened in one
    # viewer and closed in another (grid, Large Image View, Quick Viewer all
    # share the same [ / ] administration).
    selection_changed = pyqtSignal(set, object, object)
    delete_requested  = pyqtSignal(set)  # keys — right-click Delete on a filmstrip cell

    def __init__(self, parent, collection, keys, index, *,
                 settings_key="LargeImageView", auto_advance=True,
                 initial_selection: set | None = None, allow_delete: bool = False,
                 initial_brackets: tuple | None = None):
        super().__init__(parent)
        # Whether the filmstrip offers a right-click Delete (the host must handle
        # delete_requested). Off for read-only / non-culling hosts, so there is no
        # menu item that does nothing.
        self._allow_delete = bool(allow_delete)
        # Own taskbar button — a minimised large view must be findable. ORing
        # Qt.WindowType.Window onto a QDialog's existing flags does nothing
        # (Qt.WindowType.Dialog is defined as 0x2 | Window, so the bit is
        # already set) — the type has to be replaced outright.
        self.setWindowFlags(Qt.WindowType.Window
                            | Qt.WindowType.WindowTitleHint
                            | Qt.WindowType.WindowSystemMenuHint
                            | Qt.WindowType.WindowMinMaxButtonsHint
                            | Qt.WindowType.WindowCloseButtonHint)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._collection = collection
        self._keys = list(keys)
        self._index = max(0, min(index, len(self._keys) - 1)) if self._keys else 0
        self._auto_advance = auto_advance
        self._settings_key = settings_key
        self._geo_key = f"{settings_key}Large"
        self._item = None
        self._pixmap = QPixmap()
        self._fit_base_img = None   # unadjusted decode, for cheap exposure re-renders
        self._load_token = 0
        self._original_description = ""
        self._original_headline = ""
        self._panel_first_show = True
        self._selected_keys: set[str] = set(initial_selection) if initial_selection else set()
        self._bracket_left, self._bracket_right = initial_brackets if initial_brackets else (None, None)
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
        # Exposure suggestion — a live-previewed EV nudge for PhotoLab/Lightroom,
        # same sidecar mechanism as Crop (see gui/exposure_widget.py + engine/
        # sidecarlib.suggest_exposure). Sits left of Crop per the intended
        # reading order: "adjust the light, then the frame."
        from gui.exposure_widget import ExposureWidget
        self._exposure_widget = ExposureWidget(0.0)
        self._exposure_widget.live.connect(self._on_exposure_live)
        self._exposure_widget.committed.connect(self._on_exposure_committed)
        top_bar_row.addWidget(self._exposure_widget)
        # The slider is the ONE-picture half of exposure. This is the other half:
        # push the value you just dialled to the whole selection, mirroring the
        # rating row's "Apply to selected". Without it the slider could only ever
        # reach the focused picture, and the batch dialog lived in one grid only.
        self._exposure_apply_btn = QToolButton()
        self._exposure_apply_btn.setAutoRaise(True)
        self._exposure_apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._exposure_apply_btn.setStyleSheet("font-size: 10px; padding: 1px 5px;")
        self._exposure_apply_btn.clicked.connect(self._apply_exposure_to_selection)
        self._exposure_apply_btn.setVisible(False)
        top_bar_row.addWidget(self._exposure_apply_btn)
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
        # Create JPG — bake a website-ready JPG (embedded preview + crop) beside
        # the RAW for a quick blog. Only where the collection can (culling views).
        self._makejpg_btn = QToolButton()
        self._makejpg_btn.setAutoRaise(True)
        self._makejpg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._makejpg_btn.setStyleSheet("font-size: 10px; padding: 1px 5px;")
        self._makejpg_btn.setText("🖼 Make JPG")
        self._makejpg_btn.setToolTip(
            "Write a <name>.jpg next to the RAW from the camera's embedded "
            "preview + the crop — a quick blog picture without the full render.")
        self._makejpg_btn.clicked.connect(self._on_create_jpg)
        self._makejpg_btn.setVisible(hasattr(self._collection, "create_blog_jpg"))
        top_bar_row.addWidget(self._makejpg_btn)
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
        # Order on demand. The Editor keeps the order it opened with, so culling
        # never shuffles the picture under you; these re-order when YOU ask. Only
        # where the host grid can supply an order (editor_resort_keys — the Trip
        # Species Viewer). Time order toggles: oldest first follows the session,
        # newest first puts the end of an approach — often the best frames — on top.
        _orders = callable(getattr(parent, "editor_resort_keys", None))
        self._resort_btn = QToolButton()
        self._resort_btn.setAutoRaise(True)
        self._resort_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._resort_btn.setStyleSheet("font-size: 10px; padding: 1px 5px;")
        self._resort_btn.setText("⟳ Re-sort")
        self._resort_btn.setToolTip(
            "Re-sort these pictures the way the grid sorts them (F5) — picks up "
            "the colours and ratings you just gave.")
        self._resort_btn.clicked.connect(self._resort_from_host)
        self._resort_btn.setVisible(_orders)
        top_bar_row.addWidget(self._resort_btn)
        self._time_desc = None          # None = not in time order yet
        self._time_btn = QToolButton()
        self._time_btn.setAutoRaise(True)
        self._time_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._time_btn.setStyleSheet("font-size: 10px; padding: 1px 5px;")
        self._time_btn.setToolTip(
            "Order by capture time; click again to flip between oldest first "
            "and newest first.")
        self._time_btn.clicked.connect(self._toggle_time_order)
        self._time_btn.setVisible(_orders)
        self._update_time_btn()
        top_bar_row.addWidget(self._time_btn)
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

        # Left — image stage. Near-black surround (independent of the light/dark
        # theme): a neutral dark ground is the right backdrop for judging a photo.
        self._stage = QWidget()
        self._stage.setObjectName("imgStage")
        self._stage.setStyleSheet("QWidget#imgStage { background: #141414; }")
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
        self._image_label.mousePressEvent = self._on_fit_click
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
        self._fullres_base_img = None   # unadjusted decode, for cheap exposure re-renders
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
        self._embed_base_img = None     # unadjusted decode, for cheap exposure re-renders
        self._embed_overlay = ""
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
        # "NEW SPECIES" — rides the section header when the master has never had a
        # picture of this species (a first for the collection). Shown only while
        # culling; kept alive across picture changes, toggled per picture.
        self._new_species_badge = QLabel("NEW SPECIES")
        self._new_species_badge.setStyleSheet(
            f"font-family:{_MONO}; font-size:9px; font-weight:700; letter-spacing:1px;"
            f" color:#7a4b00; background:#ffd873; border-radius:3px; padding:1px 6px;")
        self._new_species_badge.setToolTip(
            "The master has no picture of this species yet — a first for your collection.")
        self._new_species_badge.hide()
        sec_species.add_trailing_widget(self._new_species_badge)
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

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #555; margin: 0;")

        # ── Both filmstrips always exist; toggle just shows/hides them ────────
        self._filmstrip_left = FilmstripWidget(collection, self._keys, self,
                                               orientation=Qt.Orientation.Vertical)
        # Protect the strip: it must always keep room for one row/column of
        # thumbnails, so a window resize shrinks the IMAGE, not the filmstrip.
        self._filmstrip_left.setMinimumWidth(_ITEM_W + 30)
        self._filmstrip_left.item_clicked.connect(self._on_filmstrip_click)
        self._filmstrip_left.item_right_clicked.connect(self._on_filmstrip_context)

        self._filmstrip_bottom = FilmstripWidget(collection, self._keys, self)
        self._filmstrip_bottom.setMinimumHeight(_ITEM_H + 22)
        self._filmstrip_bottom.item_clicked.connect(self._on_filmstrip_click)
        self._filmstrip_bottom.item_right_clicked.connect(self._on_filmstrip_context)

        # Content wrapper: image+panel splitter + sep
        # (top pane of _v_splitter in both modes)
        content_wrapper = QWidget()
        wrap_vbox = QVBoxLayout(content_wrapper)
        wrap_vbox.setContentsMargins(0, 0, 0, 0)
        wrap_vbox.setSpacing(0)
        wrap_vbox.addWidget(self._splitter, stretch=1)
        wrap_vbox.addWidget(sep)

        # _v_splitter: content (top) | bottom filmstrip (bottom). Content takes
        # all the give; the strip keeps its height and can't be collapsed away.
        self._v_splitter.addWidget(content_wrapper)
        self._v_splitter.addWidget(self._filmstrip_bottom)
        self._v_splitter.setStretchFactor(0, 1)
        self._v_splitter.setStretchFactor(1, 0)
        self._v_splitter.setCollapsible(1, False)

        # right_pane: wraps _v_splitter so it can be the right child of _outer_splitter
        right_pane = QWidget()
        right_vbox = QVBoxLayout(right_pane)
        right_vbox.setContentsMargins(0, 0, 0, 0)
        right_vbox.setSpacing(0)
        right_vbox.addWidget(self._v_splitter, stretch=1)

        # _outer_splitter: left filmstrip | right_pane
        self._outer_splitter.addWidget(self._filmstrip_left)
        self._outer_splitter.setStretchFactor(0, 0)
        self._outer_splitter.setCollapsible(0, False)   # left strip stays put
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

    def _on_fit_click(self, _e):
        """Click on the Fit picture. Normally just takes focus back from whatever
        field had it; on a MOVIE it starts playback, because a play symbol you can
        click is the one affordance everybody already knows."""
        self.setFocus()
        if getattr(self._item, "is_video", False):
            self._play_current_video()

    def _on_fit_double_click(self, e):
        # Double-click the photo → the Camera JPEG tab (opens fit, wheel-zooms to
        # 1:1). Remember the clicked spot so a later 80% visit centres there.
        if getattr(self._item, "is_video", False):
            return          # a movie has no RAW to zoom into; the click plays it
        self._fullres_point = self._fit_click_to_point(e.position())
        self._view_tabs.setCurrentIndex(self._TAB_EMBED)

    def _play_current_video(self):
        """Play the movie this picture IS, in the Quick view.

        Handing off rather than building a second player here is deliberate: the
        Quick view already has one (QMediaPlayer with sound, Space to pause), so a
        movie plays in exactly ONE kind of window wherever you start from — and
        this dialog's Space is long since taken by 'next picture'. The whole key
        list goes with it, positioned on this picture, so the arrows keep working
        there exactly as they do here. No-op on a still."""
        if self._item is None or not getattr(self._item, "is_video", False):
            return
        existing = getattr(self, "_quick_view", None)
        if existing is not None:
            try:
                existing.close()            # never two players at once
            except RuntimeError:
                pass
        from gui.quick_viewer import QuickViewer
        dlg = QuickViewer(self, self._collection, list(self._keys), self._index)
        dlg.edited.connect(self._on_quick_view_edited)
        dlg.action_requested.connect(self._on_quick_view_action)
        dlg.finished.connect(lambda _=0: setattr(self, "_quick_view", None))
        self._quick_view = dlg
        dlg.show()
        dlg.activateWindow()
        dlg.setFocus()

    def _on_quick_view_edited(self, keys):
        """A rating or colour set in the Quick view we opened — repaint here and
        tell the host, exactly as an edit made in this dialog would."""
        for k in keys:
            self.refresh_external(k)
            self.picture_changed.emit(k)

    def _on_quick_view_action(self, _what, _keys):
        """'s' / 'a' / 'c' and the copy-paste keys pressed in the player we opened.

        Deliberately NOT wired through to this dialog's own handlers: those act on
        the picture THIS window is showing, while the player can have stepped on to
        another one — and a species or a GPS written to the wrong picture is a data
        bug, not a cosmetic one. Rating and colour keys do work there (they carry
        their own target and come back through `edited`), so this says which half
        is which rather than letting a key do nothing at all."""
        qv = getattr(self, "_quick_view", None)
        if qv is not None:
            qv.flash("Naming and copy/paste live in the viewer behind this one.")

    def _close_quick_view(self):
        """Shut any Quick view we opened, so a movie can't go on playing (with
        sound) behind a viewer that has closed."""
        qv = getattr(self, "_quick_view", None)
        self._quick_view = None
        if qv is not None:
            try:
                qv.close()
            except RuntimeError:
                pass

    def _on_view_tab_changed(self, idx):
        if idx in (self._TAB_EMBED, self._TAB_FULLRES):
            if self._crop_mode:
                self._exit_crop_mode(reload=True)  # crop is a Fit-tab tool
        if idx == self._TAB_FULLRES:
            self._load_fullres()
        elif idx == self._TAB_EMBED:
            self._load_embedded()

    def _no_raw_message(self) -> str:
        """What the zoom tabs say when there is no RAW behind the picture. A movie
        is not a missing file, so it does not get the same sentence — it gets told
        what to do instead."""
        if getattr(self._item, "is_video", False):
            return "This is a movie — press v, or click the picture, to play it."
        return "No RAW file for this picture."

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
            self._embed_view.set_message(self._no_raw_message())
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
            self._embed_base_img = None
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
        # Kept UNADJUSTED — _render_embedded re-applies the LUT from here on every
        # exposure-slider tick, instead of re-extracting + re-decoding the RAW's
        # embedded JPEG each time (the expensive ~50-70ms part; the LUT itself is
        # a few ms — see gui/exposure_render.py).
        self._embed_base_img = px
        self._embed_overlay = overlay
        self._render_embedded(self._exposure_widget.value_ev)

    def _render_embedded(self, ev: float):
        """(Re)draw the Camera-JPEG tab from the cached unadjusted embed at the
        given EV. No-op if nothing has been decoded yet (e.g. the tab hasn't
        been opened for this picture)."""
        img = getattr(self, "_embed_base_img", None)
        if img is None or img.isNull():
            return
        from gui.exposure_render import apply_exposure_to_pixmap
        shown = apply_exposure_to_pixmap(img, ev)
        self._embed_view.set_image(shown, self._fullres_point,
                                   overlay=getattr(self, "_embed_overlay", ""))

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
            self._fullres_view.set_message(self._no_raw_message())
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
            self._fullres_base_img = None
            self._fullres_view.set_message("Could not decode full resolution.")
            return
        # Kept UNADJUSTED — same reasoning as _embed_base_img: re-applying the
        # LUT on an exposure-slider tick is cheap regardless of image size, but
        # re-running the RAW decode per tick would not be.
        self._fullres_base_img = px
        self._render_fullres(self._exposure_widget.value_ev)

    def _render_fullres(self, ev: float):
        """(Re)draw the 80%/full-res tab from the cached unadjusted decode at
        the given EV. No-op if nothing has been decoded yet."""
        img = getattr(self, "_fullres_base_img", None)
        if img is None or img.isNull():
            return
        from gui.exposure_render import apply_exposure_to_pixmap
        self._fullres_view.set_image(apply_exposure_to_pixmap(img, ev), self._fullres_point)

    # ── Exposure suggestion ──────────────────────────────────────────────────
    def _on_exposure_live(self, ev: float):
        """Cheap re-render of whichever tab is showing, from its cached
        UNADJUSTED base image (a LUT pass, a few ms — see
        gui/exposure_render.py) — so this can run on every slider tick. No
        sidecar write here; see _on_exposure_committed."""
        if getattr(self, "_view_tabs", None) is None:
            return
        if getattr(self, "_crop_mode", False):
            # Crop mode shows the RAW uncropped frame via its own pixmap
            # assignment (_on_uncropped_for_crop), bypassing _fit_base_img —
            # rendering from that (stale) cache here would replace the frame
            # you're actively drawing a rectangle on with a wrong image.
            return
        idx = self._view_tabs.currentIndex()
        if idx == self._TAB_FIT:
            self._render_fit(ev)
        elif idx == self._TAB_EMBED:
            self._render_embedded(ev)
        elif idx == self._TAB_FULLRES:
            self._render_fullres(ev)

    def _update_exposure_apply_btn(self):
        """Show 'Apply EV to N' only when a selection is present and this picture
        can take an exposure at all — the same gate the slider itself uses."""
        btn = getattr(self, "_exposure_apply_btn", None)
        if btn is None:
            return
        n = len(self._selected_keys)
        can = bool(self._item is not None
                   and getattr(self._item, "can_set_exposure", False))
        btn.setVisible(n > 1 and can)
        btn.setText(f"Apply EV to {n}")
        btn.setToolTip(
            f"Write the exposure now on the slider to all {n} selected pictures.")

    def _apply_exposure_to_selection(self):
        """Push the slider's current value to the whole selection."""
        keys = set(self._selected_keys)
        if not keys:
            return
        from gui.exposure_batch import write_exposure
        value = self._exposure_widget.value_ev
        n = write_exposure(self, self._collection, keys, value,
                           on_written=lambda k: self.refresh_external(
                               k, reload_image=True))
        for key in keys:
            self.picture_changed.emit(key)
            self.picture_image_changed.emit(key)   # the grid card must re-decode
        logger.info(f"Exposure {value:+.2f} EV applied to {n} picture(s).")

    def _on_exposure_committed(self, ev: float):
        """Persist the exposure suggestion to this picture's sidecars. Also
        re-renders (in case committed fires without a preceding live tick,
        e.g. a keyboard nudge with no drag)."""
        if self._item is None or not getattr(self._item, "can_set_exposure", False):
            return
        try:
            res = self._item.set_exposure(ev)
        except Exception as e:
            logger.warning(f"Exposure: could not write suggestion: {e}")
            return
        self._on_exposure_live(ev)
        if res:
            created = [k.upper() for k in ("dop", "xmp") if res.get(f"{k}_created")]
            extra = f"  (created {', '.join(created)})" if created else ""
            logger.info(f"Exposure suggestion ({ev:+.2f} EV) written to "
                        f"PhotoLab + Lightroom sidecars.{extra}")

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

    def _apply_pending_crop(self) -> bool:
        """Apply a crop that is still being drawn, for an action that CONSUMES the
        crop. Enter is what normally commits it, but handing the picture to
        PhotoLab or baking a JPG says the same thing — so those apply it for you
        and Enter becomes optional. Without this the action quietly used the
        PREVIOUS framing: PhotoLab reads the crop from the .dop and Make JPG from
        the sidecar, neither of which exists until the commit.

        `_on_crop_committed` runs synchronously off this signal, so the .dop and
        .xmp are written before we return and whatever we launch next sees the new
        frame. Crop mode with nothing drawn yet has nothing to apply and is left
        exactly as it is."""
        if not self._crop_mode or self._crop_overlay is None:
            return False
        return self._crop_overlay.commit()

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

    def _on_create_jpg(self):
        """Bake a website-ready JPG (embedded preview + crop) for the current
        picture — mirrors the culling grid's right-click action."""
        if self._item is None or not hasattr(self._collection, "create_blog_jpg"):
            return
        # Same reason as the PhotoLab hand-over: the bake reads the crop from the
        # sidecar, so an unapplied one would bake yesterday's framing into the JPG.
        # Key first — applying the crop refreshes the view and rebinds self._item.
        key = self._item.key
        self._apply_pending_crop()
        from PyQt6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            res = self._collection.create_blog_jpg(key)
        finally:
            QApplication.restoreOverrideCursor()
        st = res.get("status")
        created = [res["jpg"]] if st == "created" else []
        from gui.create_jpg_result import show_create_jpg_result
        show_create_jpg_result(
            self, created,
            skipped=1 if st == "skipped" else 0,
            no_raw=1 if st == "no_raw" else 0,
            already_jpg=1 if st == "already_jpg" else 0,
            failed=1 if st == "failed" else 0)
        if st == "created":
            # Re-decode here + in the host grid to the freshly-cropped JPG.
            self.refresh_external(key, reload_image=True)
            self.picture_changed.emit(key)
            self.picture_image_changed.emit(key)

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
        # Tell the host, the way a rating or a crop here already does: it is the
        # grid that owns the day and offers the Read & Summarize on close, and
        # place text written from this window is no less an edit than one made
        # from a card.
        self.picture_changed.emit(key)

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
        self.picture_changed.emit(key)   # the host owns the day — see _after_geocode
        place = changed.get("Location") or changed.get("City") or ""
        logger.info(f"Edit location: wrote {place!r} to {n} file(s) for this picture.")

    def contextMenuEvent(self, event):
        """Right-click anywhere in the viewer → the host grid's menu (_host_menu),
        or, for a host without one, location actions ('L' still does Get
        location). Child widgets with their own menu (the description box)
        keep theirs; this only fires for clicks they don't consume."""
        from PyQt6.QtWidgets import QMenu
        if not self._keys:
            return
        key = self._item.key if self._item else None
        if key and self._host_menu(key):
            return
        menu = QMenu(self)
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
        if event.key() == Qt.Key.Key_F5 and not self._resort_btn.isHidden():
            self._resort_from_host()
            event.accept()
            return
        # Space = next, routed through _go so it honours the 100%-tab reject-skip
        # (handle_cull_key's generic 'next' would jump straight to the literal next).
        if event.key() == Qt.Key.Key_Space and not self._desc_edit.hasFocus():
            self._next()
            event.accept()
            return
        from gui.viewer_keys import handle_cull_key, handle_video_key, text_input_focused
        # "Enter to apply" is what the crop overlay promises on screen, so honour it
        # whenever a crop is being drawn — not only while the overlay itself holds
        # keyboard focus. Anything that moves focus away (clicking a toolbar button,
        # the filmstrip, the panel) used to leave the hint quietly untrue, with no
        # way to tell from looking. Routing it here makes the promise
        # focus-independent; the overlay's own handler still serves the normal case.
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and getattr(self, "_crop_mode", False)
                and not text_input_focused()):
            self._apply_pending_crop()
            event.accept()
            return
        if self._keys and handle_cull_key(
                event, order=list(self._keys),
                current_key=self._keys[self._index],
                item_for_key=self._collection.item_for_key,
                set_cursor=self._cull_set_cursor,
                refresh_key=self._refresh_strip_items,
                open_species=self._batch_species,
                open_rating=self._cull_open_rating,
                quick_species=self._cull_quick_species,
                clipboard=self._cull_clipboard):
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
        # 'v' for video — the same key, from the same definition, as in both
        # grids. It guards on text_input_focused() rather than desc_focused
        # because the headline is a QLineEdit, and typing "vink" in it must not
        # start a movie.
        if handle_video_key(event,
                            current_key=self._keys[self._index] if self._keys else None,
                            item_for_key=self._collection.item_for_key,
                            play=lambda _k: self._play_current_video()):
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
        if k == Qt.Key.Key_X and not desc_focused:
            self._cull_set_color_quick("Blue")
            event.accept()
            return
        if (k == Qt.Key.Key_D and not desc_focused
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and self._selected_keys):
            # Same "deselect all" as the grids' Ctrl+D — Esc does it too, but
            # this keeps the binding consistent across all three viewers.
            self._clear_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    # ── Identity header (window title + name block) ───────────────────────────

    def _refresh_identity_header(self, item, key: str):
        """Repaint who this picture IS: the OS title, the title bar line, and the
        name block at the top of the side panel.

        Called on load AND after a species commit. It used to live inline in
        _load_current only, so a name or a sex/age/plumage qualifier set from the
        panel did not reach the header until you navigated away and back — the
        edit looked like it had been forgotten while the sidecar already held it.

        The name block is the detail equivalent of the Biota species page, so it
        shows ALL THREE names at once: the profile's primary (bold) on the first
        line, the other two (in profile order) on the second. A scientific name
        follows the "Genus species" convention, so its leading genus letter is
        capitalised (the resolver returns it lower-case). The sex/age/plumage
        suffix rides the primary line."""
        pos = f"  ({self._index + 1} of {len(self._keys)})"
        name = item.display_name or key
        # "Editor", not "Large Image View": the old name described the widget,
        # the new one the job, and it matches the app's existing vocabulary —
        # Viewer, Editor, never "... Window".
        # this one describes the job — species, rating, colour, crop, exposure,
        # headline, description, Make JPG, hand-off to PhotoLab. UI only; the class
        # stays LargeImageView (see docs/plans/edit_window_parity.md, D4). The
        # picture keeps the front of the title, where it identifies the window in
        # the taskbar better than any generic name would.
        self.setWindowTitle(f"{name}{pos} — Editor")
        # Title bar (mockup): "name · file", so the picture on screen is named
        # right above it as well as in the OS titlebar.
        self._titlebar_lbl.setText(f"{name}  ·  {key}")

        from utils.name_profile import all_names_ordered
        triple = getattr(item, "name_triple", None) or {"sci": getattr(item, "sci_name", "")}
        _row = {"nl": triple.get("nl", ""), "en": triple.get("en", ""),
                "sci": triple.get("sci", "")}
        lines = all_names_ordered(_row, getattr(item, "name_profile", ""))
        if not lines:
            lines = [((item.display_name or key), False)]

        def _fmt(text, is_sci):
            return (text[0].upper() + text[1:]) if (is_sci and text) else text

        _suffix = getattr(item, "qualifier_suffix", "") or ""
        self._panel_name.setText(_fmt(*lines[0]) + _suffix)
        _sec = " · ".join(_fmt(t, s) for t, s in lines[1:])
        self._panel_sci.setText(_sec)
        self._panel_sci.setVisible(bool(_sec))

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

        # Immediate visual feedback: the synchronous work below (species
        # panel rebuild, EXIF/location display) can take a while, and
        # leaving the PREVIOUS picture on screen with no cue makes a
        # navigation look unregistered — the exact "no feedback" complaint.
        # Clear it and force a paint before doing that work, same fix as the
        # slow dialogs elsewhere in the app.
        self._pixmap = QPixmap()
        self._image_label.setText("Loading…")
        QApplication.processEvents()

        # Drop the cached unadjusted base images from whatever picture this was
        # showing before — otherwise a slider drag landing in the narrow window
        # before the new picture's async decode completes would live-render the
        # PREVIOUS picture over the "Reading…" message.
        self._fit_base_img = None
        self._embed_base_img = None
        self._fullres_base_img = None
        # Sync the exposure control to THIS picture before triggering the async
        # embed load below — _on_embedded_pixmap reads the widget's current
        # value to render with, so it must already be right by the time that
        # callback fires. set_value_ev is silent (no live/committed signal),
        # so this never triggers a spurious re-write of the picture we're
        # leaving.
        self._exposure_widget.setEnabled(getattr(item, "can_set_exposure", False))
        self._exposure_widget.set_value_ev(getattr(item, "exposure", 0.0) or 0.0)
        # The Camera-JPEG tab holds one picture at a time; flipping to another must
        # re-read it (and re-label the tab) rather than leave the previous frame up.
        if getattr(self, "_embed_key", None) != key:
            self._embed_key = None
            if getattr(self, "_view_tabs", None) is not None \
                    and self._view_tabs.currentIndex() == self._TAB_EMBED:
                self._load_embedded()

        for _fs in (self._filmstrip_left, self._filmstrip_bottom):
            _fs.set_current(key, self._index, len(self._keys))
        self._refresh_identity_header(item, key)

        # FILE
        self._file_key_lbl.setText(item.key)
        exts = item.file_exts
        # A pending render (a JPG on disk the master hasn't registered yet) tints
        # the JPG token amber, so the cue is visible here too — not only on the grid
        # card. Purely derived from item state, so it clears itself once the render
        # is registered (and never persists past this window).
        if exts and getattr(item, "has_pending_render", False):
            toks = ("JPG",) if "JPG" not in exts else ()
            toks = list(exts) + list(toks)
            html = "  ·  ".join(
                '<span style="color:#b4530b;font-weight:700;">JPG</span>'
                if e == "JPG" else e for e in toks)
            self._file_exts_lbl.setText(html)
            self._file_exts_lbl._v.setToolTip(
                "This JPG render isn't in the master yet — add it from the grid "
                "card's “+ JPG”.")
        else:
            self._file_exts_lbl.setText("  ·  ".join(exts) if exts else "")
            self._file_exts_lbl._v.setToolTip("")
        self._file_exts_lbl.setVisible(bool(exts))
        dir_path = self._collection.file_directory(key)
        # Full filename (dir + UPN + file-types) on hover — the header shows the
        # short UPN, the tooltip gives the whole thing.
        _full = (dir_path + "\\" if dir_path else "") + item.key
        if exts:
            _full += "  (" + " · ".join(exts) + ")"
        self._file_key_lbl.setToolTip(_full)
        self._refresh_crop_row(item)
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

    def _refresh_crop_row(self, item):
        """The FILE panel's crop row: '⌗ Crop <size>', plus a warning when the
        picture's JPG shows the full frame, not the crop (known only once the
        image has loaded — _on_pixmap calls this again)."""
        _crop = getattr(item, "has_crop", False)
        text = ("⌗ Crop  " + _cropped_size_text(item)).rstrip() if _crop else ""
        stale = getattr(item, "jpg_out_of_date", False)
        if stale:
            from gui.picture_provider import JPG_OUT_OF_DATE_TIP
            text += f'  ·  <span style="color:{theme.WARN};">JPG out of date</span>'
            self._file_crop_lbl._v.setToolTip(JPG_OUT_OF_DATE_TIP)
        else:
            self._file_crop_lbl._v.setToolTip("")
        self._file_crop_lbl.setText(text)
        self._file_crop_lbl.setVisible(_crop)

    def _on_pixmap(self, token, pixmap):
        if token != self._load_token:
            return
        if self._item is not None:
            self._refresh_crop_row(self._item)   # the JPG-vs-crop verdict is in now
        # Kept UNADJUSTED — _render_fit re-applies the LUT from here on every
        # exposure-slider tick, same reasoning as _embed_base_img/
        # _fullres_base_img. This is the Fit tab: the FIRST thing shown when a
        # picture opens, so it must carry the exposure suggestion too, not
        # only the Camera-JPEG/80% tabs.
        self._fit_base_img = pixmap if (pixmap is not None and not pixmap.isNull()) else None
        self._render_fit(self._exposure_widget.value_ev)

    def _render_fit(self, ev: float):
        """(Re)draw the Fit tab from the cached unadjusted decode at the given
        EV. No-op (shows "(no image)") when nothing decoded."""
        base = getattr(self, "_fit_base_img", None)
        if base is None:
            self._pixmap = QPixmap()
            self._image_label.setText("(no image)")
            return
        from gui.exposure_render import apply_exposure_to_pixmap
        self._pixmap = apply_exposure_to_pixmap(base, ev)
        self._image_label.setText("")
        self._fit_pixmap()

    # ── Species section rebuild ───────────────────────────────────────────────

    def _rebuild_species_section(self, item, key: str):
        # _clear_layout deletes the widgets, so drop the reference first — reading
        # a deleted QCheckBox raises RuntimeError.
        self._species_all_box = None
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
                # Same scope affordance RATING already has ("Apply to N selected").
                # A row of pictures of the same bird is the normal case, and without
                # this the panel silently named only the one on screen. Built once
                # per section and kept alive across selection changes (rebuilding it
                # would throw away half-typed text); _update_selection_display keeps
                # its label and visibility in step.
                self._species_all_box = QCheckBox("")
                self._species_all_box.setToolTip(
                    "Off: only the picture on screen gets this species.\n"
                    "On: every picture in the selection gets it, qualifiers included.")
                self._species_all_box.setStyleSheet("font-size: 10px;")
                self._species_cl.addWidget(self._species_all_box)
                self._sync_species_all_box()
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

        self._refresh_new_species_badge(item)

    def _master_species_names(self):
        """Scientific names the master already has a picture of — read once from
        its species.csv and cached for the life of this window. None when no
        master is configured or its list can't be read, so the 'NEW SPECIES'
        badge is never shown on a guess.

        A SmallMaster laptop is fine here: it copies the whole metadata/active/
        folder (species.csv included) — only the JPG/XMP images are a subset —
        so the list is the full master's, just as of the snapshot date."""
        if hasattr(self, "_master_species_cache"):
            return self._master_species_cache
        names = None
        try:
            master = (get_setting("master", "masterbiotadatabase", fallback="") or "").strip()
            if master:
                from engine.species_baseline import collect_master_species
                got, _zero, _topic = collect_master_species(master)
                names = got or None
                logger.info(f"NEW SPECIES badge: {len(got)} species with pictures "
                            f"read from {master}\\metadata\\active\\species.csv")
            else:
                logger.info("NEW SPECIES badge: disabled (no master configured)")
        except Exception as e:
            logger.info(f"NEW SPECIES badge: master species list unavailable ({e})")
        self._master_species_cache = names
        return names

    def _refresh_new_species_badge(self, item):
        """Toggle the SPECIES-header 'NEW SPECIES' tag: on when this picture's
        species resolves to a scientific name the master has never had a picture
        of. Culling only (a master picture is by definition not new); hidden when
        the species isn't resolved yet or there's no master to compare to."""
        badge = getattr(self, "_new_species_badge", None)
        if badge is None:
            return
        is_new = False
        if not getattr(item, "from_master", False):
            names = self._master_species_names()
            if names:
                sci = ""
                triple = getattr(item, "name_triple", None)
                if isinstance(triple, dict):
                    sci = (triple.get("sci") or "").strip().lower()
                if not sci:
                    sci = (getattr(item, "sci_name", "") or "").strip().lower()
                is_new = bool(sci) and sci not in names
                logger.debug(f"NEW SPECIES badge: subject={getattr(item, 'species', '')!r} "
                             f"sci={sci!r} in_master={bool(sci) and sci in names} → new={is_new}")
        badge.setVisible(is_new)

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

    def _sync_species_all_box(self):
        """Keep the inline editor's scope tick in step with the selection: hidden
        for a selection of one (there is nothing to widen to), otherwise labelled
        with the count. Never ticks itself — widening the write stays a decision."""
        box = getattr(self, "_species_all_box", None)
        if box is None:
            return
        try:
            n = len(self._selected_keys)
            cur = self._item.key if self._item is not None else None
            show = n > 1 and cur in self._selected_keys
            box.setVisible(show)
            if show:
                box.setText(f"Apply to all {n} selected pictures")
            else:
                box.setChecked(False)
        except RuntimeError:            # widget already deleted by a rebuild
            self._species_all_box = None

    def _commit_inline_species(self, subject: str, key: str):
        """Write the inline editor's chosen subject via the item's own sidecar
        writer, then refresh the section + strips + host.

        Scope: THIS picture, unless the "apply to all N selected" tick is on —
        the same rule as the headline editor. Defaulting to one keeps a range
        selected for some other reason from being silently renamed."""
        keys = [key]
        box = getattr(self, "_species_all_box", None)
        if box is not None:
            try:
                if box.isChecked():
                    keys += [k for k in self._selected_keys if k != key]
            except RuntimeError:
                pass
        written = set()
        for k in keys:
            try:
                self._collection.item_for_key(k).set_species(subject)
                written.add(k)
            except Exception as e:
                logger.error(f"inline set_species failed for {k}: {e}")
        if not written:
            return
        if len(written) > 1:
            logger.info(f"Set species '{subject}' on {len(written)} selected pictures.")
        self._on_species_committed(written)

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
            # The name block and the window title carry the species and its
            # sex/age/plumage suffix — repaint them here, or the commit only
            # shows up after navigating away and back.
            self._refresh_identity_header(self._item, self._item.key)
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

    # ── order on demand (host-supplied) ─────────────────────────────────────
    def _update_time_btn(self):
        self._time_btn.setText({None: "🕘 Time order", False: "🕘 Old → new",
                                True: "🕘 New → old"}[self._time_desc])

    def _resort_from_host(self):
        self._time_desc = None
        self._update_time_btn()
        self._apply_host_order("sort")

    def _toggle_time_order(self):
        self._time_desc = self._time_desc is False   # None → old→new → new→old → …
        self._update_time_btn()
        # A time order is a fresh walk through the session: start at its first
        # picture — the newest for New → old — so "next" steps one picture on.
        self._apply_host_order("time_desc" if self._time_desc else "time_asc",
                               from_start=True)

    def _apply_host_order(self, mode: str, from_start: bool = False):
        """Ask the host grid for these same pictures in `mode` order and show
        them so — staying on the picture you are looking at (a re-sort mid-cull),
        or jumping to the first one (`from_start`, a time order)."""
        fn = getattr(self.parent(), "editor_resort_keys", None)
        if not callable(fn) or not self._keys:
            return
        keys = fn(list(self._keys), mode)
        if not keys or set(keys) != set(self._keys):
            return
        if keys == self._keys and not from_start:
            return
        self._save_description()
        self._save_headline()
        current = self._keys[self._index]
        self._keys = list(keys)
        self._index = 0 if from_start else self._keys.index(current)
        for _fs in (self._filmstrip_left, self._filmstrip_bottom):
            _fs.set_order(self._keys)
        self._load_current()

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
            # Hit an end (or only rejects ahead) — stay put, and say so: nothing
            # on screen changes, so without the cue it looked like a missed key.
            from gui.end_cue import bump
            bump()
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
        if self._crop_mode:
            self._exit_crop_mode(reload=False)
        self._save_description()
        self._save_headline()
        self._index = new
        self._load_current()
        self.picture_navigated.emit(key)

    def _on_filmstrip_context(self, key: str):
        """Right-click a filmstrip cell → the host grid's menu (_host_menu), or,
        for a host without one, this Editor's own Set species… / Set rating… /
        Delete, the batch ops for a '[' ... ']' selection. Acts on the current selection when the clicked cell is part of it, else on
        that one picture. Delete still goes through the host (same protection
        as the grid) via drop_keys()."""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        if key not in self._keys or self._host_menu(key):
            return
        keys = (set(self._selected_keys)
                if key in self._selected_keys and self._selected_keys else {key})
        n = len(keys)
        rep = self._collection.item_for_key(key)
        menu = QMenu(self)
        act_species = act_rating = act_del = act_exposure = None
        if getattr(rep, "can_edit_species", False):
            act_species = menu.addAction(f"Set species…  ({n} picture{'s' if n != 1 else ''})")
        if getattr(rep, "can_edit_rating", False) or getattr(rep, "can_edit_color", False):
            act_rating = menu.addAction(f"Set rating…  ({n} picture{'s' if n != 1 else ''})")
        if getattr(rep, "can_set_exposure", False):
            act_exposure = menu.addAction(
                f"Set exposure…  ({n} picture{'s' if n != 1 else ''})")
        if self._allow_delete:
            if act_species or act_rating:
                menu.addSeparator()
            act_del = menu.addAction(f"🗑 Delete  ({n} picture{'s' if n != 1 else ''})")
        if not menu.actions():
            return
        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return
        if chosen is act_del:
            self.delete_requested.emit(keys)
        elif chosen is act_exposure:
            from gui.exposure_batch import open_batch_exposure_dialog
            open_batch_exposure_dialog(
                self, self._collection, keys,
                initial=self._exposure_widget.value_ev,
                on_written=lambda k: self.refresh_external(k, reload_image=True))
            for k in keys:
                self.picture_changed.emit(k)
                self.picture_image_changed.emit(k)
        elif chosen is act_species:
            self._batch_species(keys)
        elif chosen is act_rating:
            self._batch_rating(keys)

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
        self._update_exposure_apply_btn()
        if n > 0:
            self._apply_all_btn.setText(f"Apply to {n} selected")
            self._apply_all_btn.setEnabled(True)
            self._apply_all_btn.setStyleSheet(
                "font-size: 10px; color: #aad4ff; padding: 1px 4px; margin-top: 2px;")
        else:
            self._apply_all_btn.setText("Apply to selected")
            self._apply_all_btn.setEnabled(False)
            self._apply_all_btn.setStyleSheet(
                "font-size: 10px; color: #555; padding: 1px 4px; margin-top: 2px;")
        self._sync_species_all_box()
        self.selection_changed.emit(set(self._selected_keys), self._bracket_left, self._bracket_right)

    def _clear_selection(self):
        self._selected_keys.clear()
        self._bracket_left = None
        self._bracket_right = None
        self._update_selection_display()

    def clear_selection(self):
        """Host → Editor: the shared selection was cleared in the grid."""
        if self._selected_keys or self._bracket_left or self._bracket_right:
            self._clear_selection()

    def refresh_after_host_action(self, keys):
        """Repaint after the host grid acted on these pictures from its borrowed
        menu: filmstrip cells, and — for the picture on screen — the panel, the
        species section and the name block/title (a species or sex/age/plumage
        change shows there). Keys the action removed are skipped. Does NOT emit
        picture_changed: the host made the edit and already knows."""
        for k in [k for k in keys if k in self._keys]:
            self.refresh_external(k)
        if (self._item is not None and self._item.key in keys
                and self._item.key in self._keys):
            self._rebuild_species_section(self._item, self._item.key)
            self._refresh_identity_header(self._item, self._item.key)

    def _host_menu(self, key) -> bool:
        """Right-click → the menu of the grid this Editor was opened from, when
        that grid offers one (editor_context_menu). Returns False when it does
        not, so the caller shows the Editor's own menu (Biota Browser, maps)."""
        fn = getattr(self.parent(), "editor_context_menu", None)
        if not callable(fn) or key not in self._keys:
            return False
        self._save_description()
        self._save_headline()
        # Nothing of the Editor's own is added: the menu is the grid's, exactly
        # (his call, 2026-09-30). Sex / age / plumage for the picture on screen
        # stays on the side panel's button.
        acted = set(fn(key) or ())
        if not acted:
            return True
        # A delete, or a move to another species, takes pictures out of the
        # host's collection (it is rebuilt in place, and this Editor reads the
        # same object) — drop them here too, whichever action did it.
        gone = set()
        for k in acted:
            try:
                self._collection.item_for_key(k)
            except (ValueError, KeyError):
                gone.add(k)
        if gone:
            self.drop_keys(gone)
        self.refresh_after_host_action(acted - gone)
        # A host rebuild resets its selection without going through
        # _clear_selection; follow it, or this window keeps a stale range.
        if not getattr(self.parent(), "_selected_keys", True):
            self.clear_selection()
        return True

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

    def _cull_set_color_quick(self, color: str):
        """Stamp `color` onto the selection (else the current picture) in one
        write per picture, no dialog — 'x' for prepping a batch as Blue
        before deleting. Rating is left untouched. Then advances past
        whatever was stamped: one picture forward for a lone focus (same as
        the digit colour keys), or past the END of a selected group, clearing
        the selection so the next key press lands on a single fresh picture
        instead of re-acting on the group."""
        keys = set(self._selected_keys) or ({self._item.key} if self._item else set())
        if not keys or self._collection is None:
            return
        had_selection = bool(self._selected_keys)
        key_list = list(keys)
        cur_key = self._item.key if self._item else None
        for k in key_list:
            try:
                item = self._collection.item_for_key(k)
                item.set_color(color)
            except (ValueError, KeyError):
                continue
            self._refresh_strip_items(k)
        for k in key_list:
            self.picture_changed.emit(k)
        order = self._keys
        idx_max = max((order.index(k) for k in key_list if k in order), default=None)
        if idx_max is not None:
            if had_selection:
                self._clear_selection()
            self._cull_set_cursor(order[min(idx_max + 1, len(order) - 1)])
        elif cur_key in key_list:
            self._load_current()

    def _batch_species(self, keys=None):
        # Default target (the 's' key): whatever's multi-selected, else the
        # currently-viewed picture. A caller with its own target (the
        # filmstrip right-click menu) passes keys explicitly.
        if keys is None:
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

    def _species_subject_of(self, key):
        """This picture's stored species subject — the literal XMP:Subject via
        getSubject in pipeline mode, the collection's own species in culling
        mode. Deliberately NOT item.species here: in pipeline mode that is the
        RESOLVED display name (Dutch, under most name profiles), and copying a
        resolved name onto a burst would write a different string than the one
        the guide frame actually carries."""
        ctx = self._collection.species_editor_context(key)
        if ctx is None:
            try:
                return self._collection.item_for_key(key).species or ""
            except (ValueError, KeyError):
                return ""
        fpr, fps = ctx
        if not fps or fpr is None:
            return ""
        return fpr.getSubject(Path(fps[0]).name) or ""

    def _clipboard_keys(self):
        """What the dialog-free keys act on here: the marked selection, else the
        picture on screen. Mirrors the grids' _cull_keys_target()."""
        return set(self._selected_keys) or ({self._item.key} if self._item else set())

    def _ordered(self, keys):
        """`keys` in filmstrip order, with anything no longer present dropped.
        A selection is a set and has no order of its own — any rule that means
        "the first one" needs this first."""
        wanted = set(keys)
        out = [k for k in self._keys if k in wanted]
        return out or list(keys)

    def _write_species_to_keys(self, subject: str, keys) -> int:
        """Write `subject` to every key and report how many pictures it reached
        (0 = nothing landed).

        The one write shared by the two dialog-free species routes — 'a' (the
        subject the selection already agreed on) and Ctrl+V (the subject in the
        clipboard)."""
        from gui.culling_species import remember_species_from_subject
        keys = list(keys)
        if not keys:
            return 0
        n = len(keys)
        fpr, file_paths = None, []
        for k in keys:
            ctx = self._collection.species_editor_context(k)
            if ctx is None:
                continue
            f, fps = ctx
            if fpr is None:
                fpr = f
            file_paths.extend(fps)
        if not fpr or not file_paths:
            # Sidecar / culling mode — write straight to XMP:Subject.
            for k in keys:
                try:
                    self._collection.item_for_key(k).set_species(subject)
                except Exception as e:
                    logger.error(f"Species write failed for {k}: {e}")
                    return 0
            logger.info(f"Species write: {subject!r} -> {n} picture(s).")
            remember_species_from_subject(subject)
            self._on_species_committed(set(keys))
            return n
        updated, files_written = fpr.updateSubjects(file_paths, subject)
        # A commit that writes nothing is a failed commit and has to look like
        # one — there is no status line here to carry it.
        if files_written == 0:
            logger.error(f"Species write: 0 files for {n} picture(s); "
                         f"{subject!r} is NOT on disk.")
            QMessageBox.warning(
                self, "Species not stored",
                f"Nothing was written to disk, so '{subject}' is NOT stored on "
                f"these {n} pictures. See the log.")
            return 0
        logger.info(f"Species write: {subject!r} -> {len(updated)} picture(s), "
                    f"{files_written} file(s) written.")
        remember_species_from_subject(subject)
        self._on_species_committed(updated)
        return len(updated)

    def _cull_quick_species(self, keys=None):
        """'a' — the dialog-free half of 's': copy the species the selection
        already agrees on onto every picture in it, in one batched write.
        Sex/age/plumage ride along, because a qualifier lives in the subject
        string next to the name ("greater ani, male").

        Whenever the selection does NOT say one unambiguous thing — nothing
        named, several different names, or only the one picture on screen —
        this opens the regular Set species dialog instead, so 'a' is never a
        guess. Same rule as the two grids; quick_species_subject() owns it."""
        if keys is None:
            keys = self._clipboard_keys()
        if not keys:
            return 0
        from gui.culling_species import quick_species_subject
        subject = quick_species_subject(keys, self._species_subject_of)
        if not subject:
            self._batch_species(keys)
            return 0
        return self._write_species_to_keys(subject, keys)

    def _cull_clipboard(self, what: str):
        """The four copy/paste keys. The buffers are the ones the grids use, so
        a name copied here pastes there and in any other day's viewer.

        This window already shows its own result — the name block repaints, the
        location line updates — so the message only goes to the log."""
        keys = self._clipboard_keys()
        if not keys:
            return
        from gui import viewer_clipboard as clip
        msg = ""
        if what == "copy_species":
            subject = clip.copy_species_from(keys, self._species_subject_of)
            msg = (f"Copied species: {subject}" if subject else
                   "Nothing to copy — no species on these pictures, "
                   "or more than one between them.")
        elif what == "paste_species":
            subject = clip.species_buffer()
            if not subject:
                msg = "Nothing copied yet — Ctrl+C copies a species first."
            else:
                n = self._write_species_to_keys(subject, keys)
                msg = (f"Species name set for {n} picture{'s' if n != 1 else ''}"
                       if n else "")
        elif what == "copy_gps":
            # A copy is singular, so a selection has to resolve to ONE picture:
            # the first in filmstrip order that actually has a fix.
            for k in self._ordered(keys):
                msg = clip.copy_gps_from(self._collection, k)
                if msg:
                    break
            else:
                msg = "No GPS on these pictures to copy."
        elif what == "paste_gps":
            _n, msg = clip.paste_gps_into(self._collection, keys,
                                          refresh_key=self._refresh_strip_items)
            if self._item is not None:
                self._show_location(self._item.key)
        if msg:
            logger.info(msg)

    def _batch_species_culling(self, keys):
        """fpr-free species assignment for the culling viewer: the smart culling
        dialog writes the chosen name to each picture's XMP:Subject."""
        keys = {k for k in keys if k}
        if not keys:
            return
        item0 = self._collection.item_for_key(next(iter(keys)))
        if not getattr(item0, "can_edit_species", False):
            return
        from gui.culling_species import CullingSpeciesDialog, species_prefill_for_keys
        host = self.parent()
        sc = host._culling_sc() if hasattr(host, "_culling_sc") else None

        def _species_of(k):
            try:
                return self._collection.item_for_key(k).species or ""
            except (ValueError, KeyError):
                return ""

        def _is_landscape(k):
            try:
                return bool(self._collection.item_for_key(k).is_blog_extra)
            except (ValueError, KeyError):
                return False

        # Same prefill the grid uses: one distinct name among the selection is
        # offered even when only one picture carries it (a guide frame naming a
        # burst), flagged as "set on N of M"; several distinct names keep the
        # field blank.
        current, preset_summary, n_landscape = species_prefill_for_keys(
            keys, _species_of, _is_landscape)
        dlg = CullingSpeciesDialog(sc, current, len(keys), self,
                                   n_landscape=n_landscape,
                                   preset_summary=preset_summary)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = dlg.chosen_name()
            for k in keys:
                try:
                    self._collection.item_for_key(k).set_species(name)
                except Exception as e:
                    logger.error(f"set_species failed for {k}: {e}")
            self._on_species_committed(set(keys))

    def _batch_rating(self, keys=None):
        # Default target (the 'c' key) is the selection; a caller with its own target
        # (the filmstrip right-click menu) passes keys explicitly.
        if keys is None:
            keys = set(self._selected_keys)
        if not keys:
            return
        # The 'c' key reaches this whatever the viewer, but a read-only
        # viewer with no edit carve-out must not batch-write to the master. Edit
        # flags are per-collection, so one representative item answers for all.
        rep = self._collection.item_for_key(next(iter(keys)))
        can_rate = getattr(rep, "can_edit_rating", False)
        can_color = getattr(rep, "can_edit_color", False)
        if not (can_rate or can_color):
            return
        from PyQt6.QtWidgets import QDialogButtonBox
        n = len(keys)
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
        key_list = list(keys)
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
        if cur_key and cur_key in keys:
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
        # parent= → offer the landscape marker when a headline lands on a picture
        # with no subject (see headline_desc_dialog.offer_landscape_marker).
        if not apply_headline_description(item, values, parent=self):
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

    def _species_for_raw(self, raw) -> str:
        """The species shown for a RAW in this viewer's collection, for the
        PhotoLab handover summary; "" when the picture is not in it."""
        from engine.handling import deriveUniquePictureName
        stem = Path(str(raw)).stem
        for key in (deriveUniquePictureName(Path(str(raw)).name), stem, stem.lower()):
            try:
                idx = self._collection.index_of(key)
                if idx is None or idx < 0:
                    continue
                item = self._collection.item(idx)
                if getattr(item, "key", key) == key:
                    return item.display_name or ""
            except Exception:
                continue
        return ""

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
        # A crop still being drawn is meant for THIS handover — PhotoLab reads the
        # frame from the .dop, which only exists once the crop is applied. Take the
        # key FIRST: applying the crop refreshes the view, which rebinds self._item.
        key = self._item.key
        self._apply_pending_crop()
        raw = self._collection.raw_path(key)
        if not raw:
            return
        # Tell the host we're about to render here — its focus-in scan will check
        # this folder for the JPG that comes back and offer to add it to the master.
        self.dxo_launched.emit(str(raw))
        # Hand over the crop/exposure edits held back while PhotoLab ran.
        handover_around_launch(raw, name_for=self._species_for_raw)
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
        video = getattr(self._item, "is_video", False)
        self._image_label.setCursor(Qt.CursorShape.PointingHandCursor if video
                                    else Qt.CursorShape.ArrowCursor)
        if video:
            # Stamped on the FITTED pixmap, not on self._pixmap: the badge is sized
            # to the picture it is drawn on, and a frame is ~3200 px wide while the
            # stage shows maybe 900 — stamping before the scale would shrink it to
            # a speck. Re-stamped on every fit (resize, exposure re-render), which
            # costs one small paint on a pixmap we were about to hand over anyway.
            scaled = stamp_play_marker(scaled)
        self._image_label.setPixmap(scaled)
        self._image_label.setMaximumSize(scaled.size())
        if self._crop_mode:
            self._sync_crop_overlay_geom()

    def showEvent(self, event):
        super().showEvent(event)
        # Independent taskbar entry: Windows hides any window with a native
        # "owner" HWND from the taskbar regardless of Qt's WindowType, and
        # this dialog keeps its Qt parent intentionally — see
        # gui.window_state.give_independent_taskbar_entry for why that's
        # patched after the fact instead of restructuring the parent/child
        # relationship.
        if not getattr(self, "_taskbar_fix_scheduled", False):
            self._taskbar_fix_scheduled = True
            from gui.window_state import give_independent_taskbar_entry
            QTimer.singleShot(0, lambda: give_independent_taskbar_entry(self))
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
        self._close_quick_view()
        self._save_description()
        self._save_headline()
        # Species named through the inline species panel were buffered, not
        # written per species; this is the end of that session.
        try:
            from gui.culling_species import flush_learned_species_from
            flush_learned_species_from(
                (getattr(self, "_inline_sc_cache", None),
                 getattr(self.fpr, "sc", None) if getattr(self, "fpr", None) else None),
                parent=self)
        except Exception as e:
            logger.warning(f"Saving learned species failed: {e}")
        # Save the ACTIVE splitter's live sizes; use _last_* for the inactive one
        # (querying a splitter with a hidden child returns misleading 0 values).
        save_window(self, self._geo_key,
                    vsplit=self._v_splitter.sizes() if not self._strip_left else self._last_vsplit,
                    hsplit=self._splitter.sizes(),
                    fsplit=self._outer_splitter.sizes() if self._strip_left else self._last_fsplit)
        super().closeEvent(event)
