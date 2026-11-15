"""Drop the cached previews of RAWs whose embedded JPEG an editor replaced.

Capture NX 2 (2013-2016) replaced a NEF's camera JPEG with a ~800 px JPEG of its
own crop. SV used that as the picture's preview, so those pictures showed as a
small cut-out. SV now decodes such RAWs instead (gui/preview_extract.py,
_EMBED_REWRITERS), but previews already cached keep the old cut-out until they
are dropped. This tool drops them; they are re-made from the full RAW the next
time the picture is shown.

Only this computer's preview cache (under app-data) is touched — never a
picture, a sidecar or the master. Report first; --apply to delete.

    python tools/svr_capture_nx_previews.py            # how many, which trips
    python tools/svr_capture_nx_previews.py --apply    # drop them
"""

import argparse
import collections
import csv
import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "speciesviewer"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--master", help="master folder (default: the active master)")
    ap.add_argument("--apply", action="store_true", help="delete the cached previews")
    a = ap.parse_args()

    from utils import preview_cache
    from gui.preview_extract import _rewritten_by_editor
    from utils.raw_extensions import is_raw
    master = a.master
    if not master:
        from utils.config import _config
        master = (_config.get("master", "masterbiotadatabase", fallback="")
                  or "").strip().strip("\"'")
    csv_path = Path(master) / "metadata" / "active" / "data_files.csv"
    if not csv_path.is_file():
        sys.exit(f"No master file list at {csv_path}")

    raws, trips = [], collections.Counter()
    with open(csv_path, encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            if (_rewritten_by_editor(row.get("EXIF:Software", ""))
                    and is_raw(row.get("WindowsFileName", ""))):
                p = Path(row.get("WindowsFileName", ""))
                raws.append(p)
                trips[p.name.split(" dsc")[0].split(" DSC")[0].split(" p")[0]] += 1

    cached = []
    for p in raws:
        d = preview_cache.cache_dir_for(p.parent)
        hits = [d / (p.stem + ".jpg")]
        hits += [Path(x) for x in glob.glob(str(d / (glob.escape(p.stem) + "@*.jpg")))]
        cached += [h for h in hits if h.is_file()]

    print(f"{len(raws)} RAW files in the master were saved by Capture NX; "
          f"{len(cached)} cached preview file(s) of them on this computer.")
    for name, n in trips.most_common(12):
        print(f"  {n:5}  {name}")
    if not a.apply:
        print("Nothing deleted. Run again with --apply to drop those previews.")
        return
    gone = 0
    for c in cached:
        try:
            c.unlink()
            gone += 1
        except OSError as e:
            print(f"  could not delete {c}: {e}")
    print(f"Dropped {gone} cached preview(s); they are re-made from the RAW when shown.")


if __name__ == "__main__":
    main()
