"""A country pack: the saved species lists of one country in one zip, to carry
from one computer to another (fetch at home on the big computer, use offline
on the laptop in the field).

ONE JOB: pack and unpack the saved lists of a country —
  species_lists/inat_<CC>_<group>.json   iNaturalist's species per group
  species_lists/inat_places.json         (only that country's entry travels)
  ebird/spplist_<CC>.json + ebird/taxonomy.json   eBird's birds
Nothing else: no pictures, no settings, no keys.

INVARIANT: unpacking never makes a list older or smaller by accident: a list
already here that was fetched LATER than the one in the pack is kept; nothing
is deleted. Unpacking reports what it took and what it kept.
"""
from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

MANIFEST = "country_pack.json"


def _ebird_dir() -> Path:
    from utils.cache_paths import app_data_dir
    return app_data_dir() / "ebird"


def _lists_dir() -> Path:
    from engine.inat_lists import folder
    return folder()


def files_for(region: str) -> dict[str, Path]:
    """{name in the zip: file here} of a country's saved lists."""
    region = region.upper()
    out = {}
    for f in sorted(_lists_dir().glob(f"inat_{region}_*.json")):
        out[f"species_lists/{f.name}"] = f
    for name in (f"spplist_{region}.json", "taxonomy.json"):
        f = _ebird_dir() / name
        if f.exists():
            out[f"ebird/{name}"] = f
    return out


def export(region: str, dest: Path) -> tuple[Path, list[str]]:
    """Write <dest>/species_lists_<CC>.zip; (the zip, the names in it)."""
    region = region.upper()
    files = files_for(region)
    places = {}
    try:
        all_places = json.loads((_lists_dir() / "inat_places.json").read_text(encoding="utf-8"))
        places = {region: all_places[region]} if region in all_places else {}
    except (OSError, ValueError):
        pass
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"species_lists_{region}.zip"
    tmp = out.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, f in files.items():
            z.write(f, name)
        z.writestr(MANIFEST, json.dumps({"region": region, "made": time.time(),
                                         "places": places}))
    tmp.replace(out)
    return out, list(files)


def _fetched(data: bytes | None, mtime: float) -> float:
    """When a list was fetched: its own "fetched" stamp, else the file time."""
    try:
        return float(json.loads(data or b"{}").get("fetched") or mtime)
    except (ValueError, AttributeError):
        return mtime


def unpack(zip_path: Path) -> dict[str, list[str]]:
    """Take a pack's lists in; {"taken": [...], "kept": [...]} (kept = the list
    here was fetched later)."""
    roots = {"species_lists": _lists_dir(), "ebird": _ebird_dir()}
    taken, kept = [], []
    with zipfile.ZipFile(zip_path) as z:
        info = json.loads(z.read(MANIFEST)) if MANIFEST in z.namelist() else {}
        for zi in z.infolist():
            top, _, name = zi.filename.partition("/")
            if top not in roots or not name or "/" in name or ".." in name:
                continue
            data = z.read(zi)
            here = roots[top] / name
            if here.exists():
                mine = _fetched(here.read_bytes(), here.stat().st_mtime)
                theirs = _fetched(data, time.mktime(zi.date_time + (0, 0, -1)))
                if mine > theirs:
                    kept.append(zi.filename)
                    continue
            here.parent.mkdir(parents=True, exist_ok=True)
            tmp = here.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(here)
            taken.append(zi.filename)
        places = info.get("places") or {}
        if places:
            p = _lists_dir() / "inat_places.json"
            try:
                have = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                have = {}
            have.update({k: v for k, v in places.items() if k not in have})
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(have), encoding="utf-8")
    from engine import ebird, inat_lists
    ebird.forget()
    inat_lists.forget()
    return {"taken": taken, "kept": kept}
