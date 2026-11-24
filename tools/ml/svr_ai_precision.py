"""Experiment: the AI models at 16 and 8 bit — how much faster, and do they still agree?

ONE JOB: make FP16 and INT8 versions of the two models (MegaDetector, BioCLIP 2's
image encoder) and measure, on pictures of a real day, per route (graphics card
via DirectML, and the processor alone):
  speed     ms per picture for each model
  boxes     the most confident animal box: found by both, and overlap (IoU) ≥ 0.7
  smaller   MegaDetector exported at 960 and 640 px instead of 1280 (4x fewer
            pixels at 640: small birds may be missed — the box count shows it)
  names     BioCLIP's first name the same as the full-precision model's (all
            species it knows; that is harder than a country list)

  make   (the ML venv: needs onnx + onnxconverter-common)
    %LOCALAPPDATA%/SpeciesViewer/ml/Scripts/python tools/ml/svr_ai_precision.py make
  bench  (the app's Python: onnxruntime-directml)
    PYTHONPATH=speciesviewer python tools/ml/svr_ai_precision.py bench --folder "D:/.../2026-11-23"

INVARIANT: read-only for pictures and for app-data ai/; the variants go to
app-data ai_variants/.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent

LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SpeciesViewer"
AI = LOCAL / "ai"
OUT = LOCAL / "ai_variants"
MODELS = {"md": "megadetector_v5a.onnx", "clip": "bioclip2_image.onnx"}


def make():
    import onnx
    from onnxruntime.transformers.float16 import convert_float_to_float16
    from onnxruntime.quantization import QuantType, quantize_dynamic
    OUT.mkdir(parents=True, exist_ok=True)
    for key, name in MODELS.items():
        src = AI / name
        f16 = OUT / f"{key}_fp16.onnx"
        if not f16.exists():
            t0 = time.time()
            m = onnx.load(str(src))
            # ONNX Runtime's own converter: onnxconverter-common's left BioCLIP's
            # /visual/Cast typed wrong (the model did not load)
            m16 = convert_float_to_float16(m, keep_io_types=True)
            onnx.save(m16, str(f16))
            print(f"{f16.name}: {f16.stat().st_size / 1e6:.0f} MB · {time.time() - t0:.0f} s", flush=True)
        i8 = OUT / f"{key}_int8.onnx"
        if not i8.exists():
            t0 = time.time()
            quantize_dynamic(str(src), str(i8), weight_type=QuantType.QUInt8)
            print(f"{i8.name}: {i8.stat().st_size / 1e6:.0f} MB · {time.time() - t0:.0f} s", flush=True)
    for size in (960, 640):
        f = OUT / f"md_{size}.onnx"
        if not f.exists():
            export_md(f, size)
            print(f"{f.name}: {f.stat().st_size / 1e6:.0f} MB", flush=True)


def export_md(path: Path, size: int) -> None:
    """MegaDetector v5a as tools/ml/svr_onnx_export.py makes it, at another size."""
    import torch
    from megadetector.detection.run_detector import load_detector
    m = load_detector("MDV5A").model
    m.eval()
    for p in m.parameters():
        p.requires_grad_(False)
    m = m.float().cpu()

    class Raw(torch.nn.Module):
        def __init__(self, mm):
            super().__init__()
            self.mm = mm

        def forward(self, x):
            y = self.mm(x)
            return y[0] if isinstance(y, (list, tuple)) else y
    torch.onnx.export(Raw(m), torch.zeros(1, 3, size, size), str(path),
                      input_names=["image"], output_names=["boxes"], opset_version=17)


def session(path: Path, route: str):
    import onnxruntime as ort
    opt = ort.SessionOptions()
    opt.log_severity_level = 3
    opt.enable_mem_pattern = False
    opt.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    prov = (["DmlExecutionProvider", "CPUExecutionProvider"] if route == "gpu"
            else ["CPUExecutionProvider"])
    return ort.InferenceSession(str(path), opt, providers=prov)


def iou(a, b):
    if a is None or b is None:
        return 0.0
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def bench(folder: Path, n: int, routes):
    # only here: SpeciesViewer's own utils/ would hide YOLOv5's in "make"
    sys.path.insert(0, str(HERE.parents[1] / "speciesviewer"))
    from PIL import Image, ImageOps
    from engine import ai_models
    from engine.ai_measure import animal_box, crop_image
    from engine.picture_score import image_bytes
    srcs = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".orf", ".nef", ".jpg"))
    step = max(1, len(srcs) // n)
    srcs = srcs[::step][:n]
    t0 = time.time()
    ims = [ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes(s)))).convert("RGB")
           for s in srcs]
    print(f"{len(ims)} pictures from {folder.name}; decoding took "
          f"{(time.time() - t0) / len(ims) * 1000:.0f} ms per picture", flush=True)
    clip = ai_models.bioclip()            # the names (full precision), and _prep
    md = ai_models.MegaDetector.__new__(ai_models.MegaDetector)
    # (name, detector, encoder, detector size)
    variants = [("fp32", AI / MODELS["md"], AI / MODELS["clip"], 1280)] + [
        (v, OUT / f"md_{v}.onnx", OUT / f"clip_{v}.onnx", 1280) for v in ("fp16", "int8")] + [
        (f"md {s} px", OUT / f"md_{s}.onnx", AI / MODELS["clip"], s) for s in (960, 640)]
    for route in routes:
        print(f"\n== route: {'graphics card (DirectML)' if route == 'gpu' else 'processor only'}")
        ref = None
        for v, mdp, clp, size in variants:
            if not mdp.exists() or not clp.exists():
                print(f"  {v}: not made — run 'make' first")
                continue
            try:
                md.SIZE = size
                md.sess = session(mdp, route)
                clip.sess = session(clp, route)
                md.boxes(ims[0])                                   # warm up
                clip.picture_vectors([ims[0]])
            except Exception as e:
                print(f"  {v}: does not run on this route — {str(e)[:160]}")
                continue
            boxes, names, tmd, tclip = [], [], 0.0, 0.0
            for im in ims:
                t = time.time()
                b = animal_box(md.boxes(im))
                tmd += time.time() - t
                boxes.append(b)
                t = time.time()
                vec = clip.picture_vectors([crop_image(im, b)])[0]
                tclip += time.time() - t
                names.append(clip.top(vec, 1)[0][0])
            k = len(ims)
            line = (f"  {v}: MegaDetector {tmd / k * 1000:5.0f} ms · BioCLIP {tclip / k * 1000:5.0f} ms"
                    f" · together {(tmd + tclip) / k * 1000:5.0f} ms per picture")
            if ref is None:
                ref = (boxes, names)
            else:
                same_box = sum((a is None and b is None) or iou(a, b) >= 0.7
                               for a, b in zip(ref[0], boxes))
                same_name = sum(a == b for a, b in zip(ref[1], names))
                line += f"  |  same box {same_box}/{k} · same first name {same_name}/{k}"
            print(line, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=("make", "bench"))
    ap.add_argument("--folder", default="D:/CurrentPictures/2026/2026 Winter/2026-11-23")
    ap.add_argument("-n", type=int, default=40)
    ap.add_argument("--routes", default="gpu,cpu")
    a = ap.parse_args(argv)
    if a.what == "make":
        make()
    else:
        bench(Path(a.folder), a.n, a.routes.split(","))


if __name__ == "__main__":
    main()
