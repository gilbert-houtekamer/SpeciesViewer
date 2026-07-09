# connectivity.py: reachability probes for the services SpeciesViewer depends on.
#
# Each operation should check the ONE service it needs, not a generic "internet"
# flag:
#   - publishing (blog / website / commit) WRITES over SFTP — the real dependency;
#   - species and place lookups use public APIs (GBIF, OpenStreetMap) and already
#     degrade gracefully when they're down;
#   - the website is only where results are VIEWED — never a write target, so its
#     reachability must never gate a publish.
#
# Probes are fast TCP connects (not a full HTTP GET) so a sluggish web server —
# houtekamer.com answers its TCP port instantly even when its HTTP layer stalls —
# does not read as "offline".

import socket
import time
import logging
import concurrent.futures

logger = logging.getLogger("speciesviewer")

# key -> (plain label, host, port, one-line note). Labels are user-facing and
# jargon-free; the host/protocol lives in the note/tooltip.
ENDPOINTS = {
    "publish": ("Publish server", "houtfp.ssh.transip.me", 22,
                "Upload target for blogs, the website and commits (SFTP)."),
    "lookup":  ("Species lookup", "api.gbif.org", 443,
                "GBIF — recognises species names during Read & Summarize."),
    "place":   ("Place names", "nominatim.openstreetmap.org", 443,
                "OpenStreetMap — turns GPS into place names."),
    "website": ("Your website", "houtekamer.com", 443,
                "Where published pages are viewed — not a write target."),
}

# Reachable-but-slow threshold (ms). Above this a probe still counts as up, but
# the status lamp shows amber — exactly the sluggish-website case.
SLOW_MS = 1500


def probe(host: str, port: int, timeout: float = 6.0) -> dict:
    """Fast TCP reachability check. Returns {ok, ms, slow, error}."""
    t = time.time()
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        ms = int((time.time() - t) * 1000)
        return {"ok": True, "ms": ms, "slow": ms >= SLOW_MS, "error": ""}
    except OSError as e:
        return {"ok": False, "ms": int((time.time() - t) * 1000),
                "slow": False, "error": f"{type(e).__name__}: {e}"}


def check(key: str, timeout: float = 6.0) -> dict:
    """Probe one named endpoint; returns its full descriptor + probe result."""
    label, host, port, note = ENDPOINTS[key]
    return {"key": key, "label": label, "host": host, "port": port,
            "note": note, **probe(host, port, timeout)}


def check_all(timeout: float = 6.0) -> dict:
    """Probe every endpoint in parallel. Returns {key: descriptor}."""
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ENDPOINTS)) as ex:
        futures = {ex.submit(check, k, timeout): k for k in ENDPOINTS}
        for fut in concurrent.futures.as_completed(futures):
            r = fut.result()
            out[r["key"]] = r
    return out


def publish_reachable(timeout: float = 6.0) -> bool:
    """True if the SFTP publish server answers — the real dependency for blog /
    website / commit. Website slowness is irrelevant here."""
    _label, host, port, _note = ENDPOINTS["publish"]
    return probe(host, port, timeout)["ok"]


def any_reachable(timeout: float = 6.0) -> bool:
    """General 'is the network up at all' — used for soft/degrade gates (e.g. the
    translation service). Returns on the first endpoint that answers."""
    for key in ("publish", "lookup", "website"):
        _label, host, port, _note = ENDPOINTS[key]
        if probe(host, port, timeout)["ok"]:
            return True
    return False


# Message shown when a publish is blocked — specific, not the misleading
# "no internet" (the network may be fine; the upload server is unreachable).
PUBLISH_UNREACHABLE_MSG = (
    "Couldn't reach the publish server ({host}). Your internet may be fine but "
    "the upload server is unreachable — try again shortly. "
    "(Help → Connection status shows all services.)"
).format(host=ENDPOINTS["publish"][1])
