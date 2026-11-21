"""Validate the master's lists when SpeciesViewer starts — silent unless they drifted.

ONE job: at start-up, run the routine checks of Check the Master (the same check
functions, report only) on a background thread, and log a WARNING per check that
found something, with a few examples and the last time this computer found
everything clean. Nothing is written to the master.

Why (21 Nov 2026): drift comes from deletes in Lightroom, PhotoLab or Explorer,
but also from bugs in SpeciesViewer itself. The sooner it is seen, the shorter the
stretch of log to search for the write that caused it: "clean at 18:31, 15 rows
gone at 21:02" points at what ran in between.

INVARIANTS
- Silent when clean: nothing in the log but a DEBUG line.
- Reads only. The slow on-request checks (gui.check_master.ON_REQUEST) are skipped.
- Its own small state file in app-data (never in the master's Dropbox folder):
  per master, when everything was last clean and what the last run found.
- Off with File ▸ Settings ▸ Global ▸ "Validate the master's lists at start".
"""

import json
import logging
import threading
import time

logger = logging.getLogger("speciesviewer")

SETTING = ("Global", "startup_check_master")       # "yes" (default) / "no"


def _state_file():
    from utils.cache_paths import app_data_dir
    return app_data_dir() / "check_master_state.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        f = _state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError as e:
        logger.debug(f"startup check: could not save its state: {e}")


def run_checks(master) -> dict:
    """{key: (count, sentence, examples)} for every routine check; a check that
    could not run gives (None, error, [])."""
    import gui.check_master as cm
    found = {}
    for key, title, _what, fn, _fixes in cm.CHECKS:
        if key in cm.ON_REQUEST:
            continue
        cm.SAMPLES.pop(key, None)
        try:
            n, text = fn(master)
            found[key] = (n, text, list(cm.SAMPLES.get(key, [])))
        except Exception as e:
            found[key] = (None, f"could not check: {e}", [])
    return found


def report(master, found: dict, state: dict, now: float | None = None) -> list[str]:
    """The warning lines for `found` (empty when clean) and the updated state.
    Pure apart from mutating `state` — testable without a master."""
    import gui.check_master as cm
    now = now or time.time()
    titles = {c[0]: c[1] for c in cm.CHECKS}
    mine = state.setdefault(master, {})
    before = mine.get("last", {})
    problems = {k: v for k, v in found.items() if v[0] != 0}
    lines = []
    if problems:
        clean = mine.get("clean")
        since = (f"last clean on this computer: "
                 f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(clean))}"
                 if clean else "not clean at any start on this computer yet")
        lines.append(f"The master's lists drifted ({since}). "
                     f"Master ▸ Check the Master… shows and repairs it.")
        for k, (n, text, examples) in problems.items():
            was = before.get(k)
            new = "" if n is None else (" — new since the last start" if not was
                                        else (" — same as the last start" if was == n
                                              else f" — was {was} at the last start"))
            lines.append(f"   {titles.get(k, k)}: {text}{new}")
            for x in examples:
                lines.append(f"      {x}")
    else:
        mine["clean"] = now
    mine["last"] = {k: v[0] for k, v in found.items() if v[0] is not None}
    mine["checked"] = now
    return lines


def start(main_window) -> None:
    """Called once at start-up. A daemon thread; logs through the app logger."""
    from utils.config import _config
    if _config.get(*SETTING, fallback="yes").strip().lower() != "yes":
        return
    master = _config.get("master", "masterbiotadatabase", fallback="").strip().strip('"')
    if not master:
        return

    def _work():
        t0 = time.time()
        try:
            found = run_checks(master)
        except Exception as e:
            logger.warning(f"Validating the master's lists at start failed: {e}")
            return
        state = _load_state()
        lines = report(master, found, state)
        _save_state(state)
        log = getattr(main_window, "logger", None) or logger
        if not lines:
            logger.debug(f"startup check: master lists consistent ({time.time() - t0:.1f}s)")
            return
        for line in lines:
            log.warning(line)

    threading.Thread(target=_work, daemon=True, name="startup-check").start()
