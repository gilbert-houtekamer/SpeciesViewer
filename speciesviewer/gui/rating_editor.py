"""Rating editor dialog — view and edit XMP:Rating / XMP:Label for pictures.

Pictures are shown as cards sorted by Rate (descending). The user can change
the star rating (0-5) and color label for any picture; changes are written
immediately to the XMP/JPG sidecar files via exiftool and to the CSV files.
"""

import os
import re
import logging
import subprocess
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent.parent / "assets"

from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QWidget, QScrollArea, QGridLayout,
    QLabel, QFrame, QPushButton, QSpinBox, QComboBox, QSizePolicy, QCheckBox,
    QProgressDialog, QRadioButton, QButtonGroup, QMessageBox, QStyle, QCompleter,
)
from PyQt6.QtGui import QPixmap, QImage, QGuiApplication, QIcon, QPainter, QColor
from PyQt6.QtCore import (Qt, QTimer, QSize, QRect, QEvent, pyqtSignal, QObject,
                          QRunnable, QThreadPool)

from engine.filepicture import FilePicture
from engine.speciesclassification import SpeciesClassification
from engine.filecsv import Files
from engine.picturecsv import Pictures
from engine.exif_writer import exiftool_write
from gui.window_state import restore_window, save_window
from gui import theme
from gui.image_loader import AsyncImageLoader
from gui.master_ops import MasterCardMenuMixin
from gui.picture_provider import PictureItem
from utils.config import _config, set_setting

logger = logging.getLogger("speciesviewer")

# The five label colours + the active scheme's hexes live in gui/label_colors so
# a "Look & feel" setting can re-tint them (read at import → restart to apply).
# Re-exported here because many modules import COLORS/COLOR_HEX from rating_editor.
from gui.label_colors import COLORS, COLOR_HEX, COLOR_SCHEMES  # noqa: F401
from utils.raw_extensions import RAW

# Framed-card palette (the Biota-browser look) — sourced from the shared theme
# so light/dark and the app-wide olive stay in one place.
_FRAME = theme.ACCENT           # olive picture frame (viewers set to "always olive")
_UNRATED_FRAME = theme.FRAME_UNRATED  # grey frame for an unlabelled card — olive read too close to the green label
_MAT = theme.SURFACE            # white mat inside the frame
_INK = theme.INK
_INK_SOFT = theme.INK_SOFT
_LINE = theme.LINE
_SURFACE = theme.SURFACE


# ── Standalone rate recomputation ─────────────────────────────────────────────

def _compute_rate(upn_row: dict, files_dict: dict) -> int:
    """Recompute a picture's Rate score from its current Rating/Label/files.

    Mirrors the logic of projectlib.computeRating without requiring a full
    ProjectLib instance. Called after the user edits a card so the sort order
    can be refreshed.
    """
    files = upn_row.get("Files", [])
    if not isinstance(files, list):
        files = []

    computation = 0
    dxo_used = False

    # HEIC-only pictures carry their rating in the HEIC itself; score them like
    # an XMP, but only when no XMP/JPG sidecar already drives the score.
    has_sidecar = any(
        files_dict.get(f, {}).get("WindowsExtension", "").lower() in ("xmp", "jpg", "jpeg")
        for f in files)

    for fname in files:
        if fname not in files_dict:
            continue
        frow = files_dict[fname]
        ext = frow.get("WindowsExtension", "").lower()
        rating_str = str(frow.get("XMP:Rating", ""))
        label = frow.get("XMP:Label", "")

        # mirror label→rating copy from computeRating
        if rating_str in ("0", "") and label:
            if label == "Red":
                rating_str = "4"
            elif label == "Yellow":
                rating_str = "2"
            elif label == "Green":
                rating_str = "1"

        if ext == "xmp":
            computation = 5 if not rating_str else 15 * (1 + int(rating_str))
        elif ext in ("dop", "dng"):
            dxo_used = True
        elif ext in ("jpg", "jpeg"):
            if len(files) != 1:
                computation = 5 if not rating_str else 15 * (1 + int(rating_str))
            elif rating_str in ("0", "") and not label:
                computation = 3
        elif ext == "heic" and not has_sidecar:
            computation = 5 if not rating_str else 15 * (1 + int(rating_str))

    # Small-picture penalty — mirrors computeRating exactly: multiplier starts
    # at 1 and is only recomputed from a JPG's pixel size, so a picture with no
    # JPG (ORF/XMP, HEIC) keeps multiplier 1. saved_multiplier is the running
    # max, floored at 0.1. (The earlier reimplementation left the floor at 0.1
    # for JPG-less pictures, which scaled every ORF/XMP/HEIC Rate down ~10×.)
    saved_multiplier = 0.1
    multiplier = 1.0
    for fname in files:
        frow = files_dict.get(fname)
        if frow is None:
            continue
        if frow.get("WindowsExtension", "").lower() in ("jpg", "jpeg") and frow.get("ImageSize", ""):
            try:
                parts = re.split(r"[x ]", frow["ImageSize"])
                w, h = int(parts[0]), int(parts[1])
                multiplier = float(min(1500, max(w, h))) / 1500.0
                upn_size = upn_row.get("ImageSize", "")
                if upn_size:
                    up = re.split(r"[x ]", upn_size)
                    uw, uh = int(up[0]), int(up[1])
                    if uw >= 1.5 * w or uh >= 1.5 * h:
                        multiplier *= 1.5
            except Exception:
                pass
        saved_multiplier = max(saved_multiplier, multiplier)

    computation = int(float(computation) * saved_multiplier)
    if dxo_used:
        computation += 8
    if upn_row.get("Description", ""):
        computation += 15
    return computation


# ── Sub-widgets ────────────────────────────────────────────────────────────────

class StarWidget(QWidget):
    rating_changed = pyqtSignal(int)

    def __init__(self, initial_rating: int = 0, parent=None):
        super().__init__(parent)
        self._rating = max(0, min(5, initial_rating))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        self._buttons: list[QPushButton] = []
        for i in range(1, 6):
            btn = QPushButton()
            btn.setFlat(True)
            btn.setFixedSize(22, 22)
            btn.setStyleSheet("font-size: 15px; border: none; padding: 0;")
            btn.clicked.connect(lambda _, n=i: self._on_click(n))
            btn.installEventFilter(self)   # hover-preview: fill as the pointer moves
            layout.addWidget(btn)
            self._buttons.append(btn)
        self._refresh()

    def _on_click(self, n: int):
        # Standard photo-app behaviour:
        #  - clicking an "on" star (rating >= n) turns it AND every star to its
        #    right off  → rating = n - 1  (so clicking the lowest lit star clears)
        #  - clicking an "off" star (rating < n) fills up to it, lighting every
        #    star to its left → rating = n
        self._rating = n - 1 if self._rating >= n else n
        self._refresh()
        self.rating_changed.emit(self._rating)

    # Hover-preview: pointing at a star fills up to it (in a lighter gold) without
    # committing; the fill follows the pointer and reverts on exit. Only a click
    # actually changes the rating. Disabled widgets get no preview.
    def eventFilter(self, obj, event):
        if (self.isEnabled() and obj in self._buttons
                and event.type() == QEvent.Type.Enter):
            self._paint(self._buttons.index(obj) + 1, preview=True)
        return super().eventFilter(obj, event)

    def leaveEvent(self, e):
        # Fires as the pointer moves between stars too; a 0-ms hop lets the next
        # star's Enter win, so we only revert when the pointer truly left the row.
        QTimer.singleShot(0, self._maybe_revert)
        super().leaveEvent(e)

    def _maybe_revert(self):
        if not any(b.underMouse() for b in self._buttons):
            self._refresh()

    def _refresh(self):
        self._paint(self._rating, preview=False)

    def _paint(self, upto: int, *, preview: bool):
        colour = "color:#e6a92e;" if preview else ""
        for i, btn in enumerate(self._buttons):
            btn.setText("★" if i < upto else "☆")
            btn.setStyleSheet(f"font-size: 15px; border: none; padding: 0; {colour}")

    @property
    def rating(self) -> int:
        return self._rating

    def set_rating(self, n: int):
        n = max(0, min(5, n))
        if self._rating == n:
            return
        self._rating = n
        self._refresh()


class _SwatchButton(QPushButton):
    """A colour swatch that also reports a genuine double-click.

    Qt delivers press→click→dblClick→click for a double-click on a
    QAbstractButton, so the trailing click is swallowed here — a double-click
    ends up as exactly one toggle (from the first click) plus one
    `doubleClicked`, never an accidental un-toggle."""

    doubleClicked = pyqtSignal()

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._swallow_release = False

    def mouseDoubleClickEvent(self, e):
        self._swallow_release = True
        self.doubleClicked.emit()
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._swallow_release:
            self._swallow_release = False
            self.setDown(False)
            e.accept()
            return
        super().mouseReleaseEvent(e)


class ColorSelector(QWidget):
    color_changed = pyqtSignal(str)         # emits color name, or "" when cleared
    color_double_clicked = pyqtSignal(str)  # a swatch was double-clicked ("set + move on")

    def __init__(self, initial_color: str = "", parent=None):
        super().__init__(parent)
        self._color = initial_color
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self._buttons: dict[str, QPushButton] = {}
        for name in COLORS:
            hc = COLOR_HEX[name]
            btn = _SwatchButton()
            btn.setFixedSize(16, 16)
            btn.setCheckable(True)
            btn.setToolTip(f"{name}  ·  double-click: set and drop from the current filter")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    border: 2px solid {hc};
                    border-radius: 8px;
                }}
                QPushButton:checked {{
                    background-color: {hc};
                    border: 2px solid white;
                }}
                QPushButton:disabled {{
                    background-color: transparent;
                    border: 2px dashed #cccccc;
                }}
                QPushButton:checked:disabled {{
                    background-color: #cccccc;
                    border: 2px solid #eeeeee;
                }}
            """)
            btn.clicked.connect(lambda _, n=name: self._on_click(n))
            btn.doubleClicked.connect(lambda n=name: self._on_double_click(n))
            layout.addWidget(btn)
            self._buttons[name] = btn
        self._refresh()

    def _on_click(self, name: str):
        self._color = "" if self._color == name else name
        self._refresh()
        self.color_changed.emit(self._color)

    def _on_double_click(self, name: str):
        """Double-click always SETS `name` (the first click of the pair may have
        toggled it off), then tells the host this was a deliberate commit."""
        if self._color != name:
            self._color = name
            self._refresh()
            self.color_changed.emit(self._color)
        self.color_double_clicked.emit(name)

    def _refresh(self):
        for name, btn in self._buttons.items():
            btn.setChecked(name == self._color)

    @property
    def color(self) -> str:
        return self._color

    def set_color(self, color: str):
        if self._color == color:
            return
        self._color = color
        self._refresh()

    def set_disabled_colors(self, names, tooltip: str = ""):
        """Grey out (and block clicks on) the named colours; re-enable the rest.

        Used for Option C: on a JPG-bearing picture, Blue/Purple can't be a
        deletion candidate, so those two circles are greyed with an explaining
        tooltip while Red/Yellow/Green stay live.
        """
        disabled = set(names)
        for name, btn in self._buttons.items():
            if name in disabled:
                btn.setEnabled(False)
                btn.setToolTip(tooltip or name)
            else:
                btn.setEnabled(True)
                btn.setToolTip(name)


_DISCARD_GREYED_TIP = ("Has a JPG — kept for the website and a future DxO "
                       "re-export, so it can't be a deletion candidate.")


def _snapshot_suffix() -> str:
    """"  ·  snapshot 20 Aug 2026 (9 days old)" for the read-only banner, or "".

    A read-only master on this machine is a SmallMaster copy, and this screen is
    where its age actually costs something: it is the history a lifer verdict is
    judged against and the pictures a cull is compared with. The status bar
    carries the same date, but the eyeful that matters is the one you are
    reading. Empty when there is no snapshot to date — never a guessed date.
    """
    from engine import smallmaster_stamp as _st
    root = _config.get("master", "masterbiotadatabase", fallback="")
    info = _st.read_stamp(root)
    if info is None and _config.get("master", "readonly_locked", fallback="") == "yes":
        info = _st.estimate_from_files(root)
    if info is None:
        return ""
    return "  ·  snapshot " + _st.describe(info, short=True)


def stamp_crop_marker(px: QPixmap) -> QPixmap:
    """Return a copy of the thumbnail with a small '#' chip in the top-left corner
    — the visual cue that this picture carries a crop (ours / PhotoLab / Lightroom).
    Shown over both the RAW's preview and a baked JPG render: even though a JPG's
    pixels don't reveal the crop, the crop is recorded in the RAW's sidecar and
    this is the picture that goes to the website. Subtle: a faint dark chip so the
    white glyph stays legible over any image."""
    if px.isNull():
        return px
    out = QPixmap(px)   # detach (COW) so the loader's cached pixmap isn't scribbled on
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = 12
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 110))
    p.drawRoundedRect(3, 3, s, s, 2, 2)
    f = p.font()
    f.setPixelSize(10)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255))
    p.drawText(QRect(3, 3, s, s), Qt.AlignmentFlag.AlignCenter, "#")
    p.end()
    return out


class _FramedPhoto(QLabel):
    """Photo label that pins small mono corner badges over itself (Biota look).
    Badges are child widgets repositioned to the four corners on every resize."""

    def __init__(self):
        super().__init__()
        self._badges = []   # (widget, corner) where corner ∈ {tl,tr,bl,br}

    def add_badge(self, w, corner):
        w.setParent(self)
        self._badges.append((w, corner))
        w.show()

    def reposition(self):
        m = 7
        for w, corner in self._badges:
            w.adjustSize()
            x = m if "l" in corner else self.width() - w.width() - m
            y = m if "t" in corner else self.height() - w.height() - m
            w.move(x, y)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.reposition()


def _corner_badge(text: str, *, bold: bool = False, rate: bool = False,
                  attn: bool = False) -> QLabel:
    """A mono over-image corner pill: dark for scan-data, olive for Rate, red for
    an attention flag (missing GPS / unset or uncertain species)."""
    lbl = QLabel(text)
    bg = "#b4530b" if attn else ("rgba(60,74,52,.88)" if rate else "rgba(0,0,0,.5)")
    lbl.setStyleSheet(
        f"color:#fff; background:{bg}; font-family:Consolas,monospace;"
        f" font-size:9px; font-weight:{'700' if (bold or rate or attn) else '400'};"
        f" padding:1px 6px; border-radius:9px;")
    return lbl


class PictureCard(QFrame):
    """Thumbnail + star rating + color selector + rate label for one picture.

    Renders entirely from a PictureItem (the provider seam) and writes edits
    back through it — no direct engine access. Capability flags decide whether
    the stars / colour controls are live."""

    card_changed = pyqtSignal(str)        # key — emitted after a write so the host re-sorts
    card_clicked = pyqtSignal(str)        # key — single click → cursor tracking only
    card_double_clicked = pyqtSignal(str) # key — double click → open large view
    card_right_clicked = pyqtSignal(str)  # key — right click → context menu
    status_clicked = pyqtSignal(str)      # key — status icon clicked → open SpeciesInfoDialog
    notes_clicked = pyqtSignal(str)       # key — 📝 clicked → open Headline/Description editor
    render_clicked = pyqtSignal(str)      # key — amber ➕ clicked → register new render in master
    color_double_committed = pyqtSignal(str)  # key — colour swatch double-clicked ("tagged, move on")

    def __init__(self, item: PictureItem, card_width: int, parent=None,
                 *, show_status: bool = False, show_kind_tag: bool = False,
                 framed: bool = False, viewer_key: str = "species",
                 thumb_aspect: float = 1.5):
        super().__init__(parent)
        # Width/height the framed thumbnail area reserves. Default 3:2; the
        # culling grid passes the day's own sampled aspect (usually 4:3) so a
        # single-camera trip fits with no letterbox bands.
        self._thumb_aspect = thumb_aspect if thumb_aspect and thumb_aspect > 0 else 1.5
        self.setObjectName("PictureCard")
        self._item = item
        self.upn_key = item.key
        self._selected = False
        self._cursor = False
        # framed = the Biota-browser card look (colour picture frame + corner
        # info badges + name/sci meta lines). Opt-in; the legacy stacked layout
        # is the default so untouched viewers are unaffected.
        self._framed = framed
        self._viewer_key = viewer_key   # which per-viewer corner set to apply
        self._from_master = getattr(item, "from_master", False)
        # In Trip+Master scope the grid mixes the two; tag each card with its
        # origin + the picture's web/JPG size so they're directly comparable.
        # Hosts that don't mix kinds (Day, Trip alone) leave this off.
        self._show_kind_tag = show_kind_tag
        # Preview hosts set show_status to add a status/desc/GPS badge row and a
        # clickable warning icon (→ SpeciesInfoDialog); the Rating Editor leaves
        # it off (its data is already validated).
        self._show_status = show_status
        self._status_icon = None
        self._crop_badge = None
        self._desc_badge = None
        self._gps_badge = None

        if framed:
            self._frame_color = item.color
            self._build_framed_ui(item, card_width)
            return

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(5, 5, 5, 5)
        vbox.setSpacing(3)

        # Thumbnail — clicking opens the large view. The coloured border traces
        # the colour label (less in-your-face than bullets); kept in sync by
        # _update_thumb_style. Loaded asynchronously via the provider.
        self._frame_color = item.color
        self._thumb = QLabel()
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        self._thumb.mousePressEvent = lambda e: (
            self.card_right_clicked.emit(self.upn_key)
            if e.button() == Qt.MouseButton.RightButton
            else self.card_clicked.emit(self.upn_key)
        )
        self._thumb.mouseDoubleClickEvent = lambda e: self.card_double_clicked.emit(self.upn_key)
        self._thumb_target_w = max(80, card_width - 18)
        self._thumb.setFixedHeight(max(60, self._thumb_target_w * 2 // 3))
        self._update_thumb_style()
        vbox.addWidget(self._thumb)
        item.request_thumbnail(self._thumb_target_w, self.set_thumbnail)

        # Species name (+ compact sex/age/plumage suffix, e.g. "fuut ♀ br.")
        self._lbl_name = QLabel((item.display_name or item.key)
                                + getattr(item, "qualifier_suffix", ""))
        self._lbl_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_name.setStyleSheet("font-weight: bold; font-size: 10px;")
        self._lbl_name.setWordWrap(True)
        vbox.addWidget(self._lbl_name)

        # Location line (place name / coordinates / "No GPS data available") —
        # only for providers that carry the data for free (the culling grid).
        self._lbl_location = None
        if getattr(item, "show_location_line", False):
            self._lbl_location = QLabel()
            self._lbl_location.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._lbl_location.setWordWrap(True)
            self._lbl_location.setStyleSheet("font-size: 9px; color: #2e7d32;")
            vbox.addWidget(self._lbl_location)
            self._refresh_location_line()

        # Origin tag. Master cards always carry one ("— from master (800x599) —")
        # since they're read-only references; trip cards get a matching
        # "— from trip (2000x1500) —" only in Trip+Master scope where the two
        # kinds share the grid and the user is comparing quality side-by-side.
        from_album = getattr(item, "from_album", False)
        if self._from_master or self._show_kind_tag or from_album:
            res = item.exif.jpg_dimensions or item.exif.dimensions
            # In full-master scope, the master pictures that belong to this
            # album are called out ("— from Album —", green/bold) so they stand
            # out among the plain grey "— from master —" references.
            origin = "Album" if from_album else ("master" if self._from_master else "trip")
            tag = QLabel(f"— from {origin} ({res}) —" if res
                         else f"— from {origin} —")
            tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # Green/bold marks the pictures that belong to what you opened —
            # this album, or this trip — so they read at a glance among the
            # plain grey "— from master —" references they sit beside. Same cue
            # in both viewers: green means "one of mine".
            if from_album or not self._from_master:
                tag.setStyleSheet("color: #2e7d32; font-size: 8px; "
                                  "font-style: italic; font-weight: bold;")
            else:
                tag.setStyleSheet("color: #888; font-size: 8px; font-style: italic;")
            vbox.addWidget(tag)

        # Committed-day lock: this trip picture is on a frozen day → read-only.
        if getattr(item, "frozen", False):
            lock = QLabel("🔒 committed")
            lock.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lock.setStyleSheet("color:#8a6d00; font-size:8px; font-weight:bold;")
            lock.setToolTip("This day is committed — Reset the trip to unlock it.")
            vbox.addWidget(lock)

        # Stars row + date + rate
        row_stars = QHBoxLayout()
        row_stars.setContentsMargins(0, 0, 0, 0)
        self._stars = StarWidget(item.rating)
        if item.can_edit_rating:
            self._stars.rating_changed.connect(self._on_rating_changed)
        else:
            self._stars.setEnabled(False)
        row_stars.addWidget(self._stars)
        if item.date:
            lbl_date = QLabel(item.date[:10].replace(":", "-"))
            lbl_date.setStyleSheet("font-size: 9px; color: #777;")
            row_stars.addWidget(lbl_date)
        row_stars.addStretch()
        self._rate_lbl = QLabel()
        self._rate_lbl.setStyleSheet("font-size: 10px; color: #555;")
        self.update_rate_display(item.rate)
        row_stars.addWidget(self._rate_lbl)
        vbox.addLayout(row_stars)

        # Color selector — greys Blue/Purple on a JPG-bearing picture (Option C)
        self._color_sel = ColorSelector(item.color)
        if item.can_edit_color:
            self._color_sel.color_changed.connect(self._on_color_changed)
            self._color_sel.color_double_clicked.connect(self._on_color_double_clicked)
        else:
            self._color_sel.setEnabled(False)
        if item.has_jpg and not getattr(item, "discard_allowed", False):
            self._color_sel.set_disabled_colors(("Blue", "Purple"), _DISCARD_GREYED_TIP)
        vbox.addWidget(self._color_sel)

        # key + file types (small) — refreshed when a render (JPG) appears later.
        # Word-wrap so a long key (a full file path, in the metadata-less viewer)
        # wraps instead of stretching the whole column and shrinking every image.
        self._lbl_key = QLabel(self._key_line_text())
        self._lbl_key.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_key.setStyleSheet("font-size: 9px; color: #aaa;")
        self._lbl_key.setWordWrap(True)
        self._lbl_key.setToolTip(item.key)
        vbox.addWidget(self._lbl_key)

        # Preview-only badge row: clickable status icon (species warning / OK /
        # none → SpeciesInfoDialog), a 📝 description badge, and a 📍/❓ GPS badge.
        if self._show_status:
            badge_row = QHBoxLayout()
            badge_row.setContentsMargins(0, 0, 0, 0)
            badge_row.setSpacing(4)

            self._status_icon = QPushButton()
            self._status_icon.setIconSize(QSize(16, 16))
            self._status_icon.setFixedSize(20, 20)
            self._status_icon.setFlat(True)
            self._status_icon.setStyleSheet(
                "background-color: white; color: #333; border: 1px solid #ccc; padding: 2px;")
            self._status_icon.clicked.connect(lambda: self.status_clicked.emit(self.upn_key))
            badge_row.addWidget(self._status_icon)
            badge_row.addStretch()

            # ⌗ = this picture carries a crop (ours or PhotoLab/LR). Sits left of
            # the 📝/📍 badges. "Potentially" cropped — the recipe exists even if a
            # cropped JPG render hasn't been generated yet.
            self._crop_badge = QLabel()
            self._crop_badge.setStyleSheet("font-size: 14px; color: #555;")
            self._crop_badge.setFixedSize(20, 20)
            self._crop_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge_row.addWidget(self._crop_badge)

            self._desc_badge = QLabel()
            self._desc_badge.setStyleSheet("font-size: 14px;")
            self._desc_badge.setFixedSize(20, 20)
            self._desc_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # The 📝 is a link: clicking it opens the Headline/Description editor
            # (the same thing the right-click menu does).
            self._desc_badge.setTextInteractionFlags(
                Qt.TextInteractionFlag.LinksAccessibleByMouse)
            self._desc_badge.setCursor(Qt.CursorShape.PointingHandCursor)
            self._desc_badge.linkActivated.connect(
                lambda _h: self.notes_clicked.emit(self.upn_key))
            badge_row.addWidget(self._desc_badge)

            self._gps_badge = QLabel()
            self._gps_badge.setStyleSheet("font-size: 14px;")
            self._gps_badge.setFixedSize(20, 20)
            self._gps_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge_row.addWidget(self._gps_badge)

            vbox.addLayout(badge_row)
            self._refresh_status_badges()

        if self._from_master:
            self._update_frame_style()   # paint the grey reference background now

    # ── framed (Biota-look) build ────────────────────────────────────────────
    def _build_framed_ui(self, item, card_width):
        """Colour picture frame + corner info badges + name/scientific-name meta
        + clickable stars/colours. Reuses StarWidget, ColorSelector, the provider
        write-path, and set_thumbnail — only the layout differs from legacy."""
        self.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # the thick colour frame — label colour, olive when unlabelled
        self._pframe = QFrame()
        self._pframe.setObjectName("pframe")
        fl = QVBoxLayout(self._pframe)
        fl.setContentsMargins(8, 8, 8, 8)
        fl.setSpacing(0)

        target_w = max(80, card_width - 20)
        self._thumb_target_w = target_w
        # Fixed thumbnail height, reserved up front — the row must NOT grow or
        # shrink as images stream in (that jitter made a big-day load look
        # slower than it is). Aspect is the day's sampled one (4:3 etc.); an
        # image of a different aspect is scaled to fit and letterboxed, not
        # allowed to push the row taller.
        self._thumb_fixed_h = max(70, round(target_w / self._thumb_aspect))
        self._thumb = _FramedPhoto()
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setFixedHeight(self._thumb_fixed_h)
        self._thumb.setText("…")
        self._thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        self._thumb.mousePressEvent = lambda e: (
            self.card_right_clicked.emit(self.upn_key)
            if e.button() == Qt.MouseButton.RightButton
            else self.card_clicked.emit(self.upn_key))
        self._thumb.mouseDoubleClickEvent = lambda e: self.card_double_clicked.emit(self.upn_key)
        fl.addWidget(self._thumb)
        outer.addWidget(self._pframe)

        # corners — datum per position from this viewer's configurable set. The
        # committed lock (species) and the Attention flag (preview) auto-override
        # the top-right corner, whatever datum is configured there.
        from gui.card_layout import corner_config, render_datum
        self._corner_cfg = corner_config(self._viewer_key)
        self._rate_lbl = _corner_badge("", rate=True)   # updated by update_rate_display
        self._colour_dot = None
        self._attn = self._compute_attention() if self._show_status else False
        self._attn_badge = None

        for pos in ("tl", "bl", "br", "tr"):
            datum = self._corner_cfg.get(pos, "none")
            if pos == "tr" and getattr(item, "frozen", False):
                lock = _corner_badge("🔒", bold=True)
                lock.setToolTip("This day is committed — Reset the trip to unlock it.")
                self._thumb.add_badge(lock, "tr")
                continue
            if pos == "tr" and self._show_status:
                text, _clickable = self._attn_badge_spec()
                self._attn_badge = _corner_badge(text or "", attn=True)
                self._attn_badge.setCursor(Qt.CursorShape.PointingHandCursor)
                self._attn_badge.mousePressEvent = \
                    lambda e: self.status_clicked.emit(self.upn_key)
                self._thumb.add_badge(self._attn_badge, "tr")
                self._attn_badge.setVisible(bool(text))   # hide the empty pill
                continue
            if datum == "none":
                continue
            if datum == "rate":
                self._thumb.add_badge(self._rate_lbl, pos)
            elif datum == "lock":
                if getattr(item, "frozen", False):
                    lk = _corner_badge("🔒", bold=True)
                    lk.setToolTip("This day is committed — Reset the trip to unlock it.")
                    self._thumb.add_badge(lk, pos)
            elif datum == "colour":
                self._colour_dot = QLabel()
                self._colour_dot.setFixedSize(15, 15)
                self._thumb.add_badge(self._colour_dot, pos)
                self._paint_colour_dot()
            else:
                txt = render_datum(datum, item)
                if txt is not None:
                    self._thumb.add_badge(_corner_badge(txt, bold=(datum == "format")), pos)
        self.update_rate_display(item.rate)

        # meta lines: up to three species-name lines per the display profile —
        # deduped, the scientific one italic, the sex/age/plumage suffix on the
        # primary (first) line. Held in a sub-layout so a refresh can rebuild them.
        meta = QWidget()
        ml = QVBoxLayout(meta)
        ml.setContentsMargins(10, 8, 10, 2)
        ml.setSpacing(1)
        self._name_holder = QVBoxLayout()
        self._name_holder.setContentsMargins(0, 0, 0, 0)
        self._name_holder.setSpacing(1)
        self._name_labels = []
        ml.addLayout(self._name_holder)
        self._populate_name_lines()
        # location line (preview/culling only): place name or "No GPS data available"
        self._lbl_location = None
        if getattr(item, "show_location_line", False):
            self._lbl_location = QLabel()
            self._lbl_location.setWordWrap(True)
            ml.addWidget(self._lbl_location)
            self._refresh_location_line()
        # filename + file-types (left, mono, JPG token greened) with a right-aligned
        # "⌗ Crop" mark. Crop is its own concept (independent of JPG), so the format
        # reads off the corner and no '#' is stamped on framed thumbnails.
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(8)
        self._lbl_key = QLabel(self._key_line_text())
        self._lbl_key.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace; font-size:9.5px;")
        self._lbl_key.setWordWrap(True)
        self._lbl_key.setToolTip(self._item.key)
        key_row.addWidget(self._lbl_key, 1)
        self._lbl_crop = QLabel()
        self._lbl_crop.setStyleSheet(f"color:{_FRAME}; font-family:Consolas,monospace;"
                                     f" font-size:9.5px; font-weight:700;")
        # 📝 on this line is a link to the Headline/Description editor (⌗ Crop is
        # not — it's just a marker).
        self._lbl_crop.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._lbl_crop.linkActivated.connect(self._on_crop_link)
        key_row.addWidget(self._lbl_crop, 0,
                          Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        ml.addLayout(key_row)
        self._refresh_crop_mark()
        # origin tag only when kinds are mixed (Trip+Master) or a master/album ref
        from_album = getattr(item, "from_album", False)
        if self._from_master or self._show_kind_tag or from_album:
            res = item.exif.jpg_dimensions or item.exif.dimensions
            origin = "Album" if from_album else ("master" if self._from_master else "trip")
            tag = QLabel(f"— from {origin} ({res}) —" if res else f"— from {origin} —")
            # Green/bold marks the pictures that belong to what you opened —
            # this album, or this trip — so they read at a glance among the
            # plain grey "— from master —" references beside them. Same cue in
            # both viewers: green means "one of mine".
            if from_album or not self._from_master:
                tag.setStyleSheet("color:#2e7d32; font-size:9px; font-style:italic; font-weight:bold;")
            else:
                tag.setStyleSheet("color:#888; font-size:9px; font-style:italic;")
            ml.addWidget(tag)
        outer.addWidget(meta)

        # controls: clickable stars (left) + colour swatches (right)
        controls = QWidget()
        cl = QHBoxLayout(controls)
        cl.setContentsMargins(10, 2, 10, 10)
        cl.setSpacing(6)
        self._stars = StarWidget(item.rating)
        if item.can_edit_rating:
            self._stars.rating_changed.connect(self._on_rating_changed)
        else:
            self._stars.setEnabled(False)
        cl.addWidget(self._stars)
        cl.addStretch()
        self._color_sel = ColorSelector(item.color)
        if item.can_edit_color:
            self._color_sel.color_changed.connect(self._on_color_changed)
            self._color_sel.color_double_clicked.connect(self._on_color_double_clicked)
        else:
            self._color_sel.setEnabled(False)
        if item.has_jpg and not getattr(item, "discard_allowed", False):
            self._color_sel.set_disabled_colors(("Blue", "Purple"), _DISCARD_GREYED_TIP)
        cl.addWidget(self._color_sel)
        outer.addWidget(controls)

        self._update_frame_style()   # base surface + selection border
        self._update_thumb_style()   # paints the frame colour from the label
        item.request_thumbnail(target_w, self.set_thumbnail)

    def _populate_name_lines(self) -> None:
        """(Re)build the framed card's name lines from the item's display_lines:
        the first line is the primary (bold) name, any further lines are softer,
        and whichever line IS the scientific name is italicised."""
        while self._name_holder.count():
            it = self._name_holder.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        self._name_labels = []
        lines = getattr(self._item, "display_lines", None)
        if not lines:
            name = self._item.display_name or self._item.key
            suffix = getattr(self._item, "qualifier_suffix", "") or ""
            lines = [((name + suffix).strip(), False)]
        for i, (text, is_sci) in enumerate(lines):
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            if i == 0:
                css = f"color:{_INK}; font-weight:600; font-size:14px;"
            else:
                css = f"color:{_INK_SOFT}; font-size:11.5px;"
            if is_sci:
                css += " font-style:italic;"
            lbl.setStyleSheet(css)
            self._name_holder.addWidget(lbl)
            self._name_labels.append(lbl)
        # Keep _lbl_name pointing at the primary line for any external reference.
        self._lbl_name = self._name_labels[0] if self._name_labels else QLabel()

    def _update_frame_color(self):
        """Paint the thick frame. Per the viewer's frame setting: the colour label
        floods it (grey when unlabelled), or it's always olive."""
        if getattr(self, "_corner_cfg", {}).get("frame") == "olive":
            bg = _FRAME
        else:
            hexc = COLOR_HEX.get((self._frame_color or "").capitalize())
            # No colour label -> a neutral grey frame, not the olive accent
            # (which sat too close to the green label at a glance).
            bg = hexc or _UNRATED_FRAME
        self._pframe.setStyleSheet(f"QFrame#pframe {{ background:{bg}; border-radius:8px; }}")

    def _paint_colour_dot(self):
        """Repaint the optional colour-dot corner from the current label colour."""
        if getattr(self, "_colour_dot", None) is None:
            return
        hexc = COLOR_HEX.get((self._frame_color or "").capitalize())
        if hexc:
            self._colour_dot.setStyleSheet(
                f"background:{hexc}; border:2px solid rgba(255,255,255,.9); border-radius:8px;")
            self._colour_dot.setVisible(True)
        else:
            self._colour_dot.setVisible(False)

    # ── framed preview (culling) status ──────────────────────────────────────
    def _compute_attention(self) -> bool:
        """True when this culling card needs a decision — unset/uncertain species
        or missing GPS. Frozen (committed) cards are read-only, so never flagged."""
        it = self._item
        if getattr(it, "frozen", False):
            return False
        return (not it.species) or bool(it.error_msg) or (not it.has_gps)

    def _attn_badge_spec(self):
        """(text, clickable) for the top-right Attention badge — species issues
        first (clickable → SpeciesInfoDialog), then missing GPS. ('', False) when
        nothing needs attention."""
        it = self._item
        if getattr(it, "frozen", False):
            return ("", False)
        if not it.species:
            return ("❔ species", True)
        if it.error_msg:
            return ("⚠ ID", True)
        if not it.has_gps:
            return ("❓ GPS", True)
        return ("", False)

    def _refresh_crop_mark(self):
        """The right-aligned '⌗ Crop' mark on the filename line (plus a 📝 when a
        preview card carries a description). Crop is its own signal — no '#' stamp
        on the framed image."""
        marks = []
        if getattr(self._item, "has_crop", False):
            marks.append("⌗ Crop")
        if self._show_status and self._item.description:
            if self._notes_editable():
                # A clickable link → the Headline/Description editor; the label
                # renders as rich text once it contains the anchor.
                marks.append('<a href="notes" style="text-decoration:none;"'
                             ' title="Edit headline / description">📝</a>')
            else:
                marks.append("📝")   # read-only host: an indicator, not a link
        # A fresh DxO/LR render is sitting on disk that the master doesn't track yet
        # (found by the focus-in scan). A "+ JPG" link in the same green as the JPG
        # file token — reads as "add the JPG" — → registers it in the master.
        if getattr(self._item, "has_pending_render", False):
            names = getattr(self._item, "pending_render_names", []) or []
            plural = "s" if len(names) != 1 else ""
            marks.append(
                f'<a href="render" style="color:#2e7d32;text-decoration:none;'
                f'font-weight:700;" title="New render{plural} on disk, not in the '
                f'master yet — click to add">+ JPG</a>')
        self._lbl_crop.setText("&nbsp;&nbsp;".join(marks))
        self._lbl_crop.setToolTip("This picture carries a crop" if getattr(
            self._item, "has_crop", False) else "")

    def _on_crop_link(self, href: str):
        """Route the two links that can appear on the filename line: the 📝 notes
        link and the amber ➕ new-render link (both live on _lbl_crop)."""
        if href == "render":
            self.render_clicked.emit(self.upn_key)
        else:
            self.notes_clicked.emit(self.upn_key)

    def _notes_editable(self) -> bool:
        return (getattr(self._item, "can_edit_headline", False)
                or getattr(self._item, "can_edit_description", False))

    def _refresh_framed_status(self):
        """Repaint the preview-only bits (location line, Attention badge, red
        frame) after an edit such as a species commit or a GPS backfill."""
        if not self._show_status:
            return
        self._refresh_location_line()
        text, _clickable = self._attn_badge_spec()
        if self._attn_badge is not None:
            self._attn_badge.setText(text)
            self._attn_badge.setVisible(bool(text))
            self._thumb.reposition()
        self._attn = self._compute_attention()
        self._update_frame_style()   # red border on/off

    def _refresh_status_badges(self):
        """Set the status icon + description/GPS badges from the live item.
        Re-callable after an edit (species commit, GPS fix)."""
        if self._framed or not self._show_status:
            return   # framed cards use _refresh_framed_status (Attention model)
        item = self._item
        error_msg = item.error_msg
        species = item.species
        if error_msg:
            self._status_icon.setIcon(QIcon(QPixmap(str(_ASSETS / "warning_icon.png")).scaled(16, 16)))
            self._status_icon.setText("")
            self._status_icon.setToolTip(error_msg)
        elif species:
            self._status_icon.setIcon(QIcon(QPixmap(str(_ASSETS / "ok_icon.png")).scaled(16, 16)))
            self._status_icon.setText("")
            self._status_icon.setToolTip("OK")
        else:
            self._status_icon.setIcon(QIcon())
            self._status_icon.setText("❔")
            self._status_icon.setToolTip("No species set — picture will be skipped by the pipeline.")

        has_crop = getattr(item, "has_crop", False)
        self._crop_badge.setText("⌗" if has_crop else "")
        self._crop_badge.setToolTip(
            "This picture has a crop (SpeciesViewer / PhotoLab / Lightroom)"
            if has_crop else "")

        desc = item.description
        # 📝: a link → the Headline/Description editor where editable, else a plain
        # indicator (no dead affordance on a read-only card).
        if desc and self._notes_editable():
            self._desc_badge.setText(
                '<a href="notes" style="text-decoration:none;">📝</a>')
            self._desc_badge.setCursor(Qt.CursorShape.PointingHandCursor)
            self._desc_badge.setToolTip("Edit headline / description")
        elif desc:
            self._desc_badge.setText("📝")
            self._desc_badge.unsetCursor()
            self._desc_badge.setToolTip(desc)
        else:
            self._desc_badge.setText("")
            self._desc_badge.setToolTip("")

        if item.has_gps:
            self._gps_badge.setText("📍")
            self._gps_badge.setToolTip("GPS coordinates present.")
        else:
            self._gps_badge.setText("❓")
            self._gps_badge.setToolTip("Missing GPS coordinates — fix in Lightroom.")

    def refresh(self):
        """Re-read every field from the (live) item and repaint — used after an
        external edit such as a species commit or a GPS backfill."""
        if self._framed:
            self._populate_name_lines()
            self._frame_color = self._item.color
            self._update_thumb_style()          # repaints the frame colour
            self._lbl_key.setText(self._key_line_text())
            self._refresh_crop_mark()
            self._stars.set_rating(self._item.rating)
            self._color_sel.set_color(self._item.color)
            self.update_rate_display(self._item.rate)
            self._refresh_framed_status()
            return
        # Include the sex/age/plumage suffix, exactly as the constructor does —
        # else committing "pair" repaints the card WITHOUT the ♂♀ it just gained.
        self._lbl_name.setText((self._item.display_name or self._item.key)
                               + getattr(self._item, "qualifier_suffix", ""))
        self._frame_color = self._item.color
        self._update_thumb_style()
        self._stars.set_rating(self._item.rating)
        self._color_sel.set_color(self._item.color)
        self.update_rate_display(self._item.rate)
        self._lbl_key.setText(self._key_line_text())
        self._refresh_location_line()
        self._refresh_status_badges()

    def _key_line_text(self) -> str:
        # The key line shows a short identifier: the UPN in the trip/master viewer,
        # or (metadata-less file-list mode) just the filename — never the whole
        # path, whose length would blow out the column. Full path is in the tooltip.
        key = getattr(self._item, "key_label", None) or self._item.key
        exts = self._item.file_exts
        if not exts:
            return key
        # Bold + green the JPG token so "this picture has a JPG render" reads at a
        # glance. Cards without a JPG stay plain text, so use real spaces (not
        # &nbsp;, which shows literally in plain-text mode) — the double space
        # collapses to one only on the rich-text (JPG) cards, which is fine.
        shown = [f"<b style='color:#2e7d32'>{e}</b>" if e.upper() == "JPG" else e
                 for e in exts]
        return f"{key}  {' · '.join(shown)}"

    def reload_thumbnail(self):
        """Re-request the thumbnail — used when the underlying image changed (a new
        render switched the source to a JPG, or a re-crop), unlike refresh() which
        only repaints text/widgets."""
        self._item.request_thumbnail(self._thumb_target_w, self.set_thumbnail)

    def _refresh_location_line(self):
        """Set the location line from the live item; muted grey when there's no
        place/GPS, normal green when there is."""
        if self._lbl_location is None:
            return
        text = self._item.location_line()
        muted = text == "No GPS data available"
        self._lbl_location.setText(text)
        self._lbl_location.setStyleSheet(
            "font-size: 9px; color: #999; font-style: italic;" if muted
            else "font-size: 9px; color: #2e7d32;")

    def _on_rating_changed(self, n: int):
        self._item.set_rating(n)             # writes + recomputes Rate via provider
        self.update_rate_display(self._item.rate)
        self.card_changed.emit(self.upn_key)

    def _on_color_changed(self, color: str):
        self._item.set_color(color)
        self._frame_color = color
        self._update_thumb_style()
        self.update_rate_display(self._item.rate)
        self.card_changed.emit(self.upn_key)

    def _on_color_double_clicked(self, color: str):
        """A swatch was double-clicked. ColorSelector emits color_changed first,
        so the colour is already written by the time we get here; just tell the
        host it was a deliberate "tagged, move on" so it can drop the card from
        the current filtered view."""
        if self._item.color != color:
            self._on_color_changed(color)
        self.color_double_committed.emit(self.upn_key)

    def update_rate_display(self, new_rate):
        if self._framed:
            self._rate_lbl.setText(f"Rate {new_rate}" if new_rate is not None else "Rate ?")
            if isinstance(self._thumb, _FramedPhoto):
                self._thumb.reposition()   # width changed with the new digits
            return
        self._rate_lbl.setText(f"Rate: {new_rate if new_rate is not None else '?'}")

    def update_rating_display(self, stars: int):
        self._stars.set_rating(stars)

    def update_color_display(self, color: str):
        self._color_sel.set_color(color)
        self._frame_color = color
        self._update_thumb_style()

    def _update_thumb_style(self):
        """Apply the dark placeholder bg (when no pixmap) plus the coloured
        frame border (when the picture has a colour label)."""
        has_pix = self._thumb.pixmap() is not None and not self._thumb.pixmap().isNull()
        if self._framed:
            # In framed mode the colour lives on the outer frame, not a thumb
            # border; the mat stays white. Only the placeholder bg differs.
            if has_pix:
                self._thumb.setStyleSheet(f"border:3px solid {_MAT}; border-radius:3px;")
            else:
                self._thumb.setStyleSheet(
                    f"background:{theme.SURFACE_2}; border:3px solid {_MAT}; border-radius:3px;"
                    f" color:{_INK_SOFT}; font-size:10px;")
            self._update_frame_color()
            self._paint_colour_dot()
            return
        parts = []
        if not has_pix:
            parts.append("background-color: #2a2a2a; color: #ccc; font-size: 10px;")
        hexc = COLOR_HEX.get((self._frame_color or "").capitalize())
        if hexc:
            parts.append(f"border: 3px solid {hexc}; border-radius: 4px;")
        self._thumb.setStyleSheet(" ".join(parts))

    def _update_frame_style(self):
        if self._framed:
            # Framed cards carry a surface-coloured base with the app-wide olive
            # selection border; the picture's colour label lives on the inner
            # frame, not here.
            if self._selected:
                self.setStyleSheet(f"QFrame#PictureCard {{ background:{_SURFACE};"
                                   f" border:2px solid {theme.ACCENT}; border-radius:9px; }}")
            elif self._cursor:
                self.setStyleSheet(f"QFrame#PictureCard {{ background:{_SURFACE};"
                                   f" border:1px solid #333; border-radius:9px; }}")
            elif getattr(self, "_attn", False):
                # a culling card that needs a decision — subtle amber-red edge
                self.setStyleSheet(f"QFrame#PictureCard {{ background:{_SURFACE};"
                                   f" border:2px solid {theme.WARN}; border-radius:9px; }}")
            elif self._from_master:
                # a master REFERENCE card — subtly off the normal surface so it
                # reads as "existing" (theme-aware; was a hardcoded light grey)
                self.setStyleSheet(f"QFrame#PictureCard {{ background:{theme.SURFACE_2};"
                                   f" border:1px solid {_LINE}; border-radius:9px; }}")
            else:
                self.setStyleSheet(f"QFrame#PictureCard {{ background:{_SURFACE};"
                                   f" border:1px solid {_LINE}; border-radius:9px; }}")
            return
        if self._selected:
            # Brand olive, not the old off-brand blue — the app-wide selection
            # color everywhere else since ch.15's global QSS (Phase 1) already
            # unified QTreeView/QListView/QTableView; PictureCard is a plain
            # QFrame so it needs its own explicit swap here (ch.15 Phase 2).
            self.setStyleSheet(
                f"QFrame#PictureCard {{ border: 2px solid {theme.ACCENT}; "
                f"background: rgba({theme.ACCENT_RGB}, 0.08); }}")
        elif self._cursor:
            bg = "background: #e2e2e2; " if self._from_master else ""
            self.setStyleSheet(f"QFrame#PictureCard {{ border: 1px solid #333; {bg}}}")
        elif self._from_master:
            # Off-surface card so reference pictures read as "existing master"
            # (theme-aware; was a hardcoded light grey that stayed white on dark).
            self.setStyleSheet(
                f"QFrame#PictureCard {{ background: {theme.SURFACE_2}; border: 1px solid {_LINE}; }}")
        else:
            self.setStyleSheet("")

    def set_selected(self, selected: bool):
        if self._selected == selected:
            return
        self._selected = selected
        self._update_frame_style()

    def set_cursor_focus(self, active: bool):
        if self._cursor == active:
            return
        self._cursor = active
        self._update_frame_style()

    def set_thumbnail(self, px: QPixmap):
        if px.isNull():
            self._thumb.setText("(no image)")
            self._update_thumb_style()
            return
        if self._framed:
            # Keep the reserved height; scale a too-tall (portrait) thumb down to
            # fit so the row height never shifts during load.
            fh = getattr(self, "_thumb_fixed_h", self._thumb.height())
            if fh > 0 and px.height() > fh:
                px = px.scaledToHeight(fh, Qt.TransformationMode.SmoothTransformation)
            self._thumb.setPixmap(px)
            self._update_thumb_style()
            return
        # Legacy card: grow the label to the image, keep the '#' crop stamp.
        self._thumb.setFixedHeight(px.height() + 6)   # +6 for the 3px frame border
        if getattr(self._item, "has_crop", False):
            px = stamp_crop_marker(px)
        self._thumb.setPixmap(px)
        self._update_thumb_style()


def _safe_int(s, default: int = 0) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return default


class DeletePreviewDialog(QDialog):
    """Confirm a colour-delete. With thumbnails on, it shows exactly which
    pictures will be moved to trash (trip = grey border, master = orange), so a
    destructive trip/master delete is verifiable. The 'Preview pictures before
    deletion' checkbox is shown in both states — untick to skip the thumbnails
    next time, re-tick to bring them back."""

    _THUMB_W = 130

    def __init__(self, parent, collection, keys, *, color, scope, breakdown,
                 show_thumbnails):
        super().__init__(parent)
        from gui.rating_provider import RatingEditorItem
        self.setWindowTitle(f"Delete {color} pictures")
        self.setModal(True)
        self._items = []  # keep item refs alive for the async thumbnail callbacks

        lay = QVBoxLayout(self)
        n = len(keys)
        header = QLabel(
            f"<b>{n} {color.lower()} picture{'s' if n != 1 else ''}</b> "
            f"will be moved to trash — {scope}.")
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        lay.addWidget(header)
        if breakdown:
            note = QLabel("    ".join(breakdown))
            note.setStyleSheet("color:#666;")
            lay.addWidget(note)

        if show_thumbnails:
            self.resize(760, 560)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            grid_w = QWidget()
            grid = QGridLayout(grid_w)
            cols = 5
            tw = self._THUMB_W
            for i, key in enumerate(keys):
                cell = QLabel("…")
                cell.setFixedSize(tw, tw * 2 // 3)
                cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
                master = collection.is_master(key) if hasattr(collection, "is_master") else False
                cell.setStyleSheet(
                    "border:2px solid %s; background:#f4f4f4;"
                    % ("#c07000" if master else "#cccccc"))
                cell.setToolTip(("master · " if master else "trip · ") + key)
                grid.addWidget(cell, i // cols, i % cols)
                it = RatingEditorItem(collection, key)
                self._items.append(it)
                it.request_thumbnail(tw, self._make_cb(cell))
            scroll.setWidget(grid_w)
            lay.addWidget(scroll, 1)

        self._pref = QCheckBox("Preview pictures before deletion")
        self._pref.setChecked(show_thumbnails)
        lay.addWidget(self._pref)

        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        delete = QPushButton(f"Delete {n}")
        delete.setDefault(True)
        row.addWidget(cancel)
        row.addWidget(delete)
        lay.addLayout(row)
        cancel.clicked.connect(self.reject)
        delete.clicked.connect(self.accept)

    @staticmethod
    def _make_cb(cell):
        def _cb(px):
            if px is not None and not px.isNull():
                cell.setText("")
                cell.setPixmap(px)
        return _cb

    def show_preview_pref(self) -> bool:
        return self._pref.isChecked()


# ── Main dialog ────────────────────────────────────────────────────────────────

class RatingEditorDialog(MasterCardMenuMixin, QDialog):
    """Scrollable grid of picture cards with editable stars and color labels.

    Opened from the Trip menu. Shows pictures for one day (Day scope) or for
    the whole committed trip (Trip scope), filtered by species, sorted by
    computed Rate descending.
    """

    def __init__(self, parent, trip_root_dir: str = "", day_str: str = None,
                 mode: str = "trip", scope_label: str = None,
                 initial_sci: str = None, album_mode: bool = None):
        super().__init__(parent)
        # When launched from the Biota Browser's Edit panel: open straight on this
        # species (a scientific name; resolved to the combo entry after load).
        self._initial_sci = (initial_sci or "").strip() or None
        # THREE MODES, and the last two are the same thing at different widths:
        #
        #   trip   — the trip owns its pictures (day CSVs, write-back, frozen days)
        #   album  — a date window on the master: Day / Album / Master scopes
        #   master — the master with NO window at all, one species at a time
        #
        # album and master are both "master-backed": every card comes from
        # pictures_for_species(), narrowed by set_master_scope() to whatever the
        # window allows — and master mode simply never narrows. That is the whole
        # of what used to be a separate 839-line Master Species Viewer.
        if album_mode is not None:          # pre-mode callers
            mode = "album" if album_mode else "trip"
        if mode not in ("trip", "album", "master"):
            raise ValueError(f"unknown viewer mode {mode!r}")
        self._mode = mode
        self._album_mode = mode in ("album", "master")   # cards come from the master
        self._master_only = mode == "master"             # …with no window
        self._scope_label = scope_label or {
            "trip": "Trip", "album": "Album", "master": "Master"}[mode]
        # Whether you may edit is _view_read_only, decided per load from the
        # machine's master write policy — an album's pictures being the master's
        # own is the reason editing WORKS here, not a reason to forbid it.
        self._view_read_only = self._album_mode   # until a load says otherwise
        self.setWindowTitle(f"{self._scope_label} Species Viewer"
                            if self._album_mode else "Trip Species Viewer")
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        # No root in master mode: the master needs no trip and no album.
        self._trip_root = Path(trip_root_dir) if trip_root_dir else None
        self._initial_day = day_str
        self.fpr: FilePicture | None = None
        self.columns = 3
        # Restore per-machine remembered geometry + column count (.ini).
        self._geo_key = {"trip": "RatingEditor", "album": "AlbumSpeciesViewer",
                         "master": "MasterSpeciesViewer"}[self._mode]
        _st = restore_window(self, self._geo_key, default_size=(1000, 720))
        if _st["cols"]:
            self.columns = max(1, min(8, _st["cols"]))
        self._cards: list[PictureCard] = []
        self._cards_by_key: dict[str, PictureCard] = {}
        self._jpg_only = True
        # Which name-language profile the captions use. Starts at the effective
        # default (a per-trip override wins over the global setting); the combo is
        # a per-session override on top.
        from utils.name_profile import effective_profile
        self._species_lang = effective_profile(self._trip_name_profile_override())
        # One async image loader for the dialog's lifetime; each collection
        # shares it so cancel_pending_loads() always targets the live loader.
        self._loader = AsyncImageLoader(self)
        self._collection = None
        self._selected_keys: set[str] = set()
        self._bracket_left: str | None = None
        self._bracket_right: str | None = None
        self._last_clicked_key: str | None = None
        # Trip-scope per-day tracking; None in Day scope
        self._day_objects: dict | None = None   # day_str → (Files, Pictures)
        self._upn_day_map: dict[str, str] = {}  # upn_key → day_str
        # Master DB (Trip+Master scope) — opened lazily on first use, then
        # kept for the dialog's lifetime. SQLite-backed (Plan B): the first
        # open after a master rebuild rebuilds from CSV (~2-6s); subsequent
        # opens just attach to the existing DB.
        self._master_db = None    # qt.db.master_db.MasterDB | None
        self._master_root: str | None = None
        # Album (read-only) source: the master read live by the album's date
        # range. Resolved once per dialog by _album_master_source() — days is
        # None until then, [] once we know there is nothing to read.
        self._album_db = None
        self._album_days: list[str] | None = None
        self._album_range: tuple[str, str] = ("", "")
        self._album_name = ""
        # The album window, resolved once: which UPNs are in it (per day and in
        # total) and which species it holds. This is ALL an album keeps in
        # memory — the pictures themselves stay in the master and arrive one
        # species at a time through the same query the Master Species Viewer
        # runs. _album_query says that resolution succeeded; without it (no
        # master on this machine) the album falls back to its old extracted day
        # CSVs, read-only.
        self._album_upns: set[str] = set()
        self._album_upns_by_day: dict[str, set[str]] = {}
        self._album_species: dict[str, dict] = {}   # sci → name fields
        self._album_query = False
        self._album_restrict: set[str] | None = None
        self._master_editable = False
        self._main_window = parent   # for the "✦ Species…" jump into the Biota Browser
        self._name_to_sci: dict[str, str] = {}   # combo display name → scientific name
        self._lineage_db_obj = "unset"   # cached DB for the breadcrumb (lazy)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 4)
        main_layout.setSpacing(4)

        # ── Top bar ──────────────────────────────────────────────────────────
        top = QHBoxLayout()

        # Scope radio. In read-only (Album) mode the scope is "Day / Album" and
        # there is no Trip+Master compare (the pictures already ARE the master).
        self._day_radio = QRadioButton("Day")
        self._trip_radio = QRadioButton(self._scope_label)
        # In the read-only Album viewer the third scope is "Master" = the
        # species' COMPLETE master set (every date). In a normal trip it is the
        # "Trip+Master" side-by-side compare.
        self._tm_radio = QRadioButton("Master" if self._album_mode else "Trip+Master")
        self._tm_radio.setToolTip(
            "Show this species' complete master collection (every date); the "
            "album's own pictures are tagged"
            if self._album_mode else
            "Also show this species' existing master pictures (read-only, greyed) "
            "alongside the trip's, to judge whether a new shot is website-worthy")
        self._scope_grp = QButtonGroup(self)
        self._scope_grp.addButton(self._day_radio)
        self._scope_grp.addButton(self._trip_radio)
        self._scope_grp.addButton(self._tm_radio)
        # Pick the starting scope BEFORE connecting: the handler reloads and
        # touches widgets built further down.
        (self._tm_radio if self._master_only else
         self._day_radio if day_str else self._trip_radio).setChecked(True)
        self._scope_grp.buttonToggled.connect(self._on_scope_toggled)
        self._scope_label_w = QLabel("Scope:")
        top.addWidget(self._scope_label_w)
        top.addWidget(self._day_radio)
        top.addWidget(self._trip_radio)
        top.addWidget(self._tm_radio)
        top.addSpacing(8)
        if self._master_only:
            # No window means no widths to choose between — the master IS the
            # scope. (The radios stay built so the rest of the dialog can keep
            # asking them questions; _master_only short-circuits the answers.)
            for w in (self._scope_label_w, self._day_radio,
                      self._trip_radio, self._tm_radio):
                w.hide()

        # Day picker (visible only in Day scope)
        self._day_combo = QComboBox()
        self._day_combo.setMinimumWidth(120)
        self._day_combo.currentIndexChanged.connect(self._on_day_changed)
        top.addWidget(self._day_combo)
        top.addSpacing(12)

        self._species_combo = QComboBox()
        self._species_combo.setMinimumWidth(200)
        self._species_combo.setToolTip("Species to view")
        if self._master_only:
            # ~2000 species is too many to scroll: type to narrow instead. Still
            # a combo, so every currentData() caller in this dialog is unchanged
            # — and unlike a bare line edit you can still browse the list.
            self._species_combo.setMinimumWidth(260)
            self._species_combo.setEditable(True)
            self._species_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            self._species_combo.lineEdit().setPlaceholderText(
                "Type a Dutch, English or scientific name…")
            self._species_combo.setToolTip(
                "Species to view — type any part of a Dutch, English or "
                "scientific name")
            comp = self._species_combo.completer()
            comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            comp.setFilterMode(Qt.MatchFlag.MatchContains)
            comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._species_combo.currentIndexChanged.connect(self._on_species_changed)
        top.addWidget(self._species_combo)
        # Name-language profile — which of NL/EN/Sci reads out under each picture
        # (a fallback chain, so e.g. EN/Sci never shows Dutch). Seeded from the
        # effective profile; this combo is a per-session override.
        from utils.name_profile import profile_keys, profile_short_label
        self._lang_combo = QComboBox()
        for _key in profile_keys():
            self._lang_combo.addItem(profile_short_label(_key), _key)
        self._lang_combo.setFixedWidth(92)
        self._lang_combo.setToolTip("Which name shows under each picture "
                                    "(name-language profile)")
        _li = self._lang_combo.findData(self._species_lang)
        if _li >= 0:
            self._lang_combo.setCurrentIndex(_li)
        self._lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        top.addWidget(self._lang_combo)
        top.addSpacing(12)

        # Camera filter — narrow the grid to one camera body (populated from the
        # loaded pictures' CameraModel). Handy on trips shot with two bodies.
        self._camera_combo = QComboBox()
        self._camera_combo.setMinimumWidth(130)
        self._camera_combo.setToolTip("Show only pictures from this camera body")
        self._camera_combo.currentIndexChanged.connect(self._on_camera_changed)
        top.addWidget(self._camera_combo)
        top.addSpacing(8)

        # Sort order. Rate (best first) is the default; capture time groups
        # near-identical burst frames next to each other so blue-delete culling
        # of duplicates is easy; camera groups by body.
        self._sort_combo = QComboBox()
        for label, data in (("Rate ↓", "rate"),
                            ("Time ↑", "time_asc"),
                            ("Time ↓", "time_desc"),
                            ("Camera", "camera")):
            self._sort_combo.addItem(label, data)
        self._sort_combo.setToolTip(
            "Card order — Rate (best first), capture time (old→new / new→old, "
            "groups duplicate frames), or camera body")
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        top.addWidget(self._sort_combo)
        top.addSpacing(8)

        # Map of where the SHOWN pictures of this species were photographed — the
        # trip's own GPS points (plus the master reference points in Trip+Master).
        self._map_btn = QPushButton("Map…")
        self._map_btn.setToolTip("Plot the shown pictures of this species on a map")
        self._map_btn.clicked.connect(self._on_show_map)
        top.addWidget(self._map_btn)

        # Jump to this species' pretty page in the Biota Browser (the read-only
        # twin — hero photo, grouped shots, siblings), at the trip/album level.
        self._page_btn = QPushButton("✦ Species…")
        self._page_btn.setToolTip("Open this species' page in the Biota Browser "
                                  "(hero, grouped photos, siblings)")
        self._page_btn.clicked.connect(self._open_species_page)
        top.addWidget(self._page_btn)

        # Master-backed only: re-read this species from disk, and republish its
        # page. Both act on the master, so they mean nothing for a trip that owns
        # its own pictures. Visibility follows the write policy (_apply_lock_ui).
        self._refresh_btn = QPushButton("↻")
        self._refresh_btn.setFixedWidth(38)
        self._refresh_btn.setToolTip(
            "Re-read this species from the master and repaint. "
            "Also ingests any new file rendered into the species folder since "
            "the last look — a fresh DxO JPG is invisible to the master until "
            "something scans for it, which is why a regenerated page can come "
            "back unchanged.")
        self._refresh_btn.clicked.connect(self._on_master_refresh)
        top.addWidget(self._refresh_btn)

        self._regen_btn = QPushButton("↻ Web")
        self._regen_btn.setToolTip(
            "Rebuild this species' website page from the current master data and "
            "upload it — page AND the pictures it links to — to the live site")
        self._regen_btn.clicked.connect(self._on_master_regenerate)
        top.addWidget(self._regen_btn)
        if not self._album_mode:
            self._refresh_btn.hide()
            self._regen_btn.hide()
        top.addStretch()

        self._jpg_btn = QPushButton("JPG only")
        self._jpg_btn.setCheckable(True)
        self._jpg_btn.setChecked(True)
        self._jpg_btn.setToolTip("Show only pictures that have a JPG file")
        self._jpg_btn.setStyleSheet(f"""
            QPushButton {{ border: 1px solid {_LINE}; border-radius: 3px; padding: 2px 6px; }}
            QPushButton:checked {{ background-color: #c8e6c9; border-color: #4caf50; color: #16241f; }}
        """)
        self._jpg_btn.toggled.connect(self._on_jpg_only_toggled)
        top.addWidget(self._jpg_btn)
        top.addSpacing(8)

        # Colour filter — the same selector row as the culling grid, so you can
        # narrow the view to one colour (e.g. see exactly what a Blue-delete
        # would target). Local import avoids a circular import with image_viewer.
        from gui.image_viewer import ColorFilterBar
        self._color_filter_bar = ColorFilterBar(
            on_filter_changed=self._on_color_filter_changed)
        top.addWidget(self._color_filter_bar)
        top.addSpacing(8)

        top.addWidget(QLabel("Columns:"))
        self._col_spinner = QSpinBox()
        self._col_spinner.setRange(1, 8)
        self._col_spinner.setValue(self.columns)
        self._col_spinner.valueChanged.connect(self._on_columns_changed)
        top.addWidget(self._col_spinner)
        top.addSpacing(8)

        self._del_blue_btn = QPushButton("✖ Blue")
        self._del_blue_btn.setFixedSize(66, 24)
        self._del_blue_btn.setToolTip("Delete all Blue-tagged pictures (move to trash)")
        self._del_blue_btn.setStyleSheet("""
            QPushButton { border: 1px solid #1E90FF; border-radius: 3px;
                          padding: 2px 4px; background: transparent; color: #1E90FF;
                          font-weight: bold; }
            QPushButton:hover { background: rgba(30,144,255,0.1); }
        """)
        self._del_blue_btn.clicked.connect(lambda: self._batch_delete_color("Blue"))
        top.addWidget(self._del_blue_btn)

        self._del_purple_btn = QPushButton("✖ Purple")
        self._del_purple_btn.setFixedSize(76, 24)
        self._del_purple_btn.setToolTip("Delete all Purple-tagged pictures (move to trash)")
        self._del_purple_btn.setStyleSheet("""
            QPushButton { border: 1px solid #9370DB; border-radius: 3px;
                          padding: 2px 4px; background: transparent; color: #9370DB;
                          font-weight: bold; }
            QPushButton:hover { background: rgba(147,112,219,0.1); }
        """)
        self._del_purple_btn.clicked.connect(lambda: self._batch_delete_color("Purple"))
        top.addWidget(self._del_purple_btn)

        # Start hidden in album mode; _apply_lock_ui shows them again once a load
        # has established that this machine may write the master. In an album a
        # cull deletes MASTER pictures (JPG-protected, master .trash, undoable).
        if self._album_mode:
            self._del_blue_btn.hide()
            self._del_purple_btn.hide()

        main_layout.addLayout(top)

        # Biota lineage of the species in view — the same path the Biota Browser
        # shows above its cards (biota › … › species). Plain text, not a link: the
        # "✦ Species…" button is the way into the tree.
        self._crumb = QLabel("")
        self._crumb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._crumb.setStyleSheet(
            f"color:{_INK_SOFT}; font-family:Consolas,monospace; font-size:11px;"
            f" padding:1px 2px;")
        self._crumb.setVisible(False)
        main_layout.addWidget(self._crumb)

        # Committed-day lock banner (frozen day / some frozen pictures). Hidden
        # until a load resolves the frozen state (see _apply_lock_ui).
        self._lock_banner = QLabel()
        self._lock_banner.setWordWrap(True)
        self._lock_banner.setStyleSheet(
            "background:#8a6d00; color:#fff; font-weight:bold; "
            "padding:5px 8px; border-radius:3px;")
        self._lock_banner.hide()
        main_layout.addWidget(self._lock_banner)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status = QLabel("Initializing…")
        # Was a hardcoded #555 at 10px — invisible on the dark theme. It is the
        # line that reports what loaded and what a batch action did, so it has
        # to be readable in both themes.
        self._status.setStyleSheet(f"color: {_INK_SOFT}; font-size: 11px;")
        main_layout.addWidget(self._status)

        # ── Scroll area ───────────────────────────────────────────────────────
        # ── Selection bar (hidden until ≥1 card is selected) ─────────────────
        self._sel_bar = QWidget()
        _sb_layout = QHBoxLayout(self._sel_bar)
        _sb_layout.setContentsMargins(4, 2, 4, 2)
        _sb_layout.setSpacing(8)
        self._sel_label = QLabel()
        self._sel_label.setStyleSheet("color: #1565c0; font-weight: bold;")
        _sb_layout.addWidget(self._sel_label)
        self._sel_species_btn = QPushButton("Set species…")
        self._sel_species_btn.clicked.connect(self._open_species_dialog_for_selection)
        _sb_layout.addWidget(self._sel_species_btn)
        # Move the selection to a DIFFERENT species that already exists (the two
        # swans mislabelled among the ducks). Target is constrained to the trip's
        # species list — no new species, no GBIF.
        self._sel_move_btn = QPushButton("Move to species…")
        self._sel_move_btn.setToolTip(
            "Reassign the selected pictures to an existing species")
        self._sel_move_btn.clicked.connect(self._move_selection_to_species)
        _sb_layout.addWidget(self._sel_move_btn)
        if self._album_mode:
            self._sel_species_btn.hide()   # re-shown by _apply_lock_ui if editable
            self._sel_move_btn.hide()
        _sb_layout.addStretch()
        _sel_clear_btn = QPushButton("Clear selection")
        _sel_clear_btn.clicked.connect(self._clear_selection)
        _sb_layout.addWidget(_sel_clear_btn)
        self._sel_bar.setVisible(False)
        main_layout.addWidget(self._sel_bar)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        # Wrap grid in a VBox so the stretch below it absorbs extra viewport
        # height; otherwise setWidgetResizable forces the grid to fill the
        # viewport and rows expand to fill the allocated space.
        self._scroll_content = QWidget()
        _scroll_content = self._scroll_content
        _scroll_vbox = QVBoxLayout(_scroll_content)
        _scroll_vbox.setContentsMargins(0, 0, 0, 0)
        _scroll_vbox.setSpacing(0)
        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setSpacing(6)
        _scroll_vbox.addWidget(self._grid_widget)
        _scroll_vbox.addStretch(1)
        self._scroll.setWidget(_scroll_content)
        main_layout.addWidget(self._scroll)

        # Populate day combo then load data after show
        QTimer.singleShot(0, self._init_day_combo)

    def closeEvent(self, event):
        save_window(self, self._geo_key, cols=self.columns)
        # Species named through the editor here were buffered, not written per
        # species; this is the end of that session.
        try:
            from gui.culling_species import flush_learned_species_from
            flush_learned_species_from(
                (getattr(self.fpr, "sc", None) if getattr(self, "fpr", None) else None,),
                parent=self)
        except Exception as e:
            logger.warning(f"Saving learned species failed: {e}")
        super().closeEvent(event)

    # ── Batch delete by colour ────────────────────────────────────────────────

    def _apply_lock_ui(self):
        """Reflect what this load may change in the toolbar + banner.

        Two independent locks: a committed (frozen) trip day, and — in an album —
        a machine that may not write the master. Either hides the actions that
        would need the write."""
        locked = getattr(self, "_day_scope_locked", False)
        some_frozen = bool(getattr(self, "_frozen_days", set())) and not locked
        from gui.day_freeze import FROZEN_BANNER
        if locked:
            self._lock_banner.setText("🔒 " + FROZEN_BANNER)
            self._lock_banner.show()
        elif some_frozen:
            self._lock_banner.setText(
                "🔒 Some pictures are on a committed day and are shown locked — "
                "Reset the trip to unlock them.")
            self._lock_banner.show()
        else:
            self._lock_banner.hide()
        # A fully-locked (Day scope, frozen) panel hides the cull + species-edit
        # actions. In Trip scope with only SOME frozen, they stay (they act on the
        # editable pictures; frozen keys are filtered by each handler).
        for w in (self._del_blue_btn, self._del_purple_btn,
                  self._sel_move_btn, self._sel_species_btn):
            w.setVisible(not locked and not self._view_read_only)
        if self._album_mode:
            # Both act on the master, so they follow the same write policy. ↻
            # (re-read from disk) is a read for a read-only master, but the
            # ingest half of it is not — keep the pair together.
            self._refresh_btn.setVisible(not self._view_read_only)
            self._regen_btn.setVisible(not self._view_read_only)
        if self._album_mode and self._view_read_only:
            self._lock_banner.setText(
                "👁 View only — this machine's master is read-only, so nothing "
                "here can be changed." + _snapshot_suffix()
                if self._album_query or self._master_only else
                "👁 View only — showing this album's old extracted day files; "
                "there is no master on this machine to read or write.")
            self._lock_banner.show()

    def _batch_delete_color(self, color: str):
        if not self.fpr or self._collection is None:
            return
        from gui.rating_provider import RatingEditorCollection
        if not isinstance(self._collection, RatingEditorCollection):
            return

        # Scope the delete to what the user is actually looking at. If a species
        # is selected it wins (species view → only that species' colour, across
        # trip AND master), whether in day or trip scope. With no species, a day
        # view culls the whole day and the trip view is trip-wide. `day` also
        # gates master inclusion (no master in day scope). Master pictures are
        # deleted via the master path (JPG-protected, master .trash, RO aware).
        species = self._species_combo.currentData()
        day = self._current_day() if self._day_radio.isChecked() else None
        where = ("the whole master" if self._master_only else
                 f"this {self._scope_label.lower()}"
                 + ("" if self._album_mode else " + master"))
        if species and day:
            scope = f"{species} — this day ({day})"
        elif species:
            scope = f"{species} — {where}"
        elif day:
            scope = f"this day ({day}) — all species"
        else:
            scope = f"{where} — all species"

        # `day` gates master inclusion for a TRIP (day scope shows no master
        # references). In an album the master pictures ARE the day — the scope
        # already narrowed them — so the gate must not fire.
        trip_keys, master_keys = self._collection.color_matches(
            color, species=species, day=None if self._album_query else day)
        if not trip_keys and not master_keys:
            # A MASTER picture is only visible to this when it survived the JPG
            # filter — and a picture with a JPG is never deletable. So with "JPG
            # only" on, a cull over master pictures can find nothing by
            # construction. Say that instead of a flat "none", which reads as
            # "there are no blue pictures" when there may be plenty.
            hint = ("\n\nNote: “JPG only” is on, and a picture that has a JPG is "
                    "never deleted here — so the ones a cull would remove are "
                    "exactly the ones now hidden. Switch “JPG only” off to see "
                    "them."
                    if (self._jpg_only and (self._album_query
                                            or self._tm_radio.isChecked())) else "")
            QMessageBox.information(self, f"No {color} pictures",
                                    f"No {color.lower()} pictures in {scope}.{hint}")
            return

        # A committed-day trip picture is locked — never deletable here.
        frozen_trip = [k for k in trip_keys if self._collection.is_frozen_day(k)]
        trip_deletable = [k for k in trip_keys
                          if not self._collection.jpg_path(k)
                          and not self._collection.is_frozen_day(k)]
        master_deletable = [k for k in master_keys
                            if self._collection.master_upn_deletable(k)]
        prot = ((len(trip_keys) - len(trip_deletable))
                + (len(master_keys) - len(master_deletable)))
        total = len(trip_keys) + len(master_keys)
        deletable = len(trip_deletable) + len(master_deletable)

        lines = [f"{total} {color.lower()} picture{'s' if total != 1 else ''} in {scope}."]
        if trip_deletable:
            lines.append(f"Trip → .trash: {len(trip_deletable)}")
        if master_deletable:
            lines.append(f"Master → master .trash: {len(master_deletable)}")
        if prot:
            why = "have a JPG"
            if frozen_trip:
                why += " / committed day (🔒)"
            if (len(master_keys) - len(master_deletable)) and self._collection.master_read_only:
                why += " / master read-only"
            lines.append(f"Skipped ({why}): {prot}")
        msg = "\n".join(lines)

        if deletable == 0:
            QMessageBox.information(self, f"Delete {color}", msg)
            return

        deletable_keys = trip_deletable + master_deletable
        show_thumbs = _config.get("ratingeditor", "delete_preview",
                                  fallback="yes").strip().lower() != "no"
        dlg = DeletePreviewDialog(
            self, self._collection, deletable_keys,
            color=color, scope=scope, breakdown=lines[1:], show_thumbnails=show_thumbs)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg.show_preview_pref() != show_thumbs:
            set_setting("ratingeditor", "delete_preview",
                        "yes" if dlg.show_preview_pref() else "no")

        deleted = 0
        if trip_deletable and self._trip_root is not None:
            _t, _p, deleted = self._collection.delete_trip_keys(
                trip_deletable, self._trip_root / ".trash", day_hint=day or "")
        m_deleted = 0
        for k in list(master_deletable):
            try:
                self._collection.delete_master_picture(k)
                m_deleted += 1
            except Exception as e:
                logger.error(f"Master delete {k} failed: {e}")
        logger.info(f"Batch delete {color}: {deleted} trip + {m_deleted} master picture(s) removed.")
        parts = [p for p in (f"{deleted} trip" if deleted else "",
                             f"{m_deleted} master" if m_deleted else "") if p]
        self._status.setText(
            f"Deleted {' + '.join(parts) or '0'} {color.lower()} picture(s).")
        self._rebuild_cards()

    # ── Initialisation helpers ────────────────────────────────────────────────

    def _init_day_combo(self):
        # An ALBUM's days are the days the master actually has pictures on
        # inside its date range — not whatever review/ folders happen to exist
        # here. Same list the album itself is built from, so Day scope narrows
        # the Album view instead of showing a different collection.
        days = []
        use_review_dirs = True
        if self._master_only:
            self._album_master_source()     # opens the DB + the species list
            use_review_dirs = False
        elif self._album_mode:
            db, album_days = self._album_master_source()
            days = list(album_days)
            # If the master answered at all, that answer is the album's day list
            # — an empty range means the album has no days, not that the old
            # review/ folders are worth showing instead.
            use_review_dirs = db is None
        if use_review_dirs:
            review_root = self._trip_root / "metadata" / "review"
            if review_root.exists():
                days = sorted(
                    d.name for d in review_root.iterdir()
                    if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", d.name)
                )
        self._day_combo.blockSignals(True)
        self._day_combo.clear()
        for d in days:
            self._day_combo.addItem(d)
        if self._initial_day and self._initial_day in days:
            self._day_combo.setCurrentText(self._initial_day)
        elif days:
            self._day_combo.setCurrentIndex(len(days) - 1)  # default to latest day
        self._day_combo.blockSignals(False)
        self._day_combo.setVisible(self._day_radio.isChecked())
        self._load_data()

    def _current_day(self) -> str | None:
        return self._day_combo.currentText() or None

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_data(self):
        # Non-reentrant: scope/day/species signals can fire while a load runs;
        # re-entering would rebuild the combo/grid mid-flight and delete widgets
        # out from under the outer call (observed: QComboBox C++ object deleted).
        # The master read itself is off-thread (see _attach_master).
        if getattr(self, "_loading", False):
            return
        self._loading = True
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._load_data_inner()
        finally:
            QApplication.restoreOverrideCursor()
            self._loading = False

    def _load_data_inner(self):
        saved_species = self._species_combo.currentData()  # preserve across reload
        self._status.setText("Loading…")
        self.fpr = FilePicture(None)
        self._day_objects = None
        self._upn_day_map = {}
        scope_day = self._day_radio.isChecked()
        tripmaster = self._tm_radio.isChecked()
        # An ALBUM loads no pictures at all: it resolves its window once and then
        # narrows the per-species master query to it. Only when there is no
        # master to ask does it fall back to loading its old day CSVs.
        self._album_query = self._album_mode and self._album_master_source()[0] is not None
        self._master_editable = _config.get(
            "master", "readonly", fallback="yes").strip().lower() == "no"
        # In an album, "can I edit" is the machine's master write policy — the
        # same gate the album day viewer uses, because the write goes to the same
        # place. On the CSV fallback there is nothing to write to at all.
        if self._album_mode:
            self._view_read_only = not (self._album_query and self._master_editable)
        try:
            if self._album_query:
                self._prepare_album_scope(scope_day)
            elif scope_day:
                day = self._current_day()
                if not day:
                    self._status.setText("No day selected.")
                    return
                if not self.fpr.readTripDay(str(self._trip_root), day):
                    self._status.setText(f"No data found for {day}.")
                    return
                self._status.setText(f"Day: {day}")
            else:
                self._load_trip_scope()   # Trip and Trip+Master both load the full trip
        except Exception as e:
            self._status.setText(f"Load error: {e}")
            logger.error(f"RatingEditorDialog load error: {e}", exc_info=True)
            return

        # Species editing (SpeciesInfoPanel: Suggest / Validate / Commit) needs a
        # real SpeciesClassification on fpr.sc — readTripDay / _load_trip_scope
        # leave it as the bare {} placeholder. Load the trip's collection once.
        # An album's species come from the master, not from a trip collection,
        # and it is spared the ~19k-row read on every reload.
        if not self._album_mode and not isinstance(self.fpr.sc, SpeciesClassification):
            try:
                speccoll = self._trip_root / "metadata" / "active" / "speciescollection.csv"
                self.fpr.sc = SpeciesClassification(None)
                self.fpr.sc.readSpeciesCollection(str(speccoll), False)
            except Exception as e:
                logger.warning(f"Could not load species collection for editing: {e}")

        # Frozen-day lock: a committed day (Freeze/WriteCommit done) is immutable
        # — editing it would diverge the working layer from the trip's active/
        # CSVs + biota/ tree (the blue-delete accounting gap). In Day scope the
        # whole panel locks (view_read_only); in Trip / Trip+Master each picture
        # from a committed day locks individually (🔒 badge, no edit/delete). See
        # gui/day_freeze.py.
        self._frozen_days: set[str] = set()
        self._day_scope_locked = False
        # An ALBUM has no review days of its own to freeze — its days are the
        # master's — so skip the per-day probe rather than stat a folder per day
        # for a lock that can never apply.
        if not self._album_mode:
            from gui.day_freeze import day_is_frozen
            if scope_day:
                _d = self._current_day()
                if _d and day_is_frozen(self._trip_root, _d)[0]:
                    self._frozen_days.add(_d)
                    self._day_scope_locked = True
            else:
                for _d in {d for d in self._upn_day_map.values() if d}:
                    if day_is_frozen(self._trip_root, _d)[0]:
                        self._frozen_days.add(_d)

        # Build the provider collection over the freshly-loaded data. Local
        # import avoids a circular import (rating_provider imports helpers here).
        from gui.rating_provider import RatingEditorCollection
        self._collection = RatingEditorCollection(
            self.fpr, self._day_objects, self._upn_day_map,
            jpg_only=self._jpg_only, species_lang=self._species_lang,
            loader=self._loader,
            view_read_only=(self._view_read_only or self._day_scope_locked),
            frozen_days=self._frozen_days,
        )
        if self._album_query:
            # The whole of an album scope: which UPNs the per-species master
            # query may return, plus the album's full set for the "from Album"
            # tag in Master scope.
            self._collection.set_master_scope(self._album_restrict, self._album_upns)
        self._apply_lock_ui()
        # An album is master-backed in EVERY scope — its cards come from nowhere
        # else — so the master must be attached BEFORE the combos, whose own
        # rebuild is the one that builds the grid. (A trip attaches after: there
        # the master only adds reference cards to a grid it already has.)
        if self._album_query:
            self._attach_master(rebuild=False)
        self._populate_camera_combo()   # before species combo (its signal rebuilds cards)
        self._populate_species_combo(restore_selection=saved_species)
        if tripmaster and not self._album_query:
            self._attach_master()

    def _attach_master(self, rebuild: bool = True):
        """Open the master DB for Trip+Master and apply it to the live
        collection. Plan B: the master CSVs are mirrored into a per-machine
        SQLite cache (NOT in Dropbox); after the first cold rebuild every
        subsequent open is near-instant. Kept synchronous on purpose — a
        worker thread here proved fragile (QThread teardown crashes), and the
        warm path is essentially free."""
        if self._collection is None:
            return
        if not (self._tm_radio.isChecked() or self._album_query):
            return
        if self._master_db is None:
            master_root = _config.get("master", "masterbiotadatabase", fallback="").strip()
            if not master_root or not Path(master_root).exists():
                self._status.setText(
                    "Master database not available — showing trip pictures only.")
                return
            from db.master_db import MasterDB
            self._status.setText("Opening master database…")
            try:
                db = MasterDB(master_root)
                rebuilt = db.ensure_fresh(
                    progress_cb=lambda msg: self._status.setText(msg))
            except Exception as e:
                logger.error(f"RatingEditor master load failed: {e}", exc_info=True)
                self._status.setText(
                    "Master database load failed — showing trip pictures only.")
                return
            self._master_db = db
            self._master_root = master_root
            if rebuilt:
                logger.info("Master DB was stale and has been rebuilt.")
        self._apply_master(self._master_db, rebuild=rebuild)

    def _apply_master(self, db, rebuild: bool = True):
        if self._collection is None:
            return
        if not (self._tm_radio.isChecked() or self._album_query):
            return
        # Master writes are opt-in per machine (default read-only — e.g. a
        # laptop without the full master). See Repository Settings.
        read_only = not self._master_editable
        # An album shows the species' master pictures directly (full=True) in
        # every scope — narrowed to its date window by set_master_scope. A trip
        # only does this in Trip+Master, where full=False keeps the trip's own
        # cards and shows the master ones as references beside them.
        full = self._album_mode
        self._collection.set_master(
            db, self._master_root, include=True,
            read_only=read_only, full=full)
        ro_note = " — master is read-only" if read_only else " — master editable"
        if not rebuild:
            return          # the caller's own combo pass builds the grid
        if self._species_combo.currentData() is not None:
            self._rebuild_cards()
        elif self._album_mode:
            # The album's own species list is the useful one in every scope,
            # Master included: "this is what I saw here — now show me everything
            # I have of it".
            self._status.setText(
                f"{self._species_count()} species in this {self._scope_label.lower()}"
                f"{ro_note} — select one to view pictures.")
        else:
            n_master = db.picture_count()
            label = "Master" if full else "Trip + master"
            self._status.setText(
                f"{label} ready ({n_master} master pictures{ro_note}) "
                f"— select a species.")

    def _load_trip_scope(self):
        """Load a TRIP's whole set of pictures into fpr from its per-day review
        CSVs, keeping the per-day objects because write-back persists through
        them. A trip owns its pictures, so this is where they live.

        An album reaches this only on the fallback path — no master on this
        machine to ask, so its old extracted day files are all there is."""
        self._day_objects = {}
        self.fpr.fi = Files(None)
        self.fpr.upn = Pictures(None)

        if self._album_mode:
            logger.info("album viewer: no master on this machine — falling back "
                        "to the album's extracted day CSVs (a snapshot)")
            self._set_album_window_title(
                "⚠ old extracted day files — no master on this machine")

        review_root = self._trip_root / "metadata" / "review"
        days: list[str] = []
        if review_root.exists():
            days = sorted(
                d.name for d in review_root.iterdir()
                if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", d.name)
            )

        for day in days:
            files_csv = review_root / day / "data_files.csv"
            pics_csv  = review_root / day / "data_pictures.csv"
            if not files_csv.exists() or not pics_csv.exists():
                continue
            fi_day = Files(None)
            fi_day.read_file(str(files_csv), silent=True)
            upn_day = Pictures(None)
            upn_day.read_pictures(str(pics_csv))
            self._day_objects[day] = (fi_day, upn_day)

            # Merge into fpr — dict values are shared references, so in-place
            # edits to fpr.fi.files_dict[k] automatically update fi_day too.
            self.fpr.fi.files_dict.update(fi_day.files_dict)
            for upn_key, upn_row in upn_day.UniquePictureName_dict.items():
                if upn_key not in self.fpr.upn.UniquePictureName_dict:
                    self.fpr.upn.UniquePictureName_dict[upn_key] = upn_row
                    self._upn_day_map[upn_key] = day

        # Fall back to active if no review days found (fully committed trip)
        if not self._day_objects:
            meta_active = self._trip_root / "metadata" / "active"
            fi_a = Files(None)
            upn_a = Pictures(None)
            if (meta_active / "data_files.csv").exists():
                fi_a.read_file(str(meta_active / "data_files.csv"))
            if (meta_active / "data_pictures.csv").exists():
                upn_a.read_pictures(str(meta_active / "data_pictures.csv"))
            self._day_objects["active"] = (fi_a, upn_a)
            self.fpr.fi.files_dict.update(fi_a.files_dict)
            for upn_key, upn_row in upn_a.UniquePictureName_dict.items():
                if upn_key not in self.fpr.upn.UniquePictureName_dict:
                    self.fpr.upn.UniquePictureName_dict[upn_key] = upn_row
                    self._upn_day_map[upn_key] = "active"

        n_days = len(self._day_objects)
        n_pics = len(self.fpr.upn.UniquePictureName_dict)
        # In an album this line is only reached when the master was unreachable
        # — say so, because the numbers are then an old extract, not the album.
        note = (" — read from this album's extracted day files because no master "
                "is available here, so it may be out of date"
                if self._album_mode else "")
        self._status.setText(f"{self._scope_label} — {n_days} day"
                             f"{'s' if n_days != 1 else ''}, {n_pics} pictures "
                             f"total{note}")

    # ── Album: the master, seen through a date window ─────────────────────────

    def _album_master_source(self):
        """(MasterDB, [day labels]) for this ALBUM's window, or (None, []).

        Resolves the window ONCE per dialog and keeps only what a window is:
        which UPNs it contains (per day and in total) and which species. The
        pictures themselves are never loaded here — they arrive one species at a
        time from `pictures_for_species`, narrowed by set_master_scope, which is
        the same query the Master Species Viewer runs. That is the whole
        difference between this viewer and that one.

        Opened once because open_master_db() runs ensure_fresh(), which must not
        be paid per day, per reload or per scope switch; the DB is handed to the
        Master scope too, so it never opens a second copy."""
        if self._album_days is not None:
            return self._album_db, self._album_days
        self._album_days = []          # a negative answer caches too
        master_root = (_config.get("master", "masterbiotadatabase", fallback="")
                       or "").replace('"', "").replace("'", "").strip()
        if not (master_root and Path(master_root).exists()):
            logger.info("species viewer: no master on this machine")
            return None, []
        fromdate = todate = ""
        if not self._master_only:
            from utils.virttrip_config import read_virttrip_ini
            ini = read_virttrip_ini(str(self._trip_root)) or {}
            fromdate = (ini.get("fromdate") or "").strip()
            todate = (ini.get("todate") or "").strip()
            # The album's published name ("tanzania"), not its folder
            # ("tanzaniadb") — the name the Biota Browser puts in its title too.
            self._album_name = ((ini.get("trip_name") or "").strip()
                                or self._trip_root.name)
            if not (fromdate and todate):
                logger.info("album viewer: no date range — cannot read the album "
                            "from the master")
                return None, []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            from batch import album_source
            db = album_source.open_master_db(master_root)
            if self._master_only:
                # No window: there is nothing to resolve. Every species in the
                # master is on offer and the restriction stays None, which is
                # precisely what makes this the Master Species Viewer.
                self._album_species = {
                    (r.get("sci") or "").strip(): r
                    for r in db.species_picker_rows() if (r.get("sci") or "").strip()}
                self._album_db = db
                if self._master_db is None:
                    self._master_db, self._master_root = db, master_root
                logger.info(f"master viewer: {len(self._album_species)} species, "
                            f"no date window")
                return self._album_db, self._album_days
            by_day = db.pictures_in_daterange(fromdate, todate)
            self._album_days = sorted(by_day)
            self._album_upns_by_day = {
                day: {r["UniquePictureName"] for r in rows}
                for day, rows in by_day.items()}
            self._album_upns = set().union(*self._album_upns_by_day.values()) \
                if self._album_upns_by_day else set()
            # Keep the NAME fields per species, not the rows: the caption
            # language is switchable at runtime, so the display name has to be
            # derived each time rather than frozen here.
            self._album_species = {}
            for rows in by_day.values():
                for r in rows:
                    sci = (r.get("WetSoortNaam") or "").strip()
                    if sci and sci not in self._album_species:
                        self._album_species[sci] = {
                            k: r.get(k, "") for k in
                            ("NLSoortNaam", "EngSoortNaam", "WetSoortNaam")}
            self._album_db = db
            self._album_range = (fromdate, todate)
            if self._master_db is None:      # the Master scope wants the same DB
                self._master_db, self._master_root = db, master_root
            logger.info(f"album viewer: window {fromdate} … {todate} = "
                        f"{len(self._album_upns)} master picture(s), "
                        f"{len(self._album_days)} day(s), "
                        f"{len(self._album_species)} species")
            self._set_album_window_title(
                f"live from the master, {fromdate} … {todate}")
        except Exception as e:
            logger.warning(f"album viewer: could not open the master for "
                           f"{fromdate} … {todate}: {e}", exc_info=True)
            self._album_db, self._album_days = None, []
        finally:
            QApplication.restoreOverrideCursor()
        return self._album_db, self._album_days

    def _prepare_album_scope(self, scope_day: bool) -> None:
        """Set the album's scope: which of its UPNs the per-species master query
        may return. Day = that day, Album = the whole window, Master = no
        restriction (the species everywhere, with the album's own tagged).

        There is nothing to load — that is the point of reading the master
        directly. fpr stays the empty shell the collection expects."""
        self.fpr.fi = Files(None)
        self.fpr.upn = Pictures(None)
        if self._master_only or self._tm_radio.isChecked():
            self._album_restrict = None
            scope = "the whole master"
        elif scope_day:
            day = self._current_day()
            self._album_restrict = self._album_upns_by_day.get(day, set())
            scope = f"day {day}"
        else:
            self._album_restrict = self._album_upns
            scope = f"{self._album_range[0]} … {self._album_range[1]}"
        n = "every" if self._album_restrict is None else len(self._album_restrict)
        self._status.setText(
            f"{self._scope_label} — {scope}, {n} master picture"
            f"{'' if n == 1 else 's'}.")

    def _set_album_window_title(self, source_note: str) -> None:
        """Say in the TITLE BAR where this album's pictures came from.

        The status line cannot carry it: the very next thing every load does is
        overwrite it with "N species loaded", and after that with the shown
        picture count. Where an album's data comes from is not a transient
        message — it decides whether the numbers can be trusted — so it belongs
        somewhere that stays put."""
        if self._master_only:
            self.setWindowTitle("Master Species Viewer")
            return
        name = f" · {self._album_name}" if self._album_name else ""
        self.setWindowTitle(
            f"{self._scope_label} Species Viewer{name} · {source_note}")

    def _trip_name_profile_override(self) -> str | None:
        """The per-trip / per-virttrip name-profile override (from trip.ini's
        name_profile), or None to fall back to the global default. Set in Create
        Trip ▸ Images & dates."""
        root = str(getattr(self, "_trip_root", "") or "")
        if not root:
            return None
        from utils.name_profile import NAME_PROFILES
        key = ""
        try:
            from utils.trip_config import read_trip_ini
            key = ((read_trip_ini(root) or {}).get("name_profile") or "").strip()
        except Exception:
            key = ""
        if not key:                       # a virtual trip has virttrip.ini instead
            try:
                from utils.virttrip_config import read_virttrip_ini
                key = ((read_virttrip_ini(root) or {}).get("name_profile") or "").strip()
            except Exception:
                key = ""
        return key if key in NAME_PROFILES else None

    def _species_name(self, upn_row: dict) -> str:
        from utils.name_profile import name_for
        return name_for(upn_row, self._species_lang)

    def _populate_species_combo(self, restore_selection=None):
        self._species_combo.blockSignals(True)
        self._species_combo.clear()
        if not self._master_only:
            # With 2000+ species the type-ahead IS the picker, so a "— Select a
            # species —" row at the top of the list is only noise. Master mode
            # starts on NO selection instead (see below): an empty field showing
            # its placeholder, rather than whatever species sorts first.
            self._species_combo.addItem("— Select a species —", None)

        # Per species: does any TRIP picture have a real JPG on disk yet? A
        # species whose trip pictures are all RAW/HEIC won't appear on the
        # website until a JPG is rendered, so flag it "(only RAW)" — the
        # publication to-do is then visible at a glance (no flags left ⇒ done).
        # Master pictures never count: this is about the trip's own readiness.
        names: set[str] = set()
        trip_has_jpg: dict[str, bool] = {}   # species name → ≥1 trip JPG on disk
        self._name_to_sci = {}               # display name → scientific name
        col = self._collection
        # An ALBUM's species are the ones its date window holds, resolved with
        # the window itself — no pictures loaded. The "(only RAW)" flag is a
        # trip's publication to-do and means nothing here: these pictures are
        # already in the master.
        if self._album_query:
            for sci, row in self._album_species.items():
                name = self._species_name(row)
                if not name:
                    continue
                names.add(name)
                self._name_to_sci.setdefault(name, sci)
            for name in sorted(names, key=str.casefold):
                self._species_combo.addItem(name, name)
            if self._master_only:
                self._species_combo.setCurrentIndex(-1)   # nothing picked yet
            self._species_combo.blockSignals(False)
            return self._finish_species_combo(restore_selection)
        for upn_key, upn_row in self.fpr.upn.UniquePictureName_dict.items():
            name = self._species_name(upn_row)
            if not name:
                continue
            names.add(name)
            sci = (upn_row.get("WetSoortNaam") or "").strip()
            if sci:
                self._name_to_sci.setdefault(name, sci)
            if col is not None and not col.is_master(upn_key):
                if not trip_has_jpg.get(name) and col.jpg_path(upn_key) is not None:
                    trip_has_jpg[name] = True
                else:
                    trip_has_jpg.setdefault(name, False)
        for name in sorted(names, key=str.casefold):
            # Flag species that HAVE trip pictures but no trip JPG yet.
            label = name if trip_has_jpg.get(name, True) else f"{name} (only RAW)"
            self._species_combo.addItem(label, name)

        self._species_combo.blockSignals(False)
        self._finish_species_combo(restore_selection)

    def _species_count(self) -> int:
        """How many species the picker offers — the placeholder row, where there
        is one, is not a species."""
        return self._species_combo.count() - (0 if self._master_only else 1)

    def _finish_species_combo(self, restore_selection) -> None:
        """Pick the entry to show once the combo is filled: the one that was
        selected before the reload, else a one-shot jump target, else nothing."""
        if restore_selection:
            idx = self._species_combo.findData(restore_selection)
            if idx >= 0:
                self._species_combo.setCurrentIndex(idx)  # triggers _rebuild_cards via signal
                return
        # One-shot: open directly on a requested species (Biota Browser Edit-panel
        # jump). The combo is keyed by display name, so resolve sci → name.
        if self._initial_sci:
            target = self._initial_sci.lower()
            self._initial_sci = None
            for name, sci in self._name_to_sci.items():
                if sci.strip().lower() == target:
                    idx = self._species_combo.findData(name)
                    if idx >= 0:
                        self._species_combo.setCurrentIndex(idx)  # → _rebuild_cards via signal
                        return
        self._rebuild_cards()

    # ── Grid management ───────────────────────────────────────────────────────

    def _grid_placement(self, total: int) -> list[tuple[int, int, int, int]]:
        """Return (row, col, span=1, card_w) for each of `total` card positions.

        Card width is always viewport_width / columns regardless of how many
        pictures are shown. One picture in a 3-column layout gets the same
        small card as the last picture of a full grid.
        """
        ncols = self.columns
        # Use the scroll-area's outer width minus a reserved scrollbar extent +
        # frame + right gutter, rather than viewport().width(): the viewport is
        # full width before the scrollbar appears, so cards built then come out
        # too wide and the last column clips once the scrollbar shows.
        spacing = self._grid.spacing()
        sb = self._scroll.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        frame = 2 * self._scroll.frameWidth()
        avail = self._scroll.width() - sb - frame - spacing
        if avail < 100:
            avail = 960
        card_w = max(120, (avail - spacing * (ncols - 1)) // ncols)
        return [(idx // ncols, idx % ncols, 1, card_w) for idx in range(total)]

    def _clear_grid(self):
        if self._collection is not None:
            self._collection.cancel_pending_loads()  # drop in-flight thumbnail loads
        self._cards_by_key.clear()
        for c in range(8):  # reset all possible column stretch values (spinner max = 8)
            self._grid.setColumnStretch(c, 0)
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()  # deferred deletion; avoids GC-triggered C++ cascade
        self._cards.clear()

    def _rebuild_collection(self, filter_name) -> None:
        """Re-run the collection's query for the species in view.

        ALWAYS go through here. An album has no loaded rows for rebuild() to
        resolve the scientific name from, so it must be handed in — and a
        rebuild without it silently returns nothing, which empties the grid
        rather than failing. (That is exactly what a re-sort after an edit did.)
        The Master Species Viewer passes forced_sci at every one of its call
        sites for the same reason."""
        self._collection.rebuild(
            filter_name,
            forced_sci=(self._name_to_sci.get(filter_name) or None
                        if self._album_query else None))

    def _rebuild_cards(self):
        if not self.fpr or self._collection is None:
            return
        self._clear_grid()
        self._selected_keys.clear()
        self._bracket_left = None
        self._bracket_right = None
        self._last_clicked_key = None
        filter_name = self._species_combo.currentData()
        if filter_name is None:
            self._status.setText(
                f"{self._species_count()} species loaded — "
                f"select one to view pictures.")
            self._update_crumb()
            return
        self._collection.set_species_lang(self._species_lang)
        self._collection.set_jpg_only(self._jpg_only)
        # Re-apply the colour filter from the bar — the collection is recreated
        # on every load, so the bar is the durable source of truth.
        self._collection.set_color_filter(self._color_filter_bar.selected_colors)
        # Same for the camera filter and sort order (durable on the combos).
        self._collection.set_camera_filter(self._selected_cameras())
        self._collection.set_sort(self._sort_combo.currentData())
        self._rebuild_collection(filter_name)
        n = len(self._collection)
        placement = self._grid_placement(n)
        for c in range(self.columns):
            self._grid.setColumnStretch(c, 1)

        show_kind_tag = self._tm_radio.isChecked()
        for idx in range(n):
            item = self._collection.item(idx)
            r, col, span, card_w = placement[idx]
            # Framed Biota-look card for Trip AND the read-only Album variant —
            # the Album's controls auto-disable (view_read_only → can_edit False),
            # so it gets visual parity without any misleading affordance.
            card = PictureCard(item, card_w, show_kind_tag=show_kind_tag,
                               framed=True, viewer_key="species")   # async thumb
            card.card_changed.connect(self._on_card_changed)
            card.card_clicked.connect(self._on_card_cursor)
            card.card_double_clicked.connect(self._on_card_clicked)
            card.card_right_clicked.connect(self._on_card_context_menu)
            self._grid.addWidget(card, r, col, 1, span)
            self._cards.append(card)
            self._cards_by_key[item.key] = card

        suffix = f" for {filter_name}" if filter_name else ""
        self._status.setText(f"{n} picture{'s' if n != 1 else ''}{suffix}")
        self._update_selection_display()
        self._update_crumb()
        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(0))

    def _current_sci(self) -> str:
        """Scientific name of the species selected in the combo, or ''."""
        name = self._species_combo.currentData()
        if not name:
            return ""
        return self._name_to_sci.get(name, "")

    def _lineage_db(self):
        """A MasterDB for the breadcrumb lineage, opened lazily and cached.

        Trip → the trip's own repo (matches the Trip Biota Browser and covers
        trip-only species); Album → the real master (an album's pictures ARE
        master files). Returns None when nothing is configured/available."""
        if self._lineage_db_obj != "unset":
            return self._lineage_db_obj
        self._lineage_db_obj = None
        try:
            from db.master_db import MasterDB
            if self._album_mode:
                root = _config.get("master", "masterbiotadatabase", fallback="").strip()
                cache = None
            else:
                root = (_config.get("trip", "tripbiotadatabase", fallback="")
                        or _config.get("repository", "biotadatabase", fallback="")
                        ).replace('"', "").replace("'", "").strip()
                import hashlib
                cache = ("trip_" + hashlib.sha1(root.encode("utf-8")).hexdigest()[:12]
                         if root else None)
            if root and Path(root).exists():
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    db = MasterDB(root, cache_name=cache)
                    db.ensure_fresh()   # first cold build ~2-6s, then cached
                    self._lineage_db_obj = db
                finally:
                    QApplication.restoreOverrideCursor()
        except Exception as e:
            logger.warning(f"lineage DB open failed: {e}", exc_info=True)
            self._lineage_db_obj = None
        return self._lineage_db_obj

    def _update_crumb(self):
        """Show the biota lineage of the species in view (biota › … › species),
        or hide the line when nothing is selected."""
        sci = self._current_sci()
        db = self._lineage_db() if sci else None
        if not sci or db is None:
            self._crumb.setVisible(False)
            self._crumb.clear()
            return
        try:
            lineage = db.taxon_lineage(sci)
        except Exception:
            lineage = {}
        # taxon_lineage walks Up (leaf→root); reverse to read root→leaf, and lead
        # with the tree root the Biota Browser shows.
        rungs = ["biota"] + list(reversed(list(lineage.values())))
        seen, path = set(), []
        for r in rungs:
            r = (r or "").strip()
            if r and r.lower() not in seen:
                seen.add(r.lower())
                path.append(r)
        self._crumb.setText("  ›  ".join(path))
        self._crumb.setVisible(bool(path))

    def show_species(self, sci: str) -> bool:
        """Select this species in the combo (used when the Biota Browser's Edit
        panel reuses an already-open viewer). For an editable trip, switch to
        whole-Trip scope first so the species is present regardless of the day
        showing. Returns False if the species isn't in this trip/album."""
        target = (sci or "").strip().lower()
        if not target:
            return False
        if not self._album_mode and not self._trip_radio.isChecked():
            self._trip_radio.setChecked(True)   # → reload at Trip scope
        for name, s in self._name_to_sci.items():
            if (s or "").strip().lower() == target:
                idx = self._species_combo.findData(name)
                if idx >= 0:
                    self._species_combo.setCurrentIndex(idx)
                    return True
        return False

    # ── Master-backed species actions (Album + Master modes) ──────────────────

    def _on_master_refresh(self):
        """Re-read the species in view from the master, ingesting anything new
        on disk.

        The master learns about a file only when something scans for it. Render
        a tighter crop in PhotoLab and the species folder gains a `_DxO.jpg` no
        row anywhere mentions — so "Regenerate page" faithfully rebuilds the page
        from the files it knows and it comes back unchanged. This closes that
        gap. Whole species per call, never per picture: ONE folder listing + ONE
        exiftool call."""
        sci = self._current_sci()
        if self._collection is None or not sci or self._master_db is None:
            self._status.setText("Pick a species first, then refresh it.")
            return
        added, resized, rerated, failed = [], [], [], 0
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._master_db.ensure_fresh()
            if self._master_editable:
                pics = self._master_db.pictures_for_species(sci)
                keys = [p["UniquePictureName"] for p in pics
                        if p.get("UniquePictureName")]
                try:
                    res = self._collection.refresh_master_species(pics)
                    added = res.get("added", []) or []
                    resized = res.get("resized", []) or []
                except Exception as e:
                    failed += 1
                    logger.warning(f"refresh: species scan failed: {e}", exc_info=True)
                # Stars/colour an external tool wrote into a sidecar are neither
                # a new file nor a changed pixel size, so nothing above sees
                # them. A second batched read — again ONE exiftool call.
                if keys:
                    self._rebuild_collection(self._species_combo.currentData())
                    try:
                        rerated = self._collection.resync_master_metadata(keys)
                    except Exception as e:
                        logger.warning(f"refresh: metadata resync failed: {e}")
        finally:
            QApplication.restoreOverrideCursor()

        self._rebuild_cards()
        # Counts, not inventories. Refreshing one picture used to produce a
        # paragraph naming every file; the names are in the log if they are ever
        # wanted, and the status line is one line.
        for label, rows in (("added", added), ("corrected", resized),
                            ("re-read", rerated)):
            if rows:
                logger.info(f"refresh {label}: {rows}")
        def _n(count, noun):
            return f"{count} {noun}{'' if count == 1 else 's'}"
        bits = []
        if added:
            bits.append(_n(len(added), "new file"))
        if resized:
            bits.append(_n(len(resized), "corrected JPG"))
        if rerated:
            bits.append(_n(len(rerated), "rating/colour change"))
        if bits:
            self._status.setText("Refreshed — " + ", ".join(bits) + ".")
        elif failed:
            self._status.setText(
                f"Refreshed — {failed} picture(s) could not be scanned (see the log).")
        else:
            self._status.setText("Refreshed — nothing new or changed on disk.")

    def _on_master_regenerate(self):
        """Rebuild this species' website page from the master and upload it."""
        sci = self._current_sci()
        if not sci or self._master_db is None:
            QMessageBox.information(self, "Regenerate page",
                                    "Pick a species first, then regenerate its page.")
            return
        name = self._species_combo.currentData() or sci
        if QMessageBox.question(
                self, "Regenerate species page",
                f"Rebuild the website page for “{name}” from the current master "
                f"data and upload it to the live website?\n\n"
                f"This re-picks the best photos (per age/sex/plumage) and "
                f"republishes just this species' page. It does not change your "
                f"master pictures.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        prog = QProgressDialog("Regenerating…", None, 0, 0, self)
        prog.setWindowTitle("Regenerate species page")
        prog.setWindowModality(Qt.WindowModality.ApplicationModal)
        prog.setCancelButton(None)
        prog.setMinimumDuration(0)
        prog.show()
        QApplication.processEvents()

        def _cb(msg):
            prog.setLabelText(msg)
            QApplication.processEvents()

        try:
            from engine.regenerate_species import regenerate_species_page
            res = regenerate_species_page(sci, self._master_db, progress_cb=_cb)
        except Exception as e:
            logger.error(f"Regenerate species failed: {e}", exc_info=True)
            res = {"ok": False, "error": str(e)}
        finally:
            prog.close()

        if not res.get("ok"):
            QMessageBox.warning(self, "Regenerate species page",
                                res.get("error", "Regeneration failed — see the log."))
            return
        self._status.setText(f"Regenerated and uploaded the page for {name}.")
        from urllib.parse import quote
        base = _config.get("websettings", "base_url", fallback="").strip().rstrip("/")
        if not base:
            base = "https://houtekamer.com"
        elif not base.startswith("http"):
            base = "https://" + base
        links = [f'<a href="{base + quote(u, safe="/")}">{base + u}</a>'
                 for u in res.get("urls", [])]
        box = QMessageBox(self)
        box.setWindowTitle("Regenerate species page")
        box.setIcon(QMessageBox.Icon.Information)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"Done — the page for “{name}” was rebuilt and uploaded."
                    f"<br><br>" + "<br>".join(links))
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        box.exec()

    def _open_species_page(self):
        """Jump to this species' pretty page in the Biota Browser, at the
        trip/album level (a fresh browser — any prior scope/day state is not
        preserved, by design)."""
        sci = self._current_sci()
        if not sci:
            self._status.setText("Pick a species first, then open its page.")
            return
        mw = self._main_window
        if mw is None:
            return
        opener = ("open_biota_browser" if self._master_only else
                  "open_album_biota_browser" if self._album_mode else
                  "open_trip_biota_browser")
        fn = getattr(mw, opener, None)
        if fn is not None:
            fn(start_sci=sci)

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_scope_toggled(self, _button, checked: bool):
        # buttonToggled fires for both the de-selected and newly-selected radio;
        # act only on the one that became checked.
        if not checked:
            return
        self._day_combo.setVisible(self._day_radio.isChecked())
        self._load_data()

    def _on_day_changed(self):
        if self._day_radio.isChecked():
            self._load_data()

    def _close_large_view(self):
        """Close any open large view. Its key list is a fixed snapshot of the
        grid at open time, so once the grid shows a different species the large
        view is drilling into a set the grid no longer holds — navigation and
        edits there act on stale keys. Disconnect the on-close resort first so it
        doesn't fight the rebuild that's about to run."""
        lv = getattr(self, "_large_view", None)
        if lv is None:
            return
        self._large_view = None
        try:
            lv.finished.disconnect(self._on_large_view_finished)
        except (TypeError, RuntimeError):
            pass
        lv.close()

    def _on_species_changed(self):
        # The open large view belongs to the previous species — close it before
        # switching (its snapshot would otherwise edit/navigate a stale set).
        self._close_large_view()
        # Refresh the camera list to this species' bodies before rebuilding
        # (blockSignals inside keeps this from re-triggering a rebuild).
        self._populate_camera_combo()
        self._rebuild_cards()

    def _on_lang_changed(self, _idx: int):
        old_selection = self._species_combo.currentData()  # name in old language, or None
        self._species_lang = self._lang_combo.currentData()   # profile key
        self._populate_species_combo()  # rebuilds cards showing All species

        if old_selection is None or not self.fpr:
            return

        # Find the same species in the new language by matching any name field
        new_selection = None
        for upn_row in self.fpr.upn.UniquePictureName_dict.values():
            if (upn_row.get("NLSoortNaam") == old_selection
                    or upn_row.get("EngSoortNaam") == old_selection
                    or upn_row.get("WetSoortNaam") == old_selection):
                new_selection = self._species_name(upn_row)
                break

        if new_selection:
            idx = self._species_combo.findData(new_selection)
            if idx >= 0:
                self._species_combo.setCurrentIndex(idx)

    def _on_columns_changed(self, value: int):
        self.columns = value
        self._rebuild_cards()

    def _on_camera_changed(self, _idx: int):
        self._rebuild_cards()

    def _on_sort_changed(self, _idx: int):
        self._rebuild_cards()

    def _show_species_distribution_map(self):
        """Every GPS point the master holds for the species in view (Master
        mode's map — the standalone Master Species Viewer's behaviour)."""
        sci = self._current_sci()
        if not sci or self._master_db is None:
            QMessageBox.information(self, "Map",
                                    "Pick a species first, then open the map.")
            return
        points = self._master_db.gps_points_for_species(sci)
        name = self._species_combo.currentData() or sci
        if not points:
            QMessageBox.information(
                self, "Map",
                f"No GPS points for {name}.\n\n"
                f"About 40% of the master has no GPS — older material predates "
                f"in-camera GPS. (Map-based backfill is a later phase.)")
            return
        from gui.map_view import MapView
        self._map_dlg = MapView(self, self._master_db, sci, name, points)
        self._map_dlg.show()

    def _on_show_map(self):
        """Plot the SHOWN pictures of this species on a map — the trip's own GPS
        points (and the master reference pictures' points in Trip+Master scope), so
        it needs no master. Read-only for now: it shows where the species was
        photographed; geotagging a no-GPS picture from here is a separate step (the
        write path the culling map already has, to be ported to this collection)."""
        if not self.fpr or self._collection is None:
            return
        # Master mode maps the species' DISTRIBUTION — every point the master has
        # for it, straight from the DB — rather than the points of the pictures
        # that happen to be on screen. With no date window those two questions
        # are different, and the distribution is the one worth asking.
        if self._master_only:
            self._show_species_distribution_map()
            return
        keys = [c.upn_key for c in self._cards] or list(self._collection.all_keys())
        if not keys:
            QMessageBox.information(self, "Map",
                                    "Select a species to plot its pictures.")
            return
        name = self._species_combo.currentData() or "this species"
        old = getattr(self, "_map_dlg", None)
        if old is not None:
            try:
                old.close()
            except RuntimeError:
                pass
        from gui.map_view import TripMapView
        dlg = TripMapView(
            self, self._collection, keys,
            title=f"Map — {name}", loader=self._collection._loader,
            read_only=True,
            read_only_note="Showing where this species was photographed. "
                           "Geotagging a picture from the map is coming separately.")
        self._map_dlg = dlg
        dlg.show()

    def _selected_cameras(self) -> set:
        """The camera filter as a set: {model} when one is picked, else empty."""
        model = self._camera_combo.currentData()
        return {model} if model else set()

    def _populate_camera_combo(self):
        """Fill the camera combo from the loaded collection, preserving the
        current selection across a reload when that body is still present."""
        if self._collection is None:
            return
        prev = self._camera_combo.currentData()
        self._camera_combo.blockSignals(True)
        self._camera_combo.clear()
        self._camera_combo.addItem("All cameras", None)
        # Scope to the selected species so only bodies actually present in the
        # shown pictures are offered (None → all trip cameras before a pick).
        species = self._species_combo.currentData()
        for model in self._collection.available_cameras(species):
            self._camera_combo.addItem(model, model)
        if prev:
            idx = self._camera_combo.findData(prev)
            self._camera_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._camera_combo.blockSignals(False)

    def _on_jpg_only_toggled(self, checked: bool):
        # No pre-extraction needed: the provider loads RAW previews lazily and
        # off the GUI thread as each card requests its thumbnail.
        self._jpg_only = checked
        self._rebuild_cards()

    def _on_color_filter_changed(self, _colors):
        # _rebuild_cards re-reads the bar's selection (durable across reloads).
        self._rebuild_cards()

    def _on_card_changed(self, upn_key: str):
        # The card already wrote the edit + refreshed its own Rate via the
        # provider; the host just re-sorts so the new Rate reorders the grid.
        self._resort_if_needed(focus_key=upn_key)
        self._status.setText(f"Saved {upn_key}")

    def _on_card_cursor(self, upn_key: str):
        prev = self._cards_by_key.get(self._last_clicked_key) if self._last_clicked_key else None
        if prev:
            prev.set_cursor_focus(False)
        self._last_clicked_key = upn_key
        card = self._cards_by_key.get(upn_key)
        if card:
            card.set_cursor_focus(True)

    def _on_card_clicked(self, upn_key: str):
        prev = self._cards_by_key.get(self._last_clicked_key) if self._last_clicked_key else None
        if prev:
            prev.set_cursor_focus(False)
        self._last_clicked_key = upn_key
        card = self._cards_by_key.get(upn_key)
        if card:
            card.set_cursor_focus(True)
        # Local import avoids a circular import: large_image_view imports
        # StarWidget/ColorSelector from this module.
        from gui.large_image_view import LargeImageView
        if not self.fpr or self._collection is None:
            return
        # Single instance: close any open large view first so a fast double-
        # click (or clicking another card) never stacks multiple viewers.
        existing = getattr(self, "_large_view", None)
        if existing is not None:
            try:
                existing.close()
            except RuntimeError:
                pass
            self._large_view = None

        # Navigate a fixed snapshot of the current grid order.
        keys = [c.upn_key for c in self._cards]
        if upn_key not in keys:
            return
        dlg = LargeImageView(self, self._collection, keys, keys.index(upn_key),
                             settings_key=self._geo_key, auto_advance=True,
                             initial_selection=set(self._selected_keys))
        dlg.picture_changed.connect(self._on_large_view_changed)
        dlg.picture_image_changed.connect(self._on_large_view_image_changed)
        dlg.picture_navigated.connect(self._on_large_view_navigated)
        dlg.selection_changed.connect(self._on_large_view_selection_changed)
        self._large_view = dlg
        dlg.finished.connect(self._on_large_view_finished)
        dlg.show()

    def _sync_all_cards(self):
        """Repaint every card from live data — cheap (text/widgets only, no
        thumbnail re-decode)."""
        for card in self._cards:
            card.refresh()

    def _on_large_view_changed(self, key: str):
        # The viewer already wrote the edit via the provider. Repaint EVERY card
        # from live data, not just `key`: one action can change several pictures
        # (auto-advance, Apply-to-selected, the batch dialog) and a single missed
        # picture_changed emit used to leave a card showing a stale colour/Rate
        # until a full rebuild. Refresh is text-only, so this is cheap. Then
        # re-sort so the new Rates reorder the grid.
        self._sync_all_cards()
        self._resort_if_needed(focus_key=key)
        self._status.setText(f"Saved {key}")

    def _on_large_view_finished(self, _=0):
        # Belt-and-braces: even if the very last edit's emit was missed, the grid
        # matches the data once the large view closes.
        self._large_view = None
        self._sync_all_cards()
        self._resort_if_needed(focus_key=self._last_clicked_key)

    def _on_large_view_image_changed(self, key: str):
        # The picture's IMAGE changed (a crop was written), not just its metadata —
        # re-decode the grid card so it shows the applied crop and its '#' marker.
        card = self._cards_by_key.get(key)
        if card:
            card.reload_thumbnail()

    def _on_large_view_navigated(self, key: str):
        prev = self._cards_by_key.get(self._last_clicked_key) if self._last_clicked_key else None
        if prev:
            prev.set_cursor_focus(False)
        self._last_clicked_key = key
        card = self._cards_by_key.get(key)
        if card:
            card.set_cursor_focus(True)
            self._scroll.ensureWidgetVisible(card)

    def _on_large_view_selection_changed(self, keys: set):
        self._selected_keys = keys
        self._bracket_left = None
        self._bracket_right = None
        self._update_selection_display()

    # ── Multi-select ────────────────────────────────────────────────────────

    def _update_selection_display(self):
        for c in self._cards:
            c.set_selected(c.upn_key in self._selected_keys)
        n = len(self._selected_keys)
        self._sel_bar.setVisible(n > 0)
        if n > 0:
            self._sel_label.setText(f"{n} picture{'s' if n != 1 else ''} selected")

    def _selection_from_brackets(self) -> set:
        order = [c.upn_key for c in self._cards]
        left, right = self._bracket_left, self._bracket_right
        if left is None and right is None:
            return set()
        anchor = left if right is None else (right if left is None else None)
        if anchor is not None:
            return {anchor} if anchor in order else set()
        try:
            a, b = order.index(left), order.index(right)
        except ValueError:
            surviving = next((k for k in (left, right) if k in order), None)
            return {surviving} if surviving else set()
        if a > b:
            a, b = b, a
        return set(order[a:b + 1])

    def _clear_selection(self):
        self._selected_keys.clear()
        self._bracket_left = None
        self._bracket_right = None
        self._update_selection_display()

    def _editable_keys(self, keys) -> set:
        """Drop keys that can't be edited here: committed-day trip keys (frozen —
        🔒, change only via Empty) and, in a TRIP, master keys, which are
        read-only reference cards there. In an ALBUM every key is a master key
        and they are precisely the ones that ARE editable — the write goes to the
        master, which is where an album's pictures live."""
        if self._collection is None:
            return set(keys)
        return {k for k in keys
                if (self._album_mode or not self._collection.is_master(k))
                and not self._collection.is_frozen_day(k)}

    def _open_species_dialog_for_selection(self):
        keys = self._editable_keys(self._selected_keys)
        if keys:
            self._open_species_dialog_for_keys(keys)

    def _on_card_context_menu(self, upn_key: str):
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        is_master = (self._collection is not None
                     and self._collection.is_master(upn_key))
        # In a TRIP a master card is a read-only reference sitting beside the
        # trip's own pictures, so it gets the master menu alone, limited to the
        # inventory ops that make sense on a reference (add / delete).
        if is_master and not self._album_mode:
            self._master_context_menu(upn_key, ops={"add", "delete", "delete_file"})
            return
        # In an ALBUM every card is a master picture and this is the ONLY card
        # menu there is, so it carries both halves: this viewer's own actions
        # first, then the full master surface appended (which is what
        # _build_master_menu was written for).
        keys_for_action = (self._selected_keys
                           if upn_key in self._selected_keys and self._selected_keys
                           else {upn_key})
        keys_for_action = self._editable_keys(keys_for_action)
        if not keys_for_action:
            return   # master-only selection — nothing editable
        n = len(keys_for_action)
        menu = QMenu(self)
        lbl = f"{n} picture{'s' if n != 1 else ''}"
        try:
            clicked_item = (self._collection.item_for_key(upn_key)
                            if self._collection is not None else None)
        except (ValueError, KeyError):
            clicked_item = None

        # ONE order, whichever viewer you right-click in: name it, judge it,
        # look at it, describe it, manage its files, delete it. The master-only
        # actions slot into the same blocks rather than being appended in a
        # lump, so the menu doesn't reshuffle as you move between Trip and
        # Album. _build_master_menu is called once per block with just that
        # block's ops; each call appends where we are in the menu.
        master_handlers: dict = {}

        def master_block(*ops):
            """Append this block's master actions (no-op outside album mode)."""
            if is_master and self._album_mode:
                master_handlers.update(
                    self._build_master_menu(menu, upn_key, ops=set(ops)))

        act_species = act_move = act_rate = act_addfiles = act_create_jpg = None
        # 1 — naming. In a trip these are the viewer's own; in an album naming
        # is the master's job (Move to existing species, gated on the JPG rule),
        # and "Set species" has no album equivalent: it would persist through
        # per-day CSVs an album does not own.
        if not self._view_read_only and not self._album_mode:
            act_species = menu.addAction(f"Set species…  ({lbl})")
            act_move = menu.addAction(f"Move to existing species…  ({lbl})")
        master_block("move")

        # 2 — this picture
        if not self._view_read_only:
            act_rate = menu.addAction(f"Set rating…  ({lbl})")
        act_fullres = menu.addAction(f"Full resolution  ({lbl})")
        if hasattr(self._collection, "create_blog_jpg") and not self._album_mode:
            act_create_jpg = menu.addAction(f"Create JPG (embedded + crop)  ({lbl})")
        act_geo = menu.addAction(f"Get location  ({lbl})")

        # 3 — what is written about it. Headline / Description is normally about
        # the ONE picture it titles; the editor offers the whole selection
        # behind a tick (off by default).
        act_hd = None
        if clicked_item is not None and (clicked_item.can_edit_headline
                                         or clicked_item.can_edit_description):
            menu.addSeparator()
            act_hd = menu.addAction(f"Edit headline / description…  ({lbl})")
        master_block("qualifiers")

        # 4 — its files
        if not self._view_read_only and not self._album_mode:
            menu.addSeparator()
            act_addfiles = menu.addAction(f"Add new files (scan folder)…  ({lbl})")
        master_block("add", "pin", "delete_jpg")

        # 5 — removing it
        master_block("delete", "delete_file")
        if (is_master and self._album_mode and not master_handlers
                and self._collection.master_read_only):
            info = menu.addAction(
                "Master is read-only — enable in Repository Settings")
            info.setEnabled(False)
        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return
        if chosen in master_handlers:
            master_handlers[chosen]()
            return
        if chosen == act_species:
            self._open_species_dialog_for_keys(keys_for_action)
        elif chosen == act_move:
            self._move_keys_to_species(keys_for_action)
        elif chosen == act_rate:
            self._open_batch_rate_dialog(keys_for_action)
        elif chosen == act_fullres:
            self._cull_full_res(keys_for_action)
        elif act_create_jpg is not None and chosen == act_create_jpg:
            self._create_blog_jpg(keys_for_action)
        elif chosen == act_geo:
            self._cull_geolocate(keys_for_action)
        elif act_hd is not None and chosen == act_hd:
            self._edit_headline_description(upn_key, keys_for_action)
        elif chosen == act_addfiles:
            self._do_trip_add(keys_for_action)

    def _edit_headline_description(self, upn_key: str, keys: set | None = None):
        """Open the shared Headline / Description editor for the clicked card and
        write + repaint (the 📝 mark follows the description). `keys` (the
        selection) only widens the write when "apply to all" is ticked."""
        if self._collection is None:
            return
        try:
            item = self._collection.item_for_key(upn_key)
        except (ValueError, KeyError):
            return
        items = [item]
        for k in (keys or set()):
            if k == upn_key:
                continue
            try:
                items.append(self._collection.item_for_key(k))
            except (ValueError, KeyError):
                pass
        from gui.headline_desc_dialog import (edit_headline_description,
                                              apply_headline_description_to)
        values = edit_headline_description(
            self, title=item.display_name or upn_key,
            headline=item.headline, description=item.description,
            can_edit_headline=item.can_edit_headline,
            can_edit_description=item.can_edit_description,
            n_selected=len(items))
        written = apply_headline_description_to(items, values, parent=self)
        for w in written:
            card = self._cards_by_key.get(w.key)
            if card is not None:
                card.refresh()
        if written:
            self._status.setText(
                f"Saved headline / description for {len(written)} picture(s)"
                if len(written) != 1 else
                f"Saved headline / description for {upn_key}")

    def _do_trip_add(self, keys: set):
        """Scan each selected TRIP picture's folder for files that belong to it
        but aren't tracked yet (a freshly-rendered DxO JPG + its .dop) and pull
        them into the day. The master equivalent is _do_master_add."""
        if self._collection is None or not keys:
            return
        total_jpg = total_meta = 0
        added_any = False
        for key in keys:
            try:
                res = self._collection.add_trip_files(key)
            except Exception as e:
                logger.error(f"Add files failed for {key}: {e}", exc_info=True)
                continue
            if res.get("added"):
                added_any = True
            total_jpg += res.get("jpg", 0)
            total_meta += res.get("meta", 0)
        if not added_any:
            self._status.setText(
                "No new files found in the picture's folder — nothing to add.")
            return
        parts = []
        if total_jpg:
            parts.append(f"{total_jpg} JPG" + ("s" if total_jpg != 1 else ""))
        if total_meta:
            parts.append(f"{total_meta} metafile" + ("s" if total_meta != 1 else ""))
        noun = "picture" if len(keys) == 1 else "pictures"
        self._status.setText(f"Added {' and '.join(parts)} to the {noun}.")
        self._rebuild_cards()

    def _open_species_dialog_for_keys(self, keys: set):
        if not self.fpr or not keys:
            return
        from gui.species_editor import SpeciesInfoPanel
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        file_paths, species_names, error_msgs = [], [], []
        for key in keys:
            path = self._rep_file_path(key)
            if path:
                file_paths.append(path)
                species_names.append(self.fpr.getSpeciesName(path.name) or "")
                error_msgs.append(self.fpr.getErrorMsg(path.name) or "")
        if not file_paths:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Set species — {len(file_paths)} picture{'s' if len(file_paths) != 1 else ''}")
        dlg.setMinimumWidth(600)
        dlg_layout = QVBoxLayout(dlg)
        panel = SpeciesInfoPanel(self.fpr, file_paths, species_names, error_msgs, parent=dlg)
        # Same Trip-scope persistence caveat as Move-to-species: the panel's
        # updateSubjects can't write the merged path-less CSVs, so route through
        # the per-day objects here.
        panel.committed.connect(lambda upns: (
            self._collection.persist_after_species_change(upns)
            if self._collection is not None else None,
            self._clear_selection(), dlg.accept()))
        dlg_layout.addWidget(panel)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(cancel_btn)
        dlg_layout.addLayout(row)
        dlg.show()

    # ── Move a selection to an existing species ───────────────────────────────
    def _move_selection_to_species(self):
        keys = self._editable_keys(self._selected_keys)
        if keys:
            self._move_keys_to_species(keys)

    def _move_keys_to_species(self, keys: set):
        """Reassign the given pictures to a species that already exists in the
        trip's species list (species.csv). Unlike 'Set species…' this offers no
        new-species / GBIF path — the target is picked from the known list, which
        is exactly the 'two swans mislabelled among the ducks' fix."""
        if not self.fpr or not keys:
            return
        from gui.species_editor import _sc_usable
        sc = self.fpr.sc
        if not _sc_usable(sc):
            QMessageBox.information(
                self, "Move to species",
                "The species list isn't loaded here, so I can't offer existing "
                "species to move to. Use “Set species…” instead.")
            return
        names = self._trip_species_names()
        if not names:
            QMessageBox.information(self, "Move to species",
                                    "No species are recorded on this trip yet.")
            return

        file_paths = [p for k in keys if (p := self._rep_file_path(k))]
        if not file_paths:
            return

        chosen = self._ask_existing_species(names, len(file_paths))
        if not chosen:
            return
        # Constrain to species already on THIS trip (the master viewer will later
        # allow any species ever seen). A typed name that isn't a trip species is
        # rejected rather than silently added — that's the "Set species…" path.
        if chosen.casefold() not in {n.casefold() for n in names}:
            QMessageBox.warning(
                self, "Not a trip species",
                f"“{chosen}” isn’t recorded on this trip, so I didn’t move "
                f"anything.\n\nWithin a trip you can only move pictures to a "
                f"species you already have here — use “Set species…” to introduce "
                f"a new one.")
            return
        updated_upns, files_written = self.fpr.updateSubjects(file_paths, chosen)
        # Trip scope: fpr.fi/upn are merged path-less objects, so updateSubjects
        # can't persist itself — route through the per-day CSVs (shared rows).
        if self._collection is not None:
            self._collection.persist_after_species_change(updated_upns)
        from gui.culling_species import remember_recent_species
        remember_recent_species(chosen)
        n = len(updated_upns)
        self._status.setText(
            f"Moved {n} picture{'s' if n != 1 else ''} to {chosen} "
            f"({files_written} file{'s' if files_written != 1 else ''} rewritten).")
        self._clear_selection()
        self._rebuild_cards()

    def _trip_species_names(self) -> list[str]:
        """Every species name (NL / EN / scientific) referenced by pictures in the
        loaded trip — the allowed Move-to targets. Within a trip you may only move
        a picture to a species already recorded here; the master viewer (later)
        will allow any species ever seen. All three languages are offered so the
        picker's search works whatever the display language."""
        names: set[str] = set()
        for row in self.fpr.upn.UniquePictureName_dict.values():
            for field in ("NLSoortNaam", "EngSoortNaam", "WetSoortNaam"):
                v = (row.get(field) or "").strip()
                if v:
                    names.add(v)
        return sorted(names, key=str.casefold)

    def _ask_existing_species(self, names: list[str], n_pictures: int) -> str | None:
        """Small modal picker: an editable, contains-match combo over existing
        species names. Returns the chosen name, or None on cancel."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox, QCompleter
        dlg = QDialog(self)
        dlg.setWindowTitle("Move to existing species")
        dlg.setMinimumWidth(420)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(
            f"Move {n_pictures} picture{'s' if n_pictures != 1 else ''} to:"))
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(names)
        combo.setCurrentIndex(-1)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.lineEdit().setPlaceholderText("Type a Dutch, English or scientific name…")
        # A dedicated popup (dropdown) completer — NOT the combo's built-in inline
        # one, whose inline completion garbles the field under MatchContains
        # (it appends the match instead of replacing: "dwergaals" → "dwergaalse
        # dwergaalscholver"). PopupCompletion shows a list and inserts cleanly.
        completer = QCompleter(names, dlg)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        combo.setCompleter(completer)
        lay.addWidget(combo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        combo.setFocus()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return combo.currentText().strip() or None

    def _open_batch_rate_dialog(self, keys: set):
        if not keys or self._collection is None:
            return
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox
        n = len(keys)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Set rating / color — {n} picture{'s' if n != 1 else ''}")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(f"Apply to {n} picture{'s' if n != 1 else ''}:"))

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
        if dlg.exec() == QDialog.DialogCode.Accepted:
            stars = star_w.rating
            color = color_w.color
            key_list = list(keys)
            progress = QProgressDialog("Updating…", None, 0, len(key_list), self)
            progress.setWindowTitle("Updating pictures")
            progress.setMinimumDuration(0)
            progress.show()
            for i, key in enumerate(key_list):
                # Throttle UI refresh: repainting the progress dialog every
                # iteration costs more than the work itself for large batches.
                if i % 10 == 0:
                    progress.setValue(i)
                    progress.setLabelText(f"Updating {i + 1} of {len(key_list)}…")
                    QApplication.processEvents()
                try:
                    # ONE apply, not two. set_rating() followed by set_color()
                    # spawns exiftool twice per picture at ~270 ms a spawn;
                    # apply() has always taken both values in one write.
                    apply = getattr(self._collection, "apply", None)
                    if apply is not None:
                        apply(key, new_stars=stars, new_color=(color or None))
                    else:
                        item = self._collection.item(self._collection.index_of(key))
                        item.set_rating(stars)
                        if color:
                            item.set_color(color)
                    card = self._cards_by_key.get(key)
                    if card:
                        card.refresh()
                except (ValueError, IndexError):
                    pass
            progress.setValue(len(key_list))
            self._resort_if_needed()

    def _rep_file_path(self, upn_key: str) -> Path | None:
        """Representative RAW/XMP file path for a UPN — needed by SpeciesInfoPanel."""
        row = self.fpr.upn.UniquePictureName_dict.get(upn_key, {})
        files = row.get("Files", [])
        if not isinstance(files, list):
            return None
        raw_exts_set = RAW  # canonical table — see utils/raw_extensions.py
        fallback = None
        for fname in files:
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            p = Path(frow.get("WindowsFileName", ""))
            if not p.exists():
                continue
            if ("." + frow.get("WindowsExtension", "").lower()) in raw_exts_set:
                return p
            if fallback is None:
                fallback = p
        return fallback

    def _cull_set_cursor(self, key):
        prev = self._cards_by_key.get(self._last_clicked_key) if self._last_clicked_key else None
        if prev:
            prev.set_cursor_focus(False)
        self._last_clicked_key = key
        c = self._cards_by_key.get(key)
        if c:
            c.set_cursor_focus(True)
            self._scroll.ensureWidgetVisible(c)

    def _cull_refresh_key(self, key):
        c = self._cards_by_key.get(key)
        if c:
            c.refresh()

    def _cull_keys_target(self):
        """Selection if any, else the focused picture — what 's'/'c' act on."""
        return set(self._selected_keys) or (
            {self._last_clicked_key} if self._last_clicked_key else set())

    def _cull_open_species(self):
        keys = self._cull_keys_target()
        if keys:
            self._open_species_dialog_for_keys(keys)

    def _cull_open_rating(self):
        keys = self._cull_keys_target()
        if keys:
            self._open_batch_rate_dialog(keys)

    def _cull_full_res(self, keys=None):
        """Pre-decode the selection (else focused picture) to the full-res cache,
        skipping Blue/Purple."""
        keys = keys if keys else self._cull_keys_target()
        raws = []
        for k in keys:
            try:
                item = self._collection.item_for_key(k)
            except (ValueError, KeyError):
                continue
            if (item.color or "").capitalize() in ("Blue", "Purple"):
                continue
            r = self._collection.raw_path(k)
            if r:
                raws.append(r)
        if not raws:
            logger.info("Full resolution: nothing to decode (no RAWs / all Blue or Purple).")
            return
        from gui.fullres import run_fullres_batch
        run_fullres_batch(self, raws)

    def _create_blog_jpg(self, keys=None):
        """Bake a website-ready JPG beside each selected RAW from its embedded
        preview + crop (no scaling) — a quick blog picture. Skips a picture that
        already has a JPG so nothing is clobbered."""
        if not hasattr(self._collection, "create_blog_jpg"):
            return
        keys = keys if keys else self._cull_keys_target()
        order = [c.upn_key for c in self._cards]
        ordered = [k for k in order if k in keys] or list(keys)
        created_paths, skipped, no_raw, failed, made = [], 0, 0, 0, []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for k in ordered:
                res = self._collection.create_blog_jpg(k)
                st = res.get("status")
                if st == "created":
                    created_paths.append(res.get("jpg"))
                    made.append(k)
                elif st == "skipped":
                    skipped += 1
                elif st == "no_raw":
                    no_raw += 1
                else:
                    failed += 1
        finally:
            QApplication.restoreOverrideCursor()
        for k in made:
            card = self._cards_by_key.get(k)
            if card:
                card.refresh()
                try:
                    card.reload_thumbnail()   # the card now shows the baked JPG
                except (AttributeError, RuntimeError):
                    pass
        from gui.create_jpg_result import show_create_jpg_result
        show_create_jpg_result(self, created_paths, skipped, no_raw, failed)

    def _cull_geolocate(self, keys=None):
        """Reverse-geocode the selection (else focused picture) in display order
        and write the location into each picture's XMP."""
        keys = keys if keys else self._cull_keys_target()
        order = [c.upn_key for c in self._cards]
        ordered = [k for k in order if k in keys] or list(keys)
        targets = []
        for k in ordered:
            try:
                item = self._collection.item_for_key(k)
            except (ValueError, KeyError):
                continue
            src = self._collection.gps_source(k)        # path only — batch reads GPS off-thread
            files = self._collection.location_targets(k)
            if src and files:
                targets.append((item.display_name or k, src, files))
        if not targets:
            logger.info("Get location: no GPS / writable files in the selection.")
            return
        from gui.geolocate import run_geolocate_batch
        run_geolocate_batch(self, targets)

    def keyPressEvent(self, event):
        from gui.viewer_keys import handle_cull_key, handle_grid_arrows, text_input_focused
        if handle_cull_key(event, order=[c.upn_key for c in self._cards],
                           current_key=self._last_clicked_key,
                           item_for_key=self._collection.item_for_key,
                           set_cursor=self._cull_set_cursor,
                           refresh_key=self._cull_refresh_key,
                           open_species=self._cull_open_species,
                           open_rating=self._cull_open_rating):
            event.accept()
            return
        if handle_grid_arrows(event, order=[c.upn_key for c in self._cards],
                              current_key=self._last_clicked_key,
                              columns=self.columns, set_cursor=self._cull_set_cursor):
            event.accept()
            return
        if event.key() == Qt.Key.Key_F and not text_input_focused():
            self._cull_full_res()
            event.accept()
            return
        if event.key() == Qt.Key.Key_L and not text_input_focused():
            self._cull_geolocate()
            event.accept()
            return
        if (event.key() == Qt.Key.Key_A
                and (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                and not text_input_focused()):
            self._selected_keys = {c.upn_key for c in self._cards}
            self._update_selection_display()
            event.accept()
            return
        if (event.key() == Qt.Key.Key_D
                and (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                and not text_input_focused()):
            self._clear_selection()
            event.accept()
            return
        k = event.key()
        if k == Qt.Key.Key_BracketLeft:          # [ — set left bracket at cursor
            if self._last_clicked_key:
                self._bracket_left = self._last_clicked_key
                self._selected_keys = self._selection_from_brackets()
                self._update_selection_display()
        elif k == Qt.Key.Key_BracketRight:       # ] — set right bracket at cursor
            if self._last_clicked_key:
                self._bracket_right = self._last_clicked_key
                self._selected_keys = self._selection_from_brackets()
                self._update_selection_display()
        elif k == Qt.Key.Key_Escape:
            if self._selected_keys:
                self._clear_selection()
            else:
                super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    # ── Re-sort ─────────────────────────────────────────────────────────────

    def _resort_if_needed(self, focus_key: str | None = None):
        """Re-insert cards into the grid according to the collection's current
        sort order (writes happen in the provider; this only reorders widgets).
        When `focus_key` is given, scroll that card back into view after the
        re-sort so an edited card isn't lost when its new Rate moves it."""
        if self._collection is None:
            return
        filter_name = self._species_combo.currentData()
        self._rebuild_collection(filter_name)
        new_order = [self._collection.item(i).key for i in range(len(self._collection))]
        old_order = [c.upn_key for c in self._cards]
        if old_order == new_order:
            return
        # A re-sort only re-places widgets it already has. If the query came
        # back with keys we hold no card for, the grid would end up with holes
        # (or empty) — rebuild from scratch instead of quietly dropping them.
        if any(key not in self._cards_by_key for key in new_order):
            self._rebuild_cards()
            return

        cards_by_key = {c.upn_key: c for c in self._cards}

        # Remove items from layout positions only; widgets remain children of
        # _grid_widget so no GC is triggered and no Qt object is deleted.
        while self._grid.count():
            self._grid.takeAt(0)

        placement = self._grid_placement(len(new_order))
        self._cards.clear()
        for idx, key in enumerate(new_order):
            if key not in cards_by_key:
                continue
            c = cards_by_key[key]
            r, col, span, _ = placement[idx]  # card_w unchanged; thumbnail stays as-is
            self._grid.addWidget(c, r, col, 1, span)
            self._cards.append(c)

        focus_card = cards_by_key.get(focus_key) if focus_key else None
        if focus_card is not None:
            QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(focus_card))
        else:
            QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(0))
