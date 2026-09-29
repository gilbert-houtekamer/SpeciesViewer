#!/usr/bin/env python3
"""Push colour/rating from the trip CSVs back into the sidecars that lost them.

The companion to svr_label_audit.py. The audit finds pictures whose
`data_files.csv` row records a colour that the `.xmp` on disk does not have; this
writes that colour back into the sidecar, so the two records agree again and the
value survives the next Read & Summarize (which reads the FILE, not the CSV).

FILL-ONLY, and that is the whole safety argument. A sidecar that already holds a
colour is never touched, even when the CSV disagrees — a disagreement in that
direction means somebody (PhotoLab, Lightroom, you) set it on the file, and the
file is the record the other tools read. Those cases are reported, never written.

Report-only by default; `--apply` writes. Re-run the audit afterwards: it should
come back clean.

  python tools/svr_label_repair.py <trip-metadata-dir> [day] [--apply]
  e.g. python tools/svr_label_repair.py E:/Dropbox/trips/brazil2026db/metadata --apply
"""
import csv
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))

LABEL_RE = re.compile(r'<xmp:Label>([^<]*)</xmp:Label>|xmp:Label="([^"]*)"')
RATING_RE = re.compile(r'<xmp:Rating>([^<]*)</xmp:Rating>|xmp:Rating="([^"]*)"')


def _from_file(path, rx):
    """The tag's value in the sidecar: "" when absent, None when unreadable."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            m = rx.search(fh.read())
    except OSError:
        return None
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip()


def plan_day(metadir, day, csv_wins=False, picture_root=None):
    """[(upn, xmp_path, label, rating)] to write, plus [(upn, csv, disk)] conflicts."""
    f = os.path.join(metadir, "review", day, "data_files.csv")
    if not os.path.isfile(f):
        return [], []
    with open(f, encoding="utf-8", errors="replace") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    todo, conflicts = [], []
    for r in rows:
        if (r.get("WindowsExtension") or "").lower() != "xmp":
            continue
        path = r.get("WindowsFileName") or ""
        if picture_root and path.startswith("/"):
            # Relative, as sv2 stores it — see svr_label_audit._picture_root.
            from engine.handling import resolve_path
            path = resolve_path(path, picture_root)
        if not path or not os.path.isfile(path):
            continue
        csv_lab = (r.get("XMP:Label") or "").strip()
        csv_rat = (r.get("XMP:Rating") or "").strip()
        if not csv_lab and csv_rat in ("", "0"):
            continue                       # nothing the CSV claims
        disk_lab = _from_file(path, LABEL_RE)
        if disk_lab is None:
            continue                       # unreadable — leave it alone
        if disk_lab and disk_lab != csv_lab:
            # Default: the FILE wins and we only report. --csv-wins says the CSV
            # holds the decision and the file holds a stale default — true for the
            # 2026-09-04 batch, where the sidecars carried the pipeline's Green
            # while the CSV carried his actual Blue/Yellow cull marks. Only ever
            # on an explicit say-so, because this is the overwriting direction.
            conflicts.append((r.get("UniquePictureName", ""), csv_lab, disk_lab))
            if not csv_wins:
                continue
        if disk_lab == csv_lab:
            continue                       # already agree
        disk_rat = _from_file(path, RATING_RE) or ""
        want_rat = csv_rat if (csv_rat and csv_rat != "0" and not disk_rat) else None
        todo.append((r.get("UniquePictureName", ""), path, csv_lab, want_rat))
    return todo, conflicts


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    apply_ = "--apply" in argv
    csv_wins = "--csv-wins" in argv
    if not args:
        print(__doc__)
        return 2
    metadir = args[0]
    days = [args[1]] if len(args) > 1 else sorted(
        d for d in os.listdir(os.path.join(metadir, "review"))
        if os.path.isdir(os.path.join(metadir, "review", d)))

    from svr_label_audit import _picture_root
    picture_root = _picture_root(metadir)
    all_todo, all_conflicts = [], []
    for day in days:
        todo, conflicts = plan_day(metadir, day, csv_wins, picture_root)
        if todo or conflicts:
            print(f"{day}: {len(todo)} sidecar(s) to fill, {len(conflicts)} conflict(s)")
        all_todo += todo
        all_conflicts += conflicts

    verb = "OVERWRITING disk" if csv_wins else "left alone"
    for upn, c, d in all_conflicts:
        print(f"  CONFLICT ({verb}) {upn:34} CSV={c!r} disk={d!r}")
    for upn, path, lab, rat in all_todo[:20]:
        extra = f" rating={rat}" if rat else ""
        print(f"  fill {upn:34} label={lab}{extra}")
    if len(all_todo) > 20:
        print(f"  ... and {len(all_todo) - 20} more")

    print(f"\n{len(all_todo)} sidecar(s) would be filled, "
          f"{len(all_conflicts)} conflict(s) {verb}.")
    if not apply_:
        print("DRY RUN — nothing written. Re-run with --apply.")
        return 0
    if not all_todo:
        return 0

    from engine.exif_writer import exiftool_write
    ok = fail = 0
    for upn, path, lab, rat in all_todo:
        tags = [f"-XMP:Label={lab}"] + ([f"-XMP:Rating={rat}"] if rat else [])
        try:
            exiftool_write(path, tags)
        except Exception as e:                      # noqa: BLE001 — report, continue
            print(f"  FAILED {upn}: {e}")
            fail += 1
            continue
        # Verify from the FILE, not from exiftool's say-so: this whole repair
        # exists because a write that reported success did not land.
        if _from_file(path, LABEL_RE) == lab:
            ok += 1
        else:
            print(f"  NOT LANDED {upn}: wrote {lab!r}, file still says "
                  f"{_from_file(path, LABEL_RE)!r}")
            fail += 1
    print(f"\nfilled {ok}, failed {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
