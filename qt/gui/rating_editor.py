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
    QRadioButton, QButtonGroup, QMessageBox,
)
from PyQt6.QtGui import QPixmap, QGuiApplication
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from engine.filepicture import FilePicture
from engine.filecsv import Files
from engine.picturecsv import Pictures
from ghexif.exif_writer import exiftool_write

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
        self._rating = 0 if self._rating == n else n
        self._refresh()
        self.rating_changed.emit(self._rating)

    def _refresh(self):
        for i, btn in enumerate(self._buttons):
            btn.setText("★" if i < self._rating else "☆")

    def set_rating(self, n: int):
        self._rating = max(0, min(5, n))
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
        self._color = color
        self._refresh()


class PictureCard(QFrame):
    """Thumbnail + star rating + color selector + rate label for one UPN."""

    card_changed = pyqtSignal(str)  # upn_key
    card_clicked = pyqtSignal(str)  # upn_key — thumbnail clicked → open large view

    def __init__(self, upn_key: str, upn_row: dict, jpg_path, card_width: int,
                 display_name: str = None, parent=None):
        super().__init__(parent)
        self.upn_key = upn_key
        self._upn_row = upn_row
        self._pending_rating = None
        self._pending_color = None

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(5, 5, 5, 5)
        vbox.setSpacing(3)

        # Thumbnail — clicking opens the large-view detail dialog
        self._thumb = QLabel()
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        self._thumb.mousePressEvent = lambda _: self.card_clicked.emit(self.upn_key)
        self._load_thumb(jpg_path, card_width)
        vbox.addWidget(self._thumb)

        # Species name
        species = display_name or (upn_row.get("NLSoortNaam") or upn_row.get("EngSoortNaam")
                   or upn_row.get("WetSoortNaam") or upn_key)
        lbl_name = QLabel(species)
        lbl_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_name.setStyleSheet("font-weight: bold; font-size: 10px;")
        lbl_name.setWordWrap(True)
        vbox.addWidget(lbl_name)

        # Stars row + date + rate
        row_stars = QHBoxLayout()
        row_stars.setContentsMargins(0, 0, 0, 0)
        initial_stars = _safe_int(upn_row.get("Rating", "0"))
        self._stars = StarWidget(initial_stars)
        self._stars.rating_changed.connect(self._on_rating_changed)
        row_stars.addWidget(self._stars)
        dto = upn_row.get("DateTimeOriginal", "")
        if dto:
            date_str = dto[:10].replace(":", "-")
            lbl_date = QLabel(date_str)
            lbl_date.setStyleSheet("font-size: 9px; color: #777;")
            row_stars.addWidget(lbl_date)
        row_stars.addStretch()
        self._rate_lbl = QLabel(f"Rate: {upn_row.get('Rate', '?')}")
        self._rate_lbl.setStyleSheet("font-size: 10px; color: #555;")
        row_stars.addWidget(self._rate_lbl)
        vbox.addLayout(row_stars)

        # Color selector
        self._color_sel = ColorSelector(upn_row.get("Label", ""))
        self._color_sel.color_changed.connect(self._on_color_changed)
        vbox.addWidget(self._color_sel)

        # UPN key + file types (small)
        files = upn_row.get("Files", [])
        exts = sorted({Path(f).suffix.lstrip(".").upper()
                       for f in (files if isinstance(files, list) else []) if f})
        key_line = upn_key if not exts else f"{upn_key}  {' · '.join(exts)}"
        lbl_key = QLabel(key_line)
        lbl_key.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_key.setStyleSheet("font-size: 9px; color: #aaa;")
        lbl_key.setToolTip(upn_key)
        vbox.addWidget(lbl_key)

    def _load_thumb(self, jpg_path, card_width: int):
        if jpg_path and Path(jpg_path).exists():
            px = QPixmap(str(jpg_path))
            if not px.isNull():
                target_w = max(80, card_width - 18)
                px = px.scaledToWidth(target_w, Qt.TransformationMode.SmoothTransformation)
                self._thumb.setPixmap(px)
                return
        self._thumb.setText("(no image)")
        self._thumb.setStyleSheet("color: #ccc; font-size: 10px;")

    def _on_rating_changed(self, n: int):
        self._pending_rating = n
        self.card_changed.emit(self.upn_key)

    def _on_color_changed(self, color: str):
        self._pending_color = color
        self.card_changed.emit(self.upn_key)

    def update_rate_display(self, new_rate: int):
        self._rate_lbl.setText(f"Rate: {new_rate}")

    def update_rating_display(self, stars: int):
        self._stars.set_rating(stars)

    def update_color_display(self, color: str):
        self._color_sel.set_color(color)

    def take_pending(self):
        """Return (pending_rating, pending_color) and clear them."""
        r, c = self._pending_rating, self._pending_color
        self._pending_rating = None
        self._pending_color = None
        return r, c


def _safe_int(s, default: int = 0) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return default


# ── Large-image detail dialog ─────────────────────────────────────────────────

class RatingCardDetailDialog(QDialog):
    """Full-size view of a single picture card with live-editable stars and color."""

    def __init__(self, parent, upn_key: str, upn_row: dict, jpg_path,
                 display_name: str, on_change):
        super().__init__(parent)
        self._upn_row = upn_row
        self._on_change = on_change  # callable(upn_key, new_stars_or_None, new_color_or_None)

        self.setWindowTitle(display_name or upn_key)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(int(screen.width() * 0.85), int(screen.height() * 0.85))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Large image — setMinimumSize(1,1) prevents the pixmap size from
        # becoming the window's minimum size, which would block vertical resize.
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._image_label.setMinimumSize(1, 1)
        self._pixmap = QPixmap(str(jpg_path)) if jpg_path and Path(jpg_path).exists() else QPixmap()
        layout.addWidget(self._image_label, stretch=1)

        # Controls strip — centred and limited to 1/3 of window width via
        # equal stretches on each side (stretch 1 : widget 1 : stretch 1).
        controls_widget = QWidget()
        controls_layout = QVBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(4)

        lbl_name = QLabel(display_name or upn_key)
        lbl_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_name.setStyleSheet("font-weight: bold; font-size: 13px;")
        controls_layout.addWidget(lbl_name)

        row_stars = QHBoxLayout()
        self._stars = StarWidget(_safe_int(upn_row.get("Rating", "0")))
        self._stars.rating_changed.connect(lambda n: on_change(upn_key, n, None))
        row_stars.addWidget(self._stars)
        dto = upn_row.get("DateTimeOriginal", "")
        if dto:
            lbl_date = QLabel(dto[:10].replace(":", "-"))
            lbl_date.setStyleSheet("font-size: 11px; color: #777;")
            row_stars.addWidget(lbl_date)
        row_stars.addStretch()
        self._rate_lbl = QLabel(f"Rate: {upn_row.get('Rate', '?')}")
        self._rate_lbl.setStyleSheet("font-size: 11px; color: #555;")
        row_stars.addWidget(self._rate_lbl)
        controls_layout.addLayout(row_stars)

        self._color_sel = ColorSelector(upn_row.get("Label", ""))
        self._color_sel.color_changed.connect(lambda c: on_change(upn_key, None, c))
        controls_layout.addWidget(self._color_sel)

        lbl_path = QLabel(upn_key)
        lbl_path.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_path.setStyleSheet("font-size: 9px; color: #aaa;")
        controls_layout.addWidget(lbl_path)

        ctrl_row = QHBoxLayout()
        ctrl_row.addStretch(1)
        ctrl_row.addWidget(controls_widget, 1)
        ctrl_row.addStretch(1)
        layout.addLayout(ctrl_row)

    def refresh_rate(self):
        self._rate_lbl.setText(f"Rate: {self._upn_row.get('Rate', '?')}")

    def _fit_pixmap(self):
        if self._pixmap.isNull():
            return
        size = self._image_label.size()
        if size.width() <= 1 or size.height() <= 1:
            return
        scaled = self._pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        self._image_label.setPixmap(scaled)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_pixmap()


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
        self.resize(1000, 720)
        self._trip_root = Path(trip_root_dir)
        self._initial_day = day_str
        self.fpr: FilePicture | None = None
        self.columns = 3
        self._cards: list[PictureCard] = []
        self._jpg_only = True
        self._species_lang = "NL"
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

    # ── Sorted picture list ───────────────────────────────────────────────────

    def _sorted_upns(self, filter_name=None) -> list[tuple[int, str, dict]]:
        rows = []
        for key, upn_row in self.fpr.upn.UniquePictureName_dict.items():
            if filter_name and self._species_name(upn_row) != filter_name:
                continue
            if self._jpg_only and self._get_jpg_path(upn_row) is None:
                continue
            rows.append((_safe_int(upn_row.get("Rate", 0) or 0), key, upn_row))
        rows.sort(key=lambda x: x[0], reverse=True)
        return rows

    def _get_jpg_path(self, upn_row: dict) -> Path | None:
        """Return the best available JPG path for a UPN, or None."""
        best = upn_row.get("BestJPGPathName", "")
        if best:
            p = Path(best)
            if p.exists() and p.suffix.lower() in (".jpg", ".jpeg"):
                return p
        files = upn_row.get("Files", [])
        if not isinstance(files, list):
            return None
        for fname in files:
            if fname not in self.fpr.fi.files_dict:
                continue
            frow = self.fpr.fi.files_dict[fname]
            if frow.get("WindowsExtension", "").lower() in ("jpg", "jpeg"):
                p = Path(frow.get("WindowsFileName", ""))
                if p.exists():
                    return p
        return None

    def _get_display_path(self, upn_row: dict) -> Path | None:
        """Return a JPG path for display; falls back to cached RAW thumbnail when not JPG-only."""
        jpg = self._get_jpg_path(upn_row)
        if jpg:
            return jpg
        if self._jpg_only:
            return None
        return self._get_raw_thumb(upn_row)

    _RAW_EXTS = {".nef", ".orf", ".cr2", ".arw", ".rw2"}

    def _get_raw_thumb(self, upn_row: dict) -> Path | None:
        """Return a cached JPEG thumbnail for the first RAW file in the UPN, extracting it if needed."""
        files = upn_row.get("Files", [])
        if not isinstance(files, list):
            return None
        for fname in files:
            if fname not in self.fpr.fi.files_dict:
                continue
            frow = self.fpr.fi.files_dict[fname]
            if ("." + frow.get("WindowsExtension", "").lower()) not in self._RAW_EXTS:
                continue
            raw_path = Path(frow.get("WindowsFileName", ""))
            if not raw_path.exists():
                continue
            cache_dir = raw_path.parent / ".preview_cache"
            cache_dir.mkdir(exist_ok=True)
            cache_path = cache_dir / (raw_path.stem + ".jpg")
            if cache_path.exists():
                return cache_path
            for tag in ["-PreviewImage", "-ThumbnailImage"]:
                try:
                    result = subprocess.run(
                        ["exiftool", "-b", tag, str(raw_path)],
                        capture_output=True, timeout=15,
                    )
                    if result.returncode == 0 and result.stdout:
                        cache_path.write_bytes(result.stdout)
                        return cache_path
                except Exception as e:
                    logger.warning(f"RAW thumb extraction failed for {raw_path.name}: {e}")
        return None

    def _batch_extract_raw_thumbs(self):
        """Batch-extract RAW thumbnails for all UPNs that have no JPG (pre-fills the cache)."""
        by_dir: dict[Path, list[Path]] = {}
        for upn_row in self.fpr.upn.UniquePictureName_dict.values():
            if self._get_jpg_path(upn_row):
                continue
            files = upn_row.get("Files", [])
            if not isinstance(files, list):
                continue
            for fname in files:
                if fname not in self.fpr.fi.files_dict:
                    continue
                frow = self.fpr.fi.files_dict[fname]
                if ("." + frow.get("WindowsExtension", "").lower()) not in self._RAW_EXTS:
                    continue
                raw_path = Path(frow.get("WindowsFileName", ""))
                if not raw_path.exists():
                    continue
                cache_dir = raw_path.parent / ".preview_cache"
                cache_dir.mkdir(exist_ok=True)
                if not (cache_dir / (raw_path.stem + ".jpg")).exists():
                    by_dir.setdefault(cache_dir, []).append(raw_path)
                break  # one RAW per UPN is enough

        for cache_dir, raw_files in by_dir.items():
            for tag in ["-PreviewImage", "-ThumbnailImage"]:
                still_needed = [p for p in raw_files
                                if not (cache_dir / (p.stem + ".jpg")).exists()]
                if not still_needed:
                    break
                try:
                    subprocess.run(
                        ["exiftool", "-b", tag, "-w!", str(cache_dir / "%f.jpg")]
                        + [str(p) for p in still_needed],
                        capture_output=True, timeout=120,
                    )
                except Exception as e:
                    logger.warning(f"Batch RAW thumb extraction failed: {e}")

    # ── Grid management ───────────────────────────────────────────────────────

    def _grid_placement(self, total: int) -> list[tuple[int, int, int, int]]:
        """Return (row, col, span=1, card_w) for each of `total` card positions.

        Card width is always viewport_width / columns regardless of how many
        pictures are shown. One picture in a 3-column layout gets the same
        small card as the last picture of a full grid.
        """
        ncols = self.columns
        avail = self._scroll.viewport().width()
        if avail < 100:
            avail = 960
        spacing = self._grid.spacing()
        card_w = max(120, (avail - spacing * (ncols - 1)) // ncols)
        return [(idx // ncols, idx % ncols, 1, card_w) for idx in range(total)]

    def _clear_grid(self):
        for c in range(8):  # reset all possible column stretch values (spinner max = 8)
            self._grid.setColumnStretch(c, 0)
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()  # deferred deletion; avoids GC-triggered C++ cascade
        self._cards.clear()

    def _rebuild_cards(self):
        if not self.fpr:
            return
        self._clear_grid()
        filter_name = self._species_combo.currentData()
        if filter_name is None:
            n_species = self._species_combo.count() - 1  # subtract placeholder
            self._status.setText(f"{n_species} species loaded — select one to view pictures.")
            return
        sorted_rows = self._sorted_upns(filter_name)
        placement = self._grid_placement(len(sorted_rows))
        for c in range(self.columns):
            self._grid.setColumnStretch(c, 1)

        for idx, (_, upn_key, upn_row) in enumerate(sorted_rows):
            r, col, span, card_w = placement[idx]
            img = self._get_display_path(upn_row)
            dname = self._species_name(upn_row) or upn_key
            card = PictureCard(upn_key, upn_row, img, card_w, display_name=dname)
            card.card_changed.connect(self._on_card_changed)
            card.card_clicked.connect(self._on_card_clicked)
            self._grid.addWidget(card, r, col, 1, span)
            self._cards.append(card)

        n = len(sorted_rows)
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
        self._jpg_only = checked
        if not checked and self.fpr:
            self._status.setText("Extracting RAW thumbnails…")
            self._batch_extract_raw_thumbs()
        self._rebuild_cards()

    def _on_card_changed(self, upn_key: str):
        card = next((c for c in self._cards if c.upn_key == upn_key), None)
        if not card:
            return
        new_stars, new_color = card.take_pending()
        self._apply_change(upn_key, new_stars, new_color, card)

    def _on_card_clicked(self, upn_key: str):
        if not self.fpr:
            return
        upn_row = self.fpr.upn.UniquePictureName_dict.get(upn_key)
        if not upn_row:
            return
        jpg_path = self._get_display_path(upn_row)
        dname = self._species_name(upn_row) or upn_key

        dlg_ref: list = [None]

        def on_detail_change(key, new_stars, new_color):
            card = next((c for c in self._cards if c.upn_key == key), None)
            if card:
                self._apply_change(key, new_stars, new_color, card)
            if dlg_ref[0]:
                dlg_ref[0].refresh_rate()

        dlg = RatingCardDetailDialog(self, upn_key, upn_row, jpg_path, dname, on_detail_change)
        dlg_ref[0] = dlg
        dlg.show()

    # ── Write-back ────────────────────────────────────────────────────────────

    def _apply_change(self, upn_key: str, new_stars, new_color, card: PictureCard):
        if upn_key not in self.fpr.upn.UniquePictureName_dict:
            return
        upn_row = self.fpr.upn.UniquePictureName_dict[upn_key]
        files = upn_row.get("Files", [])
        if not isinstance(files, list):
            files = []

        wrote_file = False
        for fname in files:
            if fname not in self.fpr.fi.files_dict:
                continue
            frow = self.fpr.fi.files_dict[fname]
            ext = frow.get("WindowsExtension", "").lower()
            if ext not in ("xmp", "jpg", "jpeg"):
                continue
            fpath = Path(frow.get("WindowsFileName", ""))
            if not fpath.exists():
                logger.warning(f"RatingEditor: file not found: {fpath}")
                continue

            args: list[str] = []
            if new_stars is not None:
                args.append(f"-XMP:Rating={new_stars}")
            if new_color is not None:
                # empty string clears the label
                args.append(f"-XMP:Label={new_color}")
            if args:
                try:
                    exiftool_write(fpath, args)
                    wrote_file = True
                except Exception as e:
                    logger.error(f"exiftool write failed for {fpath.name}: {e}")

            # Update in-memory file dict regardless (we already wrote to disk)
            if new_stars is not None:
                frow["XMP:Rating"] = str(new_stars)
            if new_color is not None:
                frow["XMP:Label"] = new_color

        # Update UPN in-memory
        if new_stars is not None:
            upn_row["Rating"] = str(new_stars)
        if new_color is not None:
            upn_row["Label"] = new_color

        # Recompute Rate and refresh the card's label
        new_rate = _compute_rate(upn_row, self.fpr.fi.files_dict)
        upn_row["Rate"] = str(new_rate)
        if new_stars is not None:
            card.update_rating_display(new_stars)
        if new_color is not None:
            card.update_color_display(new_color)
        card.update_rate_display(new_rate)

        # Persist CSVs
        self._write_csvs_for_upn(upn_key, wrote_file)

        # Re-sort grid if this card's position changed
        self._resort_if_needed()

        # Status feedback
        parts = [f"Saved {upn_key}"]
        if new_stars is not None:
            parts.append(f"★={new_stars}")
        if new_color is not None:
            parts.append(f"label={new_color or '(cleared)'}")
        parts.append(f"Rate={new_rate}")
        self._status.setText("  ".join(parts))

    def _write_csvs_for_upn(self, upn_key: str, wrote_file: bool):
        """Write the CSV file(s) that contain upn_key, scoped to the right day."""
        if self._day_objects is None:
            # Day scope: fpr.fi / fpr.upn point at the single day's files
            try:
                self.fpr.upn.write_project_summary()
            except Exception as e:
                logger.error(f"Failed to write data_pictures.csv: {e}")
            if wrote_file:
                try:
                    self.fpr.fi.write_project_files()
                except Exception as e:
                    logger.error(f"Failed to write data_files.csv: {e}")
            return

        # Trip scope: write only the specific day's objects
        day = self._upn_day_map.get(upn_key)
        if not day or day not in self._day_objects:
            return
        fi_day, upn_day = self._day_objects[day]
        # upn_day.UniquePictureName_dict[upn_key] is the same object reference
        # as fpr.upn.UniquePictureName_dict[upn_key], so it already has the
        # updated Rating/Label/Rate values.
        try:
            upn_day.write_project_summary()
        except Exception as e:
            logger.error(f"Failed to write data_pictures.csv for {day}: {e}")
        if wrote_file:
            try:
                fi_day.write_project_files()
            except Exception as e:
                logger.error(f"Failed to write data_files.csv for {day}: {e}")

    def _resort_if_needed(self):
        """Re-insert cards into the grid according to the current sort order."""
        filter_name = self._species_combo.currentData()
        new_order = [key for _, key, _ in self._sorted_upns(filter_name)]
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
