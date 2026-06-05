"""Full-resolution (1:1) decode + cache for the culling 100% view.

The embedded RAW preview is only ~3200x2400, so true pixel-level sharpness — the
whole point of the 100% view — needs the full sensor (e.g. 5220x3912). That means
decoding the RAW with rawpy (libraw), ~1s/file. Results are cached as full-res
JPEGs in the per-user app cache (NEVER in the data tree, same rule as the preview
cache), so the cost is paid once and later viewing is instant.

Callers skip Blue/Purple-labelled pictures (already-rejected) before asking for a
decode, and run batches on a worker thread (a group of 400 ≈ a coffee break).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("speciesviewer")


# ── Decode quality ────────────────────────────────────────────────────────────
# Configure via Settings → Image Processing.  Defaults (when no config exists)
# use the "Fast, basic" preset: DHT, FBDD Off, no post-denoise (~1 s/photo).
#
# NOTE: noise_thr (rawpy wavelet denoising) is intentionally absent.  It is
# broken with DHT and AAHD in LibRaw 0.22 — the combination produces a 2× green
# channel bias.  Post-decode OpenCV NL-means (below) is used instead.

def _read_fullres_config() -> dict:
    try:
        from utils.config import _config
        sec = "fullres"
        return {
            "algorithm":  _config.get    (sec, "algorithm",  fallback="DHT"),
            "fbdd":       _config.get    (sec, "fbdd",       fallback="Off"),
            "bright":     _config.getfloat(sec, "bright",   fallback=1.0),
            "denoise":    _config.get    (sec, "denoise",    fallback="Off"),
            "denoise_h":  _config.getint (sec, "denoise_h",  fallback=3),
            "denoise_hc": _config.getint (sec, "denoise_hc", fallback=3),
        }
    except Exception:
        return {"algorithm": "DHT", "fbdd": "Off", "bright": 1.0,
                "denoise": "Off", "denoise_h": 3, "denoise_hc": 3}


def _postprocess_params():
    """rawpy.postprocess kwargs from the Image Processing settings."""
    import rawpy
    cfg = _read_fullres_config()
    return dict(
        use_camera_wb=True,
        output_color=rawpy.ColorSpace.sRGB,
        no_auto_bright=True,
        bright=cfg["bright"],
        median_filter_passes=0,
        demosaic_algorithm=getattr(rawpy.DemosaicAlgorithm, cfg["algorithm"],
                                   rawpy.DemosaicAlgorithm.DHT),
        fbdd_noise_reduction=getattr(rawpy.FBDDNoiseReductionMode, cfg["fbdd"],
                                     rawpy.FBDDNoiseReductionMode.Off),
    )


def _apply_denoise(rgb):
    """Apply post-decode OpenCV NL-means if configured; returns rgb (may be a new array)."""
    try:
        from utils.config import _config
        if _config.get("fullres", "denoise", fallback="Off") == "OpenCV NL":
            import cv2
            import numpy as np
            h  = _config.getint("fullres", "denoise_h",  fallback=3)
            hc = _config.getint("fullres", "denoise_hc", fallback=3)
            return cv2.fastNlMeansDenoisingColored(
                np.ascontiguousarray(rgb), None, float(h), float(hc), 7, 21)
    except Exception as e:
        logger.debug(f"Full-res denoise skipped: {e}")
    return rgb


def fullres_cache_dir() -> Path:
    """Per-user cache directory for full-res JPEGs (never in the data tree)."""
    from db.master_db import _app_data_dir
    return _app_data_dir() / "fullres_cache"


def fullres_cache_path(raw_path, cache_root) -> Path:
    """Where the cached full-res JPEG for `raw_path` lives."""
    return Path(cache_root) / (Path(raw_path).stem + ".fullres.jpg")


def fullres_cache_stats():
    """(file_count, total_bytes) for the full-res cache."""
    d = fullres_cache_dir()
    count = total = 0
    if d.is_dir():
        for f in d.glob("*.fullres.jpg"):
            try:
                total += f.stat().st_size
                count += 1
            except OSError:
                pass
    return count, total


def clear_fullres_cache() -> int:
    """Delete all cached full-res JPEGs. Returns how many files were removed."""
    d = fullres_cache_dir()
    if not d.is_dir():
        return 0
    n = 0
    for f in d.glob("*.fullres.jpg"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    logger.info(f"Cleared {n} full-resolution cache file(s) from {d}.")
    return n


# ── Preview cache helpers (AppData/SpeciesViewer/preview_cache/) ──────────────

def preview_cache_root() -> Path:
    from db.master_db import _app_data_dir
    return _app_data_dir() / "preview_cache"


def preview_cache_stats() -> tuple[int, int]:
    """(file_count, total_bytes) for the preview cache."""
    d = preview_cache_root()
    count = total = 0
    if d.is_dir():
        for f in d.rglob("*.jpg"):
            try:
                total += f.stat().st_size
                count += 1
            except OSError:
                pass
    return count, total


def clear_preview_cache() -> int:
    """Delete the entire preview cache tree. Returns approximate file count."""
    import shutil
    d = preview_cache_root()
    if not d.is_dir():
        return 0
    count, _ = preview_cache_stats()
    shutil.rmtree(d, ignore_errors=True)
    logger.info(f"Cleared ~{count} preview cache file(s) from {d}.")
    return count


def run_fullres_batch(parent, raw_paths, cache_root=None):
    """Decode a list of RAWs into the full-res cache on a worker thread, behind a
    modal progress dialog with Cancel. Already-cached files are quick no-ops, so
    re-running is cheap. (Select a few hundred, start it, go for coffee.)"""
    from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal
    from PyQt6.QtWidgets import QProgressDialog

    raws = [str(r) for r in raw_paths if r]
    if not raws:
        return
    cache_root = str(cache_root or fullres_cache_dir())

    class _Worker(QObject):
        progress = pyqtSignal(int, str)
        finished = pyqtSignal(int)

        def __init__(self):
            super().__init__()
            self._cancel = False

        def cancel(self):
            self._cancel = True

        def run(self):
            done = 0
            for i, r in enumerate(raws, 1):
                if self._cancel:
                    break
                self.progress.emit(i, Path(r).name)
                if ensure_fullres_jpeg(r, cache_root):
                    done += 1
            self.finished.emit(done)

    dlg = QProgressDialog(f"Decoding {len(raws)} pictures at full resolution…",
                          "Cancel", 0, len(raws), parent)
    dlg.setWindowTitle("Full resolution")
    dlg.setMinimumDuration(0)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    thread = QThread(parent)
    worker = _Worker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.progress.connect(lambda i, name: (dlg.setValue(i),
                                             dlg.setLabelText(f"{i} / {len(raws)}\n{name}")))
    dlg.canceled.connect(worker.cancel)

    def on_finished(done):
        thread.quit()
        thread.wait()
        dlg.close()
        logger.info(f"Full resolution: {done} of {len(raws)} ready in cache.")

    worker.finished.connect(on_finished)
    dlg._thread, dlg._worker = thread, worker  # keep alive until done
    thread.start()
    dlg.exec()


def ensure_fullres_jpeg(raw_path, cache_root, quality=90):
    """Return a cached full-res JPEG Path for `raw_path`, decoding the RAW once
    via rawpy if not already cached. Returns None on failure (missing file,
    decode error, or rawpy unavailable)."""
    raw_path = Path(raw_path)
    out = fullres_cache_path(raw_path, cache_root)
    if out.exists():
        return out
    if not raw_path.exists():
        return None
    try:
        import rawpy
        from PIL import Image
        out.parent.mkdir(parents=True, exist_ok=True)
        with rawpy.imread(str(raw_path)) as raw:
            rgb = raw.postprocess(**_postprocess_params())
        rgb = _apply_denoise(rgb)
        Image.fromarray(rgb).save(str(out), "JPEG", quality=quality)
        return out
    except Exception as e:
        logger.warning(f"Full-res decode failed for {raw_path.name}: {e}")
        return None
