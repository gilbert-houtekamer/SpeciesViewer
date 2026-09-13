"""QuickViewer — a zoomable look at the camera's embedded JPEG, nothing more.

Opened with `z` from the culling grid. It shows only the camera's own embedded
JPEG (the ~3200×2400 the OM-1 writes) — no RAW decode, no metadata panel, no
species editor — so it appears instantly. `←` / `→` step through the pictures,
`Esc` closes. The same culling keys as the grid work here — `1`–`5` colour,
`q w e r t` / `Ctrl+1`–`5` stars, `0` clear — each writes and advances, so you
can cull while pixel-peeping. On close the caller moves its cursor to the last
picture shown and repaints any card it changed; nothing else changes.

Deliberately NOT the Large Image View: that one is powerful but pays a startup
cost this view exists to skip. It reuses `_FullResView` (the zoom/pan viewport)
and `handle_cull_key` (the shared culling key handler) from elsewhere.

Displays (never sets) any exposure suggestion already recorded for the
picture — see gui/exposure_render — so pixel-peeping here shows what
PhotoLab/Lightroom will actually render. No slider here; that stays in the
Large Image View. A picture with no suggestion pays nothing extra.

Also shows the picture's colour label as a border (a stylesheet on the
wrapping frame — no pixel cost) and supports `[` / `]` to mark a range's
start/end, exactly like the grid and the Large Image View: resolved locally
(this view already carries the same ordered key list) and reported up via
selection_changed, so the grid's EXISTING selection highlighting and
bulk-action menu (Set exposure/rating/species for N pictures) just work on
whatever range you marked here — no new UI, reuses the grid's own handler.
"""

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget

from gui.image_loader import AsyncImageLoader
from gui.large_image_view import _FullResView, color_to_css
from gui.preview_extract import extract_preview_bytes


class QuickViewer(QDialog):
    navigated = pyqtSignal(str)   # key last shown — the caller moves its cursor here
    edited = pyqtSignal(set)      # keys whose rating/colour changed — caller repaints them
    selection_changed = pyqtSignal(set)   # a [ / ] range was marked — caller adopts it

    def __init__(self, parent, collection, keys, index: int):
        super().__init__(parent)
        self._collection = collection
        self._keys = list(keys)
        self._index = max(0, min(index, len(self._keys) - 1)) if self._keys else 0
        self._cur_key = None
        self._cur_ev = 0.0
        self._edited: set[str] = set()
        self._bracket_left: str | None = None
        self._bracket_right: str | None = None

        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setModal(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        # A colour-label border needs something to put it on — _FullResView
        # already sets its own background stylesheet, so a plain wrapping
        # frame (rather than restyling the view itself) keeps the two concerns
        # apart.
        self._frame = QWidget()
        self._frame.setObjectName("qvFrame")
        frame_lay = QVBoxLayout(self._frame)
        frame_lay.setContentsMargins(4, 4, 4, 4)
        self._view = _FullResView(zoomable=True)
        # The scroll area must not eat the arrow keys — they navigate, they don't
        # pan (drag pans). Keep focus on the dialog.
        self._view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        frame_lay.addWidget(self._view)
        lay.addWidget(self._frame)

        self._loader = AsyncImageLoader(self)

        scr = self.screen() or QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.resize(int(g.width() * 0.85), int(g.height() * 0.85))
            self.move(g.x() + (g.width() - self.width()) // 2,
                      g.y() + (g.height() - self.height()) // 2)

        self._load()

    # ── picture loading ──────────────────────────────────────────────────────
    def _load(self):
        if not self._keys:
            self.setWindowTitle("Quick view")
            self._view.set_message("Nothing to show.")
            return
        key = self._keys[self._index]
        self._cur_key = key
        self.setWindowTitle(f"Quick view — {key}  ({self._index + 1} of {len(self._keys)})")
        raw = self._collection.raw_path(key)
        if not raw:
            self._view.set_message("No RAW file for this picture.")
            return
        # Any exposure suggestion already set (large viewer / grid batch) is
        # applied on display, so QuickViewer shows what PhotoLab/Lightroom will
        # actually render rather than the untouched embed. Zero extra cost for
        # the normal case of no suggestion — apply_exposure_to_pixmap
        # short-circuits and returns the pixmap unchanged when ev is 0.
        try:
            item = self._collection.item_for_key(key)
            self._cur_ev = getattr(item, "exposure", 0.0) or 0.0
            self._apply_frame_color(getattr(item, "color", "") or "")
        except (ValueError, KeyError):
            self._cur_ev = 0.0
            self._apply_frame_color("")
        self._view.set_message("Reading the camera's JPEG…")
        self._loader.cancel_all()
        raw_s = str(raw)

        def produce():
            data = extract_preview_bytes(Path(raw_s))
            if not data:
                return None
            img = QImage.fromData(data)
            return img if not img.isNull() else None

        self._loader.request(produce, lambda px, k=key: self._show(px, k))

    def _show(self, px, key):
        if key != self._cur_key:
            return                       # a later step superseded this one
        if px is None or px.isNull():
            self._view.set_message("This RAW carries no embedded JPEG.")
            return
        from gui.exposure_render import apply_exposure_to_pixmap
        px = apply_exposure_to_pixmap(px, self._cur_ev)
        overlay = f"camera JPEG {px.width()}×{px.height()}"
        if self._cur_ev:
            from gui.exposure_widget import format_ev
            overlay += f"  ·  {format_ev(round(self._cur_ev * 3))} suggested"
        self._view.set_image(px, (0.5, 0.5), overlay=overlay)

    def _apply_frame_color(self, color: str):
        css = color_to_css(color)
        if css:
            self._frame.setStyleSheet(
                f"QWidget#qvFrame {{ border: 4px solid {css}; border-radius: 6px; }}")
        else:
            self._frame.setStyleSheet("")

    def _go(self, delta: int):
        if not self._keys:
            return
        self._index = max(0, min(self._index + delta, len(self._keys) - 1))
        self._load()

    def _goto_key(self, key: str):
        """handle_cull_key's advance-after-edit → step to `key` and load it."""
        try:
            self._index = self._keys.index(key)
        except ValueError:
            return
        self._load()

    def _mark_edited(self, key: str):
        """handle_cull_key's refresh callback — fires after a successful write."""
        self._edited.add(key)

    # ── range selection ([ / ]) ──────────────────────────────────────────────
    def _selection_from_brackets(self) -> set:
        """Mirrors the grid's and the Large Image View's own copy of this
        exactly (same shape, same self._keys order) — see image_viewer.py /
        large_image_view.py. One marker alone selects just that picture; both
        select the (order-independent) range between them."""
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

    # ── keys ─────────────────────────────────────────────────────────────────
    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.close()
            return
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._go(-1)
            return
        if k in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._go(1)
            return
        if k == Qt.Key.Key_BracketLeft and self._keys:
            self._bracket_left = self._keys[self._index]
            self.selection_changed.emit(self._selection_from_brackets())
            return
        if k == Qt.Key.Key_BracketRight and self._keys:
            self._bracket_right = self._keys[self._index]
            self.selection_changed.emit(self._selection_from_brackets())
            return
        # Colour / star keys — same handler the grid uses: write then advance.
        from gui.viewer_keys import handle_cull_key
        if self._keys and handle_cull_key(
                e, order=list(self._keys),
                current_key=self._keys[self._index],
                item_for_key=self._collection.item_for_key,
                set_cursor=self._goto_key,
                refresh_key=self._mark_edited,
                open_species=None, open_rating=None):
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        self._loader.cancel_all()
        if self._edited:
            self.edited.emit(set(self._edited))
        if self._cur_key:
            self.navigated.emit(self._cur_key)
        super().closeEvent(e)
