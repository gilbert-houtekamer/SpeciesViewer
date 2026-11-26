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

# 16 bit (2026-11-24): 1.9x faster on a laptop's built-in graphics (SmallBoy), the
# same box on 99 % of pictures (tools/ml/svr_ai_precision.py). Inputs and outputs
# stay 32 bit, so nothing else changes.
DETECTOR_FILE = "megadetector_v5a_fp16.onnx"
CLIP_IMAGE_FILE = "bioclip2_image_fp16.onnx"
BIOCLIP_FILES = (CLIP_IMAGE_FILE, "bioclip2_names.npy", "bioclip2_names.json",
                 "bioclip2.json")
# Part of every cache key that holds model output. NOT changed for 16 bit: its
# boxes and vectors are the same as 32 bit's for all practical use, and a new key
# would make every master picture be looked at again (and empty the yardstick).
MODEL_VERSION = "md5a+bioclip2"

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
        self.sess = _session(folder / CLIP_IMAGE_FILE)
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
        self._mats: dict = {}            # allowed → the name matrix cut to it
        self._by_common = None
        self._sub_lock = threading.Lock()

    def match_list(self, allowed, taxonomy: dict) -> frozenset:
        """eBird's names (lower-case) as BioCLIP's own names. eBird follows the
        newest genera — Ardea ibis, Astur gentilis, Curruca communis — where
        BioCLIP still has Bubulcus ibis, Accipiter gentilis, Sylvia communis;
        without this those species could never be named (the cattle egret, GH
        2026-11-24: 46 of NL's 588). A name BioCLIP lacks is matched on the
        English name, else on the species epithet within the same family —
        only when exactly one bird fits. `taxonomy` = ebird.taxonomy()."""
        if self._by_common is None:
            by_common, by_ep_fam = {}, {}
            for s, c in zip(self.sci, self.common):
                cl, fam = self.group.get(s, ("", ""))
                if cl != "aves":
                    continue
                if c:
                    by_common.setdefault(c.lower(), set()).add(s.lower())
                by_ep_fam.setdefault((s.split()[-1].lower(), fam), set()).add(s.lower())
            self._by_common, self._by_ep_fam = by_common, by_ep_fam
            self._have = {s.lower() for s in self.sci}
        out = set()
        for a in allowed:
            if a in self._have:
                out.add(a)
                continue
            common, family = taxonomy.get(a, ("", ""))
            hit = self._by_common.get(common.lower(), set()) if common else set()
            if len(hit) != 1:
                hit = self._by_ep_fam.get((a.split()[-1], family.lower()), set())
            if len(hit) == 1:
                out |= hit
        return frozenset(out)

    def match_any(self, allowed, english: dict) -> frozenset:
        """iNaturalist's names (lower-case, any group) as BioCLIP's own names.
        iNaturalist follows the newest genera, BioCLIP often an older one: the
        same name, else the one BioCLIP name with the same English name, else
        the one BioCLIP name with the same species epithet in a class that the
        list's other names are in — only when exactly one fits.
        `english` = {name: English name} (engine.inat_lists.english_names)."""
        if getattr(self, "_by_common_all", None) is None:
            by_common: dict = {}
            by_ep_cls: dict = {}
            for s, c in zip(self.sci, self.common):
                cls = self.group.get(s, ("", ""))[0]
                if c:
                    by_common.setdefault(c.lower(), set()).add(s.lower())
                by_ep_cls.setdefault((s.split()[-1].lower(), cls), set()).add(s.lower())
            self._by_common_all, self._by_ep_cls = by_common, by_ep_cls
            self._have = {s.lower() for s in self.sci}
        out = {a for a in allowed if a in self._have}
        lower = {s.lower(): s for s in self.sci}
        classes = {self.group.get(lower[a], ("", ""))[0] for a in out} - {""}
        for a in allowed:
            if a in out:
                continue
            hit = self._by_common_all.get((english.get(a) or "").lower(), set())                 if english.get(a) else set()
            if len(hit) != 1:
                hit = set().union(*(self._by_ep_cls.get((a.split()[-1], c), set())
                                    for c in classes)) if classes else set()
            if len(hit) == 1:
                out |= hit
        return frozenset(out)

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

    def _matrix(self, allowed):
        """(column indices | None, the name matrix for `allowed`). The cut-out
        is kept: cutting it from the 768 × 867,455 matrix took 17 ms per name,
        nearly all of a name's time (2026-11-26)."""
        if allowed is None:
            return None, self.vectors
        key = frozenset(allowed)
        with self._sub_lock:
            if getattr(self, "_mats", None) is None:
                self._mats = {}
            mat = self._mats.get(key)
        idx = self.subset(key)
        if mat is None:
            mat = np.ascontiguousarray(self.vectors[:, idx])
            with self._sub_lock:
                if len(self._mats) >= 8:              # a handful of lists per run
                    self._mats.pop(next(iter(self._mats)))
                self._mats[key] = mat
        return idx, mat

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
        best first, among `allowed` names (None = all ~870,000)."""
        return self.top_many([vec], k, allowed)[0]

    def top_many(self, vecs, k: int = 3, allowed=None,
                 chunk: int = 32) -> list[list[tuple[str, str, float]]]:
        """top() for many picture vectors at once: one matrix product per chunk
        instead of one per picture — against all names 1.6 ms a picture in a
        batch, 50 ms alone (2026-11-26). `chunk` rows at a time keeps the
        product small (32 × 867,455 floats = 110 MB)."""
        idx, mat = self._matrix(allowed)
        out: list = []
        if mat.shape[1] == 0:
            return [[] for _ in vecs]
        V = np.asarray(np.stack([np.asarray(v, np.float32) for v in vecs]), np.float32)             if len(vecs) else np.zeros((0, mat.shape[0]), np.float32)
        kk = min(k, mat.shape[1])
        for i in range(0, len(V), chunk):
            logits = self.scale * (V[i:i + chunk] @ mat)
            logits -= logits.max(axis=1, keepdims=True)
            p = np.exp(logits)
            p /= p.sum(axis=1, keepdims=True)
            best = np.argpartition(-p, kk - 1, axis=1)[:, :kk]
            for row, cols in zip(p, best):
                cols = cols[np.argsort(-row[cols])]
                out.append([(self.sci[int(c if idx is None else idx[c])],
                             self.common[int(c if idx is None else idx[c])],
                             float(row[c])) for c in cols])
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
