"""eBird's species list of a country — what the AI may choose from there.

ONE JOB: the scientific names of the bird species eBird lists for a region (an
ISO country code such as NL or GM), fetched once and kept in app-data `ebird/`
(spplist_<region>.json + the eBird taxonomy), so a trip without internet still
has it. The key comes from the config, `[ebird] api_key`, and is sent only to
api.ebird.org.

INVARIANT: never fatal and never blocking for long. No key, no internet and no
saved list = None (the caller then chooses among all species); a saved list is
used even when it is old. Only birds: eBird has no other animals.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger("speciesviewer")

API = "https://api.ebird.org/v2"
REFRESH_S = 180 * 24 * 3600          # a saved list is refreshed after half a year
_lock = threading.Lock()
_memo: dict[str, frozenset | None] = {}
_taxonomy: dict | None = None


def _dir() -> Path:
    from utils.cache_paths import app_data_dir
    return app_data_dir() / "ebird"


def api_key() -> str:
    from utils.config import _config
    return (_config.get("ebird", "api_key", fallback="") or "").strip()


def _get(url: str, path: Path, key: str):
    """The saved file; fetched (and saved) when missing or older than REFRESH_S
    and a key is there. A failed fetch falls back to the saved file."""
    saved = None
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            saved = None
        if saved is not None and time.time() - path.stat().st_mtime < REFRESH_S:
            return saved
    if not key:
        return saved
    try:
        req = urllib.request.Request(url, headers={"X-eBirdApiToken": key})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
        return data
    except Exception as e:                       # offline, bad key, …: keep going
        logger.info(f"eBird: {url.rsplit('/', 1)[-1]} not fetched ({e}); "
                    f"{'using the saved copy' if saved is not None else 'no list'}")
        return saved


def taxonomy() -> dict:
    """{lower-case scientific name: (English name, family)} from the saved eBird
    taxonomy ({} before the first list was fetched)."""
    global _taxonomy
    with _lock:
        if _taxonomy is None:
            try:
                rows = json.loads((_dir() / "taxonomy.json").read_text(encoding="utf-8"))
                _taxonomy = {t["sciName"].lower(): (t.get("comName", ""), t.get("familySciName", ""))
                             for t in rows if t.get("category") == "species"}
            except (OSError, ValueError, KeyError):
                return {}
        return _taxonomy


def forget() -> None:
    """Forget the lists looked up this session (a new key: try again)."""
    with _lock:
        _memo.clear()


def species(region: str) -> frozenset | None:
    """Lower-case scientific names of the bird species on eBird's list for
    `region`, or None when there is no list (no key and nothing saved)."""
    region = (region or "").strip().upper()
    if not region:
        return None
    with _lock:
        if region in _memo:
            return _memo[region]
        key = api_key()
        codes = _get(f"{API}/product/spplist/{region}", _dir() / f"spplist_{region}.json", key)
        tax = _get(f"{API}/ref/taxonomy/ebird?fmt=json", _dir() / "taxonomy.json", key)
        out = None
        if codes and tax:
            sci = {t["speciesCode"]: t["sciName"] for t in tax if t.get("category") == "species"}
            out = frozenset(sci[c].lower() for c in codes if c in sci) or None
        _memo[region] = out
        return out
