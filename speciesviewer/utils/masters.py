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


def _new_folder(data: dict, name: str) -> str:
    """A folder (relative to app-data) that no master uses and does not exist."""
    used = {m["folder"].lower() for m in data["masters"]}
    base = f"masters/{_folder_name(name)}"
    folder, n = base, 2
    while folder.lower() in used or (app_data_dir() / folder).exists():
        folder, n = f"{base} {n}", n + 1
    return folder


def add(name: str) -> dict:
    """Register a new, empty master (not main, not active). Its folder is
    created on first use. Returns the new entry."""
    name = name.strip()
    if not name:
        raise MasterError("A master needs a name.")
    data = load()
    if any(m["name"].lower() == name.lower() for m in data["masters"]):
        raise MasterError(f"A master named {name!r} already exists.")
    entry = {"name": name, "folder": _new_folder(data, name), "main": False}
    data["masters"].append(entry)
    save(data)
    return entry


# Sections that describe a COLLECTION (where its data lives, where it is
# published, what is open in it) rather than this computer. "Copy machine
# settings" leaves these out, so a new master never points at another
# master's data or website.
COLLECTION_SECTIONS = ("master", "trip", "virttrip", "project", "repository",
                       "Repositories", "websettings", "currentpictures",
                       "gpslogs", "addmaster", "utilities")


def create(name: str, copy_machine_settings_from: Path | None = None) -> dict:
    """Register a new master (not main, not active). With
    `copy_machine_settings_from` (the main master's sv_config file) its config
    starts as a copy of that file minus COLLECTION_SECTIONS — DxO path,
    exiftool workers, window sizes, but no paths to data and no website.
    Without it, the config is created empty on first start, exactly as for a
    brand-new user. Never copies state/ or db/."""
    entry = add(name)
    if copy_machine_settings_from:
        import configparser
        from utils.ini_io import read_ini
        src = configparser.ConfigParser()   # same parser settings as utils.config
        read_ini(src, copy_machine_settings_from)
        for section in COLLECTION_SECTIONS:
            src.remove_section(section)
        dest = folder_of(entry) / "config" / Path(copy_machine_settings_from).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            src.write(f)
    return entry


def main_master(data: dict) -> dict:
    return next(m for m in data["masters"] if m.get("main"))


def make_main(name: str) -> None:
    """Move the "main" flag to `name`. Exactly one master is main, always."""
    data = load()
    target = find(data, name)
    for m in data["masters"]:
        m["main"] = m is target
    save(data)


def rename(old: str, new: str) -> None:
    """Change a master's display name. Its folder keeps its name. When `old`
    is the master THIS process has open, the process follows (ACTIVE and
    SV_MASTER) — otherwise the next batch step would ask for a master that no
    longer exists, and stop."""
    new = new.strip()
    if not new:
        raise MasterError("A master needs a name.")
    data = load()
    entry = find(data, old)
    if any(m is not entry and m["name"].lower() == new.lower() for m in data["masters"]):
        raise MasterError(f"A master named {new!r} already exists.")
    entry["name"] = new
    if data["active"] == old:
        data["active"] = new
    save(data)
    if ACTIVE["name"] == old:
        ACTIVE["name"] = new
        os.environ[ENV_VAR] = new


def trash_dir() -> Path:
    return app_data_dir() / ".trash"


def remove(name: str) -> Path:
    """Zip a master's app-data folder to .trash, verify the zip, then delete
    the folder and its entry. Returns the zip. Never touches the master's
    DATA (pictures, CSVs) — only its settings, state and cache here.

    Refused for the main master, and for the one that is open (here or as
    the next start's). db/*.db is left out of the zip: it is rebuilt from the
    CSVs and can be hundreds of MB. The pending-writes log beside it is kept."""
    import shutil
    import time
    import zipfile
    data = load()
    entry = find(data, name)
    if entry.get("main"):
        raise MasterError(f"“{name}” is the main master. Make another master "
                          "the main one first.")
    if name in (ACTIVE["name"], data["active"]):
        raise MasterError(f"“{name}” is open. Switch to another master first.")
    folder = folder_of(entry)
    if folder.resolve() == app_data_dir().resolve():
        raise MasterError(f"“{name}” uses the shared app-data folder and "
                          "cannot be removed this way.")
    trash_dir().mkdir(parents=True, exist_ok=True)
    zip_path = trash_dir() / f"master {_folder_name(name)} {time.strftime('%Y-%m-%d %H%M%S')}.zip"
    files = [p for p in folder.rglob("*") if p.is_file()
             and not (p.parent.name == "db" and p.suffix == ".db")] if folder.exists() else []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(_ENTRY_FILE, json.dumps(entry, indent=2, ensure_ascii=False))
        for p in files:
            z.write(p, p.relative_to(folder))
    with zipfile.ZipFile(zip_path) as z:
        bad = z.testzip()
        if bad or len(z.namelist()) != len(files) + 1:
            raise MasterError(f"The backup {zip_path} did not verify ({bad or 'file count'}); "
                              "nothing was removed.")
    data["masters"].remove(entry)
    save(data)
    if folder.exists():
        shutil.rmtree(folder)
    return zip_path


_ENTRY_FILE = "masters-entry.json"


def trashed_masters() -> list[tuple[Path, dict]]:
    """The removed masters that can be restored: (zip, its masters.json
    entry), newest first. Anything in .trash that is not a master zip is
    ignored."""
    import zipfile
    found = []
    for z in sorted(trash_dir().glob("master *.zip"), key=lambda p: p.stat().st_mtime,
                    reverse=True) if trash_dir().exists() else []:
        try:
            with zipfile.ZipFile(z) as zf:
                found.append((z, json.loads(zf.read(_ENTRY_FILE).decode("utf-8"))))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            continue
    return found


def restore(zip_path: Path) -> dict:
    """Bring a removed master back: its old name (or "<name> (restored)" when
    that is taken), a fresh folder, never main. The zip is deleted only after
    the files are out and the entry is saved — the restored folder is then
    the copy. Returns the new entry."""
    import zipfile
    data = load()
    with zipfile.ZipFile(zip_path) as zf:
        old = json.loads(zf.read(_ENTRY_FILE).decode("utf-8"))
        taken = {m["name"].lower() for m in data["masters"]}
        name, n = old["name"], 1
        while name.lower() in taken:
            name = f"{old['name']} (restored)" if n == 1 else f"{old['name']} (restored {n})"
            n += 1
        entry = {"name": name, "folder": _new_folder(data, name), "main": False}
        folder = folder_of(entry).resolve()
        for member in zf.namelist():
            if member == _ENTRY_FILE:
                continue
            target = (folder / member).resolve()
            if folder not in target.parents:
                raise MasterError(f"{zip_path.name}: unexpected path {member!r}; nothing restored.")
        folder.mkdir(parents=True)
        zf.extractall(folder, [m for m in zf.namelist() if m != _ENTRY_FILE])
    data["masters"].append(entry)
    save(data)
    zip_path.unlink()
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


def banner_text() -> str:
    """What the window says about the master: "" for the main master (no
    change to its look), otherwise a label naming the test master."""
    return "" if IS_MAIN else f"TEST: {ACTIVE['name']}"


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
