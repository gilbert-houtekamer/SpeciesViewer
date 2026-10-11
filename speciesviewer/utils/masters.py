"""Which master this process works on, and where that master's per-machine
files live (docs/plans/multiple_masters_plan.md).

ONE JOB: turn "the active master" into one folder — ACTIVE_DIR — under which
config/, state/, db/ and runtime/ belong to that master alone.

A master's DATA (pictures, CSVs, admin/) lives wherever the user put it; this
module only decides where its SETTINGS, remembered state and SQLite cache live.

    <app-data>/masters.json              which masters exist, which is active,
                                         which one is the main master
    <app-data>/config, state, db, ...    folder "." — the master that existed
                                         before masters.json did
    <app-data>/masters/<name>/...        every master created afterwards

All masters are equal; "main" is a flag that moves. No masters.json means one
master, "Main", folder "." — today's layout, so nothing moves.

INVARIANT: a process opens the folder of exactly ONE master, chosen once at
import. SV_MASTER (environment) overrides masters.json; it is set here for the
whole process, so every batch step the GUI launches inherits the same master
even if masters.json changes meanwhile. Switching master = restart.

Imported by utils.config before anything else is read — so no logging and no
import of utils.config here.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from utils.cache_paths import app_data_dir

ENV_VAR = "SV_MASTER"
MASTERS_FILE = app_data_dir() / "masters.json"
_DEFAULT = {"active": "Main",
            "masters": [{"name": "Main", "folder": ".", "main": True}]}


class MasterError(RuntimeError):
    """masters.json unreadable, or a master name that does not exist. Raised
    rather than silently falling back to the main master: working on the
    wrong master without knowing it is the one thing this module must prevent."""


def load() -> dict:
    """masters.json as a dict; the one-master default when the file is absent."""
    if not MASTERS_FILE.exists():
        return json.loads(json.dumps(_DEFAULT))
    try:
        data = json.loads(MASTERS_FILE.read_text(encoding="utf-8"))
        names = [m["name"] for m in data["masters"]]
        mains = [m for m in data["masters"] if m.get("main")]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise MasterError(f"{MASTERS_FILE} cannot be read ({e}). "
                          "Fix or remove it; removing it means one master, 'Main'.")
    if len(mains) != 1 or len(set(names)) != len(names):
        raise MasterError(f"{MASTERS_FILE}: need exactly one main master and "
                          f"unique names, found {names} (main: "
                          f"{[m['name'] for m in mains]}).")
    return data


def save(data: dict) -> None:
    """Write masters.json atomically (tmp + replace)."""
    MASTERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MASTERS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, MASTERS_FILE)


def find(data: dict, name: str) -> dict:
    for m in data["masters"]:
        if m["name"] == name:
            return m
    raise MasterError(f"No master named {name!r}. Known: "
                      f"{', '.join(m['name'] for m in data['masters'])}.")


def folder_of(master: dict) -> Path:
    return app_data_dir() / master["folder"]


def _folder_name(name: str) -> str:
    """A folder name that is valid on Windows, macOS and Linux."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return safe or "master"


def add(name: str) -> dict:
    """Register a new, empty master (not main, not active). Its folder is
    created on first use. Returns the new entry."""
    name = name.strip()
    if not name:
        raise MasterError("A master needs a name.")
    data = load()
    if any(m["name"].lower() == name.lower() for m in data["masters"]):
        raise MasterError(f"A master named {name!r} already exists.")
    used = {m["folder"].lower() for m in data["masters"]}
    base = f"masters/{_folder_name(name)}"
    folder, n = base, 2
    while folder.lower() in used or (app_data_dir() / folder).exists():
        folder, n = f"{base} {n}", n + 1
    entry = {"name": name, "folder": folder, "main": False}
    data["masters"].append(entry)
    save(data)
    return entry


def set_active(name: str) -> None:
    """Make `name` the master the NEXT start opens. The running process keeps
    its own (see INVARIANT)."""
    data = load()
    find(data, name)
    data["active"] = name
    save(data)


def _resolve() -> tuple[dict, Path]:
    data = load()
    name = os.environ.get(ENV_VAR) or data.get("active") or "Main"
    master = find(data, name)
    os.environ[ENV_VAR] = name        # every child process: the same master
    return master, folder_of(master)


ACTIVE, ACTIVE_DIR = _resolve()
IS_MAIN = bool(ACTIVE.get("main"))
# The master that existed before masters.json: the only one whose files were
# ever in the repository root, so the only one the old-location migration is for.
IS_LEGACY_FOLDER = ACTIVE["folder"] in (".", "")


def describe() -> str:
    """One line for the start-up log."""
    role = "main master" if IS_MAIN else "TEST master (not main)"
    return f"Master: {ACTIVE['name']} — {role} — settings in {ACTIVE_DIR}"


if __name__ == "__main__":
    # Until the Master menu exists:  python -m utils.masters list | add NAME | use NAME
    import sys
    cmd, *rest = sys.argv[1:] or ["list"]
    if cmd == "add" and rest:
        print(f"Added: {add(' '.join(rest))}")
    elif cmd == "use" and rest:
        set_active(" ".join(rest))
        print(f"Next start opens: {' '.join(rest)}")
    elif cmd == "list":
        d = load()
        for m in d["masters"]:
            flags = (" [main]" if m.get("main") else "") + \
                    (" [active]" if m["name"] == d["active"] else "")
            print(f"{m['name']}{flags}  ->  {folder_of(m)}")
    else:
        print(__doc__.split("\n\n")[0])
        print("usage: python -m utils.masters list | add NAME | use NAME")
