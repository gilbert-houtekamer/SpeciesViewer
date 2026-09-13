"""Live exposure-adjustment rendering for the large viewer's fast (embedded-
JPEG) preview.

Applies an EV shift as a 256-entry lookup table via OpenCV (cv2.LUT) — the
same class of pixel-touching pass fullres.py already runs for scale/denoise.
Measured on a real OM-1 embedded JPEG (3200x2400): a LUT costs ~2-5 ms, where
the naive equivalent (numpy float32 multiply + clip) cost ~80-90 ms for the
same result — the LUT is what makes this cheap enough to run on every slider
tick. A future highlights/midtones/shadows/blacks curve would bake into the
same 256-entry table at the same cost, not a second pass.
"""

import numpy as np


def exposure_lut(ev: float) -> np.ndarray:
    """256-entry uint8 lookup table for a plain EV (stop) shift:
    output = input * 2**ev, clipped to 0-255. ev=0 (or falsy) is identity."""
    if not ev:
        return np.arange(256, dtype=np.uint8)
    gain = 2.0 ** ev
    return np.clip(np.arange(256, dtype=np.float32) * gain, 0, 255).astype(np.uint8)


def apply_exposure_to_array(arr: np.ndarray, ev: float) -> np.ndarray:
    """Apply `ev` stops to an HxWx3 uint8 array via cv2.LUT. Returns `arr`
    unchanged (same object) when ev is 0/None — callers must not assume a
    fresh array in that case."""
    if not ev:
        return arr
    import cv2
    return cv2.LUT(arr, exposure_lut(ev))


def apply_exposure_to_qimage(img, ev: float):
    """Return a NEW QImage with `ev` stops of exposure applied, or `img`
    unchanged (same object) when ev is 0/None. Accepts any QImage; converts to
    RGB888 internally since that's what the numpy round-trip needs.

    The returned QImage owns its own buffer (`.copy()`) — required because the
    numpy array built from `adjusted` would otherwise be garbage-collected
    while Qt still held a raw pointer into it, corrupting or crashing on the
    next repaint."""
    if not ev:
        return img
    from PyQt6.QtGui import QImage

    if img.format() != QImage.Format.Format_RGB888:
        img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return img
    # Qt pads each row to its own stride (bytesPerLine), which can exceed
    # w*3 — reshaping straight to (h, w, 3) on the raw buffer would shear the
    # image diagonally by the padding. Slice to the real row width first.
    stride = img.bytesPerLine()
    buf = img.constBits()
    buf.setsize(stride * h)
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, stride)[:, : w * 3].reshape(h, w, 3)
    adjusted = np.ascontiguousarray(apply_exposure_to_array(arr, ev))
    out = QImage(adjusted.data, w, h, adjusted.strides[0], QImage.Format.Format_RGB888)
    return out.copy()


def apply_exposure_to_pixmap(pixmap, ev: float):
    """Same as apply_exposure_to_qimage, but for a QPixmap — what actually
    flows through the large viewer: AsyncImageLoader converts every decoded
    QImage to a QPixmap (QPixmap.fromImage) before handing it to a callback,
    since QPixmap can only safely be built on the GUI thread. Returns `pixmap`
    unchanged (same object) when ev is 0/None."""
    if not ev:
        return pixmap
    from PyQt6.QtGui import QPixmap
    adjusted_img = apply_exposure_to_qimage(pixmap.toImage(), ev)
    return QPixmap.fromImage(adjusted_img)
