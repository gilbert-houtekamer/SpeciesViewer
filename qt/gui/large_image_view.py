"""Merged large-image viewer — one dialog, navigable across a picture list.

Renders entirely from the picture-provider seam: the host passes a
PictureCollection plus a fixed snapshot of keys (its current grid order) and a
start index. The viewer reads each picture's fields from the PictureItem, loads
its image asynchronously, and writes edits back through the item — so it serves
both hosts (Species preview, Rating Editor) with no engine knowledge.

Navigation: ←/→ step through the keys; assigning a star rating or a colour
label auto-advances to the next picture (the cull-with-few-keystrokes flow).
After each edit it emits picture_changed(key) so the host can refresh that card
and (Rating Editor) re-sort. The keys snapshot is fixed for the session, so the
navigation order stays stable even when the host re-sorts its grid underneath.

DISCARD COLOUR RULE (Option C): when has_jpg is True the Blue/Purple circles are
greyed (a JPG-bearing picture can never be a deletion candidate).
"""

import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QTextEdit, QSizePolicy,
)
from PyQt6.QtGui import QPixmap, QGuiApplication, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from gui.rating_editor import StarWidget, ColorSelector, COLOR_HEX
from gui.window_state import restore_window, save_window, clamp_to_visible_screen

logger = logging.getLogger("speciesviewer")

# Colours that mean "deletion candidate" — greyed on JPG-bearing pictures.
_DISCARD_COLORS = ("Blue", "Purple")
_DISCARD_GREYED_TIP = ("Has a JPG — kept for the website and a future DxO "
                       "re-export, so it can't be a deletion candidate.")


def color_to_css(color: str) -> str:
    """Map a label name ("Red", "red", …) to a CSS colour; "" → empty.
    Falls back to using the name verbatim (CSS understands "red" etc.)."""
    if not color:
        return ""
    return COLOR_HEX.get(color.capitalize(), color)


class LargeImageView(QDialog):
    picture_changed = pyqtSignal(str)  # key — emitted after an edit so the host refreshes/re-sorts

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
        # Give the dialog real min/max buttons (a QDialog shows only close on
        # Windows) — the viewer often opens large.
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        # The dialog itself holds keyboard focus by default so ←/→ navigate;
        # clicking the description box moves focus there (then arrows edit text).
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._collection = collection
        self._keys = list(keys)
        self._index = max(0, min(index, len(self._keys) - 1)) if self._keys else 0
        self._auto_advance = auto_advance
        self._settings_key = settings_key
        self._geo_key = f"{settings_key}Large"
        self._item = None
        self._pixmap = QPixmap()
        self._load_token = 0            # drops stale async image deliveries
        self._original_description = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(6)

        # ── Image stage: a tight coloured frame hugs the scaled pixmap, centred.
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
        # Clicking the image takes focus away from the description box, so ←/→
        # navigate again (a clear way to "leave" the text field).
        self._image_label.mousePressEvent = lambda _e: self.setFocus()
        frame_l.addWidget(self._image_label)
        stage_h.addWidget(self._img_frame)
        stage_h.addStretch(1)
        stage_v.addLayout(stage_h)
        stage_v.addStretch(1)
        layout.addWidget(self._stage, stretch=1)

        # ── Controls strip (centred, ~1/3 width) ──────────────────────────
        controls = QWidget()
        cl = QVBoxLayout(controls)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)

        name_row = QHBoxLayout()
        name_row.addStretch(1)
        self._lbl_name = QLabel("")
        self._lbl_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_name.setStyleSheet("font-weight: bold; font-size: 13px;")
        name_row.addWidget(self._lbl_name)
        self._lbl_pos = QLabel("")
        self._lbl_pos.setStyleSheet("font-size: 11px; color: #888;")
        name_row.addSpacing(8)
        name_row.addWidget(self._lbl_pos)
        name_row.addStretch(1)
        cl.addLayout(name_row)

        row = QHBoxLayout()
        self._stars = StarWidget(0)
        self._stars.rating_changed.connect(self._on_rating)
        row.addWidget(self._stars)
        self._lbl_date = QLabel("")
        self._lbl_date.setStyleSheet("font-size: 11px; color: #777;")
        row.addWidget(self._lbl_date)
        row.addStretch()
        self._rate_lbl = QLabel("")
        self._rate_lbl.setStyleSheet("font-size: 11px; color: #555;")
        row.addWidget(self._rate_lbl)
        cl.addLayout(row)

        self._color_sel = ColorSelector("")
        self._color_sel.color_changed.connect(self._on_color)
        cl.addWidget(self._color_sel)

        ctrl_row = QHBoxLayout()
        ctrl_row.addStretch(1)
        ctrl_row.addWidget(controls, 1)
        ctrl_row.addStretch(1)
        layout.addLayout(ctrl_row)

        # ── Description (Ctrl+S to save; auto-saved on navigate / close) ───
        desc_caption = QLabel("Enter a story about this picture to be shown on the website (optional)")
        desc_caption.setStyleSheet("font-size: 11px; font-weight: bold;")
        desc_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc_caption)

        self._desc_edit = QTextEdit()
        fm = self._desc_edit.fontMetrics()
        self._desc_edit.setFixedHeight(int(fm.lineSpacing() * 3.5) + 8)
        self._desc_edit.setAcceptRichText(False)
        desc_row = QHBoxLayout()
        desc_row.addStretch(1)
        desc_row.addWidget(self._desc_edit, 3)
        desc_row.addStretch(1)
        layout.addLayout(desc_row)

        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save_description)

        self._apply_default_or_remembered_geometry()
        self._load_current()

    def keyPressEvent(self, event):
        # ←/→ navigate — but only when the description box doesn't have focus
        # (when it does, the QTextEdit consumes the arrows for cursor movement
        # and this handler isn't reached anyway; the guard is belt-and-braces).
        k = event.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right) and not self._desc_edit.hasFocus():
            self._prev() if k == Qt.Key.Key_Left else self._next()
            event.accept()
            return
        super().keyPressEvent(event)

    # ── Load the current picture ──────────────────────────────────────────
    def _load_current(self):
        if not self._keys:
            return
        key = self._keys[self._index]
        try:
            item = self._collection.item_for_key(key)
        except (ValueError, KeyError):
            return  # key no longer in the collection
        self._item = item

        self.setWindowTitle(item.display_name or key)
        self._lbl_name.setText(item.display_name or key)
        self._lbl_pos.setText(f"{self._index + 1} of {len(self._keys)}")

        self._stars.set_rating(item.rating)       # programmatic — no signal
        self._stars.setEnabled(item.can_edit_rating)

        self._color_sel.set_color(item.color)
        if item.can_edit_color:
            self._color_sel.setEnabled(True)
            self._color_sel.set_disabled_colors(
                _DISCARD_COLORS if item.has_jpg else (), _DISCARD_GREYED_TIP)
        else:
            self._color_sel.setEnabled(False)

        self._lbl_date.setText(str(item.date)[:10].replace(":", "-") if item.date else "")
        self._rate_lbl.setText(f"Rate: {item.rate}" if item.rate is not None else "")
        self._apply_frame_color(item.color or "")

        self._original_description = item.description or ""
        self._desc_edit.blockSignals(True)
        self._desc_edit.setPlainText(self._original_description)
        self._desc_edit.setReadOnly(not item.can_edit_description)
        self._desc_edit.setToolTip("" if item.can_edit_description
                                   else "Descriptions can't be saved for this picture.")
        self._desc_edit.blockSignals(False)

        # Async image — drop deliveries from a previous picture.
        self._load_token += 1
        tok = self._load_token
        self._pixmap = QPixmap()
        self._image_label.setText("Loading…")
        item.request_full_pixmap(lambda px, t=tok: self._on_pixmap(t, px))

        # Keep keyboard focus on the dialog (not auto-parked in the description
        # box) so ←/→ keep navigating through a culling run.
        self.setFocus()

    def _on_pixmap(self, token, pixmap):
        if token != self._load_token:
            return  # stale — the user already moved on
        self._pixmap = pixmap if (pixmap is not None and not pixmap.isNull()) else QPixmap()
        if self._pixmap.isNull():
            self._image_label.setText("(no image)")
        else:
            self._fit_pixmap()

    # ── Navigation ────────────────────────────────────────────────────────
    def _go(self, delta: int):
        if not self._keys:
            return
        self._save_description()
        new = max(0, min(self._index + delta, len(self._keys) - 1))
        if new == self._index:
            return
        self._index = new
        self._load_current()

    def _prev(self):
        self._go(-1)

    def _next(self):
        self._go(+1)

    # ── Edits (write via the item, then advance) ──────────────────────────
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

    # ── Colour frame ────────────────────────────────────────────────────────
    def _apply_frame_color(self, color: str):
        css = color_to_css(color)
        if css:
            self._img_frame.setStyleSheet(
                f"QWidget#imgFrame {{ border: 4px solid {css}; border-radius: 6px; }}")
        else:
            self._img_frame.setStyleSheet("")

    # ── Geometry: default size, remembered per settings_key, clamped on-screen
    def _apply_default_or_remembered_geometry(self):
        avail = QGuiApplication.primaryScreen().availableGeometry()
        restore_window(self, self._geo_key,
                       default_size=(int(avail.width() * 0.7), int(avail.height() * 0.8)))

    # ── Pixmap fit ─────────────────────────────────────────────────────────
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
            scaled = self._pixmap  # never upscale
        else:
            scaled = self._pixmap.scaled(avail_w, avail_h,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
        self._image_label.setPixmap(scaled)
        self._image_label.setMaximumSize(scaled.size())

    def showEvent(self, event):
        super().showEvent(event)
        clamp_to_visible_screen(self)
        self.setFocus()  # arrows navigate from the start
        QTimer.singleShot(0, self._fit_pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_pixmap()

    def closeEvent(self, event):
        self._save_description()
        save_window(self, self._geo_key)
        super().closeEvent(event)
