"""Reverse-geocoding: GPS coordinates → human place-name text for the XMP.

Primary source is Nominatim (OpenStreetMap), online — it returns fine-grained
address parts (village/municipality/county/state/country) and often a natural
feature (nature reserve, lake), which is what a birding caption wants. An
offline fallback (reverse_geocoder, nearest city) is a possible later addition.

Kept free of PyQt so it can be run as a standalone probe:
    python qt/gui/geolocate.py "<file.xmp|file.ORF>"
    python qt/gui/geolocate.py 41.1881 23.1015
"""

from __future__ import annotations

import json
import logging
import subprocess

logger = logging.getLogger("speciesviewer")

# Nominatim requires a descriptive User-Agent identifying the app + a contact.
_USER_AGENT = "SpeciesViewer/1.0 (gilbert@houtekamer.com)"

# Machine-config home for the user-tunable lookup options (Settings ▸ Location
# Settings). Read live so a change takes effect on the next 'Get location'. Kept
# here (not in the PyQt settings module) so this file stays import-light + the
# standalone probe can read them too.
_CONFIG_SECTION = "geolocate"
_CONFIG_DEFAULTS = {"lang": "en", "zoom": 14, "overwrite": False}


def load_geolocate_config() -> dict:
    """{'lang', 'zoom', 'overwrite'} from the machine config, with defaults."""
    from utils.config import _config
    return {
        "lang": _config.get(_CONFIG_SECTION, "lang", fallback=_CONFIG_DEFAULTS["lang"]),
        "zoom": _config.getint(_CONFIG_SECTION, "zoom", fallback=_CONFIG_DEFAULTS["zoom"]),
        "overwrite": _config.getboolean(_CONFIG_SECTION, "overwrite",
                                        fallback=_CONFIG_DEFAULTS["overwrite"]),
    }


def save_geolocate_config(cfg: dict) -> None:
    from utils.config import _config, persist_config
    if not _config.has_section(_CONFIG_SECTION):
        _config.add_section(_CONFIG_SECTION)
    for k, v in cfg.items():
        _config.set(_CONFIG_SECTION, k, str(v))
    persist_config()


def gps_decimal(path):
    """Signed decimal (lat, lon) read from a file's GPS (via exiftool's Composite,
    which applies the N/S/E/W refs), or None if there is no GPS."""
    out = subprocess.run(
        ["exiftool", "-n", "-G", "-j",
         "-Composite:GPSLatitude", "-Composite:GPSLongitude",
         "-XMP:GPSLatitude", "-XMP:GPSLongitude", str(path)],
        capture_output=True, text=True, encoding="utf-8").stdout
    rows = json.loads(out) if out.strip() else []
    if not rows:
        return None
    d = rows[0]
    lat = d.get("Composite:GPSLatitude", d.get("XMP:GPSLatitude"))  # both signed; not EXIF (unsigned)
    lon = d.get("Composite:GPSLongitude", d.get("XMP:GPSLongitude"))
    if lat is None or lon is None:
        return None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


# Per-step read memo: {normpath: (mtime_ns, fields)}. Keyed on the file's mtime so
# any write (exiftool replaces the file, bumping mtime) auto-invalidates the entry
# on the next read — a self-invalidating cache, no manual eviction needed. Spares
# the trip/preview large viewer an exiftool spawn when you step back to a picture
# (the sidecar viewer serves this from its row cache and never reaches here).
_LOCATION_READ_CACHE: dict = {}


def read_location_fields(path):
    """Read the location text already in a file's XMP (the same tags we write),
    so the viewer can show it without the CSV. Empty strings when absent.
    Memoised by path+mtime so repeated reads of an unchanged file are free."""
    import os
    key = os.path.normcase(os.path.normpath(str(path)))
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        mtime = None
    if mtime is not None:
        hit = _LOCATION_READ_CACHE.get(key)
        if hit is not None and hit[0] == mtime:
            return dict(hit[1])     # copy — callers must not mutate the cached dict
    out = subprocess.run(
        ["exiftool", "-config", _exiftool_config(), "-charset", "utf8", "-j",
         "-location", "-city", "-state", "-country", "-countrycode",
         "-XMP-sv:Municipality", str(path)],
        capture_output=True, text=True, encoding="utf-8").stdout
    rows = json.loads(out) if out.strip() else []
    d = rows[0] if rows else {}
    fields = {
        "Location": d.get("Location", "") or "",
        "City": d.get("City", "") or "",
        "Municipality": d.get("Municipality", "") or "",
        "State": d.get("State", "") or "",
        "Country": d.get("Country", "") or "",
        "CountryCode": d.get("CountryCode", "") or "",
    }
    if mtime is not None:
        _LOCATION_READ_CACHE[key] = (mtime, dict(fields))
    return fields


class GeoConnectivityError(Exception):
    """Nominatim could not be reached at all (DNS failure / connection refused /
    timeout) — distinct from a query that reached the server and just returned
    nothing useful. Lets a batch caller stop hammering a dead network with one
    query per remaining place instead of repeating the same failure hundreds of
    times (e.g. hotel wifi drops mid-batch)."""


def reverse_geocode(lat, lon, *, lang="en", zoom=14, timeout=10):
    """Raw Nominatim reverse-geocode result (dict), or None on failure.
    `lang` (accept-language) controls the script of the names — without it
    Nominatim returns the local language (e.g. Greek), so "en"/"nl" is what makes
    the names usable in a caption. zoom ~14 lands around village/suburb level.
    Raises GeoConnectivityError when the network itself is the problem (as
    opposed to a bad/empty response), so callers can fail fast."""
    import requests
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"format": "jsonv2", "lat": lat, "lon": lon,
                    "zoom": zoom, "addressdetails": 1, "accept-language": lang},
            headers={"User-Agent": _USER_AGENT}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        raise GeoConnectivityError(str(e)) from e
    except Exception as e:
        logger.warning(f"Nominatim reverse-geocode failed for {lat},{lon}: {e}")
        return None


# How a coordinate we wrote was obtained. Empty (or anything else) means the
# CAMERA recorded it — not ours to overwrite. This is the value the map's move rule
# reads, and it is the ONLY thing that grants permission to move a picture.
GPS_MANUAL = "MANUAL"

# One arcsecond ~ 31 m. A place-name lookup is good to somewhere between tens and
# hundreds of metres, so writing hundredths of an arcsecond (~0.3 m) would claim an
# accuracy we simply do not have.
_ARCSEC = 1.0 / 3600.0


def coarse_coord(lat, lon, arcsec: int = 1):
    """Round (lat, lon) to whole arcseconds — the honest precision of a coordinate
    derived from a PLACE NAME rather than measured by a receiver.

    This is a truthfulness device and a human-facing one: a coarse coordinate LOOKS
    approximate ("7 deg 12' 52\" N" beside a camera's "…52.66\" N"), which is the
    cue that it still wants refining. It is deliberately NOT the provenance flag —
    permission to move lives in GPSProcessingMethod, because 1.9% of the master's
    genuine camera fixes already print as whole arcseconds, and because any tool
    that rewrites GPS can reformat a value and silently create or destroy a signal
    carried in its decimals.
    """
    step = _ARCSEC * max(1, int(arcsec))
    return (round(lat / step) * step, round(lon / step) * step)


def search_places(query, *, lang="en", limit=8, timeout=10, viewbox=None):
    """FORWARD geocode — a place NAME → candidate coordinates. The mirror of
    reverse_geocode, and the answer to "I wrote down where I was, but the camera
    recorded no GPS": you type "Jetwing St. Andrews, Nuwara Eliya" and get the
    coordinate to place the picture on.

    Returns [{name, lat, lon, kind}, …] best-match first, or [] on failure /
    no match. `viewbox` (minlon, minlat, maxlon, maxlat) softly prefers results
    inside the area you are already looking at — the same hotel name can exist in
    three countries, and the trip's own map is the best hint we have about which
    one you mean. It is a preference, not a filter: an exact match outside the box
    is still returned rather than silently dropped.
    """
    q = (query or "").strip()
    if not q:
        return []
    params = {"format": "jsonv2", "q": q, "limit": limit,
              "addressdetails": 1, "accept-language": lang}
    if viewbox:
        params["viewbox"] = ",".join(f"{v:.6f}" for v in viewbox)
        params["bounded"] = 0          # prefer, don't restrict
    try:
        import requests
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params=params,
                         headers={"User-Agent": _USER_AGENT}, timeout=timeout)
        r.raise_for_status()
        rows = r.json() or []
    except Exception as e:
        logger.warning(f"Nominatim place search failed for {q!r}: {e}")
        return []

    out = []
    for row in rows:
        try:
            out.append({
                "name": row.get("display_name", "") or q,
                "lat":  float(row["lat"]),
                "lon":  float(row["lon"]),
                # e.g. "hotel", "peak", "nature_reserve" — tells a lake from a town
                "kind": (row.get("type") or row.get("category") or "").replace("_", " "),
            })
        except (KeyError, TypeError, ValueError):
            continue
    logger.info(f"Place search {q!r}: {len(out)} candidate(s).")
    return out


# Address keys, most-to-least descriptive, that make a good caption "Location"
# (a named natural/leisure feature reads better than an administrative unit).
_LOCATION_KEYS = (
    "nature_reserve", "protected_area", "national_park", "park", "leisure",
    "tourism", "water", "bay", "wetland", "peak", "natural", "locality",
    "neighbourhood", "suburb",
)


def place_fields(result):
    """Map a Nominatim result to the location fields we'd write to the XMP.

    Location prefers a named feature (a reserve / lake / park) over the
    settlement, since that's what reads well in a website caption; the website's
    getFormattedLocation() then shows a long Location alone, else 'Location
    (City, State, Country)'."""
    if not result:
        return {}
    a = result.get("address", {})
    city = (a.get("city") or a.get("town") or a.get("village") or a.get("hamlet")
            or a.get("municipality") or "")
    location = ""
    for k in _LOCATION_KEYS:
        if a.get(k):
            location = a[k]
            break
    # The result's own primary name (e.g. the feature the point sits in) is a
    # good descriptive Location too, when it isn't just the settlement.
    if not location:
        name = result.get("name") or ""
        if name and name != city:
            location = name
    return {
        "Location": location,
        "City": city,
        "Municipality": a.get("municipality") or "",  # compare-only (no standard XMP tag)
        "State": a.get("state") or a.get("region") or "",
        "Country": a.get("country") or "",
        "CountryCode": (a.get("country_code") or "").upper(),
        "display": result.get("display_name", ""),
    }


def batch_gps(paths):
    """{normalised path: (lat, lon)} read for many files in ONE exiftool call —
    so the batch reads GPS off the GUI thread instead of one call per picture
    (which froze the UI before the progress dialog appeared)."""
    import os
    from engine.exif_writer import run_exiftool_files
    paths = [str(p) for p in paths if p]
    if not paths:
        return {}
    # Composite is signed for RAW EXIF; XMP:GPSLatitude is signed for sidecars.
    # Never EXIF:GPSLatitude (that's the unsigned magnitude). Files go through a
    # -@ argfile so a big day (100s of files) can't exceed the command-line limit.
    out = run_exiftool_files(
        ["-n", "-G", "-j", "-Composite:GPSLatitude", "-Composite:GPSLongitude",
         "-XMP:GPSLatitude", "-XMP:GPSLongitude"], paths).stdout
    rows = json.loads(out) if out.strip() else []
    res = {}
    for r in rows:
        lat = r.get("Composite:GPSLatitude", r.get("XMP:GPSLatitude"))
        lon = r.get("Composite:GPSLongitude", r.get("XMP:GPSLongitude"))
        if lat is None or lon is None:
            continue
        try:
            res[os.path.normcase(os.path.normpath(r.get("SourceFile", "")))] = (float(lat), float(lon))
        except (TypeError, ValueError):
            pass
    return res


def batch_datetime_gps(paths):
    """{normalised path: {"date": DateTimeOriginal str, "has_gps": bool,
    "cur_alt": float | None}} read for many files in ONE exiftool call — the
    tracklog-geotag dialog's read of just-copied pictures (batch_gps's twin,
    plus the capture time correlation needs). Same tag set as batch_gps, with
    DateTimeOriginal and GPSAltitude added — the altitude lets a re-run skip
    photos that already carry a close-enough value instead of re-proposing
    every one of them (see engine.tracklog_geotag._altitude_already_close)."""
    import os
    from engine.exif_writer import run_exiftool_files
    paths = [str(p) for p in paths if p]
    if not paths:
        return {}
    out = run_exiftool_files(
        ["-n", "-G", "-j", "-EXIF:DateTimeOriginal", "-XMP:DateTimeOriginal",
         "-Composite:GPSLatitude", "-Composite:GPSLongitude",
         "-XMP:GPSLatitude", "-XMP:GPSLongitude",
         "-Composite:GPSAltitude", "-XMP:GPSAltitude"], paths).stdout
    rows = json.loads(out) if out.strip() else []
    res = {}
    for r in rows:
        lat = r.get("Composite:GPSLatitude", r.get("XMP:GPSLatitude"))
        lon = r.get("Composite:GPSLongitude", r.get("XMP:GPSLongitude"))
        alt = r.get("Composite:GPSAltitude", r.get("XMP:GPSAltitude"))
        # A RAW/JPG reports its date under EXIF; a plain .xmp sidecar (this
        # handler's usual input — see batch_gps's identical GPS split above)
        # has no EXIF group at all and reports it under XMP instead.
        date = r.get("EXIF:DateTimeOriginal", r.get("XMP:DateTimeOriginal"))
        key = os.path.normcase(os.path.normpath(r.get("SourceFile", "")))
        try:
            cur_alt = float(alt) if alt is not None else None
        except (TypeError, ValueError):
            cur_alt = None
        res[key] = {"date": str(date or ""),
                   "has_gps": lat is not None and lon is not None,
                   "cur_alt": cur_alt}
    return res


def haversine_m(a, b):
    """Great-circle distance in metres between (lat, lon) points a and b."""
    import math
    (lat1, lon1), (lat2, lon2) = a, b
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


# XMP tags written for each location field. Standard ones round-trip against the
# pipeline's -location/-city/-state/-country read; Municipality is a custom tag
# (no standard slot exists) written via the -config namespace below.
_LOCATION_TAGS = {
    "Location": "XMP-iptcCore:Location",
    "City": "XMP-photoshop:City",
    "Municipality": "XMP-sv:Municipality",
    "State": "XMP-photoshop:State",
    "Country": "XMP-photoshop:Country",
    "CountryCode": "XMP-iptcCore:CountryCode",
}


def _exiftool_config():
    """Path to the exiftool config defining XMP-sv:Municipality (needed to WRITE
    the custom tag; reading it needs no config)."""
    from pathlib import Path
    return str(Path(__file__).with_name("sv_exiftool.config"))


def write_location(path, fields, overwrite=True, protect=()):
    """Write the non-empty location fields into a file's XMP (sidecar or JPG).
    overwrite=False → only fill fields that are currently empty (don't replace
    existing values). `protect` names fields that are fill-only EVEN when
    overwrite=True — a value already on the file is kept (e.g. a hand-entered
    Location like "Hotel Bourbon Atibaia" must survive a coarse reverse-geocode).
    Uses -config so the custom Municipality tag is defined; values go through a -@
    argfile (run_exiftool_files) for unicode safety."""
    import os
    from engine.exif_writer import run_exiftool_files
    from pathlib import Path
    protect = set(protect)
    base = {k: tag for k, tag in _LOCATION_TAGS.items() if fields.get(k)}
    key = os.path.normcase(os.path.normpath(str(path)))
    current = {}
    if (not overwrite or protect) and base:
        current = {key: read_location_fields(path)}
        # A field is skipped when the file already has a value AND we're not allowed
        # to replace it — either globally (overwrite=False) or field-wise (protect).
        to_write = {k: tag for k, tag in base.items()
                    if not (current[key].get(k) and (not overwrite or k in protect))}
    else:
        to_write = dict(base)
    args = [f"-{tag}={fields[k]}" for k, tag in to_write.items()]
    if not args:
        return False
    try:
        r = run_exiftool_files(args + ["-overwrite_original"], [str(path)],
                               pre_args=["-config", _exiftool_config()])
        if r.returncode == 0:
            # Reflect the file's effective location (written + protect-kept) into the
            # master index — same contract as write_location_batch.
            _mirror_location_to_master(_effective_location_by_upn(
                [str(path)], base, fields, current,
                {str(path): tuple(to_write)}, {str(path)}))
        return r.returncode == 0
    except Exception as e:
        logger.warning(f"Location write failed for {Path(path).name}: {e}")
        return False


def read_location_fields_batch(paths):
    """{normcase(normpath): location-fields} read for MANY files in ONE exiftool
    call — the batched twin of read_location_fields, used so a fill-empties-only
    write doesn't pay a per-file exiftool spawn just to check what's already there."""
    import os
    from engine.exif_writer import run_exiftool_files
    paths = [str(p) for p in paths if p]
    if not paths:
        return {}
    out = run_exiftool_files(
        ["-j", "-location", "-city", "-state", "-country", "-countrycode",
         "-XMP-sv:Municipality"], paths, pre_args=["-config", _exiftool_config()]).stdout
    rows = json.loads(out) if out.strip() else []
    res = {}
    for d in rows:
        key = os.path.normcase(os.path.normpath(d.get("SourceFile", "")))
        res[key] = {
            "Location": d.get("Location", "") or "",
            "City": d.get("City", "") or "",
            "Municipality": d.get("Municipality", "") or "",
            "State": d.get("State", "") or "",
            "Country": d.get("Country", "") or "",
            "CountryCode": d.get("CountryCode", "") or "",
        }
    return res


def read_place_and_provenance_batch(paths):
    """{normcase(normpath): {…location fields…, 'GPSProcessingMethod': str}} for MANY
    files in ONE exiftool call — the place-sync backfill reads what a sidecar already
    holds (its place name AND how its coordinate was obtained) so it can reflect both
    into the master index without a network lookup."""
    import os
    from engine.exif_writer import run_exiftool_files
    paths = [str(p) for p in paths if p]
    if not paths:
        return {}
    out = run_exiftool_files(
        ["-j", "-location", "-city", "-state", "-country", "-countrycode",
         "-XMP-sv:Municipality", "-GPSProcessingMethod"], paths,
        pre_args=["-config", _exiftool_config()]).stdout
    rows = json.loads(out) if out.strip() else []
    res = {}
    for d in rows:
        key = os.path.normcase(os.path.normpath(d.get("SourceFile", "")))
        res[key] = {
            "Location": d.get("Location", "") or "",
            "City": d.get("City", "") or "",
            "Municipality": d.get("Municipality", "") or "",
            "State": d.get("State", "") or "",
            "Country": d.get("Country", "") or "",
            "CountryCode": d.get("CountryCode", "") or "",
            "GPSProcessingMethod": d.get("GPSProcessingMethod", "") or "",
        }
    return res


def write_location_batch(files, fields, *, overwrite=True, protect=()):
    """Write the non-empty location `fields` (one place) to MANY files in as few
    exiftool calls as possible — one call per distinct set of tags-to-write,
    rather than one spawn per file. All files at a given place share the same
    fields, so this collapses hundreds of per-file writes into ~one call. Returns
    the number of files written.

    overwrite=False → only fill fields that are currently empty; files are grouped
    by which subset is empty (freshly-ingested sidecars are all empty, so this is
    still a single call), keeping the arg set consistent within each call.
    `protect` names fields that are fill-only EVEN when overwrite=True — a value
    already on a file is kept (e.g. a hand-entered Location must survive a coarse
    reverse-geocode). When set it forces the per-file current read like
    overwrite=False, but only for the protected fields."""
    import os
    from engine.exif_writer import run_exiftool_files
    protect = set(protect)
    base = {k: _LOCATION_TAGS[k] for k in _LOCATION_TAGS if fields.get(k)}
    files = [str(f) for f in files if f]
    if not base or not files:
        return 0
    # Read the current values whenever a field might be KEPT (overwrite=False, or a
    # protected field): we need them both to decide what to write and to know what the
    # file will END UP holding — its effective location, which the master index mirrors.
    need_current = not (overwrite and not (protect & set(base)))
    current = read_location_fields_batch(files) if need_current else {}
    write_keys_by_file = {}
    groups = {}
    for f in files:
        cur = current.get(os.path.normcase(os.path.normpath(f)), {}) if need_current else {}
        if need_current:
            # Skip a field only where the file already has it AND replacing it isn't
            # allowed — globally (overwrite=False) or field-wise (protect).
            wkeys = tuple(k for k in base
                          if not (cur.get(k) and (not overwrite or k in protect)))
        else:
            wkeys = tuple(base)                      # overwrite everything
        write_keys_by_file[f] = wkeys
        if wkeys:
            groups.setdefault(wkeys, []).append(f)
    written = 0
    written_files = set()
    for keys, group_files in groups.items():
        args = [f"-{_LOCATION_TAGS[k]}={fields[k]}" for k in keys]
        try:
            r = run_exiftool_files(args + ["-overwrite_original"], group_files,
                                   pre_args=["-config", _exiftool_config()])
            if r.returncode == 0:
                written += len(group_files)
                written_files.update(group_files)
            else:
                logger.warning(f"Batched location write returned {r.returncode}: {r.stderr.strip()}")
        except Exception as e:
            logger.warning(f"Batched location write failed for {len(group_files)} files: {e}")
    # Reflect each touched file's EFFECTIVE location (a written field's new value, or
    # the value a protect/fill-only skip left in place) into the master index, so the
    # album/day cards and website show the name instead of raw coordinates — even for a
    # Location we deliberately did NOT overwrite.
    _mirror_location_to_master(
        _effective_location_by_upn(files, base, fields, current,
                                   write_keys_by_file, written_files))
    return written


def _effective_location_by_upn(files, base, fields, current, write_keys_by_file,
                               written_files) -> dict:
    """Per UniquePictureName, the place fields the files now actually hold: a WRITTEN
    field takes its new value, a KEPT/skipped field keeps what was already on the file.
    So the master index reflects the sidecar truth — including a protected Location we
    chose not to overwrite. A field whose write FAILED is dropped back to the kept
    value (the file never took the new one)."""
    import os
    from pathlib import Path
    from engine.filecsv import deriveUniquePictureName
    out: dict = {}
    for f in files:
        cur = current.get(os.path.normcase(os.path.normpath(f)), {})
        wkeys = write_keys_by_file.get(f, ())
        eff = {}
        for k in base:
            if k in wkeys and f in written_files:
                eff[k] = fields[k]
            elif cur.get(k):
                eff[k] = cur[k]
        if not eff:
            continue
        upn = deriveUniquePictureName(Path(f).name)
        if upn:
            out.setdefault(upn, {}).update(eff)
    return out


def _mirror_location_to_master(loc_by_upn: dict) -> None:
    """Best-effort: reflect just-written place names into the master's picture rows,
    so the album/day cards, the maps and the website — which read the master INDEX,
    not each sidecar — show the name instead of falling back to raw coordinates.

    The sidecar stays the truth (the XMP was written first); this only keeps the
    index in step, exactly what MasterDB.apply_location_fields is for. Only master
    pictures are touched (an UPN the master lacks updates nothing); a read-only or
    absent master is a silent no-op — the sidecar write already succeeded, so a
    location write never fails on the mirror."""
    try:
        from pathlib import Path
        from utils.config import _config
        loc_by_upn = {u: f for u, f in (loc_by_upn or {}).items() if u and f}
        if not loc_by_upn:
            return
        master_root = (_config.get("master", "masterbiotadatabase", fallback="")
                       or "").replace('"', "").replace("'", "").strip()
        if not master_root or not Path(master_root).is_dir():
            return
        if _config.get("master", "readonly", fallback="yes").strip().lower() != "no":
            return  # read-only master (SmallBoy) — never write
        from db.master_db import MasterDB
        MasterDB(master_root).apply_location_fields(loc_by_upn)
    except Exception as e:
        logger.debug(f"location master-mirror skipped: {e}")


def write_gps(files, lat, lon, *, method: str = GPS_MANUAL):
    """Write signed-decimal GPS (lat, lon) into the XMP of one or more files —
    used by the viewer's copy/paste-GPS for shots the sv2 sibling/time/bracket
    backfill can't reach. RAW is never touched; the pipeline + viewer read it back
    via Composite/XMP:GPSLatitude. Returns the number of files written.

    `method` stamps GPSProcessingMethod alongside the fix. It defaults to MANUAL
    because EVERY coordinate this function writes is one a human caused — dragged
    onto the map, copied from a neighbour, or derived from a place name. None of
    them came from a receiver, and that is exactly what makes them safe to move and
    refine later. A camera's own fix never passes through here, so it keeps whatever
    (usually nothing) the camera recorded, and the map will refuse to move it.
    """
    from engine.exif_writer import run_exiftool_files
    files = [str(f) for f in files if f]
    if not files or lat is None or lon is None:
        return 0
    args = [f"-XMP:GPSLatitude={lat}", f"-XMP:GPSLongitude={lon}", "-overwrite_original"]
    if method:
        # Written to BOTH namespaces: exiftool's Composite/EXIF read path is what
        # the pipeline harvests, and the XMP copy is what survives in a sidecar
        # beside a RAW we must not touch.
        args[2:2] = [f"-EXIF:GPSProcessingMethod={method}",
                     f"-XMP:GPSProcessingMethod={method}"]
    try:
        r = run_exiftool_files(args, files)
        if r.returncode != 0:
            logger.warning(f"GPS write returned {r.returncode}: {r.stderr.strip()}")
            return 0
        _mirror_gps_to_master(files, lat, lon, method=method)
        return len(files)
    except Exception as e:
        logger.warning(f"GPS write failed: {e}")
        return 0


def _mirror_gps_to_master(files, lat, lon, method: str | None = None):
    """Best-effort: also write the just-placed coordinate into the master's
    pictures.GPSPosition, so the No-GPS count and the map read it straight from
    the UPN (the "one truth") instead of re-opening each sidecar — which is what
    made switching to a big album freeze for ~30 s. Only master pictures are
    touched (apply_gps skips any UPN the master doesn't have, e.g. a plain trip
    shot); a read-only master or an unreachable DB is a silent no-op — the sidecar
    write above already succeeded, so the geotag itself never fails on the mirror.

    `method` also mirrors GPSProcessingMethod (MANUAL for anything a human placed).
    Without it the master row carries the coordinate but no provenance, so on the
    NEXT session the map — which reads provenance from the master index, not the
    sidecar — mistook a hand-placed picture for a camera fix and refused to move it."""
    try:
        from pathlib import Path
        from utils.config import _config
        master_root = (_config.get("master", "masterbiotadatabase", fallback="")
                       or "").replace('"', "").replace("'", "").strip()
        if not master_root or not Path(master_root).is_dir():
            return
        if _config.get("master", "readonly", fallback="yes").strip().lower() != "no":
            return  # read-only master (SmallBoy) — never write
        from engine.filecsv import deriveUniquePictureName
        gps_by_upn = {deriveUniquePictureName(Path(f).name): (lat, lon)
                      for f in files if f}
        gps_by_upn.pop("", None)
        if gps_by_upn:
            from db.master_db import MasterDB
            MasterDB(master_root).apply_gps(gps_by_upn, method=method)
    except Exception as e:
        logger.debug(f"GPS master-mirror skipped: {e}")


def write_altitude(files, altitude_m, *, method: str = GPS_MANUAL):
    """Write a signed altitude in metres into the XMP of one or more files —
    the tracklog-geotag dialog's altitude fill (camera GPS never records
    altitude; a tracklog is the only source). Sibling to write_gps. RAW is
    never touched; the sidecar carries it. Returns the number of files written.

    GPSAltitudeRef encodes the sign SEPARATELY from GPSAltitude (which is a
    magnitude): "0" = above sea level, "1" = below. A polder shot's altitude
    can legitimately be negative (NAP), so getting the ref bit right matters —
    dropping it silently turns a negative elevation positive."""
    from engine.exif_writer import run_exiftool_files
    files = [str(f) for f in files if f]
    if not files or altitude_m is None:
        return 0
    ref = "1" if altitude_m < 0 else "0"
    args = [f"-XMP-exif:GPSAltitude={abs(altitude_m)}",
           f"-XMP-exif:GPSAltitudeRef={ref}", "-overwrite_original"]
    if method:
        args[2:2] = [f"-EXIF:GPSProcessingMethod={method}",
                     f"-XMP:GPSProcessingMethod={method}"]
    try:
        r = run_exiftool_files(args, files)
        if r.returncode != 0:
            logger.warning(f"Altitude write returned {r.returncode}: {r.stderr.strip()}")
            return 0
        _mirror_altitude_to_master(files, altitude_m)
        return len(files)
    except Exception as e:
        logger.warning(f"Altitude write failed: {e}")
        return 0


def _mirror_altitude_to_master(files, altitude_m):
    """Best-effort: also write the just-placed altitude into the master's
    pictures.GPSAltitude, mirroring _mirror_gps_to_master's guards exactly
    (read-only or unconfigured master is a silent no-op — the sidecar write
    already succeeded, so the geotag itself never fails on the mirror)."""
    try:
        from pathlib import Path
        from utils.config import _config
        master_root = (_config.get("master", "masterbiotadatabase", fallback="")
                       or "").replace('"', "").replace("'", "").strip()
        if not master_root or not Path(master_root).is_dir():
            return
        if _config.get("master", "readonly", fallback="yes").strip().lower() != "no":
            return  # read-only master (SmallBoy) — never write
        from engine.filecsv import deriveUniquePictureName
        alt_by_upn = {deriveUniquePictureName(Path(f).name): altitude_m
                     for f in files if f}
        alt_by_upn.pop("", None)
        if alt_by_upn:
            from db.master_db import MasterDB
            MasterDB(master_root).apply_altitude(alt_by_upn)
    except Exception as e:
        logger.debug(f"Altitude master-mirror skipped: {e}")


# ── THROWAWAY: safe-compare dry run ───────────────────────────────────────────
# While True, 'l' / run_geolocate_batch does NOT touch any file — it writes a CSV
# comparing the CURRENT vs PROPOSED location for each picture, so the override
# policy can be decided from real data first. Set False (or delete this block and
# the dry_run branch) to restore real writing.
DRY_RUN_COMPARE = False


# Raw Nominatim address keys worth seeing in the compare, to decide the mapping.
_RAW_ADDRESS_KEYS = ["nature_reserve", "protected_area", "leisure", "tourism",
                     "neighbourhood", "suburb", "road", "hamlet", "village",
                     "town", "city", "municipality", "county", "state", "country"]


def _write_compare_csv(rows):
    """rows: list of (label, (lat,lon), current_fields, proposed_fields, raw_address).
    Writes a current-vs-proposed comparison CSV (+ every raw Nominatim address
    part) to the user's home dir."""
    import csv
    import time
    from pathlib import Path
    fields = ["Location", "City", "Municipality", "State", "Country", "CountryCode"]
    path = Path.home() / f"location_compare_{time.strftime('%Y%m%d-%H%M%S')}.csv"
    header = ["Picture", "Lat", "Lon"]
    for f in fields:
        header += [f + "_current", f + "_proposed", f + "_changes"]
    header += ["display"] + ["nm_" + k for k in _RAW_ADDRESS_KEYS]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:  # -sig → Excel reads UTF-8
        w = csv.writer(fh)
        w.writerow(header)
        for label, (lat, lon), cur, new, addr in rows:
            row = [label, f"{lat:.6f}", f"{lon:.6f}"]
            for f in fields:
                c = cur.get(f, "") or ""
                n = new.get(f, "") or ""
                row += [c, n, "CHANGE" if c != n else ""]
            row.append(new.get("display", ""))
            row += [(addr or {}).get(k, "") for k in _RAW_ADDRESS_KEYS]
            w.writerow(row)
    logger.info(f"Location safe-compare CSV written: {path}")
    return str(path)


def run_geolocate_batch(parent, targets, *, lang=None, zoom=None, min_move_m=100.0,
                        throttle_s=1.1, dry_run=None, overwrite=None, on_done=None):
    """Reverse-geocode and write location for an ordered list of targets, on a
    worker thread behind a modal progress dialog with Cancel.

    targets: list of (label, gps_source_path | None, [files_to_write]).
    GPS is read for all targets in one batched exiftool call ON THE WORKER THREAD
    (so the dialog shows immediately instead of the UI freezing while ~hundreds of
    per-file reads run). Dedup-by-distance: consecutive shots within `min_move_m`
    of the last queried fix reuse its result. Nominatim queries are throttled.
    lang / zoom / overwrite default to the user's Location Settings (the
    [geolocate] config) when left None, so a settings change takes effect on the
    next run; callers can still pass explicit values.
    dry_run (default DRY_RUN_COMPARE): write a comparison CSV instead of the XMP.
    overwrite False: only fill empty location fields, never replace existing.
    on_done(): called after writes complete but BEFORE dlg.close() — use this to
    refresh UI from the just-written XMP while still inside the exec() loop so
    Qt repaints the caller's window on dialog close."""
    from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal
    from PyQt6.QtWidgets import QProgressDialog
    import os
    import time

    cfg = load_geolocate_config()
    if lang is None:
        lang = cfg["lang"]
    if zoom is None:
        zoom = cfg["zoom"]
    if overwrite is None:
        overwrite = cfg["overwrite"]
    if dry_run is None:
        dry_run = DRY_RUN_COMPARE
    targets = list(targets)
    if not targets:
        return

    class _Worker(QObject):
        progress = pyqtSignal(int, str)
        finished = pyqtSignal(int, int, int, str, bool)  # queried, written, total, csv_path, network_down

        def __init__(self):
            super().__init__()
            self._cancel = False

        def cancel(self):
            self._cancel = True

        def run(self):
            # All GPS in one batched exiftool call (off the GUI thread).
            self.progress.emit(0, "Reading GPS…")
            gps_map = batch_gps([gsrc for (_, gsrc, _) in targets if gsrc])
            # Every place already looked up this batch: (pt, fields, addr). A new
            # fix within min_move_m of ANY of these reuses it (not just the
            # previous one), so a card whose shots are all near the same spot —
            # even interleaved with shots elsewhere — costs one query per place,
            # not one per picture. The throttled online query is the slow part.
            # cache: distinct places looked up this batch, indexed by position.
            # place_files[idx] accumulates every file to write for that place, so
            # the XMP writes happen ONCE PER PLACE after the query loop (one
            # exiftool call per place) instead of one spawn per picture — the
            # slow part of a big card was hundreds of per-file exiftool launches.
            cache = []                                      # [(latlon, fields, addr)]
            place_files = {}                                # idx -> [files]
            queried = written = 0
            compare_rows = []
            # Two consecutive connection-level failures (not just an empty/odd
            # result) mean Nominatim is unreachable, not that this one place is
            # awkward — stop querying rather than repeat the same failure once
            # per remaining place (e.g. hotel wifi drops mid-batch).
            consecutive_conn_fail = 0
            network_down = False
            for i, (label, gps_source, files) in enumerate(targets, 1):
                if self._cancel or network_down:
                    break
                self.progress.emit(i, label)
                latlon = gps_map.get(os.path.normcase(os.path.normpath(gps_source))) if gps_source else None
                if not latlon or not files:
                    continue
                idx = next((j for j, (pt, _f, _a) in enumerate(cache)
                            if haversine_m(pt, latlon) < min_move_m), None)
                if idx is not None:
                    fields, addr = cache[idx][1], cache[idx][2]   # reuse — same place
                else:
                    if queried > 0:
                        time.sleep(throttle_s)              # honour ≤1 req/s
                    try:
                        res = reverse_geocode(*latlon, lang=lang, zoom=zoom)
                        consecutive_conn_fail = 0
                    except GeoConnectivityError as e:
                        queried += 1
                        consecutive_conn_fail += 1
                        if consecutive_conn_fail >= 2:
                            network_down = True
                            logger.warning(
                                f"Place lookup stopped — Nominatim unreachable ({e}). "
                                f"Skipping remaining lookups this run; re-run 'Get location' "
                                f"once you're back online.")
                            break
                        logger.debug(f"Place lookup connection failed for {label}: {e}")
                        continue
                    fields = place_fields(res)
                    addr = (res or {}).get("address", {})
                    queried += 1
                    cache.append((latlon, fields, addr))
                    idx = len(cache) - 1
                if not fields:
                    continue
                if dry_run:
                    # Safe compare — read current, record, touch nothing.
                    compare_rows.append((label, latlon, read_location_fields(files[0]), fields, addr))
                elif any(fields.get(k) for k in _LOCATION_TAGS):
                    place_files.setdefault(idx, []).extend(files)
            # Batched writes: one place at a time, each in (about) one exiftool call.
            if not dry_run and place_files:
                self.progress.emit(len(targets), "Writing location names…")
                for idx, files in place_files.items():
                    written += write_location_batch(files, cache[idx][1], overwrite=overwrite)
            csv_path = _write_compare_csv(compare_rows) if (dry_run and compare_rows) else ""
            self.finished.emit(queried, written, len(targets), csv_path, network_down)

    dlg = QProgressDialog(f"Looking up locations for {len(targets)} pictures…",
                          "Cancel", 0, len(targets), parent)
    dlg.setWindowTitle("Get location")
    dlg.setMinimumDuration(0)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    thread = QThread(parent)
    worker = _Worker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.progress.connect(lambda i, name: (dlg.setValue(i),
                                             dlg.setLabelText(f"{i} / {len(targets)}\n{name}")))
    dlg.canceled.connect(worker.cancel)

    def on_finished(queried, written, total, csv_path, network_down):
        thread.quit()
        thread.wait()
        if on_done is not None:
            try:
                on_done()
            except Exception as e:
                logger.warning(f"run_geolocate_batch on_done raised: {e}")
        dlg.close()
        if csv_path:
            logger.info(f"Get location (SAFE COMPARE — no files changed): {total} pictures, "
                        f"{queried} Nominatim queries → {csv_path}")
            try:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.information(
                    parent, "Location — safe compare (nothing changed)",
                    f"No files were modified.\n\nCurrent-vs-proposed comparison for "
                    f"{total} pictures written to:\n{csv_path}\n\n({queried} Nominatim queries.)")
            except Exception:
                pass
        elif network_down:
            logger.info(f"Get location: wrote {written}/{total} pictures "
                        f"({queried} Nominatim queries) — stopped early, Nominatim unreachable. "
                        f"Re-run 'Get location' once you're back online.")
        else:
            logger.info(f"Get location: wrote {written}/{total} pictures "
                        f"({queried} Nominatim queries; the rest reused nearby fixes).")

    worker.finished.connect(on_finished)
    dlg._thread, dlg._worker = thread, worker
    thread.start()
    dlg.exec()


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) == 3:
        lat, lon = float(sys.argv[1]), float(sys.argv[2])
    elif len(sys.argv) == 2:
        gps = gps_decimal(sys.argv[1])
        if not gps:
            print("No GPS found in", sys.argv[1]); sys.exit(1)
        lat, lon = gps
        print(f"GPS: {lat:.6f}, {lon:.6f}")
    else:
        print(__doc__); sys.exit(1)
    try:
        res = reverse_geocode(lat, lon)
    except GeoConnectivityError as e:
        print(f"Reverse-geocode failed — Nominatim unreachable: {e}"); sys.exit(1)
    if not res:
        print("Reverse-geocode failed (offline / blocked / rate-limited?)."); sys.exit(1)
    print("\ndisplay_name:", res.get("display_name", ""))
    print("\naddress parts:")
    for k, v in res.get("address", {}).items():
        print(f"  {k:18} {v}")
    print("\nproposed XMP fields:")
    for k, v in place_fields(res).items():
        if k != "display":
            print(f"  {k:12} {v}")
