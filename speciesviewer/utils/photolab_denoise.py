"""What denoise method SpeciesViewer stamps into a picture's DxO `.dop` when it
writes a crop — so the RAW opens in PhotoLab already set to the method this
machine uses, instead of picking it (and dodging the v7/v8 clutter) each time.

The method lives in the `.dop`'s Overrides block, right beside the crop
SpeciesViewer already writes there (validated live):

    NoiseActive = true,
    NoiseRemovalMethod = "<code>",    # omitted for Standard — base stays "standard"

The label→code map is PhotoLab-VERSION specific — `DeepRaw2RGBv7` is XD3 in PL9,
and a PL8/PL7 install uses different codes — so it is a per-version table, kept in
`photolab_denoise.ini` (a per-machine file seeded from the defaults below and
hand-editable). The codes were harvested from the user's own `.dop` library; only
XD3 = `DeepRaw2RGBv7` is confirmed by a before/after diff, the rest are inferred
and editable.

Written ONLY on the crop path (`engine.sidecarlib.suggest_crop`) — the "preparing
a JPG" moment. A RAW we never crop is never touched. Read live, so a settings
change takes effect on the next crop.
"""

from __future__ import annotations

import configparser
import logging

from utils.config import CONFIG_DIR

logger = logging.getLogger("speciesviewer")

INI_PATH = CONFIG_DIR / "photolab_denoise.ini"

# label -> .dop code. "" = Standard / High Quality (NoiseActive=true only; base
# method stays "standard"). Insertion order is the order the GUI lists them.
# ALL THREE user-validated live (menu labels match each PhotoLab version's own UI;
# PL7 still calls it "High Quality", PL8/PL9 renamed it "Standard"):
#   v9: DeepPRIME 3 = DeepRaw2RGBv6, DeepPRIME XD3 = DeepRaw2RGBv7
#   v8: PRIME = ultra, DeepPRIME XD2 = DeepRaw2RGBv4, DeepPRIME 3 = DeepRaw2RGBv6
#   v7: PRIME = ultra, DeepPRIME = DeepRaw2RGB, DeepPRIME XD = DeepRaw2RGBv2
# Caveat: the .dop recipe schema Version is forward-incompatible, so v8/v7 only
# take on a .dop that PhotoLab of THAT version authored (delete the .dop, let PL
# make a fresh one, then crop). Editable in the GUI if a menu ever changes.
_DEFAULT_MAP = {
    "v9": {
        "Standard": "",
        "DeepPRIME 3": "DeepRaw2RGBv6",
        "DeepPRIME XD3": "DeepRaw2RGBv7",
    },
    "v8": {
        "Standard": "",
        "PRIME": "ultra",
        "DeepPRIME XD2": "DeepRaw2RGBv4",
        "DeepPRIME 3": "DeepRaw2RGBv6",
    },
    "v7": {
        "High Quality": "",
        "PRIME": "ultra",
        "DeepPRIME": "DeepRaw2RGB",
        "DeepPRIME XD": "DeepRaw2RGBv2",
    },
}
_DEFAULT_SELECTION = {"version": "v9", "method": "DeepPRIME XD3", "enabled": "no"}


def _load() -> configparser.ConfigParser:
    # interpolation=None: codes/labels are literal (no '%' surprises).
    # optionxform=str: preserve label case ("DeepPRIME XD3", not "deepprime xd3").
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    if INI_PATH.exists():
        try:
            cp.read(INI_PATH, encoding="utf-8")
        except Exception as e:
            logger.warning(f"photolab_denoise.ini unreadable ({e}); using defaults.")
    changed = False
    if not cp.has_section("selection"):
        cp.add_section("selection")
        for k, v in _DEFAULT_SELECTION.items():
            cp.set("selection", k, v)
        changed = True
    for ver, methods in _DEFAULT_MAP.items():
        if not cp.has_section(ver):
            cp.add_section(ver)
            for label, code in methods.items():
                cp.set(ver, label, code)
            changed = True
    if changed:
        _save(cp)
    return cp


def _save(cp: configparser.ConfigParser) -> None:
    try:
        with open(INI_PATH, "w", encoding="utf-8") as f:
            cp.write(f)
    except Exception as e:
        logger.warning(f"Could not write {INI_PATH}: {e}")


def available_versions() -> list[str]:
    return [s for s in _load().sections() if s != "selection"]


def methods_for_version(version: str) -> list[tuple[str, str]]:
    """[(label, code)] for a PhotoLab version, in ini order."""
    cp = _load()
    if not cp.has_section(version):
        return []
    return [(label, cp.get(version, label)) for label in cp.options(version)]


def load_map() -> dict:
    """The whole label→code table as {version: [(label, code), ...]}, in ini order.
    For the settings dialog's editable view."""
    cp = _load()
    return {v: [(lbl, cp.get(v, lbl)) for lbl in cp.options(v)]
            for v in cp.sections() if v != "selection"}


def save_map(mapping: dict) -> None:
    """Replace the version sections with `mapping` ({version: [(label, code)]}),
    leaving [selection] intact. Blank/None codes are stored as empty (Standard)."""
    cp = _load()
    for v in [s for s in cp.sections() if s != "selection"]:
        cp.remove_section(v)
    for v, methods in mapping.items():
        if not v:
            continue
        cp.add_section(v)
        for label, code in methods:
            if label:
                cp.set(v, label, code or "")
    _save(cp)


def ini_path() -> str:
    """Where the editable table lives on disk (shown in the settings dialog)."""
    return str(INI_PATH)


def load_selection() -> dict:
    cp = _load()
    return {
        "version": cp.get("selection", "version",
                          fallback=_DEFAULT_SELECTION["version"]),
        "method": cp.get("selection", "method",
                         fallback=_DEFAULT_SELECTION["method"]),
        "enabled": cp.getboolean("selection", "enabled",
                                 fallback=_DEFAULT_SELECTION["enabled"] == "yes"),
    }


def save_selection(version: str, method: str, enabled: bool) -> None:
    cp = _load()
    if not cp.has_section("selection"):
        cp.add_section("selection")
    cp.set("selection", "version", version)
    cp.set("selection", "method", method)
    cp.set("selection", "enabled", "yes" if enabled else "no")
    _save(cp)


def denoise_override_for_crop():
    """Value to pass to sidecarlib.write_dop_edits(denoise=...) on a crop write:

      - None      -> feature off / selection invalid: don't touch denoise, leave
                     whatever PhotoLab would default to.
      - ""        -> Standard: write `NoiseActive = true` only.
      - "<code>"  -> write `NoiseActive = true` + `NoiseRemovalMethod = "<code>"`.

    (Empty string and None are DIFFERENT — callers must test `is None`.)
    """
    sel = load_selection()
    if not sel["enabled"]:
        return None
    for label, code in methods_for_version(sel["version"]):
        if label == sel["method"]:
            return code
    logger.warning(
        f"photolab denoise: selection {sel['version']}/{sel['method']!r} not in "
        f"the ini map — skipping the denoise stamp.")
    return None
