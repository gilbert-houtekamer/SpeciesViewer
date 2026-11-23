"""Purple marks — what "Mark the bins Purple" changed, so it can be undone.

ONE JOB: remember, per marking, each picture and the colour it had before it
was set to Purple (Green or none), and hand that back for an undo.

INVARIANT: a marking is recorded BEFORE any colour is written, and only the
pictures that really took the colour stay in it (`confirm`); an undo puts back
exactly the recorded colour, and only on a picture that is still Purple — one
you changed since is left as you left it. The log lives in app-data (one JSON
file per machine), never in the master.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _path() -> Path:
    from utils.cache_paths import app_data_dir
    return app_data_dir() / "scores" / "purple_marks.json"


def _load(path: Path | None = None) -> list[dict]:
    p = path or _path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(batches: list[dict], path: Path | None = None) -> None:
    p = path or _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(batches, indent=1), encoding="utf-8")
    tmp.replace(p)


def start(master: str, sci: str, before: dict, *, run: str = "",
          path: Path | None = None) -> str:
    """Record a marking about to happen: {UniquePictureName: colour before}.
    `run` ties the markings of one all-species run together (Clean Up the
    Master). Returns its id."""
    batches = _load(path)
    bid = f"{time.strftime('%Y%m%d-%H%M%S')}-{len(batches)}"
    batches.append({"id": bid, "master": str(master), "sci": sci, "run": run,
                    "when": time.strftime("%Y-%m-%d %H:%M"), "before": dict(before)})
    _save(batches, path)
    return bid


def confirm(bid: str, done, *, path: Path | None = None) -> None:
    """Keep only the pictures that really became Purple; drop an empty marking."""
    batches = _load(path)
    for b in batches:
        if b["id"] == bid:
            b["before"] = {u: c for u, c in b["before"].items() if u in set(done)}
    _save([b for b in batches if b["before"]], path)


def last(master: str, sci: str, *, path: Path | None = None) -> dict | None:
    """The most recent marking of this species in this master, or None."""
    for b in reversed(_load(path)):
        if b["master"] == str(master) and b["sci"] == sci:
            return b
    return None


def last_run(master: str, *, path: Path | None = None) -> tuple[str, list[dict]]:
    """The most recent all-species run of this master: (run id, its markings),
    or ("", []) when there is none left."""
    batches = [b for b in _load(path) if b["master"] == str(master) and b.get("run")]
    if not batches:
        return "", []
    run = batches[-1]["run"]
    return run, [b for b in batches if b.get("run") == run]


def forget(bid: str, *, path: Path | None = None) -> None:
    _save([b for b in _load(path) if b["id"] != bid], path)
