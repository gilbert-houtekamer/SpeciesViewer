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
    QLabel, QFrame, QPushButton, QSpinBox, QComboBox, QSizePolicy,
    QProgressDialog, QRadioButton, QButtonGroup, QMessageBox, QStyle,
)
from PyQt6.QtGui import QPixmap, QImage, QGuiApplication, QIcon
from PyQt6.QtCore import (Qt, QTimer, QSize, pyqtSignal, QObject, QRunnable,
                          QThreadPool)

from engine.filepicture import FilePicture
from engine.filecsv import Files
from engine.picturecsv import Pictures
from engine.exif_writer import exiftool_write
from gui.window_state import restore_window, save_window
from gui.image_loader import AsyncImageLoader
from gui.picture_provider import PictureItem
from utils.config import _config

logger = logging.getLogger("speciesviewer")

COLORS = ["Red", "Yellow", "Green", "Blue", "Purple"]
COLOR_HEX = {
    "Red":    "#FF4C4C",
    "Yellow": "#FFD700",
    "Green":  "#32CD32",
    "Blue":   "#1E90FF",
    "Purple": "#9370DB",
}


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
                rating_str = "3"
            elif label == "Yellow":
                rating_str = "2"
            elif label == "Green":
                rating_str = "0"

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

    def _refresh(self):
        for i, btn in enumerate(self._buttons):
            btn.setText("★" if i < self._rating else "☆")

    @property
    def rating(self) -> int:
        return self._rating

    def set_rating(self, n: int):
        n = max(0, min(5, n))
        if self._rating == n:
            return
        self._rating = n
        self._refresh()


class ColorSelector(QWidget):
    color_changed = pyqtSignal(str)  # emits color name, or "" when cleared

    def __init__(self, initial_color: str = "", parent=None):
        super().__init__(parent)
        self._color = initial_color
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self._buttons: dict[str, QPushButton] = {}
        for name in COLORS:
            hc = COLOR_HEX[name]
            btn = QPushButton()
            btn.setFixedSize(16, 16)
            btn.setCheckable(True)
            btn.setToolTip(name)
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
            layout.addWidget(btn)
            self._buttons[name] = btn
        self._refresh()

    def _on_click(self, name: str):
        self._color = "" if self._color == name else name
        self._refresh()
        self.color_changed.emit(self._color)

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

    def __init__(self, item: PictureItem, card_width: int, parent=None,
                 *, show_status: bool = False, show_kind_tag: bool = False):
        super().__init__(parent)
        self.setObjectName("PictureCard")
        self._item = item
        self.upn_key = item.key
        self._selected = False
        self._cursor = False
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
        self._desc_badge = None
        self._gps_badge = None

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

        # Species name
        self._lbl_name = QLabel(item.display_name or item.key)
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
            if from_album:
                tag.setStyleSheet("color: #2e7d32; font-size: 8px; "
                                  "font-style: italic; font-weight: bold;")
            else:
                tag.setStyleSheet("color: #888; font-size: 8px; font-style: italic;")
            vbox.addWidget(tag)

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
        else:
            self._color_sel.setEnabled(False)
        if item.has_jpg:
            self._color_sel.set_disabled_colors(("Blue", "Purple"), _DISCARD_GREYED_TIP)
        vbox.addWidget(self._color_sel)

        # key + file types (small)
        exts = item.file_exts
        key_line = item.key if not exts else f"{item.key}  {' · '.join(exts)}"
        lbl_key = QLabel(key_line)
        lbl_key.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_key.setStyleSheet("font-size: 9px; color: #aaa;")
        lbl_key.setToolTip(item.key)
        vbox.addWidget(lbl_key)

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

            self._desc_badge = QLabel()
            self._desc_badge.setStyleSheet("font-size: 14px;")
            self._desc_badge.setFixedSize(20, 20)
            self._desc_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
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

    def _refresh_status_badges(self):
        """Set the status icon + description/GPS badges from the live item.
        Re-callable after an edit (species commit, GPS fix)."""
        if not self._show_status:
            return
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

        desc = item.description
        self._desc_badge.setText("📝" if desc else "")
        self._desc_badge.setToolTip(desc or "")

        if item.has_gps:
            self._gps_badge.setText("📍")
            self._gps_badge.setToolTip("GPS coordinates present.")
        else:
            self._gps_badge.setText("❓")
            self._gps_badge.setToolTip("Missing GPS coordinates — fix in Lightroom.")

    def refresh(self):
        """Re-read every field from the (live) item and repaint — used after an
        external edit such as a species commit or a GPS backfill."""
        self._lbl_name.setText(self._item.display_name or self._item.key)
        self._frame_color = self._item.color
        self._update_thumb_style()
        self._stars.set_rating(self._item.rating)
        self._color_sel.set_color(self._item.color)
        self.update_rate_display(self._item.rate)
        self._refresh_location_line()
        self._refresh_status_badges()

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

    def update_rate_display(self, new_rate):
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
        parts = []
        if not has_pix:
            parts.append("background-color: #2a2a2a; color: #ccc; font-size: 10px;")
        hexc = COLOR_HEX.get((self._frame_color or "").capitalize())
        if hexc:
            parts.append(f"border: 3px solid {hexc}; border-radius: 4px;")
        self._thumb.setStyleSheet(" ".join(parts))

    def _update_frame_style(self):
        if self._selected:
            self.setStyleSheet(
                "QFrame#PictureCard { border: 2px solid #2196f3; "
                "background: rgba(33, 150, 243, 0.08); }")
        elif self._cursor:
            bg = "background: #e2e2e2; " if self._from_master else ""
            self.setStyleSheet(f"QFrame#PictureCard {{ border: 1px solid #333; {bg}}}")
        elif self._from_master:
            # Light-grey card so reference pictures read as "existing master".
            self.setStyleSheet(
                "QFrame#PictureCard { background: #ececec; border: 1px solid #cfcfcf; }")
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
        else:
            # +6 leaves room for the 3px frame border so the pixmap isn't clipped
            self._thumb.setFixedHeight(px.height() + 6)
            self._thumb.setPixmap(px)
        self._update_thumb_style()


def _safe_int(s, default: int = 0) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return default


# ── Main dialog ────────────────────────────────────────────────────────────────

class RatingEditorDialog(QDialog):
    """Scrollable grid of picture cards with editable stars and color labels.

    Opened from the Trip menu. Shows pictures for one day (Day scope) or for
    the whole committed trip (Trip scope), filtered by species, sorted by
    computed Rate descending.
    """

    def __init__(self, parent, trip_root_dir: str, day_str: str = None,
                 read_only: bool = False, scope_label: str = "Trip"):
        super().__init__(parent)
        # read_only: a whole-collection viewer (no rating/colour edits, no
        # delete, no Trip+Master compare). Used by the Album (virtual-trip)
        # variant, whose pictures are the master's shared files — editing them
        # would be a master write-back, deliberately deferred.
        self._read_only = bool(read_only)
        self._scope_label = scope_label
        self.setWindowTitle("Rating Editor — " + scope_label if read_only else "Rating Editor")
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        self._trip_root = Path(trip_root_dir)
        self._initial_day = day_str
        self.fpr: FilePicture | None = None
        self.columns = 3
        # Restore per-machine remembered geometry + column count (.ini).
        self._geo_key = "RatingEditor"
        _st = restore_window(self, self._geo_key, default_size=(1000, 720))
        if _st["cols"]:
            self.columns = max(1, min(8, _st["cols"]))
        self._cards: list[PictureCard] = []
        self._cards_by_key: dict[str, PictureCard] = {}
        self._jpg_only = True
        self._species_lang = "NL"
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
        self._tm_radio = QRadioButton("Master" if self._read_only else "Trip+Master")
        self._tm_radio.setToolTip(
            "Show this species' complete master collection (every date), read-only"
            if self._read_only else
            "Also show this species' existing master pictures (read-only, greyed) "
            "alongside the trip's, to judge whether a new shot is website-worthy")
        self._scope_grp = QButtonGroup(self)
        self._scope_grp.addButton(self._day_radio)
        self._scope_grp.addButton(self._trip_radio)
        self._scope_grp.addButton(self._tm_radio)
        (self._day_radio if day_str else self._trip_radio).setChecked(True)
        self._scope_grp.buttonToggled.connect(self._on_scope_toggled)
        top.addWidget(QLabel("Scope:"))
        top.addWidget(self._day_radio)
        top.addWidget(self._trip_radio)
        top.addWidget(self._tm_radio)
        top.addSpacing(8)

        # Day picker (visible only in Day scope)
        self._day_combo = QComboBox()
        self._day_combo.setMinimumWidth(120)
        self._day_combo.currentIndexChanged.connect(self._on_day_changed)
        top.addWidget(self._day_combo)
        top.addSpacing(12)

        top.addWidget(QLabel("Species:"))
        self._species_combo = QComboBox()
        self._species_combo.setMinimumWidth(200)
        self._species_combo.currentIndexChanged.connect(self._on_species_changed)
        top.addWidget(self._species_combo)
        self._lang_combo = QComboBox()
        self._lang_combo.addItems(["NL", "EN", "Sci"])
        self._lang_combo.setFixedWidth(55)
        self._lang_combo.setToolTip("Species name language")
        self._lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        top.addWidget(self._lang_combo)
        top.addStretch()

        self._jpg_btn = QPushButton("JPG only")
        self._jpg_btn.setCheckable(True)
        self._jpg_btn.setChecked(True)
        self._jpg_btn.setToolTip("Show only pictures that have a JPG file")
        self._jpg_btn.setStyleSheet("""
            QPushButton { border: 1px solid #aaa; border-radius: 3px; padding: 2px 6px; }
            QPushButton:checked { background-color: #c8e6c9; border-color: #4caf50; }
        """)
        self._jpg_btn.toggled.connect(self._on_jpg_only_toggled)
        top.addWidget(self._jpg_btn)
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

        # Album (read-only) viewer never deletes — the files are shared master
        # files. Hide the cull buttons entirely.
        if self._read_only:
            self._del_blue_btn.hide()
            self._del_purple_btn.hide()

        main_layout.addLayout(top)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status = QLabel("Initializing…")
        self._status.setStyleSheet("color: #555; font-size: 10px;")
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
        _sel_species_btn = QPushButton("Set species…")
        _sel_species_btn.clicked.connect(self._open_species_dialog_for_selection)
        _sb_layout.addWidget(_sel_species_btn)
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
        super().closeEvent(event)

    # ── Batch delete by colour ────────────────────────────────────────────────

    def _batch_delete_color(self, color: str):
        if not self.fpr or self._collection is None:
            return
        wanted = color.lower()
        matching = [k for k, row in self.fpr.upn.UniquePictureName_dict.items()
                    if (row.get("Label", "") or "").lower() == wanted
                    and not self._collection.is_master(k)]
        if not matching:
            QMessageBox.information(self, f"No {color} pictures",
                                    f"No {color.lower()} trip pictures found.")
            return
        protected = sum(1 for k in matching if self._collection.jpg_path(k))
        deletable = len(matching) - protected
        # Describe the scope clearly — trip-scope ignores the species filter, so
        # the count may be higher than what is visible in the current grid.
        if self._day_radio.isChecked():
            scope = f"this day ({self._current_day() or '?'})"
        elif self._species_combo.currentData():
            scope = "this trip — all species"
        else:
            scope = "this trip"
        n = len(matching)
        lines = [f"{n} {color.lower()} picture{'s' if n != 1 else ''} in {scope}."]
        if protected:
            lines.append(f"Skipped (have JPG): {protected}")
        lines.append(f"Will be moved to .trash: {deletable}")
        msg = "\n".join(lines)
        if deletable == 0:
            QMessageBox.information(self, f"Delete {color}", msg)
            return
        if QMessageBox.question(
                self, f"Delete {color} pictures", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return
        day_hint = self._current_day() or "" if self._day_radio.isChecked() else ""
        from gui.rating_provider import RatingEditorCollection
        if not isinstance(self._collection, RatingEditorCollection):
            return
        _total, _prot, deleted = self._collection.delete_by_color(
            color, self._trip_root / ".trash", day_hint=day_hint)
        logger.info(f"Batch delete {color}: {deleted} picture(s) moved to trash.")
        self._status.setText(
            f"Deleted {deleted} {color.lower()} picture{'s' if deleted != 1 else ''} "
            f"(moved to .trash).")
        self._rebuild_cards()

    # ── Initialisation helpers ────────────────────────────────────────────────

    def _init_day_combo(self):
        review_root = self._trip_root / "metadata" / "review"
        days = []
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
        try:
            if scope_day:
                day = self._current_day()
                if not day:
                    self._status.setText("No day selected.")
                    return
                ok = self.fpr.readTripDay(str(self._trip_root), day)
                if not ok:
                    self._status.setText(f"No data found for {day}.")
                    return
                self._status.setText(f"Day: {day}")
            else:
                self._load_trip_scope()   # Trip and Trip+Master both load the full trip
        except Exception as e:
            self._status.setText(f"Load error: {e}")
            logger.error(f"RatingEditorDialog load error: {e}", exc_info=True)
            return

        # Build the provider collection over the freshly-loaded data. Local
        # import avoids a circular import (rating_provider imports helpers here).
        from gui.rating_provider import RatingEditorCollection
        self._collection = RatingEditorCollection(
            self.fpr, self._day_objects, self._upn_day_map,
            jpg_only=self._jpg_only, species_lang=self._species_lang,
            loader=self._loader, view_read_only=self._read_only,
        )
        self._populate_species_combo(restore_selection=saved_species)
        if tripmaster:
            self._attach_master()

    def _attach_master(self):
        """Open the master DB for Trip+Master and apply it to the live
        collection. Plan B: the master CSVs are mirrored into a per-machine
        SQLite cache (NOT in Dropbox); after the first cold rebuild every
        subsequent open is near-instant. Kept synchronous on purpose — a
        worker thread here proved fragile (QThread teardown crashes), and the
        warm path is essentially free."""
        if not self._tm_radio.isChecked() or self._collection is None:
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
        self._apply_master(self._master_db)

    def _apply_master(self, db):
        if self._collection is None or not self._tm_radio.isChecked():
            return
        # Master writes are opt-in per machine (default read-only — e.g. a
        # laptop without the full master). See Repository Settings.
        read_only = _config.get(
            "master", "readonly", fallback="yes").strip().lower() != "no"
        # Album viewer's "Master" scope: show the species' complete master set
        # (full=True) and force read-only regardless of the master toggle.
        full = self._read_only
        self._collection.set_master(
            db, self._master_root, include=True,
            read_only=read_only or full, full=full)
        ro_note = " — master is read-only" if (read_only or full) else " — master editable"
        if self._species_combo.currentData() is not None:
            self._rebuild_cards()
        else:
            n_master = db.picture_count()
            label = "Master" if full else "Trip + master"
            self._status.setText(
                f"{label} ready ({n_master} master pictures{ro_note}) "
                f"— select a species.")

    def _load_trip_scope(self):
        """Load all review-day CSVs into fpr, keeping per-day objects for write-back."""
        review_root = self._trip_root / "metadata" / "review"
        days: list[str] = []
        if review_root.exists():
            days = sorted(
                d.name for d in review_root.iterdir()
                if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", d.name)
            )

        self._day_objects = {}
        self.fpr.fi = Files(None)
        self.fpr.upn = Pictures(None)

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
        self._status.setText(f"Trip — {n_days} day{'s' if n_days != 1 else ''}, {n_pics} pictures total")

    def _species_name(self, upn_row: dict) -> str:
        lang = self._species_lang
        if lang == "EN":
            return (upn_row.get("EngSoortNaam") or upn_row.get("NLSoortNaam")
                    or upn_row.get("WetSoortNaam") or "").strip()
        if lang == "Sci":
            return (upn_row.get("WetSoortNaam") or upn_row.get("NLSoortNaam")
                    or upn_row.get("EngSoortNaam") or "").strip()
        return (upn_row.get("NLSoortNaam") or upn_row.get("EngSoortNaam")
                or upn_row.get("WetSoortNaam") or "").strip()

    def _populate_species_combo(self, restore_selection=None):
        self._species_combo.blockSignals(True)
        self._species_combo.clear()
        self._species_combo.addItem("— Select a species —", None)

        names: set[str] = set()
        for upn_row in self.fpr.upn.UniquePictureName_dict.values():
            name = self._species_name(upn_row)
            if name:
                names.add(name)
        for name in sorted(names, key=str.casefold):
            self._species_combo.addItem(name, name)

        self._species_combo.blockSignals(False)
        if restore_selection:
            idx = self._species_combo.findData(restore_selection)
            if idx >= 0:
                self._species_combo.setCurrentIndex(idx)  # triggers _rebuild_cards via signal
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
            n_species = self._species_combo.count() - 1  # subtract placeholder
            self._status.setText(f"{n_species} species loaded — select one to view pictures.")
            return
        self._collection.set_species_lang(self._species_lang)
        self._collection.set_jpg_only(self._jpg_only)
        self._collection.rebuild(filter_name)
        n = len(self._collection)
        placement = self._grid_placement(n)
        for c in range(self.columns):
            self._grid.setColumnStretch(c, 1)

        show_kind_tag = self._tm_radio.isChecked()
        for idx in range(n):
            item = self._collection.item(idx)
            r, col, span, card_w = placement[idx]
            card = PictureCard(item, card_w, show_kind_tag=show_kind_tag)   # async thumb
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
        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(0))

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

    def _on_species_changed(self):
        self._rebuild_cards()

    def _on_lang_changed(self, _idx: int):
        old_selection = self._species_combo.currentData()  # name in old language, or None
        self._species_lang = self._lang_combo.currentText()
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

    def _on_jpg_only_toggled(self, checked: bool):
        # No pre-extraction needed: the provider loads RAW previews lazily and
        # off the GUI thread as each card requests its thumbnail.
        self._jpg_only = checked
        self._rebuild_cards()

    def _on_card_changed(self, upn_key: str):
        # The card already wrote the edit + refreshed its own Rate via the
        # provider; the host just re-sorts so the new Rate reorders the grid.
        self._resort_if_needed()
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
                             settings_key="RatingEditor", auto_advance=True,
                             initial_selection=set(self._selected_keys))
        dlg.picture_changed.connect(self._on_large_view_changed)
        dlg.picture_navigated.connect(self._on_large_view_navigated)
        dlg.selection_changed.connect(self._on_large_view_selection_changed)
        self._large_view = dlg
        dlg.finished.connect(lambda _=0: setattr(self, "_large_view", None))
        dlg.show()

    def _on_large_view_changed(self, key: str):
        # The viewer already wrote the edit via the provider; repaint that card
        # and re-sort the grid so the new Rate reorders it.
        card = self._cards_by_key.get(key)
        if card:
            card.refresh()
        self._resort_if_needed()
        self._status.setText(f"Saved {key}")

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
        """Drop master reference keys — they're read-only (v1)."""
        if self._collection is None:
            return set(keys)
        return {k for k in keys if not self._collection.is_master(k)}

    def _open_species_dialog_for_selection(self):
        keys = self._editable_keys(self._selected_keys)
        if keys:
            self._open_species_dialog_for_keys(keys)

    def _on_card_context_menu(self, upn_key: str):
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        # Master cards get their own add/delete menu (Phase 3 master mutate).
        if self._collection is not None and self._collection.is_master(upn_key):
            self._master_context_menu(upn_key)
            return
        keys_for_action = (self._selected_keys
                           if upn_key in self._selected_keys and self._selected_keys
                           else {upn_key})
        keys_for_action = self._editable_keys(keys_for_action)
        if not keys_for_action:
            return   # master-only selection — nothing editable
        n = len(keys_for_action)
        menu = QMenu(self)
        lbl = f"{n} picture{'s' if n != 1 else ''}"
        act_species = menu.addAction(f"Set species…  ({lbl})")
        act_rate = menu.addAction(f"Set rating…  ({lbl})")
        act_fullres = menu.addAction(f"Full resolution  ({lbl})")
        act_geo = menu.addAction(f"Get location  ({lbl})")
        chosen = menu.exec(QCursor.pos())
        if chosen == act_species:
            self._open_species_dialog_for_keys(keys_for_action)
        elif chosen == act_rate:
            self._open_batch_rate_dialog(keys_for_action)
        elif chosen == act_fullres:
            self._cull_full_res(keys_for_action)
        elif chosen == act_geo:
            self._cull_geolocate(keys_for_action)

    # ── master add/delete context menu ────────────────────────────────────────
    def _master_context_menu(self, upn_key: str):
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        col = self._collection
        if col is None:
            return
        if col.master_read_only:
            menu = QMenu(self)
            info = menu.addAction("Master is read-only — enable in Repository Settings")
            info.setEnabled(False)
            menu.exec(QCursor.pos())
            return
        menu = QMenu(self)
        act_add = menu.addAction("Add new files for this picture (scan folder)")
        act_del = None
        file_acts: dict = {}
        if col.master_upn_deletable(upn_key):
            menu.addSeparator()
            act_del = menu.addAction("Delete picture (move to trash)")
            nonjpg = col.master_nonjpg_files(upn_key)
            if len(nonjpg) > 1:
                sub = menu.addMenu("Delete single file")
                for fn in nonjpg:
                    file_acts[sub.addAction(fn)] = fn
        elif col.master_upn_has_jpg(upn_key):
            menu.addSeparator()
            info = menu.addAction("Delete blocked — picture has a JPG (protected)")
            info.setEnabled(False)
        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return
        if chosen == act_add:
            self._do_master_add(upn_key)
        elif act_del is not None and chosen == act_del:
            self._do_master_delete(upn_key)
        elif chosen in file_acts:
            self._do_master_delete_file(upn_key, file_acts[chosen])

    def _do_master_delete(self, upn_key: str):
        from db.master_mutate import MasterMutateError
        files = self._collection.master_nonjpg_files(upn_key)
        if QMessageBox.question(
                self, "Delete master picture",
                f"Move {len(files)} file(s) to the master trash and remove this "
                f"picture from the collection?\n\nFiles can be restored from "
                f".trash until purged.") != QMessageBox.StandardButton.Yes:
            return
        try:
            res = self._collection.delete_master_picture(upn_key)
        except MasterMutateError as e:
            QMessageBox.warning(self, "Delete blocked", str(e))
            return
        self._status.setText(
            f"Deleted — {len(res['trashed'])} file(s) moved to .trash. "
            f"Run Master → Recount species to refresh rollups.")
        self._rebuild_cards()

    def _do_master_delete_file(self, upn_key: str, fname: str):
        from db.master_mutate import MasterMutateError
        if QMessageBox.question(
                self, "Delete file",
                f"Move {fname} to the master trash? The picture keeps its other "
                f"files.") != QMessageBox.StandardButton.Yes:
            return
        try:
            self._collection.delete_master_file(upn_key, fname)
        except MasterMutateError as e:
            QMessageBox.warning(self, "Delete blocked", str(e))
            return
        self._status.setText(f"Deleted {fname} (moved to .trash).")
        self._rebuild_cards()

    def _do_master_add(self, upn_key: str):
        from db.master_mutate import MasterMutateError
        try:
            res = self._collection.add_master_files(upn_key)
        except MasterMutateError as e:
            QMessageBox.warning(self, "Add files", str(e))
            return
        if not res["added"]:
            self._status.setText(
                "No new files found for this picture in its species folder.")
            return
        parts = []
        if res["jpg"]:
            parts.append(f"{res['jpg']} JPG" + ("s" if res["jpg"] != 1 else ""))
        if res["meta"]:
            parts.append(f"{res['meta']} metafile" + ("s" if res["meta"] != 1 else ""))
        self._status.setText(f"Added {' and '.join(parts)} to the picture.")
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
        panel.committed.connect(lambda upns: (self._clear_selection(), dlg.accept()))
        dlg_layout.addWidget(panel)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(cancel_btn)
        dlg_layout.addLayout(row)
        dlg.show()

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
                progress.setValue(i)
                progress.setLabelText(f"Updating {i + 1} of {len(key_list)}…")
                QApplication.processEvents()
                try:
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
        raw_exts_set = {".nef", ".orf", ".cr2", ".arw", ".rw2"}
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

    def _resort_if_needed(self):
        """Re-insert cards into the grid according to the collection's current
        sort order (writes happen in the provider; this only reorders widgets)."""
        if self._collection is None:
            return
        filter_name = self._species_combo.currentData()
        self._collection.rebuild(filter_name)
        new_order = [self._collection.item(i).key for i in range(len(self._collection))]
        old_order = [c.upn_key for c in self._cards]
        if old_order == new_order:
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

        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(0))
