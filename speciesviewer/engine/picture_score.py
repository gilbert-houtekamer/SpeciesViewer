"""Picture score — how sharp is the subject of ONE picture, and what does it look like.

ONE JOB: measure a picture and remember the measurement. No selection, no Qt.

  - SUBJECT SHARPNESS, not image sharpness. The frame (at LONG_EDGE px) is cut
    into GRID tiles; each tile's detail is its Laplacian energy after a light
    blur (so sensor noise does not count as detail). `sharp` is the mean of the
    sharpest TOP_SHARE of tiles — where the bird is, if it is sharp — so a sharp
    bird on 10 % of a soft frame scores high. `isolation` = those tiles / the
    median tile: high means a sharp subject on a soft background.
  - `dhash`: an 8×8 difference hash of a heavily blurred frame — the layout
    (where the subject is, its shape), not the texture. Two frames whose hashes
    differ little are the same pose.
  - `tiles`: the whole tile grid (rows × cols, upright frame), kept so a view
    can draw where the score looked.
  - `fine`: the same measure on a finer FINE_GRID, at the picture's OWN size
    (the camera's embedded JPEG: full size on the Nikons, 3200 px on the OM-1)
    — the pixel level that 1600 px smooths away, where "equally sharp" frames
    are told apart. Costs no extra decode: the full-size picture is decoded
    anyway before it is shrunk. `native` = its long edge in pixels.

INVARIANT: a measurement is cached per file path and is only reused while the
file's size and modification time are unchanged and SCORE_VERSION is the same.
The cache lives under app-data (rebuildable, never in Dropbox); nothing in the
master is ever written.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import numpy as np

logger = logging.getLogger("speciesviewer")

SCORE_VERSION = 4
LONG_EDGE = 1600                # analyse at this size (embedded previews are larger)
GRID = (16, 12)                 # tiles across × down
FINE_GRID = (48, 36)            # the pixel-level grid, at the picture's own size
TOP_SHARE = 0.03                # "the subject" = the sharpest 3 % of tiles (≥ 3)

EMPTY = {"sharp": 0.0, "isolation": 0.0, "clipped": 0.0, "dhash": "0" * 16, "tiles": None,
         "fine": None, "native": 0}


def _energy_grid(gray, grid):
    """Mean Laplacian energy per tile (rows × cols) of a grayscale frame, after
    a light blur so sensor noise does not count as detail."""
    import cv2
    f = gray.astype(np.float32) / 255.0
    lap = cv2.Laplacian(cv2.GaussianBlur(f, (0, 0), 1.0), cv2.CV_32F, ksize=3)
    energy = lap * lap
    gx, gy = grid
    th, tw = energy.shape[0] // gy, energy.shape[1] // gx
    return energy[:th * gy, :tw * gx].reshape(gy, th, gx, tw).mean(axis=(1, 3))


def score_image(jpeg: bytes) -> dict | None:
    """Measure one JPEG (bytes). None when it cannot be decoded."""
    import cv2
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    h, w = img.shape
    fine = np.clip(_energy_grid(img, FINE_GRID) * 1e4, 0, 65000).astype(np.float16)  # own size
    s = LONG_EDGE / max(h, w)
    if s < 1:
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    grid = _energy_grid(img, GRID)
    tiles = np.sort(grid.ravel())
    k = max(3, int(len(tiles) * TOP_SHARE))
    top = float(tiles[-k:].mean())
    med = float(np.median(tiles))
    clipped = float(((img >= 250) | (img <= 5)).mean())
    # 8×8 difference hash of a BLURRED frame — a 16×16 hash of the sharp frame
    # saw sand grains and a 1-second camera twitch as a "new picture".
    coarse = cv2.GaussianBlur(img, (0, 0), max(img.shape) / 150)
    small = cv2.resize(coarse, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).ravel()
    dhash = int("".join("1" if b else "0" for b in bits), 2)
    return {"sharp": top * 1e4, "isolation": top / (med + 1e-9),
            "clipped": clipped, "dhash": f"{dhash:016x}",
            "tiles": (grid * 1e4).astype(np.float32), "fine": fine, "native": max(h, w)}


def subject_box(tiles, pad: int = 1):
    """Where the subject is: the sharpest tile plus `pad` tiles around it, as
    fractions (x0, y0, x1, y1) of the frame. None without tiles."""
    if tiles is None:
        return None
    rows, cols = tiles.shape
    r, c = np.unravel_index(int(np.argmax(tiles)), tiles.shape)
    return (max(0, c - pad) / cols, max(0, r - pad) / rows,
            min(cols, c + pad + 1) / cols, min(rows, r + pad + 1) / rows)


def fine_in(fine, box, top: int = 3) -> float | None:
    """Pixel-level sharpness inside `box` (fractions): the mean of its `top`
    sharpest fine tiles. Comparing frames on the SAME box compares the same
    part of the scene."""
    if fine is None or box is None:
        return None
    rows, cols = fine.shape
    x0, y0, x1, y1 = box
    c0, c1 = int(x0 * cols), max(int(x0 * cols) + 1, int(np.ceil(x1 * cols)))
    r0, r1 = int(y0 * rows), max(int(y0 * rows) + 1, int(np.ceil(y1 * rows)))
    sub = np.asarray(fine[r0:r1, c0:c1], np.float32).ravel()
    if sub.size == 0:
        return None
    sub.sort()
    return float(sub[-top:].mean())


def frame_top(fine, top: int = 3) -> float | None:
    """The same measure as fine_in, over the whole frame: its sharpest part."""
    if fine is None:
        return None
    a = np.asarray(fine, np.float32).ravel()
    a.sort()
    return float(a[-top:].mean())


def hamming(a: str, b: str) -> float:
    """Share (0..1) of the 64 hash bits that differ."""
    return bin(int(a, 16) ^ int(b, 16)).count("1") / 64.0


def image_bytes(src: Path) -> bytes | None:
    """A JPEG of the picture, whatever the file: a camera JPG as it is, a HEIC
    (iPhone) / PNG / TIFF converted, a RAW through SpeciesViewer's own preview
    path (the camera's embedded JPEG, else a decode)."""
    from utils.raw_extensions import is_jpeg, is_raw
    try:
        if is_jpeg(src):
            return src.read_bytes()
        if not is_raw(src):
            import io
            import pillow_heif
            from PIL import Image, ImageOps
            pillow_heif.register_heif_opener()
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=90)
                return buf.getvalue()
        from gui.preview_extract import preview_jpeg_bytes
        return preview_jpeg_bytes(src)
    except Exception as e:
        logger.warning(f"picture score: could not read {src.name}: {e}")
        return None


# ── cache (app-data, never the master) ───────────────────────────────────────

def default_cache_path() -> Path:
    from utils.cache_paths import app_data_dir
    return app_data_dir() / "scores" / "picture_scores.sqlite"


class Cache:
    """path -> measurement, valid while size + mtime + SCORE_VERSION match."""

    def __init__(self, path: Path | None = None):
        path = Path(path) if path else default_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()     # one connection, many scoring threads
        self.db.execute("create table if not exists score4 (path text primary key, "
                        "mtime integer, size integer, version integer, sharp real, "
                        "isolation real, clipped real, dhash text, tiles blob, "
                        "fine blob, native integer)")

    def get(self, p: Path) -> dict | None:
        try:
            st = p.stat()
        except OSError:
            return None
        with self.lock:
            r = self.db.execute("select mtime,size,version,sharp,isolation,clipped,dhash,"
                                "tiles,fine,native from score4 where path=?",
                                (str(p),)).fetchone()
        if r and r[0] == st.st_mtime_ns and r[1] == st.st_size and r[2] == SCORE_VERSION:
            d = dict(zip(("sharp", "isolation", "clipped", "dhash"), r[3:7]))
            d["tiles"] = np.frombuffer(r[7], np.float32).reshape(GRID[1], GRID[0])
            d["fine"] = np.frombuffer(r[8], np.float16).reshape(FINE_GRID[1], FINE_GRID[0])
            d["native"] = r[9]
            return d
        return None

    def put(self, p: Path, sc: dict) -> None:
        st = p.stat()
        with self.lock:
            self.db.execute("insert or replace into score4 values (?,?,?,?,?,?,?,?,?,?,?)",
                            (str(p), st.st_mtime_ns, st.st_size, SCORE_VERSION,
                             sc["sharp"], sc["isolation"], sc["clipped"], sc["dhash"],
                             np.ascontiguousarray(sc["tiles"], np.float32).tobytes(),
                             np.ascontiguousarray(sc["fine"], np.float16).tobytes(),
                             int(sc["native"])))
            self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.close()


def score_pictures(pics: list[dict], cache: Cache, *,
                   progress: Callable[[int, int], None] | None = None,
                   cancelled: Callable[[], bool] | None = None,
                   on_decoded: Callable[[dict, bytes], None] | None = None,
                   workers: int = 4) -> int:
    """Add the measurement to every picture dict (keyed by p["src"], a Path), in
    place. `on_decoded(p, jpeg)` sees each picture that had to be decoded (the
    contact sheet saves its large view then). Returns how many were decoded
    (the rest came from the cache)."""
    decoded = [0]

    def one(p):
        if cancelled and cancelled():
            p.update(EMPTY)
            return
        sc = cache.get(p["src"])
        if sc is None:
            data = image_bytes(p["src"])
            if data and on_decoded:
                on_decoded(p, data)
            sc = score_image(data) if data else None
            if sc:
                cache.put(p["src"], sc)
                decoded[0] += 1
        p.update(sc or EMPTY)

    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        for i, _ in enumerate(ex.map(one, pics), 1):
            if progress:
                progress(i, len(pics))
    logger.info(f"picture score: {len(pics)} pictures, {decoded[0]} decoded "
                f"({time.time() - t0:.0f} s)")
    return decoded[0]
