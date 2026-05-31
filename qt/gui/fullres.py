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


def fullres_cache_dir() -> Path:
    """Per-user cache directory for full-res JPEGs (never in the data tree)."""
    from db.master_db import _app_data_dir
    return _app_data_dir() / "fullres_cache"


def fullres_cache_path(raw_path, cache_root) -> Path:
    """Where the cached full-res JPEG for `raw_path` lives."""
    return Path(cache_root) / (Path(raw_path).stem + ".fullres.jpg")


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
            rgb = raw.postprocess()  # default AHD demosaic, sRGB
        Image.fromarray(rgb).save(str(out), "JPEG", quality=quality)
        return out
    except Exception as e:
        logger.warning(f"Full-res decode failed for {raw_path.name}: {e}")
        return None
