"""Which Country values does the master hold, and does each one resolve to an ISO
country? READ-ONLY on the master: reads metadata/active/data_pictures.csv.

    python tools/svr_country_inventory.py [master_root]

Unknown values are listed first: add them to OURS in tools/svr_make_countries.py and
regenerate. Run this after a trip, so a new country or a new spelling from the place
lookup is seen. Exit 1 when anything is unknown.
"""

import csv
import sys
from collections import Counter
from pathlib import Path

SV = Path(__file__).resolve().parents[1] / "speciesviewer"
sys.path.insert(0, str(SV))

from engine.countries import inventory  # noqa: E402


def _master_root() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    from configparser import ConfigParser
    from utils import ini_io
    from utils.runtime_files import svconfig_path
    cfg = ConfigParser(interpolation=None)
    ini_io.read_ini(cfg, svconfig_path())
    return Path(cfg.get("master", "masterbiotadatabase"))


def main():
    src = _master_root() / "metadata" / "active" / "data_pictures.csv"
    with src.open(encoding="utf-8", newline="") as f:
        counts = Counter((r.get("Country") or "").strip() for r in csv.DictReader(f, delimiter=";"))
    rows = inventory(counts)
    print(f"{src}\n{'stored':32} {'pictures':>8}  iso  {'how':8} shown (EN)")
    for stored, n, iso, how, en, note in rows:
        print(f"{stored:32} {n:8}  {iso or '--':3}  {how:8} {en}"
              + (f"   [{note}]" if note else ""))
    unknown = sum(1 for r in rows if r[3] == "unknown")
    print(f"\n{len(rows)} spellings, {unknown} unknown, "
          f"{counts.get('', 0)} pictures without a country")
    return 1 if unknown else 0


if __name__ == "__main__":
    sys.exit(main())
