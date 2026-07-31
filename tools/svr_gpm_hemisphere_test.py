"""GPM hemisphere round-trip test — verify copy/paste GPS keeps the sign on S/W.

Backlog item #1: "GPM on a southern/western trip NEEDS TEST — copy/paste GPS +
place is verified in the north; confirm the hemisphere sign handling on a trip
with S/W coordinates."

What this exercises (the REAL copy/paste code, not a stand-in):
  copy step  -> geolocate.gps_decimal / batch_gps  (read a source picture's fix)
  paste step -> geolocate.write_gps                 (write it into a target XMP)
  master     -> engine.geo.parse_gps_dms            (how the master stores/reads it)

For each of the four quadrants (NE / NW / SE / SW) plus the equator/prime-meridian
edges, it writes a real-world coordinate into a FRESH .xmp sidecar via the exact
function copy/paste uses, then reads it back three ways and checks the SIGN and the
magnitude survive. A southern (negative) latitude or western (negative) longitude
that comes back positive is the bug this guards against.

No pictures and no master are touched: write_gps' master-mirror is stubbed out, and
all files live in a throwaway temp dir. Needs exiftool on PATH (same as the app).

Run (PowerShell):
    $env:PYTHONPATH = "d:/code/SpeciesViewer/speciesviewer"
    python tools/svr_gpm_hemisphere_test.py

Exit code 0 = all round-trips preserved the hemisphere; 1 = at least one failed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# --- make the app packages importable (gui/, engine/) -----------------------
_SV = Path(__file__).resolve().parent.parent / "speciesviewer"
if str(_SV) not in sys.path:
    sys.path.insert(0, str(_SV))

from gui import geolocate          # noqa: E402
from engine.geo import parse_gps_dms  # noqa: E402

# Do NOT let a live write reach the real master DB while testing. The sign
# behaviour we care about is entirely in the sidecar write + read-back; the
# master mirror is a separate, already-tested path and pointing it at the global
# master.db during a test would be a side effect (see project_master_geotag).
geolocate._mirror_gps_to_master = lambda *a, **k: None

TOL = 1e-4  # degrees; ~11 m — far tighter than any hemisphere flip

# label, lat, lon, expected NS ref, expected EW ref
CASES = [
    ("NE  Helgoland (verified north)",  54.182100,   7.885700,  "N", "E"),
    ("NW  Tucson AZ",                   32.221700, -110.926300, "N", "W"),
    ("SE  Sydney AU",                  -33.868800,  151.209300, "S", "E"),
    ("SW  Galapagos / Ecuador",         -0.743300,  -90.313300, "S", "W"),
    ("SE  Pilanesberg ZA",             -25.250000,   27.116700, "S", "E"),
    ("SW  Ushuaia (deep south/west)",  -54.801900, -68.303000,  "S", "W"),
    ("Equator / prime meridian edge",   -0.000100,   -0.000100, "S", "W"),
]


def _dms_string(xmp: str) -> str:
    """The human 'deg/min/sec + N/S/E/W' form exiftool derives — exactly what the
    master stores in pictures.GPSPosition and later parses with parse_gps_dms."""
    out = subprocess.run(
        ["exiftool", "-Composite:GPSPosition", "-s3", str(xmp)],
        capture_output=True, text=True, encoding="utf-8").stdout.strip()
    if out:
        return out
    # A bare XMP sidecar has no EXIF refs, so Composite:GPSPosition can be empty;
    # fall back to the individual XMP lat/lon, which already carry the hemisphere.
    out = subprocess.run(
        ["exiftool", "-XMP:GPSLatitude", "-XMP:GPSLongitude", "-s3", str(xmp)],
        capture_output=True, text=True, encoding="utf-8").stdout.strip()
    return ", ".join(out.splitlines())


def _refs(xmp: str) -> tuple[str, str]:
    """The hemisphere letters exiftool reports for the written coordinate."""
    dms = _dms_string(xmp)
    ns = "S" if " S" in f" {dms}" else ("N" if " N" in f" {dms}" else "?")
    ew = "W" if " W" in f" {dms}" else ("E" if " E" in f" {dms}" else "?")
    return ns, ew


def _ok(expect, got) -> bool:
    return got is not None and abs(got[0] - expect[0]) < TOL and abs(got[1] - expect[1]) < TOL


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="gpm_hemi_"))
    print(f"scratch: {tmp}\n")
    failures = 0
    hdr = f"{'case':34} {'gps_decimal':>22} {'batch_gps':>22} {'parse_dms':>22}  refs  result"
    print(hdr)
    print("-" * len(hdr))

    for label, lat, lon, exp_ns, exp_ew in CASES:
        expect = (lat, lon)
        xmp = tmp / (label.split()[0].strip() + ".xmp")

        # PASTE: the exact call the viewer's "Paste GPS + place" makes.
        n = geolocate.write_gps([str(xmp)], lat, lon)

        # COPY / read-back path 1: single-file read (large viewer, copy source).
        rd = geolocate.gps_decimal(str(xmp))
        # read-back path 2: batched read (pipeline / overview No-GPS scan).
        bm = geolocate.batch_gps([str(xmp)])
        bd = bm.get(os.path.normcase(os.path.normpath(str(xmp))))
        # master storage path: the DMS string the master keeps, parsed back.
        pd = parse_gps_dms(_dms_string(str(xmp)))

        ns, ew = _refs(str(xmp))

        checks = [n == 1, _ok(expect, rd), _ok(expect, bd), _ok(expect, pd),
                  ns == exp_ns, ew == exp_ew]
        good = all(checks)
        failures += 0 if good else 1

        def fmt(v):
            return f"{v[0]:+.4f},{v[1]:+.4f}" if v else "None"

        print(f"{label:34} {fmt(rd):>22} {fmt(bd):>22} {fmt(pd):>22}  "
              f"{ns}{ew}   {'PASS' if good else 'FAIL'}")
        if not good:
            print(f"    expected {fmt(expect)} refs {exp_ns}{exp_ew}; "
                  f"wrote={n} decimal={fmt(rd)} batch={fmt(bd)} dms={fmt(pd)} "
                  f"({_dms_string(str(xmp))})")

    print("-" * len(hdr))
    total = len(CASES)
    print(f"\n{total - failures}/{total} passed.")
    if failures:
        print("FAIL — a hemisphere sign was lost somewhere in the copy/paste GPS path.")
    else:
        print("PASS — S/W coordinates keep their sign through write, read-back and master parse.")
    # leave the scratch dir for inspection on failure; remove it on success
    if not failures:
        try:
            for p in tmp.iterdir():
                p.unlink()
            tmp.rmdir()
        except OSError:
            pass
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
