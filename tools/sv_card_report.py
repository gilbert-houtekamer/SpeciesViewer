#!/usr/bin/env python3
"""Can this SD card be formatted?

Reads a card's own ingest record, matches every frame it holds against the
laptop, and says whether each one was kept or deliberately discarded. Copies
nothing and writes nothing — not to the card, not to the project.

    python tools/sv_card_report.py K:/ "D:/CurrentPictures/2026/2026 North Greece"
    python tools/sv_card_report.py K:/ "…/2026 North Greece" --list unaccounted

See docs/plans/empty_sdcard_plan.md.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "speciesviewer"))

from engine import card_empty                                    # noqa: E402
from utils.disk_space import human_bytes                         # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("card", help="the card to test, e.g. K:/")
    ap.add_argument("project", help="the trip's pictures folder (holding the day folders)")
    ap.add_argument("--list", dest="show", metavar="VERDICT",
                    help="also list the filenames with this verdict "
                         f"({', '.join(card_empty.VERDICT_ORDER)})")
    args = ap.parse_args()

    # A Windows console defaults to cp1252, where the tick and cross this report
    # ends on are un-encodable and the whole run dies on its last line.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    report = card_empty.classify_card(args.card, args.project)

    print()
    for line in card_empty.format_report(report):
        print(line)

    full = report.get("fullness") or {}
    if full:
        print(f"\nCard: {human_bytes(full['total'])}, "
              f"{human_bytes(full['free'])} free ({full['pct_used']:.1f}% full)")

    if args.show:
        names = [r.get("laptop_name") or r["name"]
                 for r in report["files"] if r["verdict"] == args.show]
        print(f"\n{len(names)} {args.show}:")
        for n in names:
            print(f"  {n}")

    print()
    if card_empty.can_empty(report):
        print("✅ Every frame on this card has a verdict — it can be emptied.")
        warn = card_empty.fullness_warning(report)
        if warn:
            print(f"⚠  {warn}")
    else:
        print("❌ NOT safe to empty:")
        for b in report["blockers"]:
            print(f"   - {b}")
    return 0 if card_empty.can_empty(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
