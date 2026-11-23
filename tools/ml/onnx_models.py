"""The two AI models through ONNX Runtime — NO PyTorch (the piece that would later
move into SpeciesViewer itself).

ONE JOB: given a picture (PIL image), MegaDetector's boxes and BioCLIP 2's top
species, from the files tools/ml/svr_onnx_export.py wrote to app-data `ai/`.
Only numpy, Pillow and onnxruntime. Runs on the graphics card where ONNX Runtime
offers one (DirectML on Windows, CoreML on a Mac), else on the CPU.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def providers() -> list[str]:
    import onnxruntime as ort
    have = ort.get_available_providers()
    return [p for p in ("DmlExecutionProvider", "CoreMLExecutionProvider",
                        "CUDAExecutionProvider") if p in have] + ["CPUExecutionProvider"]


def _session(path: Path):
    import onnxruntime as ort
    opt = ort.SessionOptions()
    opt.log_severity_level = 3
    return ort.InferenceSession(str(path), opt, providers=providers())


class BioClip:
    def __init__(self, folder: Path):
        cfg = json.loads((folder / "bioclip2.json").read_text(encoding="utf-8"))
        self.size = cfg["size"]
        self.mean = np.array(cfg["mean"], np.float32).reshape(1, 3, 1, 1)
        self.std = np.array(cfg["std"], np.float32).reshape(1, 3, 1, 1)
        self.scale = float(cfg["logit_scale"])
        self.sess = _session(folder / "bioclip2_image.onnx")
        self.all_vectors = np.load(folder / "bioclip2_names.npy").astype(np.float32)  # 768 × N
        self.all_names = json.loads((folder / "bioclip2_names.json").read_text(encoding="utf-8"))
        self.keep(None)

    def keep(self, mask) -> None:
        """Narrow the candidates (a bool per name), or None for all."""
        if mask is None:
            self.vectors, self.names = self.all_vectors, self.all_names
        else:
            idx = np.flatnonzero(np.asarray(mask))
            self.vectors = np.ascontiguousarray(self.all_vectors[:, idx])
            self.names = [self.all_names[i] for i in idx]

    def _prep(self, ims) -> np.ndarray:
        from PIL import Image
        arr = np.stack([np.asarray(im.convert("RGB").resize(
            (self.size, self.size), Image.Resampling.BILINEAR), np.float32) / 255.0
            for im in ims]).transpose(0, 3, 1, 2)
        return (arr - self.mean) / self.std

    def picture_vectors(self, ims, batch: int = 32) -> np.ndarray:
        out = []
        for i in range(0, len(ims), batch):
            out.append(self.sess.run(None, {"image": self._prep(ims[i:i + batch])})[0])
        return np.concatenate(out) if out else np.zeros((0, 768), np.float32)

    def top(self, vecs: np.ndarray, k: int = 5) -> list[list[tuple[str, str, float]]]:
        """[(scientific name, common name, probability)] per picture, best first."""
        res = []
        for v in vecs:
            logits = self.scale * (v @ self.vectors)
            logits -= logits.max()
            p = np.exp(logits)
            p /= p.sum()
            best = np.argpartition(-p, k)[:k]
            best = best[np.argsort(-p[best])]
            row = []
            for j in best:
                tax, common = self.names[j]
                row.append((" ".join(tax[-2:]).strip().capitalize(), common or "", float(p[j])))
            res.append(row)
        return res


class MegaDetector:
    SIZE = 1280
    CATS = {0: "animal", 1: "person", 2: "vehicle"}

    def __init__(self, folder: Path):
        self.sess = _session(folder / "megadetector_v5a.onnx")

    def boxes(self, im, threshold: float = 0.1, iou: float = 0.45) -> list[dict]:
        """[{cat, conf, box [x0,y0,x1,y1] as fractions}] of an upright PIL image."""
        from PIL import Image
        W, H = im.size
        r = self.SIZE / max(W, H)
        w, h = round(W * r), round(H * r)
        canvas = Image.new("RGB", (self.SIZE, self.SIZE), (114, 114, 114))
        dx, dy = (self.SIZE - w) // 2, (self.SIZE - h) // 2
        canvas.paste(im.convert("RGB").resize((w, h), Image.Resampling.BILINEAR), (dx, dy))
        x = (np.asarray(canvas, np.float32) / 255.0).transpose(2, 0, 1)[None]
        pred = self.sess.run(None, {"image": x})[0][0]          # N × (4 + 1 + classes)
        conf_cls = pred[:, 5:] * pred[:, 4:5]
        cls = conf_cls.argmax(1)
        conf = conf_cls.max(1)
        sel = conf >= threshold
        if not sel.any():
            return []
        b, conf, cls = pred[sel, :4], conf[sel], cls[sel]
        xy0 = b[:, :2] - b[:, 2:] / 2
        xy1 = b[:, :2] + b[:, 2:] / 2
        boxes = np.concatenate([xy0, xy1], 1)
        keep = _nms(boxes + cls[:, None] * 4096, conf, iou)   # per class
        out = []
        for i in keep:
            x0, y0, x1, y1 = boxes[i]
            out.append({"cat": self.CATS.get(int(cls[i]), str(int(cls[i]))),
                        "conf": round(float(conf[i]), 3),
                        "box": [round(float(np.clip((x0 - dx) / w, 0, 1)), 4),
                                round(float(np.clip((y0 - dy) / h, 0, 1)), 4),
                                round(float(np.clip((x1 - dx) / w, 0, 1)), 4),
                                round(float(np.clip((y1 - dy) / h, 0, 1)), 4)]})
        return out


def _nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    order = scores.argsort()[::-1]
    keep = []
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx0 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy0 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx1 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy1 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx1 - xx0, 0, None) * np.clip(yy1 - yy0, 0, None)
        o = inter / (area[i] + area[order[1:]] - inter + 1e-9)
        order = order[1:][o <= iou]
    return keep
