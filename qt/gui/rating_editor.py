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

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QScrollArea, QGridLayout,
    QLabel, QFrame, QPushButton, QSpinBox, QComboBox, QSizePolicy,
    QRadioButton, QButtonGroup, QMessageBox, QStyle,
)
from PyQt6.QtGui import QPixmap, QImage, QGuiApplication, QIcon
from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal, QObject, QRunnable, QThreadPool

from engine.filepicture import FilePicture
from engine.filecsv import Files
from engine.picturecsv import Pictures
from ghexif.exif_writer import exiftool_write
from gui.window_state import restore_window, save_window
from gui.image_loader import AsyncImageLoader
from gui.picture_provider import PictureItem

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

    saved_multiplier = 0.1
    for fname in files:
        if fname not in files_dict:
            continue
        frow = files_dict[fname]
        if frow.get("WindowsExtension", "").lower() not in ("jpg", "jpeg"):
            continue
        img_size = frow.get("ImageSize", "")
        if not img_size:
            continue
        try:
            parts = re.split(r"[x ]", img_size)
            w, h = int(parts[0]), int(parts[1])
            multiplier = float(min(1500, max(w, h))) / 1500.0
            upn_size = upn_row.get("ImageSize", "")
            if upn_size:
                up = re.split(r"[x ]", upn_size)
                uw, uh = int(up[0]), int(up[1])
                if uw >= 1.5 * w or uh >= 1.5 * h:
                    multiplier *= 1.5
            saved_multiplier = max(saved_multiplier, multiplier)
        except Exception:
            pass

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

    card_changed = pyqtSignal(str)   # key — emitted after a write so the host re-sorts
    card_clicked = pyqtSignal(str)   # key — thumbnail clicked → open large view
    status_clicked = pyqtSignal(str) # key — status icon clicked → open SpeciesInfoDialog

    def __init__(self, item: PictureItem, card_width: int, parent=None,
                 *, show_status: bool = False):
        super().__init__(parent)
        self._item = item
        self.upn_key = item.key
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
        self._thumb.mousePressEvent = lambda _: self.card_clicked.emit(self.upn_key)
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

    def _refresh_status_badges(self):
        """Set the status icon + description/GPS badges from the live item.
        Re-callable after an edit (species commit, GPS fix)."""
        if not self._show_status:
            return
        item = self._item
        error_msg = item.error_msg
        species = item.species
        if error_msg:
            self._status_icon.setIcon(QIcon(QPixmap("qt/assets/warning_icon.png").scaled(16, 16)))
            self._status_icon.setText("")
            self._status_icon.setToolTip(error_msg)
        elif species:
            self._status_icon.setIcon(QIcon(QPixmap("qt/assets/ok_icon.png").scaled(16, 16)))
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
        self._refresh_status_badges()

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

    def __init__(self, parent, trip_root_dir: str, day_str: str = None):
        super().__init__(parent)
        self.setWindowTitle("Rating Editor")
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
        # Trip-scope per-day tracking; None in Day scope
        self._day_objects: dict | None = None   # day_str → (Files, Pictures)
        self._upn_day_map: dict[str, str] = {}  # upn_key → day_str

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 4)
        main_layout.setSpacing(4)

        # ── Top bar ──────────────────────────────────────────────────────────
        top = QHBoxLayout()

        # Scope radio
        self._day_radio = QRadioButton("Day")
        self._trip_radio = QRadioButton("Trip")
        grp = QButtonGroup(self)
        grp.addButton(self._day_radio)
        grp.addButton(self._trip_radio)
        (self._day_radio if day_str else self._trip_radio).setChecked(True)
        self._day_radio.toggled.connect(self._on_scope_toggled)
        top.addWidget(QLabel("Scope:"))
        top.addWidget(self._day_radio)
        top.addWidget(self._trip_radio)
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

        main_layout.addLayout(top)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status = QLabel("Initializing…")
        self._status.setStyleSheet("color: #555; font-size: 10px;")
        main_layout.addWidget(self._status)

        # ── Scroll area ───────────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        # Wrap grid in a VBox so the stretch below it absorbs extra viewport
        # height; otherwise setWidgetResizable forces the grid to fill the
        # viewport and rows expand to fill the allocated space.
        _scroll_content = QWidget()
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
        saved_species = self._species_combo.currentData()  # preserve across reload
        self._status.setText("Loading…")
        self.fpr = FilePicture(None)
        self._day_objects = None
        self._upn_day_map = {}
        scope_day = self._day_radio.isChecked()
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
                self._load_trip_scope()
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
            loader=self._loader,
        )
        self._populate_species_combo(restore_selection=saved_species)

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

        for idx in range(n):
            item = self._collection.item(idx)
            r, col, span, card_w = placement[idx]
            card = PictureCard(item, card_w)   # requests its own thumbnail (async)
            card.card_changed.connect(self._on_card_changed)
            card.card_clicked.connect(self._on_card_clicked)
            self._grid.addWidget(card, r, col, 1, span)
            self._cards.append(card)
            self._cards_by_key[item.key] = card

        suffix = f" for {filter_name}" if filter_name else ""
        self._status.setText(f"{n} picture{'s' if n != 1 else ''}{suffix}")
        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(0))

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_scope_toggled(self, day_checked: bool):
        self._day_combo.setVisible(day_checked)
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

    def _on_card_clicked(self, upn_key: str):
        # Local import avoids a circular import: large_image_view imports
        # StarWidget/ColorSelector from this module.
        from gui.large_image_view import LargeImageView
        if not self.fpr:
            return
        upn_row = self.fpr.upn.UniquePictureName_dict.get(upn_key)
        if not upn_row:
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

        item = self._collection.item(self._collection.index_of(upn_key))
        card = self._cards_by_key.get(upn_key)
        img_path = self._collection.display_path(upn_key)
        dlg_ref: list = [None]

        def on_rating(n):
            item.set_rating(n)
            if card:
                card.update_rating_display(n)
                card.update_rate_display(item.rate)
            if dlg_ref[0] is not None:
                dlg_ref[0].set_rate(item.rate)
            self._resort_if_needed()

        def on_color(c):
            item.set_color(c)
            if card:
                card.update_color_display(c)
                card.update_rate_display(item.rate)
            if dlg_ref[0] is not None:
                dlg_ref[0].set_rate(item.rate)
            self._resort_if_needed()

        dlg = LargeImageView(
            self, image_path=img_path, display_name=item.display_name,
            rating=item.rating,
            color=item.color,
            description=item.description,  # read-only here for now
            rate=item.rate,
            date=item.date,
            has_jpg=item.has_jpg,
            on_rating=on_rating if item.can_edit_rating else None,
            on_color=on_color if item.can_edit_color else None,
            settings_key="RatingEditor",
        )
        dlg_ref[0] = dlg
        self._large_view = dlg
        dlg.finished.connect(lambda _=0: setattr(self, "_large_view", None))
        dlg.show()

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
