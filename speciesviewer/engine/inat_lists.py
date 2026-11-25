"""iNaturalist's species of a country, per group — what the AI may choose from
there besides eBird's birds (eBird has no other animals).

ONE JOB: the scientific names of the species people PHOTOGRAPHED in a country
(iNaturalist, research grade: confirmed by others), for one group such as
mammals or dragonflies, fetched once and kept in app-data `species_lists/` so a
trip without internet still has them:
  species_lists/inat_<CC>_<group>.json  {"fetched": time, "place": id,
                                         "species": {name: [observations, English name]}}
  species_lists/inat_places.json        {CC: iNaturalist place id}
The groups follow the ones of waarneming.nl / observation.org (GROUPS). Only
api.inaturalist.org is asked (no key; at most one call a second, as they ask).

INVARIANT: never fatal and never blocking for long once fetched. No internet and
no saved list = None; a saved list is used even when old. A species with fewer
than `min_records` observations in the country is left out (one observation is
often a wrong name, a garden plant or a stray).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger("speciesviewer")

API = "https://api.inaturalist.org/v1"
REFRESH_S = 365 * 24 * 3600     # a saved list is refreshed after a year
MIN_RECORDS = 3                 # default: fewer observations in the country = left out
PAGE = 500                      # species per call (iNaturalist's maximum)
PAUSE_S = 1.0                   # iNaturalist asks for at most ~1 call a second

_BUTTERFLIES = 47224            # Papilionoidea
_INSECT_GROUPS = (47157, 47792, 47651, 47201, 47208, 47822, 47744)

# key: (label, iNaturalist taxon ids to include, taxon ids to leave out).
# Ids checked against iNaturalist 2026-11-25.
GROUPS: dict[str, tuple[str, tuple[int, ...], tuple[int, ...]]] = {
    "mammals":      ("Mammals", (40151,), ()),
    "reptiles":     ("Reptiles and amphibians", (26036, 20978), ()),
    "butterflies":  ("Butterflies", (_BUTTERFLIES,), ()),
    "moths":        ("Moths", (47157,), (_BUTTERFLIES,)),
    "dragonflies":  ("Dragonflies and damselflies", (47792,), ()),
    "grasshoppers": ("Grasshoppers and crickets", (47651,), ()),
    "bees":         ("Bees, wasps and ants", (47201,), ()),
    "beetles":      ("Beetles", (47208,), ()),
    "flies":        ("Flies and mosquitoes", (47822,), ()),
    "bugs":         ("Bugs, plant lice and cicadas", (47744,), ()),
    "insects":      ("Other insects", (47158,), _INSECT_GROUPS),
    "spiders":      ("Spiders and other arachnids", (47119,), ()),
    "molluscs":     ("Molluscs", (47115,), ()),
    "fish":         ("Fish", (47178,), ()),
    "plants":       ("Plants", (47126,), ()),
    "fungi":        ("Fungi", (47170,), ()),
}

_lock = threading.Lock()
_net_lock = threading.Lock()
_memo: dict = {}
_last_call = 0.0


def folder() -> Path:
    """Where the saved lists live (part of what a country pack carries)."""
    from utils.cache_paths import app_data_dir
    return app_data_dir() / "species_lists"


def _fetch(url: str, timeout: int = 60):
    global _last_call
    with _net_lock:
        wait = PAUSE_S - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SpeciesViewer"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        finally:
            _last_call = time.time()


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def place_id(region: str) -> int | None:
    """iNaturalist's place for a country (ISO code): saved, else looked up by
    the country's English name (the place of admin level 0 with that name)."""
    path = folder() / "inat_places.json"
    places = _read(path) or {}
    if region in places:
        return places[region]
    from engine.countries import country_name
    name = country_name(region, "en")
    if not name or name.upper() == region:
        return None
    q = urllib.parse.urlencode({"q": name})
    found = [p for p in _fetch(f"{API}/places/autocomplete?{q}").get("results") or []
             if p.get("admin_level") == 0]
    if not found:
        return None
    places[region] = int(found[0]["id"])
    _write(path, places)
    return places[region]


def _species_counts(place: int, group: str, progress=None) -> dict:
    """{scientific name: [observations, English name]} of a group in a place."""
    _label, include, leave_out = GROUPS[group]
    out: dict[str, list] = {}
    page = 1
    while True:
        params = {"place_id": place, "taxon_id": ",".join(map(str, include)),
                  "quality_grade": "research", "per_page": PAGE, "page": page,
                  "locale": "en"}
        if leave_out:
            params["without_taxon_id"] = ",".join(map(str, leave_out))
        d = _fetch(f"{API}/observations/species_counts?{urllib.parse.urlencode(params)}")
        for r in d.get("results") or []:
            t = r.get("taxon") or {}
            name = " ".join((t.get("name") or "").split()[:2])   # a subspecies → its species
            if len(name.split()) != 2:
                continue
            have = out.setdefault(name, [0, t.get("preferred_common_name") or ""])
            have[0] += int(r.get("count") or 0)
        if progress:
            progress(f"{group}: {len(out)} of {d.get('total_results', '?')} species")
        if page * PAGE >= int(d.get("total_results") or 0):
            return out
        page += 1


def fetch(region: str, group: str, progress=None, force: bool = False) -> dict | None:
    """{scientific name: [observations, English name]} of a group in a country:
    the saved file, fetched when missing or older than REFRESH_S (`force`:
    fetched again now). None = nothing saved and no internet."""
    region = (region or "").strip().upper()
    path = folder() / f"inat_{region}_{group}.json"
    saved = _read(path)
    if saved and not force and time.time() - float(saved.get("fetched", 0)) < REFRESH_S:
        return saved["species"]
    try:
        place = place_id(region)
        if place is None:
            return saved["species"] if saved else None
        species = _species_counts(place, group, progress)
        _write(path, {"fetched": time.time(), "place": place, "species": species})
        return species
    except Exception as e:                      # offline, iNaturalist down, …: keep going
        logger.info(f"iNaturalist: {region} {group} not fetched ({e}); "
                    f"{'using the saved copy' if saved else 'no list'}")
        return saved["species"] if saved else None


def species(region: str, group: str, min_records: int = MIN_RECORDS) -> frozenset | None:
    """Lower-case scientific names of a group's species in a country with at
    least `min_records` observations, or None when there is no list."""
    key = ((region or "").strip().upper(), group, min_records)
    with _lock:
        if key in _memo:
            return _memo[key]
    counts = fetch(key[0], group) if key[0] and group in GROUPS else None
    out = None
    if counts is not None:
        out = frozenset(n.lower() for n, (k, _c) in counts.items() if k >= min_records)
    with _lock:
        _memo[key] = out
    return out


def english_names(region: str, groups) -> dict:
    """{lower-case scientific name: English name} of those saved lists — for
    matching iNaturalist's newest genus to BioCLIP's older one."""
    out = {}
    for g in groups:
        for n, (_k, common) in (fetch(region, g) or {}).items():
            if common:
                out[n.lower()] = common
    return out


DEFAULT_GROUPS = "mammals"     # GH 2026-11-25: "mammals are always good"


def _parse(text: str) -> list[str]:
    return [g.strip().lower() for g in (text or "").replace(";", ",").split(",")
            if g.strip().lower() in GROUPS]


def chosen_groups(region: str) -> list[str]:
    """The groups to use for a country: [ai] groups_<CC> in the config, else
    [ai] groups (all other countries), else mammals only. "none" = no groups."""
    from utils.config import _config
    region = (region or "").strip().upper()
    text = ((_config.get("ai", f"groups_{region}", fallback="") if region else "")
            or _config.get("ai", "groups", fallback=DEFAULT_GROUPS))
    return _parse(text)


def countries_set() -> dict[str, list[str]]:
    """{ISO code: groups} of the countries with their own choice (Settings ▸
    AI species lists); "" = all other countries."""
    from utils.config import _config
    out = {"": chosen_groups("")}
    if _config.has_section("ai"):
        for k, v in _config.items("ai"):
            if k.startswith("groups_") and len(k) == 9:
                out[k[7:].upper()] = _parse(v)
    return out


def set_groups(region: str, groups) -> None:
    """Save the groups for a country ("" = all other countries); no groups is
    saved as "none" (else the country would fall back to the others' choice)."""
    from utils.config import _config, persist_config
    region = (region or "").strip().upper()
    if not _config.has_section("ai"):
        _config.add_section("ai")
    key = f"groups_{region.lower()}" if region else "groups"
    _config.set("ai", key, ", ".join(g for g in GROUPS if g in groups) or "none")
    persist_config()
    forget()


def remove_country(region: str) -> None:
    """The country goes back to the others' choice. Its saved lists stay on disk."""
    from utils.config import _config, persist_config
    region = (region or "").strip().upper()
    if region and _config.has_section("ai") and _config.remove_option("ai", f"groups_{region.lower()}"):
        persist_config()
    forget()


def saved(region: str, group: str) -> tuple[int, float] | None:
    """(species seen MIN_RECORDS+ times, when fetched) of the list on this
    computer — no internet; None when it was never fetched."""
    d = _read(folder() / f"inat_{(region or '').strip().upper()}_{group}.json")
    if not d:
        return None
    n = sum(1 for k, _c in (d.get("species") or {}).values() if k >= MIN_RECORDS)
    return n, float(d.get("fetched", 0))


def forget() -> None:
    """Forget the lists read this session (a list fetched or a choice changed);
    also the AI's lists made from them (engine.ai_measure)."""
    with _lock:
        _memo.clear()
    try:
        from engine import ai_measure
        ai_measure._matched.clear()
    except Exception:
        pass
