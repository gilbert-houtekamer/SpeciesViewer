"""Fetch the optional AI models (engine.ai_models) from the download site.

ONE JOB: read the manifest at MODELS_URL (file names, sizes, SHA-256), download
every file that is missing or different into app-data `ai/`, and say how far it is.
A broken connection costs nothing: a file grows as `<name>.part` and the next run
carries on where it stopped (HTTP Range).

INVARIANT: a model file only appears under its real name after its size AND its
SHA-256 match the manifest — so ai_models.available() never sees half a file.
Nothing outside `ai/` is touched; a file not in the manifest is left alone.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger("speciesviewer")

# The models live beside the author's website (2026-11-24). A new model version
# gets a new folder (v2/ …), so an old SpeciesViewer keeps finding its own files.
MODELS_URL = "https://houtekamer.com/speciesviewer/ai/v1/"
MANIFEST = "manifest.json"
CHUNK = 1 << 20
SPARE = 500 * 1024 * 1024          # leave this much free on the disk


class Cancelled(Exception):
    pass


def _get(url: str, start: int = 0, timeout: float = 30):
    import requests
    from utils import site_config
    # identity: the server would gzip .json/.txt on the fly — then the sizes
    # and a resumed Range no longer match the file itself
    headers = {"User-Agent": site_config.user_agent() or "SpeciesViewer",
               "Accept-Encoding": "identity"}
    if start:
        headers["Range"] = f"bytes={start}-"
    r = requests.get(url, headers=headers, stream=True, timeout=timeout)
    r.raise_for_status()
    return r


def manifest(base: str = MODELS_URL) -> dict:
    """{"version", "files": [{"name", "size", "sha256"}], ...} from the site."""
    r = _get(base + MANIFEST)
    m = json.loads(r.content.decode("utf-8"))
    if not m.get("files"):
        raise ValueError("The download list is empty")
    return m


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _have(folder: Path, f: dict) -> bool:
    """The file is there with the manifest's size — and its checksum, the first
    time (remembered in `<name>.ok`, so a later check does not read GBs again)."""
    p = folder / f["name"]
    if not p.is_file() or p.stat().st_size != f["size"]:
        return False
    ok = folder / (f["name"] + ".ok")
    try:
        if ok.read_text(encoding="utf-8").strip() == f["sha256"]:
            return True
    except OSError:
        pass
    if sha256(p) != f["sha256"]:
        return False
    ok.write_text(f["sha256"], encoding="utf-8")
    return True


def to_fetch(m: dict, folder: Path) -> list[dict]:
    return [f for f in m["files"] if not _have(folder, f)]


def still_needed(m: dict, folder: Path) -> int:
    """Bytes still to download (a .part counts for what it already holds)."""
    n = 0
    for f in to_fetch(m, folder):
        part = folder / (f["name"] + ".part")
        n += f["size"] - (part.stat().st_size if part.is_file() else 0)
    return n


def download(m: dict, folder: Path | None = None, base: str = MODELS_URL,
             progress=None, cancelled=lambda: False) -> list[str]:
    """Fetch what is missing. progress(done_bytes, total_bytes, file_name).
    Returns the names written. Raises Cancelled, OSError (disk), or the
    network's own error — a .part stays for the next run."""
    if folder is None:
        from engine.ai_models import ai_dir
        folder = ai_dir()
    folder.mkdir(parents=True, exist_ok=True)
    todo = to_fetch(m, folder)
    total = still_needed(m, folder)
    free = shutil.disk_usage(folder).free
    if total + SPARE > free:
        raise OSError(f"Not enough free space in {folder}: {total / 1e9:.1f} GB "
                      f"needed, {free / 1e9:.1f} GB free")
    done, written = 0, []
    for f in todo:
        part = folder / (f["name"] + ".part")
        have = part.stat().st_size if part.is_file() else 0
        if have > f["size"]:
            part.unlink()
            have = 0
        if have < f["size"]:
            r = _get(base + f["name"], have)
            if have and r.status_code != 206:        # the server ignored Range
                have = 0
            with r, open(part, "ab" if have else "wb") as out:
                for block in r.iter_content(CHUNK):
                    if cancelled():
                        raise Cancelled()
                    out.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total, f["name"])
        if part.stat().st_size != f["size"] or sha256(part) != f["sha256"]:
            part.unlink()
            raise ValueError(f"{f['name']} did not arrive intact — try again")
        target = folder / f["name"]
        part.replace(target)
        (folder / (f["name"] + ".ok")).write_text(f["sha256"], encoding="utf-8")
        written.append(f["name"])
        logger.info(f"AI models: {f['name']} downloaded ({f['size'] / 1e6:.0f} MB)")
    return written
