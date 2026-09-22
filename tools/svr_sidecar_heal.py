"""Find RAWs with no .xmp sidecar, and create the missing ones.

WHY THIS EXISTS. Ingest (sv0ReadSDCard) creates one sidecar per RAW as its LAST
phase, and that phase is skipped silently whenever the run is cancelled or errors
after the copy — the summary line then reads "…; 0 sidecar(s)." at INFO level,
which looks exactly like a clean run that had nothing to do. On 2026-09-21 that
happened to one day of the Brazil trip: 1066 RAWs arrived, 678 of them without a
sidecar, and nothing said so. A species named on such a picture had no file on
disk to be written to and was lost at the next Read & Summarize.

The commit path no longer depends on that (FilePicture.updateSubjects authors the
sidecar it needs), but a day full of sidecar-less RAWs is still wrong: the culling
viewer, the ratings, the crops and the tracklog geotag all read the sidecar.

READ-ONLY BY DEFAULT. It reports; `--apply` creates. Creation is the same exiftool
recipe ingest uses — engine.sidecarlib.sidecar_opts, one definition — so a sidecar
healed here is indistinguishable from one written at ingest. Nothing is ever
overwritten: a RAW that already has an .xmp is left alone, whatever is in it.

    python tools/svr_sidecar_heal.py "D:/CurrentPictures/2026/2026 Brazil"
    python tools/svr_sidecar_heal.py "D:/CurrentPictures/2026/2026 Brazil" --apply
    python tools/svr_sidecar_heal.py <dir> --day 2026-09-13 --apply
"""

import argparse
import os
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "speciesviewer"))

from engine.sidecarlib import ensure_xmps, existing_xmp   # noqa: E402
from utils.raw_extensions import RAW                      # noqa: E402

# Sidecars are created in batches so a long run reports progress rather than going
# quiet for minutes — the same reason ingest chunks its own pass.
BATCH = 200


def scan(root: Path, day: str = "") -> list:
    """Every (folder, raws, missing) under `root`, deepest folder first.

    A folder with no RAW is not reported at all: "0 of 0" is not a finding.
    """
    rows = []
    for folder, _dirs, files in os.walk(root):
        fpath = Path(folder)
        if day and fpath.name != day:
            continue
        raws = [fpath / f for f in files if Path(f).suffix.lower() in RAW]
        if not raws:
            continue
        missing = [r for r in raws if existing_xmp(r) is None]
        rows.append((fpath, raws, missing))
    return sorted(rows, key=lambda r: str(r[0]))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="trip folder, day folder, or any folder above them")
    ap.add_argument("--day", default="", help="only this day folder, e.g. 2026-09-13")
    ap.add_argument("--apply", action="store_true",
                    help="actually create the missing sidecars (default: report only)")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"Not a folder: {root}")
        return 2

    rows = scan(root, args.day)
    if not rows:
        print(f"No RAW files under {root}"
              + (f" for day {args.day}" if args.day else ""))
        return 0

    total_raw = sum(len(r[1]) for r in rows)
    total_missing = sum(len(r[2]) for r in rows)
    print(f"{root}")
    for folder, raws, missing in rows:
        # ASCII only: this prints to a Windows console, which is cp1252.
        mark = "!! " if missing else "   "
        print(f"{mark}{folder.name:<14} {len(raws):>6} raw  "
              f"{len(raws) - len(missing):>6} with sidecar  "
              f"{len(missing):>6} MISSING")
    print(f"\n{total_missing} of {total_raw} RAW(s) have no .xmp sidecar.")

    if not total_missing:
        return 0
    if not args.apply:
        print("\nReport only — nothing was written. Re-run with --apply to create them.")
        return 0

    t0 = time.time()
    created = failed = 0
    todo = [r for _f, _raws, missing in rows for r in missing]
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        got = ensure_xmps(chunk)
        created += len(got)
        failed += len(chunk) - len(got)
        done = min(i + BATCH, len(todo))
        print(f"  {done}/{len(todo)} … {created} created, {failed} failed "
              f"({time.time() - t0:.1f}s)")

    print(f"\nCreated {created} sidecar(s), {failed} failed, "
          f"in {time.time() - t0:.1f}s.")
    print("Next: Read & Summarize the day(s) so the new files enter the trip data.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
