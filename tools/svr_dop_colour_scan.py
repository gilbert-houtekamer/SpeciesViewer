"""Report what is inside the .dop files in a culling folder — READ-ONLY.

Why it exists: with XMP writing off, the colour/rating channel is the .dop
(PhotoLab's own recipe, which is the right channel when a .dop exists). But
`sidecarlib.ensure_dop` also CREATES a .dop from scratch when there is none, and
a from-scratch .dop is known not to work: PhotoLab owns the master UUID and
treats an unknown one as a virtual copy, and an empty `Base` with schema "19.5"
was implicated in a crash. So a day of culling can leave behind a pile of
SpeciesViewer-authored .dop files that hold real colour work in a form PhotoLab
will misread.

Before deleting those, you want to know two things:
  1. what colour each one is carrying, and whether the .xmp already has it;
  2. what you would LOSE by deleting them and re-applying one colour to all.

This tool answers both and writes nothing. It also refuses to lump
PhotoLab-authored .dop files in with ours: a real recipe is not ours to delete,
and telling them apart is the whole safety property here.

    python tools/svr_dop_colour_scan.py "<folder>" [--include-trash]
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import sys
from pathlib import Path

# A .dop we authored: our Software stamp AND an empty Base (PhotoLab always
# writes a full ~300-key Base). Both conditions, so a PL recipe that merely
# passed through us is never mistaken for a virgin one.
_SOFTWARE = re.compile(r'Software\s*=\s*"([^"]*)"')
_COLOUR = re.compile(r'ColorLabel\s*=\s*"([^"]*)"')
_RATING = re.compile(r'\bRating\s*=\s*(-?\d+)')
_VERSION = re.compile(r'Version\s*=\s*"([^"]*)"')
_EMPTY_BASE = re.compile(r'Base\s*=\s*\{\s*\}', re.S)

_XMP_LABEL = re.compile(r'xmp:Label\s*=\s*"([^"]*)"|<xmp:Label>([^<]*)</xmp:Label>')
_XMP_RATING = re.compile(r'xmp:Rating\s*=\s*"([^"]*)"|<xmp:Rating>([^<]*)</xmp:Rating>')


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _first(m) -> str:
    if not m:
        return ""
    return next((g for g in m.groups() if g), "")


def scan_dop(path: Path) -> dict:
    text = _read(path)
    software = _first(_SOFTWARE.search(text))
    return {
        "path": path,
        "software": software,
        "colour": _first(_COLOUR.search(text)),
        "rating": _first(_RATING.search(text)),
        "version": _first(_VERSION.search(text)),
        "empty_base": bool(_EMPTY_BASE.search(text)),
        "ours": software == "SpeciesViewer" and bool(_EMPTY_BASE.search(text)),
    }


def xmp_for(dop: Path) -> Path:
    """'<stem>.ORF.dop' → '<stem>.xmp' (the sidecar sits beside the RAW)."""
    name = dop.name
    if name.lower().endswith(".dop"):
        name = name[:-4]                       # drop .dop → '<stem>.ORF'
    return dop.with_name(Path(name).stem + ".xmp")


def scan(folder: Path, include_trash: bool) -> list[dict]:
    pattern = "**/*.dop" if include_trash else "*.dop"
    rows = []
    for dop in sorted(folder.glob(pattern)):
        if not include_trash and ".trash" in dop.parts:
            continue
        info = scan_dop(dop)
        x = xmp_for(dop)
        xtext = _read(x) if x.is_file() else ""
        info["xmp"] = x
        info["xmp_exists"] = x.is_file()
        info["xmp_label"] = _first(_XMP_LABEL.search(xtext)) if xtext else ""
        info["xmp_rating"] = _first(_XMP_RATING.search(xtext)) if xtext else ""
        info["in_trash"] = ".trash" in dop.parts
        rows.append(info)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="the day / culling folder to scan")
    ap.add_argument("--include-trash", action="store_true",
                    help="also scan .trash (already-deleted pictures)")
    ap.add_argument("--all", action="store_true", help="list every file, not just a sample")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Not a directory: {folder}")
        return 2

    rows = scan(folder, args.include_trash)
    if not rows:
        print(f"No .dop files under {folder}")
        return 0

    ours = [r for r in rows if r["ours"]]
    theirs = [r for r in rows if not r["ours"]]

    print(f"Folder: {folder}")
    print(f"  .dop files found            : {len(rows)}")
    print(f"  written by SpeciesViewer    : {len(ours)}   (empty Base — PhotoLab cannot use these)")
    print(f"  written by PhotoLab/other   : {len(theirs)}   ** NOT ours to delete **")
    if theirs:
        for r in theirs[:5]:
            print(f"      {r['path'].name}   software={r['software']!r} version={r['version']!r}")
    print()

    colours = collections.Counter(r["colour"] or "(none)" for r in ours)
    print("  colours held in OUR .dop files:")
    for name, n in colours.most_common():
        print(f"      {name:10} {n}")
    print()

    # The decision this report exists for.
    missing = [r for r in ours if r["colour"] and not r["xmp_label"]]
    already = [r for r in ours if r["colour"] and r["xmp_label"]]
    conflict = [r for r in ours if r["colour"] and r["xmp_label"]
                and r["xmp_label"].lower() != r["colour"].lower()]
    no_colour = [r for r in ours if not r["colour"]]
    no_xmp = [r for r in ours if not r["xmp_exists"]]

    print("  what the .xmp sidecars say:")
    print(f"      colour only in the .dop   : {len(missing)}   <- would be LOST by deleting the .dop")
    print(f"      colour already in the .xmp: {len(already)}")
    print(f"      the two DISAGREE          : {len(conflict)}")
    print(f"      .dop carries no colour    : {len(no_colour)}")
    print(f"      no .xmp beside the RAW    : {len(no_xmp)}")
    print()

    if colours and len(colours) > 1:
        top, topn = colours.most_common(1)[0]
        others = sum(n for c, n in colours.items() if c != top)
        print(f"  ⚠ NOT all one colour: {topn} are {top}, but {others} are not.")
        print("    Deleting the .dop files and re-applying one colour to everything")
        print("    would silently flatten these:")
        for r in ours:
            if (r["colour"] or "(none)") != top:
                print(f"      {r['path'].name}   {r['colour'] or '(none)'}")
        print()

    sample = ours if args.all else ours[:12]
    print(f"  detail ({'all' if args.all else f'first {len(sample)} of {len(ours)}'}):")
    print(f"      {'file':44} {'.dop':8} {'.xmp':8} rating")
    for r in sample:
        print(f"      {r['path'].name[:44]:44} {(r['colour'] or '-'):8} "
              f"{(r['xmp_label'] or '-'):8} {r['rating'] or '-'}")
    print()
    print("READ-ONLY: nothing was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
