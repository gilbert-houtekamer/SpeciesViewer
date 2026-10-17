#!/usr/bin/env python3
"""Dutch names from Wikidata for pictured species that have none — dry run, review, apply.

ONE JOB: fill the EMPTY Dutch name of species the master has pictures of, from
the Dutch label of the Wikidata item with that scientific name (property P225;
Wikidata is CC0). Birds have their names from IOC (svr_names_from_ioc.py); this
is for the rest, where many species simply have no Dutch name. A name that is
there is never replaced: Wikidata is less authoritative than IOC.

    python tools/svr_names_from_wikidata.py --master E:/Dropbox/naturedb --out D:/code/taxonomy_main
        dry run: writes <out>/wikidata_dutch_review.csv, changes nothing
    edit that file: set Action to "skip" for a wrong name, or correct the Dutch column
    python tools/svr_names_from_wikidata.py --master E:/Dropbox/naturedb --out D:/code/taxonomy_main --apply
        writes the rows with Action "fill" from the REVIEWED file (Dutch, and an
        English name where the list has none) into
        speciescollection.csv and species.csv (backups first). Close SpeciesViewer first.

The dry run marks a label "check" when it looks English (shares words with the
English name), looks like a scientific name (starts with the genus), or when
Wikidata has several. A label that only repeats the scientific name is left out.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))
from utils.atomic_replace import replace_with_retry  # noqa: E402

SPARQL = "https://query.wikidata.org/sparql"
AGENT = "SpeciesViewer/0.4 (species name lookup; https://speciesviewer.com)"
REVIEW = "wikidata_dutch_review.csv"
FIELDS = ["Action", "ScientificName", "Group", "Pictures", "EnglishName", "Dutch", "Note"]


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh, delimiter=";")
        return rd.fieldnames, list(rd)


def write_csv(path, header, rows, backup_dir, stamp):
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_dir / f"pre-wikidata-dutch-{stamp}_{path.name}")
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    replace_with_retry(tmp, path)


def pictured_without_dutch(active):
    """[(scientific name, group, pictures, English name)] — pictured, no Dutch name, not a bird."""
    _h, coll = read_csv(active / "speciescollection.csv")
    by = {r["ScientificName"].strip().lower(): r for r in coll}

    def group(r):
        for _ in range(30):
            if r is None:
                return ""
            if r["Level"] == "Level.CLASS":
                return r["ScientificName"].strip().lower()
            r = by.get(r["Up"].strip().lower())
        return ""

    out = []
    for r in read_csv(active / "species.csv")[1]:
        if r["Level"] != "Level.SPECIES" or int(r.get("iPictureCollectionCount") or 0) <= 0:
            continue
        c = by.get(r["ScientificName"].strip().lower())
        if c is None or c["DutchName"].strip() or group(c) == "aves":
            continue
        out.append((c["ScientificName"].strip().lower(), group(c), int(r["iPictureCollectionCount"]),
                    c["EnglishName"].strip()))
    return out


def wikidata_labels(names):
    """{scientific name lower: {Dutch labels}} — one SPARQL query per 100 names."""
    found = {}
    for i in range(0, len(names), 100):
        values = " ".join(f'"{n[0].upper() + n[1:]}"' for n in names[i:i + 100])
        q = (f"SELECT ?name ?nl WHERE {{ VALUES ?name {{ {values} }} ?t wdt:P225 ?name . "
             f'?t rdfs:label ?nl FILTER(lang(?nl)="nl") }}')
        req = urllib.request.Request(f"{SPARQL}?format=json&query={urllib.parse.quote(q)}",
                                     headers={"User-Agent": AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        for b in data["results"]["bindings"]:
            found.setdefault(b["name"]["value"].lower(), set()).add(b["nl"]["value"].strip())
        time.sleep(1)                        # be polite to the query service
    return found


def judge(sci, english, labels):
    """(action, Dutch name, note) for one species."""
    real = sorted({x.lower() for x in labels if x.lower() != sci})
    if not real:
        return None
    name = real[0]
    notes = []
    if len(real) > 1:
        notes.append(f"Wikidata has {len(real)}: {', '.join(real)}")
    eng = set(english.lower().replace("-", " ").split())
    if eng and len(eng & set(name.replace("-", " ").split())) >= 1 and name != english.lower():
        notes.append("shares words with the English name — Dutch?")
    if name == english.lower():
        notes.append("same as the English name")
    if name.split()[0] == sci.split()[0] or name.split()[0][:5] == sci.split()[0][:5]:
        notes.append("starts like the scientific name")
    return ("check" if notes else "fill"), name, "; ".join(notes)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--master", required=True)
    ap.add_argument("--out", default=".", help=f"folder for {REVIEW}")
    ap.add_argument("--apply", action="store_true",
                    help='write the rows marked "fill" in the reviewed file (close SpeciesViewer first)')
    a = ap.parse_args()
    active = Path(a.master) / "metadata" / "active"
    review_path = Path(a.out) / REVIEW

    if not a.apply:
        todo = pictured_without_dutch(active)
        labels = wikidata_labels([t[0] for t in todo])
        rows = []
        for sci, grp, pics, eng in todo:
            j = judge(sci, eng, labels.get(sci, set()))
            if j:
                rows.append({"Action": j[0], "ScientificName": sci, "Group": grp, "Pictures": pics,
                             "EnglishName": eng, "Dutch": j[1], "Note": j[2]})
        rows.sort(key=lambda r: (r["Action"], -r["Pictures"]))
        with open(review_path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, delimiter=";")
            w.writeheader()
            w.writerows(rows)
        n = {k: sum(r["Action"] == k for r in rows) for k in ("fill", "check")}
        print(f"{len(todo)} pictured species without a Dutch name; Wikidata names {len(rows)} "
              f"({n['fill']} fill, {n['check']} check).")
        print(f"Review: {review_path}")
        print('Set Action to "fill" or "skip" (a "check" row is not written), then run again with --apply.')
        return 0

    with open(review_path, encoding="utf-8-sig", newline="") as fh:
        text = fh.read()
    # Excel may save it back tab-separated
    first = text.splitlines()[0] if text else ""
    sep = "\t" if first.count("\t") > first.count(";") else ";"
    reviewed = [r for r in csv.DictReader(text.splitlines(), delimiter=sep)
                if r["Action"].strip().lower() == "fill"]
    fill = {r["ScientificName"].strip().lower(): r["Dutch"].strip().lower()
            for r in reviewed if r["Dutch"].strip()}
    # an English name the reviewer typed is filled the same way: only where none
    fill_en = {r["ScientificName"].strip().lower(): r["EnglishName"].strip().lower()
               for r in reviewed if r["EnglishName"].strip()}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for name in ("speciescollection.csv", "species.csv"):
        path = active / name
        header, rows = read_csv(path)
        n = n_en = 0
        for r in rows:
            sci = r["ScientificName"].strip().lower()
            if r["Level"] != "Level.SPECIES":
                continue
            if sci in fill and not r["DutchName"].strip():
                r["DutchName"] = fill[sci]
                n += 1
            if sci in fill_en and not r["EnglishName"].strip():
                r["EnglishName"] = fill_en[sci]
                n_en += 1
        write_csv(path, header, rows, active / "backup", stamp)
        print(f"  {name}: {n} Dutch and {n_en} English names filled")
    print(f"  backups: {active / 'backup'}\\pre-wikidata-dutch-{stamp}_*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
