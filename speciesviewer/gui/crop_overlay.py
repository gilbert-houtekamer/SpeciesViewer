"""Interactive crop overlay for the large image viewer.

Drawn on top of the UNCROPPED frame, it lets the user rough out a crop the way
PhotoLab/Lightroom do — left-drag a rectangle, then drag it around or grab a
corner to resize — with the aspect ratio locked to the frame's own proportion
(so 4:3 bodies stay 4:3, 3:2 stay 3:2). Enter applies (the host writes it to the
sidecars as a suggestion for PL/LR); Esc cancels.

The overlay is a transparent child sized to exactly cover the displayed pixmap,
so its own width/height ARE the frame — a crop rectangle maps to a normalised
{left,top,width,height} (0-1) by simple division. Rect only; no straighten.
"""

from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen, QFont
from PyQt6.QtWidgets import QWidget

_HANDLE_HIT = 12    # px — how close to a corner counts as grabbing it
_MIN_FRAC = 0.05    # smallest crop, as a fraction of the frame

# Standard long-side sizes (RAW px) the crop can snap to, and the tolerance:
# accept a preset when the current long side is < preset × 1.05 (so a size just
# above a preset rounds DOWN to it), otherwise ceiling up to the next one.
_SIZE_PRESETS = (1000, 1200, 1500, 2000)
_SNAP_TOL = 1.05


def _snap_target(long_px: int):
    """The standard size the given long side snaps to, or None if it's larger
    than the biggest preset's tolerance (nothing to round up to)."""
    for p in _SIZE_PRESETS:
        if long_px < p * _SNAP_TOL:
            return p
    return None


# Common photographic aspect ratios (long/short). Sensors sit a hair off these
# (OM-1: 5220×3912 = 1.3344, not 1.3333), so locking the crop to the exact sensor
# ratio yields ugly short sides like 1200×899. Snapping to the nominal ratio gives
# the round numbers a 4:3 / 3:2 crop "should" have.
_COMMON_RATIOS = (4 / 3, 5 / 4, 3 / 2, 16 / 10, 16 / 9, 1 / 1)


def nominal_aspect(w: int, h: int):
    """The width/height aspect to lock the crop to: the nearest common ratio when
    the frame is within 2% of one (almost always), else the frame's exact ratio.
    Orientation preserved. None when dimensions are unknown."""
    if not w or not h:
        return None
    landscape = w >= h
    r = (w / h) if landscape else (h / w)   # long/short, ≥ 1
    best = min(_COMMON_RATIOS, key=lambda c: abs(c - r))
    if abs(best - r) / r > 0.02:
        best = r
    return best if landscape else 1.0 / best


class CropOverlay(QWidget):
    committed = pyqtSignal(dict)   # normalised {left,top,width,height}
    cancelled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._aspect = 3.0 / 2.0        # w/h; set per image via set_aspect
        self._full_w = 0                # full-frame pixel size, for the size readout
        self._full_h = 0
        self._rect: QRectF | None = None  # crop in widget (pixel) coords
        self._mode = None               # 'new'|'move'|'nw'|'ne'|'sw'|'se'
        self._anchor = QPointF()        # fixed point during a drag
        self._move_off = QPointF()      # cursor→rect offset while moving
        self._flash_msg = ""            # transient note shown in the hint line

    # ── configuration ────────────────────────────────────────────────────────
    def set_aspect(self, aspect: float):
        self._aspect = aspect if aspect and aspect > 0 else 1.0

    def set_full_size(self, w: int, h: int):
        """The full-frame pixel dimensions, so the readout can show the crop's
        approximate size in RAW pixels."""
        self._full_w, self._full_h = int(w or 0), int(h or 0)

    def crop_pixels(self):
        """The crop's size in full-frame pixels, or None if unknown. Uses ONE
        uniform frame→widget scale (the widget's integer pixmap dims make the
        per-axis ratios differ by ~1px otherwise — which showed as 2000×1499)."""
        if not self._rect or not self._full_w or self.width() <= 0:
            return None
        scale = self._full_w / self.width()   # same basis snap_to_standard builds with
        r = self._rect.normalized()
        return (round(r.width() * scale), round(r.height() * scale))

    def set_normalized(self, crop: dict | None):
        """Seed the crop from a normalised crop, or None for a blank canvas."""
        if crop and self.width() > 1 and self.height() > 1:
            w, h = self.width(), self.height()
            self._rect = QRectF(crop["left"] * w, crop["top"] * h,
                                crop["width"] * w, crop["height"] * h)
        else:
            self._rect = None
        self.update()

    def normalized(self) -> dict | None:
        if not self._rect or self.width() <= 0 or self.height() <= 0:
            return None
        w, h = float(self.width()), float(self.height())
        r = self._rect.normalized()
        left = max(0.0, min(1.0, r.left() / w))
        top = max(0.0, min(1.0, r.top() / h))
        return {
            "left": left,
            "top": top,
            "width": max(0.0, min(1.0 - left, r.width() / w)),
            "height": max(0.0, min(1.0 - top, r.height() / h)),
        }

    def has_crop(self) -> bool:
        return self._rect is not None

    # ── snap to a standard size ────────────────────────────────────────────────
    def snap_to_standard(self):
        """Resize the crop so its long side matches the nearest standard size
        (keeping the aspect and the current centre). No-op with a flashed note if
        there's no crop yet or it's larger than the biggest preset. Returns the
        target size, or None."""
        if not self._rect:
            self.flash("Draw a crop first")
            return None
        px = self.crop_pixels()
        if not px or not self._full_w or not self._full_h:
            return None
        long_px = max(px)
        target = _snap_target(long_px)
        if target is None:
            self.flash(f"Long side {long_px}px is above {_SIZE_PRESETS[-1]} — kept free")
            return None
        # Build a rectangle whose FRAME-pixel long side is exactly `target`, at the
        # locked aspect. Uniform scale (KeepAspectRatio) means frame px × s = screen
        # px, so the readout reads back the round target and target/aspect.
        s = self.width() / self._full_w
        A = self._aspect
        if A >= 1:                       # landscape: long side is the width
            w_frame, h_frame = target, target / A
        else:                            # portrait: long side is the height
            h_frame, w_frame = target, target * A
        w, h = w_frame * s, h_frame * s
        c = self._rect.center()
        left = min(max(0.0, c.x() - w / 2), max(0.0, self.width() - w))
        top = min(max(0.0, c.y() - h / 2), max(0.0, self.height() - h))
        self._rect = QRectF(left, top, w, h)
        self.flash(f"Snapped to {target}px")
        self.update()
        return target

    def toggle_orientation(self):
        """Swap the locked aspect between portrait and landscape (w/h ↔ h/w), so
        an upright frame can still yield a horizontal crop (and vice-versa). Any
        current rectangle is rotated 90° around its centre, then shrunk to fit
        inside the frame."""
        if not self._aspect:
            return
        self._aspect = 1.0 / self._aspect
        if self._rect:
            r = self._rect.normalized()
            c = r.center()
            long_side = max(r.width(), r.height())
            if self._aspect >= 1:            # landscape now: width is the long side
                w = long_side
                h = w / self._aspect
            else:                            # portrait now: height is the long side
                h = long_side
                w = h * self._aspect
            fit = min(1.0, self.width() / w if w else 1.0,
                      self.height() / h if h else 1.0)
            w, h = w * fit, h * fit
            left = min(max(0.0, c.x() - w / 2), max(0.0, self.width() - w))
            top = min(max(0.0, c.y() - h / 2), max(0.0, self.height() - h))
            self._rect = QRectF(left, top, w, h)
        self.update()

    def flash(self, msg: str, ms: int = 1800):
        self._flash_msg = msg
        self.update()
        QTimer.singleShot(ms, self._clear_flash)

    def _clear_flash(self):
        self._flash_msg = ""
        try:
            self.update()
        except RuntimeError:
            pass

    # ── geometry helpers ──────────────────────────────────────────────────────
    def _corners(self) -> dict:
        r = self._rect
        return {"nw": r.topLeft(), "ne": r.topRight(),
                "sw": r.bottomLeft(), "se": r.bottomRight()}

    def _hit_corner(self, pos: QPointF):
        if not self._rect:
            return None
        for name, pt in self._corners().items():
            if (abs(pos.x() - pt.x()) <= _HANDLE_HIT
                    and abs(pos.y() - pt.y()) <= _HANDLE_HIT):
                return name
        return None

    def _rect_from_anchor(self, anchor: QPointF, cur: QPointF) -> QRectF:
        """An aspect-locked rectangle spanning from a fixed anchor corner to the
        cursor, clamped to the frame."""
        dx, dy = cur.x() - anchor.x(), cur.y() - anchor.y()
        # Pick the dominant axis so the drag feels natural, then derive the other.
        if abs(dx) >= abs(dy) * self._aspect:
            w = abs(dx)
            h = w / self._aspect
        else:
            h = abs(dy)
            w = h * self._aspect
        sx = 1 if dx >= 0 else -1
        sy = 1 if dy >= 0 else -1
        # Clamp the size so the rectangle stays inside the frame from the anchor.
        max_w = (self.width() - anchor.x()) if sx > 0 else anchor.x()
        max_h = (self.height() - anchor.y()) if sy > 0 else anchor.y()
        scale = min(1.0,
                    max_w / w if w else 1.0,
                    (max_h * self._aspect) / w if w else 1.0)
        w *= scale
        h = w / self._aspect
        left = anchor.x() if sx > 0 else anchor.x() - w
        top = anchor.y() if sy > 0 else anchor.y() - h
        return QRectF(left, top, w, h)

    def _clamp_move(self, rect: QRectF) -> QRectF:
        x = min(max(0.0, rect.left()), self.width() - rect.width())
        y = min(max(0.0, rect.top()), self.height() - rect.height())
        return QRectF(x, y, rect.width(), rect.height())

    # ── mouse ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(e)
            return
        pos = e.position()
        corner = self._hit_corner(pos)
        if corner:
            self._mode = corner
            opp = {"nw": "se", "ne": "sw", "sw": "ne", "se": "nw"}[corner]
            self._anchor = self._corners()[opp]
        elif self._rect and self._rect.contains(pos):
            self._mode = "move"
            self._move_off = pos - self._rect.topLeft()
        else:
            self._mode = "new"
            self._anchor = pos
            self._rect = QRectF(pos, pos)
        self.setFocus()
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._mode in (None,):
            # hover feedback: corner → diagonal resize, inside → move
            c = self._hit_corner(pos)
            if c:
                self.setCursor(Qt.CursorShape.SizeFDiagCursor if c in ("nw", "se")
                               else Qt.CursorShape.SizeBDiagCursor)
            elif self._rect and self._rect.contains(pos):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if self._mode == "move":
            self._rect = self._clamp_move(QRectF(pos - self._move_off,
                                                 self._rect.size()))
        else:  # 'new' or a corner resize — both span from a fixed anchor
            self._rect = self._rect_from_anchor(self._anchor, pos)
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(e)
            return
        # Discard an accidental micro-drag so a stray click doesn't leave a dot.
        if (self._rect and (self._rect.width() < self._MIN_PX()
                            or self._rect.height() < self._MIN_PX())):
            self._rect = None
        self._mode = None
        self.update()

    def mouseDoubleClickEvent(self, e):
        if self._rect:
            self._emit_commit()

    def _MIN_PX(self) -> float:
        return _MIN_FRAC * min(self.width(), self.height())

    # ── keyboard ──────────────────────────────────────────────────────────────
    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._rect:
            self._emit_commit()
            e.accept()
        elif e.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            e.accept()
        else:
            super().keyPressEvent(e)

    def commit(self) -> bool:
        """Apply the drawn crop, exactly as Enter does. Public so a host action
        that CONSUMES the crop — hand the picture to PhotoLab, bake a JPG — can
        apply it first instead of silently using the previous framing. Returns
        True when a crop was emitted; a rectangle that collapsed to nothing is
        not a crop."""
        crop = self.normalized()
        if crop and crop["width"] > 0 and crop["height"] > 0:
            self.committed.emit(crop)
            return True
        return False

    def _emit_commit(self):
        self.commit()

    # ── paint ─────────────────────────────────────────────────────────────────
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        full = QRectF(0, 0, self.width(), self.height())
        if not self._rect:
            p.fillRect(full, QColor(0, 0, 0, 60))
            self._draw_hint(p, self._flash_msg or "Drag to draw a crop  ·  Esc to cancel")
            return

        r = self._rect.normalized()
        # Dim everything outside the crop (four bands around it).
        shade = QColor(0, 0, 0, 120)
        p.fillRect(QRectF(0, 0, full.width(), r.top()), shade)
        p.fillRect(QRectF(0, r.bottom(), full.width(), full.height() - r.bottom()), shade)
        p.fillRect(QRectF(0, r.top(), r.left(), r.height()), shade)
        p.fillRect(QRectF(r.right(), r.top(), full.width() - r.right(), r.height()), shade)

        # Rule-of-thirds guides.
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        for i in (1, 2):
            x = r.left() + r.width() * i / 3
            y = r.top() + r.height() * i / 3
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))

        # Crop border + corner handles.
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawRect(r)
        p.setBrush(QColor(255, 255, 255))
        p.setPen(Qt.PenStyle.NoPen)
        for pt in self._corners().values():
            p.drawRect(QRectF(pt.x() - 4, pt.y() - 4, 8, 8))

        # Approximate crop size in RAW pixels, as plain white text under the crop
        # (with a faint shadow for legibility over any background).
        px = self.crop_pixels()
        if px:
            self._draw_size(p, r, f"{px[0]} × {px[1]}")

        self._draw_hint(p, self._flash_msg or "Enter to apply  ·  Esc to cancel")

    def _draw_size(self, p: QPainter, r: QRectF, text: str):
        f = QFont()
        f.setPixelSize(14)
        f.setBold(True)
        p.setFont(f)
        # Keep the label on-screen even when the crop hugs the bottom edge.
        y = r.bottom() + 4 if r.bottom() + 24 < self.height() else r.bottom() - 24
        box = QRectF(r.left(), y, r.width(), 22)
        p.setPen(QColor(0, 0, 0, 160))
        p.drawText(box.translated(1, 1),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)
        p.setPen(QColor(255, 255, 255))
        p.drawText(box,
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)

    def _draw_hint(self, p: QPainter, text: str):
        p.setPen(QColor(255, 255, 255, 220))
        f = QFont()
        f.setPixelSize(12)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 6, self.width(), 20),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)
