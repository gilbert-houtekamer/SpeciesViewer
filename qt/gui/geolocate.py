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


def gps_decimal(path):
    """Signed decimal (lat, lon) read from a file's GPS (via exiftool's Composite,
    which applies the N/S/E/W refs), or None if there is no GPS."""
    out = subprocess.run(
        ["exiftool", "-n", "-G", "-j",
         "-Composite:GPSLatitude", "-Composite:GPSLongitude",
         "-GPSLatitude", "-GPSLongitude", str(path)],
        capture_output=True, text=True, encoding="utf-8").stdout
    rows = json.loads(out) if out.strip() else []
    if not rows:
        return None
    d = rows[0]
    lat = d.get("Composite:GPSLatitude", d.get("EXIF:GPSLatitude", d.get("XMP:GPSLatitude")))
    lon = d.get("Composite:GPSLongitude", d.get("EXIF:GPSLongitude", d.get("XMP:GPSLongitude")))
    if lat is None or lon is None:
        return None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def read_location_fields(path):
    """Read the location text already in a file's XMP (the same tags we write),
    so the viewer can show it without the CSV. Empty strings when absent."""
    out = subprocess.run(
        ["exiftool", "-charset", "utf8", "-j",
         "-location", "-city", "-state", "-country", "-countrycode", str(path)],
        capture_output=True, text=True, encoding="utf-8").stdout
    rows = json.loads(out) if out.strip() else []
    d = rows[0] if rows else {}
    return {
        "Location": d.get("Location", "") or "",
        "City": d.get("City", "") or "",
        "State": d.get("State", "") or "",
        "Country": d.get("Country", "") or "",
        "CountryCode": d.get("CountryCode", "") or "",
    }


def reverse_geocode(lat, lon, *, lang="en", zoom=14, timeout=10):
    """Raw Nominatim reverse-geocode result (dict), or None on failure.
    `lang` (accept-language) controls the script of the names — without it
    Nominatim returns the local language (e.g. Greek), so "en"/"nl" is what makes
    the names usable in a caption. zoom ~14 lands around village/suburb level."""
    try:
        import requests
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"format": "jsonv2", "lat": lat, "lon": lon,
                    "zoom": zoom, "addressdetails": 1, "accept-language": lang},
            headers={"User-Agent": _USER_AGENT}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"Nominatim reverse-geocode failed for {lat},{lon}: {e}")
        return None


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
        "State": a.get("state") or a.get("region") or "",
        "Country": a.get("country") or "",
        "CountryCode": (a.get("country_code") or "").upper(),
        "display": result.get("display_name", ""),
    }


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


# XMP tags written for each location field (round-trip-verified against the
# pipeline's -location/-city/-state/-country read).
_LOCATION_TAGS = {
    "Location": "XMP-iptcCore:Location",
    "City": "XMP-photoshop:City",
    "State": "XMP-photoshop:State",
    "Country": "XMP-photoshop:Country",
    "CountryCode": "XMP-iptcCore:CountryCode",
}


def write_location(path, fields):
    """Write the non-empty location fields into a file's XMP (sidecar or JPG)."""
    from ghexif.exif_writer import exiftool_write
    from pathlib import Path
    args = [f"-{tag}={fields[k]}" for k, tag in _LOCATION_TAGS.items() if fields.get(k)]
    if not args:
        return False
    try:
        exiftool_write(Path(path), args)
        return True
    except Exception as e:
        logger.warning(f"Location write failed for {Path(path).name}: {e}")
        return False


# ── THROWAWAY: safe-compare dry run ───────────────────────────────────────────
# While True, 'l' / run_geolocate_batch does NOT touch any file — it writes a CSV
# comparing the CURRENT vs PROPOSED location for each picture, so the override
# policy can be decided from real data first. Set False (or delete this block and
# the dry_run branch) to restore real writing.
DRY_RUN_COMPARE = True


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
    fields = ["Location", "City", "State", "Country", "CountryCode"]
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


def run_geolocate_batch(parent, targets, *, lang="en", min_move_m=100.0, throttle_s=1.1,
                        dry_run=None):
    """Reverse-geocode and write location for an ordered list of targets, on a
    worker thread behind a modal progress dialog with Cancel.

    targets: list of (label, (lat, lon) | None, [files_to_write]).
    Dedup-by-distance: consecutive shots within `min_move_m` of the last queried
    fix reuse its result (no new query) — birds at one spot share a location.
    Nominatim queries are throttled (`throttle_s`) to honour the usage policy.
    dry_run (default DRY_RUN_COMPARE): write a comparison CSV instead of the XMP."""
    from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal
    from PyQt6.QtWidgets import QProgressDialog
    import time

    if dry_run is None:
        dry_run = DRY_RUN_COMPARE
    targets = list(targets)
    if not targets:
        return

    class _Worker(QObject):
        progress = pyqtSignal(int, str)
        finished = pyqtSignal(int, int, int, str)  # queried, written, total, csv_path

        def __init__(self):
            super().__init__()
            self._cancel = False

        def cancel(self):
            self._cancel = True

        def run(self):
            last_pt = last_fields = last_addr = None
            queried = written = 0
            compare_rows = []
            for i, (label, latlon, files) in enumerate(targets, 1):
                if self._cancel:
                    break
                self.progress.emit(i, label)
                if not latlon or not files:
                    continue
                if (last_pt is not None and last_fields is not None
                        and haversine_m(last_pt, latlon) < min_move_m):
                    fields, addr = last_fields, last_addr   # reuse — same place
                else:
                    if queried > 0:
                        time.sleep(throttle_s)              # honour ≤1 req/s
                    res = reverse_geocode(*latlon, lang=lang)
                    fields = place_fields(res)
                    addr = (res or {}).get("address", {})
                    queried += 1
                    last_pt, last_fields, last_addr = latlon, fields, addr
                if not fields:
                    continue
                if dry_run:
                    # Safe compare — read current, record, touch nothing.
                    compare_rows.append((label, latlon, read_location_fields(files[0]), fields, addr))
                elif any(fields.get(k) for k in _LOCATION_TAGS):
                    for f in files:
                        write_location(f, fields)
                    written += 1
            csv_path = _write_compare_csv(compare_rows) if (dry_run and compare_rows) else ""
            self.finished.emit(queried, written, len(targets), csv_path)

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

    def on_finished(queried, written, total, csv_path):
        thread.quit()
        thread.wait()
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
    res = reverse_geocode(lat, lon)
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
