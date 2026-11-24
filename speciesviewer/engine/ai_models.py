"""The two optional AI models — MegaDetector (where is the animal) and BioCLIP 2
(what is it) — through ONNX Runtime, without PyTorch.

ONE JOB: load the model files from app-data `ai/` once per run of the program and
answer two questions about a picture (a PIL image): the boxes of the animals in
it, and the top species for a picture vector. Runs on the graphics card where
ONNX Runtime offers one (DirectML on Windows, CoreML on a Mac), else on the CPU.

INVARIANT: optional. `available()` says whether the runtime and all model files
are present; nothing else in SpeciesViewer may assume they are. Read-only: the
model files are never written here (tools/ml/svr_onnx_export.py makes them).
See docs/plans/ai_day_plan.md.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger("speciesviewer")

DETECTOR_FILE = "megadetector_v5a.onnx"
BIOCLIP_FILES = ("bioclip2_image.onnx", "bioclip2_names.npy", "bioclip2_names.json",
                 "bioclip2.json")
MODEL_VERSION = "md5a+bioclip2"       # part of every cache key that holds model output

_lock = threading.Lock()
# ONE model run at a time: the DirectML route on Windows does not allow two runs
# on a session at once (an access violation that takes the program down — seen
# 2026-11-24 while scrolling fast through the Editor). The GPU is one device anyway.
_run_lock = threading.Lock()
_detector = None
_bioclip = None


def ai_dir() -> Path:
    from utils.cache_paths import app_data_dir
    return app_data_dir() / "ai"


def runtime_problem() -> str:
    """"" when ONNX Runtime loads, else why not (missing, or a DLL that fails)."""
    try:
        import onnxruntime  # noqa: F401
        return ""
    except Exception as e:          # ImportError, or OSError from a DLL
        return f"{type(e).__name__}: {e}"


def runtime_available() -> bool:
    return not runtime_problem()


def missing_files(folder: Path | None = None) -> list[str]:
    folder = folder or ai_dir()
    return [f for f in (DETECTOR_FILE,) + BIOCLIP_FILES if not (folder / f).exists()]


def available() -> bool:
    return runtime_available() and not missing_files()


def reset() -> None:
    """Forget the loaded models (after a download replaced their files)."""
    global _detector, _bioclip
    with _lock:
        _detector = _bioclip = None


def providers() -> list[str]:
    import onnxruntime as ort
    have = ort.get_available_providers()
    return [p for p in ("DmlExecutionProvider", "CoreMLExecutionProvider",
                        "CUDAExecutionProvider") if p in have] + ["CPUExecutionProvider"]


def _session(path: Path):
    import onnxruntime as ort
    opt = ort.SessionOptions()
    opt.log_severity_level = 3
    opt.enable_mem_pattern = False                       # what DirectML asks for
    opt.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(str(path), opt, providers=providers())


def detector() -> "MegaDetector":
    global _detector
    with _lock:
        if _detector is None:
            _detector = MegaDetector(ai_dir())
        return _detector


def bioclip() -> "BioClip":
    global _bioclip
    with _lock:
        if _bioclip is None:
            _bioclip = BioClip(ai_dir())
        return _bioclip


class BioClip:
    def __init__(self, folder: Path):
        cfg = json.loads((folder / "bioclip2.json").read_text(encoding="utf-8"))
        self.size = int(cfg["size"])
        self.mean = np.array(cfg["mean"], np.float32).reshape(1, 3, 1, 1)
        self.std = np.array(cfg["std"], np.float32).reshape(1, 3, 1, 1)
        self.scale = float(cfg["logit_scale"])
        self.sess = _session(folder / "bioclip2_image.onnx")
        self.vectors = np.load(folder / "bioclip2_names.npy").astype(np.float32)   # 768 × N
        names = json.loads((folder / "bioclip2_names.json").read_text(encoding="utf-8"))
        self.sci = [" ".join(t[-2:]).strip().capitalize() for t, _c in names]
        self.common = [c or "" for _t, c in names]
        self.taxa = [t for t, _c in names]
        # sci -> (class, family), e.g. ("aves", "accipitridae"); the taxonomy is
        # [kingdom, phylum, class, order, family, genus, epithet]
        self.group = {}
        for sci, tx in zip(self.sci, self.taxa):
            if len(tx) >= 5:
                self.group.setdefault(sci, (str(tx[2]).lower(), str(tx[4]).lower()))
        self._subsets: dict = {}
        self._sub_lock = threading.Lock()

    def subset(self, allowed) -> np.ndarray | None:
        """Column indices of the names in `allowed` (lower-case scientific
        names), cached per set; None = all names."""
        if allowed is None:
            return None
        key = frozenset(allowed)
        with self._sub_lock:
            idx = self._subsets.get(key)
            if idx is None:
                idx = np.array([i for i, s in enumerate(self.sci) if s.lower() in key], np.int64)
                self._subsets[key] = idx
            return idx

    def _prep(self, ims) -> np.ndarray:
        from PIL import Image
        arr = np.stack([np.asarray(im.convert("RGB").resize(
            (self.size, self.size), Image.Resampling.BILINEAR), np.float32) / 255.0
            for im in ims]).transpose(0, 3, 1, 2)
        return (arr - self.mean) / self.std

    def picture_vectors(self, ims, batch: int = 32) -> np.ndarray:
        out = []
        for i in range(0, len(ims), batch):
            x = self._prep(ims[i:i + batch])
            with _run_lock:
                out.append(self.sess.run(None, {"image": x})[0])
        return np.concatenate(out) if out else np.zeros((0, self.vectors.shape[0]), np.float32)

    def top(self, vec: np.ndarray, k: int = 3, allowed=None) -> list[tuple[str, str, float]]:
        """[(scientific name, common name, probability)] for ONE picture vector,
        best first, among `allowed` names (None = all ~950,000)."""
        idx = self.subset(allowed)
        mat = self.vectors if idx is None else self.vectors[:, idx]
        if mat.shape[1] == 0:
            return []
        logits = self.scale * (np.asarray(vec, np.float32) @ mat)
        logits -= logits.max()
        p = np.exp(logits)
        p /= p.sum()
        k = min(k, p.size)
        best = np.argpartition(-p, k - 1)[:k]
        best = best[np.argsort(-p[best])]
        out = []
        for j in best:
            g = int(j if idx is None else idx[j])
            out.append((self.sci[g], self.common[g], float(p[j])))
        return out


class MegaDetector:
    SIZE = 1280
    CATS = {0: "animal", 1: "person", 2: "vehicle"}

    def __init__(self, folder: Path):
        self.sess = _session(folder / DETECTOR_FILE)

    def boxes(self, im, threshold: float = 0.1, iou: float = 0.45) -> list[dict]:
        """[{cat, conf, box [x0,y0,x1,y1] as fractions}] of an upright PIL image."""
        from PIL import Image
        W, H = im.size
        r = self.SIZE / max(W, H)
        w, h = max(1, round(W * r)), max(1, round(H * r))
        canvas = Image.new("RGB", (self.SIZE, self.SIZE), (114, 114, 114))
        dx, dy = (self.SIZE - w) // 2, (self.SIZE - h) // 2
        canvas.paste(im.convert("RGB").resize((w, h), Image.Resampling.BILINEAR), (dx, dy))
        x = (np.asarray(canvas, np.float32) / 255.0).transpose(2, 0, 1)[None]
        with _run_lock:
            pred = self.sess.run(None, {"image": x})[0][0]       # N × (4 + 1 + classes)
        conf_cls = pred[:, 5:] * pred[:, 4:5]
        cls = conf_cls.argmax(1)
        conf = conf_cls.max(1)
        sel = conf >= threshold
        if not sel.any():
            return []
        b, conf, cls = pred[sel, :4], conf[sel], cls[sel]
        boxes = np.concatenate([b[:, :2] - b[:, 2:] / 2, b[:, :2] + b[:, 2:] / 2], 1)
        out = []
        for i in nms(boxes + cls[:, None] * 4096, conf, iou):          # per class
            x0, y0, x1, y1 = boxes[i]
            out.append({"cat": self.CATS.get(int(cls[i]), str(int(cls[i]))),
                        "conf": round(float(conf[i]), 3),
                        "box": [round(float(np.clip((x0 - dx) / w, 0, 1)), 4),
                                round(float(np.clip((y0 - dy) / h, 0, 1)), 4),
                                round(float(np.clip((x1 - dx) / w, 0, 1)), 4),
                                round(float(np.clip((y1 - dy) / h, 0, 1)), 4)]})
        return out


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    order = scores.argsort()[::-1]
    keep = []
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order.size:
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        xx0 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy0 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx1 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy1 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx1 - xx0, 0, None) * np.clip(yy1 - yy0, 0, None)
        o = inter / (area[i] + area[rest] - inter + 1e-9)
        order = rest[o <= iou]
    return keep
