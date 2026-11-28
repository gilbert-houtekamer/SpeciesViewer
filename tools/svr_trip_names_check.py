"""Do your trips' and albums' species names still mean something to the MASTER?
REPORT ONLY: reads species lists and alias tables, writes nothing.

    python tools/svr_trip_names_check.py [--all] [--csv OUT]

A trip keeps the species list it was made with (decided 2026-11-28: no refresh;
an alias is the answer). That only works when every name a trip uses is known to
the master — as a species, a Dutch or English name, or an alias in
speciesnames.csv. This lists the names that are not, so you can choose, one by
one, which ones to add as an alias.

By default only the names ON a trip's PICTURES are looked at (each day's
metadata/review/<day>/data_pictures.csv: WetSoortNaam, NLSoortNaam, EngSoortNaam).
--all also looks at the whole species list each trip and album was copied from.

Each name gets one verdict:
  missing     the master does not know this name; the trip's species does exist
              in the master → a candidate alias "name → master species"
  above       the master files this name one or more levels HIGHER: the family
              (a family with one species, "secretarybird" → sagittariidae), or
              the species of a trip's subspecies → no alias; the master's own
              index decides, look at it when it matters
  other       the master knows the name, but as ANOTHER species (a split, a
              rename that moved the name) → never an alias; look at it by hand
  contested   the master's alias table says a split gave this name away; it is
              used for neither species on purpose → nothing to do
  no species  the trip's scientific name is neither a species nor an alias in
              the master → the species itself needs an alias (or a decision)
Names that resolve to the right species are not listed.

ONE JOB: report. INVARIANT: nothing in a trip, an album or the master is
written; the only file it can write is the --csv you name.
"""

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

SV = Path(__file__).resolve().parents[1] / "speciesviewer"
sys.path.insert(0, str(SV))

from utils.config import _config, get_trips_root, get_virttrips_root  # noqa: E402
from engine.speciesclassification import SpeciesClassification  # noqa: E402

LIST = Path("metadata") / "active" / "speciescollection.csv"
DAYS = Path("metadata") / "review"
PICTURE_COLUMNS = {"ScientificName": "WetSoortNaam", "DutchName": "NLSoortNaam",
                   "EnglishName": "EngSoortNaam"}
SPECIES_LEVELS = {"Level.SPECIES", "Level.SUBSPECIES"}
COLUMNS = (("ScientificName", "sci"), ("DutchName", "nl"), ("EnglishName", "en"))


def _rows(path: Path):
    try:
        with path.open(encoding="utf-8", newline="") as f:
            yield from csv.DictReader(f, delimiter=";")
    except (OSError, UnicodeDecodeError) as e:
        print(f"  ! not readable: {path} ({e})")


def _repos():
    """(kind, name, folder) of every trip and album with a species list."""
    for kind, root in (("trip", get_trips_root()), ("album", get_virttrips_root())):
        if not root or not Path(root).is_dir():
            continue
        for d in sorted(Path(root).iterdir()):
            if (d / LIST).is_file():
                yield kind, d.name, d


def _used_rows(d: Path):
    """The species names on the trip's pictures, as species-list rows, with the
    number of pictures each combination is on."""
    counts = defaultdict(int)
    days = sorted((d / DAYS).glob("*/data_pictures.csv")) if (d / DAYS).is_dir() else []
    for f in days + [d / "metadata" / "active" / "data_pictures.csv"]:
        if not f.is_file():
            continue
        for r in _rows(f):
            key = tuple((r.get(c) or "").strip() for c in PICTURE_COLUMNS.values())
            if key[0]:
                counts[key] += 1
    return [(dict(zip(PICTURE_COLUMNS, k), Level="Level.SPECIES"), n)
            for k, n in counts.items()]


def _master():
    root = _config.get("master", "masterbiotadatabase", fallback="").strip().strip('"')
    path = Path(root) / LIST
    if not root or not path.is_file():
        sys.exit(f"No master species list found ({path}).")
    sc = SpeciesClassification(None)
    sc.readSpeciesCollection(str(path), False)      # reads; also its alias table
    return path, sc


def _means(m, name: str) -> str:
    """What the master makes of `name`: a scientific name, "" = unknown."""
    if name in m.sc_dict:
        return name
    return (m.dutch_dict.get(name) or m.english_dict.get(name)
            or m.alias_dict.get(name) or "")


def _above(m, sci: str) -> set:
    """Every level above `sci` in the master (the species of a subspecies, its
    genus-less family, order, …)."""
    up, seen = set(), sci
    for _ in range(20):
        row = m.sc_dict.get(seen)
        nxt = ((row or {}).get("SubspeciesOf") or (row or {}).get("Up") or "").strip().lower()
        if not nxt or nxt in up:
            break
        up.add(nxt)
        if nxt != (row.get("Up") or "").strip().lower():
            up.add((row.get("Up") or "").strip().lower())
        seen = nxt
    up.discard("")
    return up


def check(m, rows):
    """Yield (verdict, language, name, trip species, master species) per problem."""
    for r in rows:
        if (r.get("Level") or "").strip() not in SPECIES_LEVELS:
            continue
        sci = (r.get("ScientificName") or "").strip().lower()
        if not sci:
            continue
        now = sci if sci in m.sc_dict else m.alias_dict.get(sci, "")
        if not now:
            yield "no species", "sci", sci, sci, ""
            continue
        for col, lang in COLUMNS:
            name = (r.get(col) or "").strip().lower()
            if not name:
                continue
            if name in m.contested:
                yield "contested", lang, name, sci, now
                continue
            got = _means(m, name)
            if got == now:
                continue
            verdict = ("missing" if not got else
                       "above" if got in _above(m, now) else "other")
            yield verdict, lang, name, sci, got or now


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all", action="store_true",
                    help="the whole copied list, not only the species with pictures")
    ap.add_argument("--csv", help="also write the findings to this CSV file")
    a = ap.parse_args(argv)

    mpath, m = _master()
    print(f"Master list: {mpath}  ({len(m.sc_dict)} entries, {len(m.alias_dict)} aliases, "
          f"{len(m.contested)} contested)\n")

    # (verdict, lang, name, trip sci, master sci) -> [where]
    found = defaultdict(list)
    for kind, name, d in _repos():
        used = _used_rows(d)
        n = 0
        for row, pics in used:
            for hit in check(m, [row]):
                found[hit].append(f"{name} ({pics} pic)")
                n += 1
        listed = 0
        if a.all:
            rows = list(_rows(d / LIST))
            listed = len(rows)
            for hit in check(m, rows):
                if f"{name} (list)" not in found[hit]:
                    found[hit].append(f"{name} (list)")
                    n += 1
        print(f"  {kind:5} {name:28} {len(used):5} names on pictures"
              + (f", {listed} in its list" if a.all else "") + f" — {n} to look at")

    order = {"missing": 0, "other": 1, "above": 2, "no species": 3, "contested": 4}
    hits = sorted(found.items(), key=lambda kv: (order[kv[0][0]], kv[0][2]))
    for verdict in order:
        group = [(k, w) for k, w in hits if k[0] == verdict]
        if not group:
            continue
        print(f"\n── {verdict} ({len(group)}) ──")
        for (_, lang, name, sci, master_sci), where in group:
            target = (f"→ {master_sci}" if verdict == "missing" else
                      f"master: {master_sci}, trip: {sci}" if verdict in ("other", "above") else
                      f"(trip: {sci})")
            print(f"  {lang:3} {name:40} {target}   [{', '.join(where)}]")

    if a.csv:
        with open(a.csv, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Verdict", "Language", "Name", "TripSpecies", "MasterSpecies", "Where"])
            for (verdict, lang, name, sci, master_sci), where in hits:
                w.writerow([verdict, lang, name, sci, master_sci, ", ".join(where)])
        print(f"\nWritten: {a.csv}")

    counts = defaultdict(int)
    for k, _ in hits:
        counts[k[0]] += 1
    print("\n" + ", ".join(f"{counts[v]} {v}" for v in order) + " — nothing was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
