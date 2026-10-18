#!/usr/bin/env python3
"""The starter species list for new masters — made from a converted master.

ONE JOB: write speciesviewer/dist/installation files/initial_speciescollection.csv
and speciesnames.csv (the alias table) from a master's active lists, so a new
master starts with the same species order, names and aliases (ch.6).

Left out, because they belong to that master and not to a new one:
  - topic rows (Level.TOPICS / TOPIC / TOPICLEAF, e.g. landscape 2025) — the
    app adds them when a picture needs one;
  - placeholder species ("? phacelia") and the aliases pointing at them.
Repaired: the garbled apostrophe 'â€™' (UTF-8 read as Windows-1252) becomes "'".

    python tools/svr_make_starter_list.py --master E:/Dropbox/naturedb
        dry run: prints what would be written
    ... --apply
        writes both files into the installation folder (read-only on the master)

INVARIANT: every row's parent ('Up') is in the list and every alias points at
a species in the list; the tool refuses to write otherwise.
"""
from __future__ import annotations

import argparse
import ast
import csv
import sys
from pathlib import Path

INSTALL = Path(__file__).resolve().parent.parent / "speciesviewer" / "dist" / "installation files"
TOPIC_LEVELS = {"Level.TOPICS", "Level.TOPIC", "Level.TOPICLEAF"}
GARBLED = ("â€", "Ã", "�")


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    return rows[0], rows[1:]


def starter_rows(header, body):
    """The master's list without topic rows and placeholder species."""
    col = {name: i for i, name in enumerate(header)}
    dropped = {r[col["ScientificName"]] for r in body
               if r[col["Level"]] in TOPIC_LEVELS or r[col["ScientificName"]].startswith("?")}
    kept = []
    for r in body:
        if r[col["ScientificName"]] in dropped:
            continue
        leaves = ast.literal_eval(r[col["Leaves"]]) if r[col["Leaves"]] else []
        if any(leaf in dropped for leaf in leaves):
            r = list(r)
            r[col["Leaves"]] = str([leaf for leaf in leaves if leaf not in dropped])
        kept.append([field.replace("â€™", "'") for field in r])
    return kept, dropped


def check(header, body, aliases):
    col = {name: i for i, name in enumerate(header)}
    names = {r[col["ScientificName"]] for r in body}
    problems = [f"parent missing: {r[col['ScientificName']]} → {r[col['Up']]}"
                for r in body if r[col["Up"]] and r[col["Up"]] not in names]
    problems += [f"alias points nowhere: {a[0]} → {a[2]}" for a in aliases if a[2] not in names]
    problems += [f"garbled: {r[:4]}" for r in body + aliases
                 if any(bad in ";".join(r) for bad in GARBLED)]
    problems += [f"row with {len(r)} fields: {r[:2]}" for r in body if len(r) != len(header)]
    return problems


def write_rows(path, header, body):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\r\n")
        w.writerow(header)
        w.writerows(body)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--master", required=True, type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    active = args.master / "metadata" / "active"
    header, body = read_rows(active / "speciescollection.csv")
    if "Sequence" not in header:
        sys.exit("This master's species list is not converted (no Sequence column).")
    alias_header, aliases = read_rows(active / "speciesnames.csv")

    kept, dropped = starter_rows(header, body)
    repaired = sum("â€™" in ";".join(r) for r in body)
    aliases_kept = [a for a in aliases if a[2] not in dropped]
    problems = check(header, kept, aliases_kept)

    species = sum(r[0] == "Level.SPECIES" for r in kept)
    print(f"species list: {len(kept)} rows ({species} species); left out: {sorted(dropped)}")
    print(f"aliases: {len(aliases_kept)} of {len(aliases)}; garbled apostrophes repaired: {repaired}")
    if problems:
        print("REFUSED:\n  " + "\n  ".join(problems[:20]))
        return 1
    if not args.apply:
        print("dry run — nothing written (add --apply)")
        return 0
    write_rows(INSTALL / "initial_speciescollection.csv", header, kept)
    write_rows(INSTALL / "speciesnames.csv", alias_header, aliases_kept)
    print(f"written to {INSTALL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
