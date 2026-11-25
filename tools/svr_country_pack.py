"""Country packs — carry a country's saved species lists (eBird's birds,
iNaturalist's other groups) from one computer to another.

ONE JOB: command-line front for engine.country_pack (the GUI comes later).

  python tools/svr_country_pack.py fetch MG mammals reptiles dragonflies
        (fetch / refresh iNaturalist's lists for those groups, and eBird's birds)
  python tools/svr_country_pack.py export MG --to "\\\\SMALLBOY\\transfer"
  python tools/svr_country_pack.py import "\\\\SMALLBOY\\transfer\\species_lists_MG.zip"
  python tools/svr_country_pack.py list        (what is saved on this computer)

Import never replaces a list that was fetched later here; it deletes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "speciesviewer"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("region")
    f.add_argument("groups", nargs="*", help="default: the groups set for that country")
    e = sub.add_parser("export")
    e.add_argument("region")
    e.add_argument("--to", default=".")
    i = sub.add_parser("import")
    i.add_argument("zip")
    sub.add_parser("list")
    a = ap.parse_args(argv)
    from engine import country_pack, ebird, inat_lists

    if a.cmd == "fetch":
        region = a.region.upper()
        groups = a.groups or inat_lists.chosen_groups(region)
        bad = [g for g in groups if g not in inat_lists.GROUPS]
        if bad:
            print(f"Unknown group(s): {', '.join(bad)}. Groups: {', '.join(inat_lists.GROUPS)}")
            return 1
        birds = ebird.species(region)
        print(f"birds (eBird): {len(birds) if birds else 'no list (eBird key set?)'}")
        for g in groups:
            got = inat_lists.fetch(region, g, progress=lambda s: print("  " + s, flush=True))
            n = inat_lists.species(region, g)
            print(f"{inat_lists.GROUPS[g][0]}: "
                  f"{'no list' if got is None else f'{len(got)} species, {len(n)} seen 3+ times'}")
    elif a.cmd == "export":
        out, names = country_pack.export(a.region, Path(a.to))
        print(f"{out}\n  " + "\n  ".join(names))
    elif a.cmd == "import":
        res = country_pack.unpack(Path(a.zip))
        print(f"taken: {len(res['taken'])}\n  " + "\n  ".join(res["taken"]))
        if res["kept"]:
            print(f"kept (fetched later here): {len(res['kept'])}\n  " + "\n  ".join(res["kept"]))
    else:
        for p in sorted(inat_lists.folder().glob("inat_*_*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            print(f"{p.stem[5:]:24s} {len(d.get('species', {})):6d} species  "
                  f"fetched {datetime.fromtimestamp(d.get('fetched', 0)):%Y-%m-%d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
