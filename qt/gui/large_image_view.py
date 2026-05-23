"""Merged large-image viewer — one dialog with ALL features.

Replaces the two near-duplicate large views:
  - SingleImageViewer (image_viewer.py)      had description editing only
  - RatingCardDetailDialog (rating_editor.py) had stars + color only

This version shows the image plus stars, colour, and description together.
The host (Species Image Preview, per-file engine; or Rating Editor, per-UPN
engine) supplies initial values and write-back callbacks via the constructor,
so the dialog stays engine-agnostic — the only seam between the two engines.

Layout note: controls are below the image for now (matching what both old
dialogs did). The PhotoMechanic horizon — right-hand control panel + bottom
filmstrip — is the next iteration; see memory project_viewer_convergence.

DISCARD COLOUR RULE (Option C): when has_jpg is True the Blue/Purple circles
are greyed (a JPG-bearing picture can never be a deletion candidate). See
memory color-labels-and-delete-workflow.
"""

import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QTextEdit, QSizePolicy,
)
from PyQt6.QtGui import QPixmap, QGuiApplication, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QTimer

from gui.rating_editor import StarWidget, ColorSelector, _safe_int, COLOR_HEX
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
    def __init__(self, parent, *, image_path, display_name="",
                 rating=0, color="", description="", rate=None, date="",
                 has_jpg=False, pixmap=None,
                 on_rating=None, on_color=None, on_description=None,
                 settings_key="LargeImageView"):
        """
        image_path / display_name      what to show (image_path also titles the window).
        pixmap                         pre-decoded QPixmap to display; if given it
                                       overrides loading from image_path. Needed for
                                       RAW (.ORF etc.) which QPixmap can't load directly —
                                       the host passes its extracted preview pixmap.
        rating / color / description   initial values (rating int 0-5).
        rate / date                    read-only display extras (or None/"").
        has_jpg                        True → grey Blue/Purple (Option C).
        on_rating(int)                 called when stars change   (None = read-only).
        on_color(str)                  called when colour changes ("" clears).
        on_description(str)            called to persist description (None = read-only).
        settings_key                   namespace for remembered geometry (.ini).
        """
        super().__init__(parent)
        # Give the dialog real min/max buttons (a QDialog shows only close on
        # Windows) — the viewer often opens large, so maximize/minimize helps.
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        self._settings_key = settings_key
        self._geo_key = f"{settings_key}Large"
        self._on_description = on_description
        self._original_description = description or ""

        self.setWindowTitle(display_name or Path(str(image_path)).name)

        if pixmap is not None and not pixmap.isNull():
            self._pixmap = pixmap
        else:
            self._pixmap = (QPixmap(str(image_path))
                            if image_path and Path(str(image_path)).exists()
                            else QPixmap())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(6)

        # ── Image ─────────────────────────────────────────────────────────
        # The expanding "stage" defines the available area; inside it a tight
        # coloured frame hugs the scaled pixmap (sized in _fit_pixmap) and is
        # centred horizontally + vertically. The frame's border carries the
        # picture's colour label, updated live when the user picks a colour.
        self._stage = QWidget()
        self._stage.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Expanding)
        stage_v = QVBoxLayout(self._stage)
        stage_v.setContentsMargins(0, 0, 0, 0)
        stage_v.addStretch(1)
        stage_h = QHBoxLayout()
        stage_h.setContentsMargins(0, 0, 0, 0)
        stage_h.addStretch(1)
        self._img_frame = QWidget()
        self._img_frame.setObjectName("imgFrame")
        frame_l = QVBoxLayout(self._img_frame)
        frame_l.setContentsMargins(4, 4, 4, 4)  # room for the coloured border
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(1, 1)
        frame_l.addWidget(self._image_label)
        stage_h.addWidget(self._img_frame)
        stage_h.addStretch(1)
        stage_v.addLayout(stage_h)
        stage_v.addStretch(1)
        layout.addWidget(self._stage, stretch=1)
        self._apply_frame_color(color or "")

        # ── Controls strip (centred, ~1/3 width) ──────────────────────────
        controls = QWidget()
        cl = QVBoxLayout(controls)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)

        lbl_name = QLabel(display_name or Path(str(image_path)).name)
        lbl_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_name.setStyleSheet("font-weight: bold; font-size: 13px;")
        cl.addWidget(lbl_name)

        row = QHBoxLayout()
        self._stars = StarWidget(_safe_int(rating))
        if on_rating is not None:
            self._stars.rating_changed.connect(on_rating)
        else:
            self._stars.setEnabled(False)
        row.addWidget(self._stars)
        if date:
            lbl_date = QLabel(str(date)[:10].replace(":", "-"))
            lbl_date.setStyleSheet("font-size: 11px; color: #777;")
            row.addWidget(lbl_date)
        row.addStretch()
        self._rate_lbl = QLabel(f"Rate: {rate}" if rate is not None else "")
        self._rate_lbl.setStyleSheet("font-size: 11px; color: #555;")
        row.addWidget(self._rate_lbl)
        cl.addLayout(row)

        self._color_sel = ColorSelector(color or "")
        self._on_color = on_color
        if on_color is not None:
            self._color_sel.color_changed.connect(self._on_color_picked)
        else:
            self._color_sel.setEnabled(False)
        if has_jpg:
            self._color_sel.set_disabled_colors(_DISCARD_COLORS, _DISCARD_GREYED_TIP)
        cl.addWidget(self._color_sel)

        ctrl_row = QHBoxLayout()
        ctrl_row.addStretch(1)
        ctrl_row.addWidget(controls, 1)
        ctrl_row.addStretch(1)
        layout.addLayout(ctrl_row)

        # ── Description (Ctrl+S to save; auto-save on close) ───────────────
        desc_caption = QLabel("Enter a story about this picture to be shown on the website (optional)")
        desc_caption.setStyleSheet("font-size: 11px; font-weight: bold;")
        desc_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc_caption)

        self._desc_edit = QTextEdit()
        fm = self._desc_edit.fontMetrics()
        self._desc_edit.setFixedHeight(int(fm.lineSpacing() * 3.5) + 8)
        self._desc_edit.setAcceptRichText(False)
        self._desc_edit.setPlainText(self._original_description)
        if on_description is None:
            self._desc_edit.setReadOnly(True)
            self._desc_edit.setToolTip("Descriptions can't be saved for this picture.")
        desc_row = QHBoxLayout()
        desc_row.addStretch(1)
        desc_row.addWidget(self._desc_edit, 3)
        desc_row.addStretch(1)
        layout.addLayout(desc_row)

        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save_description)

        self._apply_default_or_remembered_geometry()

    # ── Rate refresh (host calls after a star/colour edit recomputes Rate) ─
    def set_rate(self, rate):
        self._rate_lbl.setText(f"Rate: {rate}" if rate is not None else "")

    # ── Colour frame ────────────────────────────────────────────────────────
    def _apply_frame_color(self, color: str):
        css = color_to_css(color)
        if css:
            self._img_frame.setStyleSheet(
                f"QWidget#imgFrame {{ border: 4px solid {css}; border-radius: 6px; }}")
        else:
            self._img_frame.setStyleSheet("")  # no label → no frame

    def _on_color_picked(self, color: str):
        self._apply_frame_color(color)  # live frame update
        if self._on_color is not None:
            self._on_color(color)        # persist via host

    # ── Description persistence ────────────────────────────────────────────
    def _save_description(self):
        if self._on_description is None:
            return
        text = self._desc_edit.toPlainText()
        if text == self._original_description:
            return
        try:
            self._on_description(text)
            self._original_description = text
        except Exception as e:
            logger.warning(f"LargeImageView: description save failed: {e}")

    # ── Geometry: default size, remembered per settings_key, clamped on-screen
    def _apply_default_or_remembered_geometry(self):
        # Per-dialog geometry in the per-machine .ini. Large views have no
        # columns, so a distinct "<key>Large" namespace avoids colliding with
        # the grid dialog that shares the same settings_key.
        avail = QGuiApplication.primaryScreen().availableGeometry()
        restore_window(self, self._geo_key,
                       default_size=(int(avail.width() * 0.7), int(avail.height() * 0.8)))

    # ── Pixmap fit ─────────────────────────────────────────────────────────
    def _fit_pixmap(self):
        if self._pixmap.isNull():
            self._image_label.setText("(no image)")
            return
        # Available area = the stage minus the frame's border margins.
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
        # Cap (don't fix) the label to the scaled pixmap: the frame still hugs
        # the photo via the label's sizeHint, but minimumSize(1,1) lets the
        # dialog shrink — setFixedSize here would lock the dialog's minimum size
        # to the current image and block making the window smaller.
        self._image_label.setMaximumSize(scaled.size())

    def showEvent(self, event):
        super().showEvent(event)
        clamp_to_visible_screen(self)
        QTimer.singleShot(0, self._fit_pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_pixmap()

    def closeEvent(self, event):
        self._save_description()
        save_window(self, self._geo_key)
        super().closeEvent(event)
