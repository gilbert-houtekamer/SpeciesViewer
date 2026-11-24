"""Put the AI model files on the download site (for engine/ai_download.py).

ONE JOB: from a working `ai/` folder (app-data, made by tools/ml/svr_onnx_export.py)
and the 16-bit models (app-data `ai_variants/`, made by tools/ml/svr_ai_precision.py
make) make the files to publish — the species-name vectors at half precision (1.3 GB
instead of 2.7; checked 2026-11-24: on 346 pictures not one first name changed,
certainty moved at most 0.07 %-points) — write `manifest.json` (name, size,
SHA-256 per file) and a NOTICE.txt with the licences, and upload them over SFTP
to the website's folder + /speciesviewer/ai/v2/ (= engine.ai_download.MODELS_URL).
v1 (the 32-bit models) stays on the site for SpeciesViewers before 16 bit.

INVARIANT: dry run unless --apply. The manifest is uploaded LAST, after every file
is on the server with the right size, so a half-finished upload is never offered.
Each file is checked afterwards over https (size). The working ai/ folder is
only read.

  PYTHONPATH=speciesviewer python tools/svr_ai_publish.py            # dry run
  PYTHONPATH=speciesviewer python tools/svr_ai_publish.py --apply
"""
from __future__ import annotations

import argparse
import json
import posixpath
import shutil
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "speciesviewer"))

from engine import ai_download, ai_models  # noqa: E402
from utils.cache_paths import app_data_dir  # noqa: E402

REMOTE_DIR = "speciesviewer/ai/v2"            # below the website's folder
# the 16-bit models, as tools/ml/svr_ai_precision.py names them → their name here
FP16 = {"md_fp16.onnx": ai_models.DETECTOR_FILE, "clip_fp16.onnx": ai_models.CLIP_IMAGE_FILE}
REPLACES = ["megadetector_v5a.onnx", "bioclip2_image.onnx"]   # v1's models

NOTICE = """SpeciesViewer — the optional AI models

megadetector_v5a_fp16.onnx
  MegaDetector v5a (Microsoft AI for Good Lab), MIT licence.
  https://github.com/agentmorris/MegaDetector — converted to ONNX, 16 bit.

bioclip2_image_fp16.onnx, bioclip2.json
  BioCLIP 2 (Imageomics Institute), MIT licence.
  https://huggingface.co/imageomics/bioclip-2 — the image encoder, converted to
  ONNX, 16 bit.

bioclip2_names.npy, bioclip2_names.json
  BioCLIP 2's text vectors for the species names of TreeOfLife-200M
  (names and taxonomy: CC0 1.0), stored at half precision.
  https://huggingface.co/datasets/imageomics/TreeOfLife-200M

The MIT licence: Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including without
limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom
the Software is furnished to do so, subject to the following conditions: The
above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS
IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.
"""


def stage(src: Path, out: Path, fp16: Path) -> list[Path]:
    """The files to publish, in `out` (copies; the npy at half precision)."""
    out.mkdir(parents=True, exist_ok=True)
    files = []
    origin = {v: fp16 / k for k, v in FP16.items()}
    for name in (ai_models.DETECTOR_FILE,) + ai_models.BIOCLIP_FILES:
        s, t = origin.get(name, src / name), out / name
        if not s.is_file():
            raise SystemExit(f"missing: {s}")
        if not t.is_file() or t.stat().st_mtime < s.stat().st_mtime:
            if name.endswith(".npy"):
                np.save(t, np.load(s).astype(np.float16))
            else:
                shutil.copy2(s, t)
        files.append(t)
    (out / "NOTICE.txt").write_text(NOTICE, encoding="utf-8")
    files.append(out / "NOTICE.txt")
    return files


def make_manifest(files: list[Path]) -> dict:
    rows = []
    for p in files:
        print(f"  checksum {p.name} ({p.stat().st_size / 1e6:.0f} MB)", flush=True)
        rows.append({"name": p.name, "size": p.stat().st_size, "sha256": ai_download.sha256(p)})
    return {"version": ai_models.MODEL_VERSION + " fp16", "made": time.strftime("%Y-%m-%d"),
            "files": rows, "replaces": REPLACES}


def upload(out: Path, m: dict) -> None:
    from engine import sftplib
    from utils.config import _config
    s = sftplib.Sftp(password=_config.get("websettings", "Sftppwd", fallback="").strip("'"))
    s.connect()
    c = s.connection
    remote = posixpath.join(s.remotePrefix(), REMOTE_DIR)
    try:
        c.makedirs(remote)
        for f in m["files"]:
            r = posixpath.join(remote, f["name"])
            try:
                if c.stat(r).st_size == f["size"]:
                    print(f"  on the server already: {f['name']}")
                    continue
            except OSError:
                pass
            t0 = time.time()
            print(f"  uploading {f['name']} ({f['size'] / 1e6:.0f} MB)...", flush=True)
            c.put(str(out / f["name"]), r + ".part", preserve_mtime=False)
            if c.stat(r + ".part").st_size != f["size"]:
                raise SystemExit(f"{f['name']}: wrong size on the server - stopped")
            try:
                c.remove(r)
            except OSError:
                pass
            c.rename(r + ".part", r)
            print(f"    done in {time.time() - t0:.0f} s", flush=True)
        (out / ai_download.MANIFEST).write_text(json.dumps(m, indent=1), encoding="utf-8")
        c.put(str(out / ai_download.MANIFEST), posixpath.join(remote, ai_download.MANIFEST),
              preserve_mtime=False)
    finally:
        s.disconnect()


def check_web(m: dict) -> bool:
    import requests
    ok = True
    got = ai_download.manifest()
    if got != m:
        print("  BAD: the manifest on the web differs from this one")
        ok = False
    for f in m["files"]:
        r = requests.head(ai_download.MODELS_URL + f["name"], timeout=30, allow_redirects=True,
                          headers={"Accept-Encoding": "identity"})
        size = int(r.headers.get("Content-Length", -1))
        good = r.status_code == 200 and size == f["size"]
        ok &= good
        print(f"  {'ok ' if good else 'BAD'} {f['name']}: http {r.status_code}, {size} bytes")
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ai_models.ai_dir()))
    ap.add_argument("--fp16", default=str(app_data_dir() / "ai_variants"))
    ap.add_argument("--stage", default=str(app_data_dir() / "ai_publish_v2"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true", help="only check what is on the web")
    a = ap.parse_args(argv)
    out = Path(a.stage)
    print(f"making the files in {out}")
    files = stage(Path(a.src), out, Path(a.fp16))
    m = make_manifest(files)
    total = sum(f["size"] for f in m["files"])
    print(f"{len(files)} files, {total / 1e9:.2f} GB -> {ai_download.MODELS_URL}")
    if a.check:
        print("checking over https:")
        print("all good" if check_web(m) else "SOMETHING IS WRONG - see above")
        return
    if not a.apply:
        print("dry run - nothing uploaded. Run with --apply to upload.")
        return
    upload(out, m)
    print("checking over https:")
    print("all good" if check_web(m) else "SOMETHING IS WRONG - see above")


if __name__ == "__main__":
    main()
