"""List — and on request remove — the old plain copies of the master's lists
(backlog 1.23 step 2). Dry run by default.

The rotation (engine/backup_copies.py) also runs by itself after the daily
backup at start-up; this tool is for seeing exactly what goes before it does,
and for the first big cleanup.

  python tools/svr_prune_backup_copies.py            # dry run: the list
  python tools/svr_prune_backup_copies.py --apply    # remove them
  python tools/svr_prune_backup_copies.py --keep 5   # keep more
  python tools/svr_prune_backup_copies.py --archive  # first: archive the 2026
                                                     # ghost-row evidence (verified)

Copies dated in the ghost-row window (June/July 2026) are never removed until
--archive has put a verified gzipped copy of them in admin/archive/.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--master", help="master folder (default: the configured one)")
    ap.add_argument("--keep", type=int, default=None, help="copies of each kind to keep")
    ap.add_argument("--apply", action="store_true", help="remove them (default: list only)")
    ap.add_argument("--archive", action="store_true",
                    help="gzip + verify the ghost-row evidence copies into admin/archive/")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    from engine import backup_copies as bc
    master = a.master
    if not master:
        from utils.config import _config
        master = _config.get("master", "masterbiotadatabase", fallback="")
    master = Path(master.strip().strip('"'))
    if not master.is_dir():
        print(f"Master folder not found: {master}")
        return 1
    keep = a.keep if a.keep is not None else bc.KEEP_COPIES
    if keep < 1:
        print("--keep must be at least 1.")
        return 1

    if a.archive:
        print(f"Archiving the ghost-row evidence into {bc.archive_dir(master)} …")
        n, size, problems = bc.archive_evidence(master)
        print(f"{n} archived and verified, {size / 1e6:,.0f} MB.")
        for p in problems:
            print(f"  PROBLEM: {p}")
        if problems:
            return 2
        print()

    waiting = [p for p in bc.evidence_copies(master)
               if not (bc.archive_dir(master) / (p.name + ".gz")).is_file()]
    if waiting:
        print(f"{len(waiting)} ghost-row evidence cop{'y' if len(waiting) == 1 else 'ies'} "
              f"not archived yet — protected. Run with --archive first.")
        print()

    items = bc.plan(master, keep)
    total = sum(it["bytes"] for it in items)
    print(f"Master: {master}   keeping the newest {keep} of each kind per folder\n")
    for it in items:
        rel = it["path"].relative_to(master).as_posix()
        print(f"{it['bytes'] / 1e6:>8,.0f} MB  {rel}")
    print(f"\n{len(items)} file(s), {total / 1e6:,.0f} MB would go."
          if not a.apply else "")
    if not items:
        print("Nothing to remove.")
        return 0
    if not a.apply:
        print("Dry run — nothing removed. Add --apply to remove them.")
        return 0
    n, freed = bc.apply(items)
    print(f"Removed {n} of {len(items)} file(s), {freed / 1e6:,.0f} MB.")
    return 0 if n == len(items) else 2


if __name__ == "__main__":
    sys.exit(main())
