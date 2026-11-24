"""What an AI… Accept on a day changed, so it can be undone.

ONE JOB: remember, per Accept, each picture's colour and species BEFORE, and
what the AI set, and hand that back for an undo.

INVARIANT: an Accept is recorded BEFORE anything is written, and only the
pictures that really took the change stay in it (`confirm`). An undo puts back
the recorded value only where the picture still has what the AI set — anything
you changed since is left as you left it. The log lives in app-data (one JSON
file per machine), never beside the pictures.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _path() -> Path:
    from utils.cache_paths import app_data_dir
    return app_data_dir() / "scores" / "day_ai_accepts.json"


def _load(path: Path | None = None) -> list[dict]:
    try:
        return json.loads((path or _path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(entries: list[dict], path: Path | None = None) -> None:
    p = path or _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    tmp.replace(p)


def start(folder: str, colours: dict, species: dict, *, path: Path | None = None) -> str:
    """Record an Accept about to happen. `colours` / `species` =
    {key: [before, set]}. Returns its id."""
    entries = _load(path)
    eid = f"{time.strftime('%Y%m%d-%H%M%S')}-{len(entries)}"
    entries.append({"id": eid, "folder": str(folder), "when": time.strftime("%Y-%m-%d %H:%M"),
                    "colours": {k: list(v) for k, v in colours.items()},
                    "species": {k: list(v) for k, v in species.items()}})
    _save(entries, path)
    return eid


def confirm(eid: str, colours_done, species_done, *, path: Path | None = None) -> None:
    """Keep only what really changed; drop an Accept that changed nothing."""
    entries = _load(path)
    for e in entries:
        if e["id"] == eid:
            e["colours"] = {k: v for k, v in e["colours"].items() if k in set(colours_done)}
            e["species"] = {k: v for k, v in e["species"].items() if k in set(species_done)}
    _save([e for e in entries if e["colours"] or e["species"]], path)


def last(folder: str, *, path: Path | None = None) -> dict | None:
    for e in reversed(_load(path)):
        if e["folder"] == str(folder):
            return e
    return None


def forget(eid: str, *, path: Path | None = None) -> None:
    _save([e for e in _load(path) if e["id"] != eid], path)


def undo_plan(entry: dict, colour_now, species_now) -> tuple[dict, dict, int]:
    """({key: colour to put back}, {key: species to put back}, how many you
    changed since) — only where the picture still has what the AI set."""
    colours, species, changed = {}, {}, 0
    for k, (before, done) in entry.get("colours", {}).items():
        if (colour_now(k) or "") == (done or ""):
            colours[k] = before or ""
        else:
            changed += 1
    for k, (before, done) in entry.get("species", {}).items():
        if (species_now(k) or "") == (done or ""):
            species[k] = before or ""
        else:
            changed += 1
    return colours, species, changed
