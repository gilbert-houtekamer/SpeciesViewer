"""Move the master's leftover work folders aside — dry run by default
(backlog 1.23 step 3). See engine/leftover_cleanup.py for what counts.

  python tools/svr_clean_leftovers.py             # dry run: what would move
  python tools/svr_clean_leftovers.py --apply     # move them to admin/.trash/<stamp>/
  python tools/svr_clean_leftovers.py --restore   # put the newest batch back
  python tools/svr_clean_leftovers.py --empty     # delete admin/.trash for good
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))


def _mb(n: int) -> str:
    return f"{n / 1e6:,.0f} MB"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--master", help="master folder (default: the configured one)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="move them to admin/.trash")
    g.add_argument("--restore", action="store_true", help="put the newest batch back")
    g.add_argument("--empty", action="store_true", help="delete admin/.trash for good")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    from engine import leftover_cleanup as lc
    master = a.master
    if not master:
        from utils.config import _config
        master = _config.get("master", "masterbiotadatabase", fallback="")
    master = Path(master.strip().strip('"'))
    if not master.is_dir():
        print(f"Master folder not found: {master}")
        return 1

    if a.restore:
        b = lc.batches(master)
        if not b:
            print("Nothing to restore.")
            return 0
        n, problems = lc.restore(master, b[-1])
        print(f"Put back {n} folder(s) from {b[-1].name}.")
        for p in problems:
            print(f"  {p}")
        return 0 if not problems else 2

    if a.empty:
        b = lc.batches(master)
        if not b:
            print("The leftovers trash is empty.")
            return 0
        print(f"Deleting {lc.trash_root(master)} for good ({len(b)} batch(es)) …")
        print(f"Freed {_mb(lc.empty(master))}.")
        return 0

    items, kept = lc.plan(master)
    print(f"Master: {master}\n")
    for it in items:
        rel = it["path"].relative_to(master).as_posix()
        print(f"{_mb(it['bytes']):>10}  {it['files']:>6} files  {it['kind']:<12} {rel}")
    total = sum(it["bytes"] for it in items)
    print(f"\n{len(items)} folder(s), {_mb(total)}.")
    for k in kept:
        print(f"  kept: {k}")
    if not items:
        return 0
    if not a.apply:
        print("\nDry run — nothing moved. Add --apply to move them to admin/.trash.")
        return 0
    dest, n, size = lc.apply(master, items)
    print(f"\nMoved {n} of {len(items)} folder(s), {_mb(size)}, to {dest}.")
    print("Put back with --restore; delete for good with --empty.")
    return 0 if n == len(items) else 2


if __name__ == "__main__":
    sys.exit(main())
