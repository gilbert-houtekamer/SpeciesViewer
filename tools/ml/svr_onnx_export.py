"""EXPERIMENT — convert the two AI models to ONNX, so SpeciesViewer can run them
without PyTorch (ONNX Runtime: DirectML on Windows, CoreML on a Mac, else the CPU).

ONE JOB: write, to app-data `ai/`, at FULL precision (32-bit, the same numbers as
the PyTorch run):
  bioclip2_image.onnx     BioCLIP 2's picture side: 1×3×224×224 → a unit vector
  bioclip2_names.npy      the ~950,000 species names as vectors (copied as is)
  bioclip2_names.json     their taxonomy + common names (copied as is)
  bioclip2.json           the scale that turns similarities into percentages,
                          and the picture preparation (size, mean, std)
  megadetector_v5a.onnx   MegaDetector v5a: 1×3×1280×1280 → raw boxes (NMS outside)
The PyTorch originals stay untouched; tools/ml/svr_onnx_check.py compares.

Runs in the experiment environment:
  %LOCALAPPDATA%\\SpeciesViewer\\ml\\Scripts\\python.exe tools/ml/svr_onnx_export.py
  ... --only bioclip | megadetector
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path


def ai_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    p = base / "SpeciesViewer" / "ai"
    p.mkdir(parents=True, exist_ok=True)
    return p


def export_bioclip(out: Path) -> None:
    import torch
    import torch.nn.functional as F
    import open_clip
    from bioclip.predict import TreeOfLifeClassifier

    model, _pre = open_clip.create_model_from_pretrained("hf-hub:imageomics/bioclip-2")
    model.eval()

    class ImageSide(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, x):
            return F.normalize(self.m.encode_image(x), dim=-1)

    x = torch.randn(2, 3, 224, 224)
    torch.onnx.export(ImageSide(model), x, str(out / "bioclip2_image.onnx"),
                      input_names=["image"], output_names=["vector"],
                      dynamic_axes={"image": {0: "n"}, "vector": {0: "n"}},
                      opset_version=17)
    # The name vectors + names: the files pybioclip itself downloaded.
    clf = TreeOfLifeClassifier(device="cpu")
    shutil.copyfile(clf.get_cached_datafile("embeddings/txt_emb_species.npy"),
                    out / "bioclip2_names.npy")
    shutil.copyfile(clf.get_cached_datafile("embeddings/txt_emb_species.json"),
                    out / "bioclip2_names.json")
    (out / "bioclip2.json").write_text(json.dumps({
        "logit_scale": float(model.logit_scale.exp().item()),
        "size": 224, "resize": "bilinear, squashed to a square (no crop)",
        "mean": [0.48145466, 0.4578275, 0.40821073],
        "std": [0.26862954, 0.26130258, 0.27577711],
        "source": "hf-hub:imageomics/bioclip-2 (MIT); names: imageomics/TreeOfLife-200M",
    }, indent=1), encoding="utf-8")


def export_megadetector(out: Path) -> None:
    import torch
    from megadetector.detection.run_detector import load_detector
    det = load_detector("MDV5A")
    m = det.model
    m.eval()
    for p in m.parameters():
        p.requires_grad_(False)
    m = m.float().cpu()

    class Raw(torch.nn.Module):          # YOLOv5 in eval returns (pred, features)
        def __init__(self, mm):
            super().__init__()
            self.mm = mm

        def forward(self, x):
            y = self.mm(x)
            return y[0] if isinstance(y, (list, tuple)) else y

    x = torch.zeros(1, 3, 1280, 1280)
    torch.onnx.export(Raw(m), x, str(out / "megadetector_v5a.onnx"),
                      input_names=["image"], output_names=["boxes"], opset_version=17)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", choices=["bioclip", "megadetector"])
    a = ap.parse_args(argv)
    out = ai_dir()
    t0 = time.time()
    if a.only in (None, "megadetector"):
        export_megadetector(out)
        # YOLOv5's own `utils` must not shadow anything later
        for k in [k for k in sys.modules if k == "utils" or k.startswith("utils.")]:
            del sys.modules[k]
        print(f"MegaDetector done ({time.time() - t0:.0f} s)", flush=True)
    if a.only in (None, "bioclip"):
        export_bioclip(out)
        print(f"BioCLIP 2 done ({time.time() - t0:.0f} s)", flush=True)
    for f in sorted(out.iterdir()):
        print(f"  {f.name}: {f.stat().st_size / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
