"""Ch.6 — check the new taxonomy tables (taxa.csv + names.csv) for invariants.

Read-only. Takes the folder svr_taxonomy_build.py wrote, plus the master for
today's pictured species. Prints PASS / FAIL / WARN per check with the
offenders, writes check.md beside the tables, exits 1 when anything FAILs.

FAIL = the tables are wrong and must not be used.
WARN = allowed, but something to look at (a missing name, a tie in the order).

    python tools/svr_taxonomy_check.py --tables D:/code/taxonomy_trial \
        --master E:/Dropbox/naturedb
"""
import argparse
import ast
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

RANKS = ["kingdom", "phylum", "class", "order", "family", "species"]
PREFIX_RE = re.compile(r"^\d+\s+")
SHOW = 25          # offenders listed per check


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def as_list(cell):
    return ast.literal_eval(cell) if cell else []


class Report:
    def __init__(self):
        self.lines, self.failed = [], 0

    def check(self, title, offenders, level="FAIL"):
        verdict = "PASS" if not offenders else level
        if verdict == "FAIL":
            self.failed += 1
        head = f"{verdict:4}  {title}" + (f" — {len(offenders)}" if offenders else "")
        print(head)
        self.lines.append(f"- **{verdict}** {title}" + (f" — {len(offenders)}" if offenders else ""))
        for o in offenders[:SHOW]:
            print(f"        {o}")
            self.lines.append(f"    - {o}")
        if len(offenders) > SHOW:
            print(f"        … and {len(offenders) - SHOW} more")
            self.lines.append(f"    - … and {len(offenders) - SHOW} more")


def today_pictured(master, klass):
    """Today's species with pictures in one class, as {name: pictures}."""
    active = Path(master) / "metadata/active"
    sc = {r["ScientificName"]: r for r in read_csv(active / "speciescollection.csv")}

    def in_class(name):
        seen = set()
        while name in sc and name not in seen:
            seen.add(name)
            if sc[name]["Level"] == "Level.CLASS":
                return name == klass
            name = sc[name]["Up"]
        return False

    out = {}
    for r in read_csv(active / "species.csv"):
        if r["Level"] == "Level.SPECIES" and r["ScientificName"] in sc and in_class(r["ScientificName"]):
            n = int(r["iPictureCollectionCount"] or 0)
            if n:
                out[r["ScientificName"]] = n
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tables", required=True, help="folder with taxa.csv and names.csv")
    ap.add_argument("--master", required=True)
    ap.add_argument("--klass", default="aves", help="class the tables cover (default aves)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    folder = Path(a.tables)
    taxa = read_csv(folder / "taxa.csv")
    names = read_csv(folder / "names.csv")
    rep = Report()

    # ---- the tree
    by_name = defaultdict(list)
    for t in taxa:
        by_name[t["ScientificName"]].append(t)
    rep.check("every scientific name is one taxon (no duplicates)",
              [f"{n} ×{len(ts)} ({', '.join(t['Rank'] for t in ts)})"
               for n, ts in by_name.items() if len(ts) > 1])
    tax = {n: ts[0] for n, ts in by_name.items()}

    rep.check("no name carries a sort number (‘040 passeriformes’)",
              [n for n in tax if PREFIX_RE.match(n)])
    rep.check("every rank is a known one",
              [f"{n}: {t['Rank']}" for n, t in tax.items() if t["Rank"] not in RANKS])

    bad_parent, bad_order, no_kingdom = [], [], []
    for n, t in tax.items():
        p = t["Parent"]
        if t["Rank"] == "kingdom":
            if p:
                bad_parent.append(f"{n}: a kingdom with parent {p}")
            continue
        if p not in tax:
            bad_parent.append(f"{n} ({t['Rank']}): parent '{p}' is not in the table")
            continue
        if t["Rank"] in RANKS and tax[p]["Rank"] in RANKS and \
                RANKS.index(tax[p]["Rank"]) >= RANKS.index(t["Rank"]):
            bad_order.append(f"{n} ({t['Rank']}) under {p} ({tax[p]['Rank']})")
        seen, cur = set(), n
        while cur in tax and tax[cur]["Rank"] != "kingdom" and cur not in seen:
            seen.add(cur)
            cur = tax[cur]["Parent"]
        if cur not in tax or tax[cur]["Rank"] != "kingdom":
            no_kingdom.append(n)
    rep.check("every parent exists", bad_parent)
    rep.check("a parent is always a higher rank (a level may be skipped)", bad_order)
    rep.check("every taxon reaches a kingdom (no loops, no dead ends)", no_kingdom)
    rep.check("every taxon above species has children",
              [f"{n} ({t['Rank']})" for n, t in tax.items() if t["Rank"] != "species"
               and not any(c["Parent"] == n for c in tax.values())])

    # ---- the order
    bad_seq, ties, before_parent = [], [], []
    children = defaultdict(list)
    for n, t in tax.items():
        try:
            t["_seq"] = float(t["Sequence"])
        except ValueError:
            bad_seq.append(f"{n}: '{t['Sequence']}'")
            continue
        children[t["Parent"]].append(t)
    rep.check("every taxon has a numeric sort number", bad_seq)
    for p, kids in children.items():
        seqs = defaultdict(list)
        for k in kids:
            seqs[k["_seq"]].append(k["ScientificName"])
        ties += [f"under {p or '(top)'}: {', '.join(v)} share {s:g}" for s, v in seqs.items() if len(v) > 1]
        if p in tax and "_seq" in tax[p]:
            before_parent += [f"{k['ScientificName']} ({k['_seq']:g}) sorts before its parent {p} "
                              f"({tax[p]['_seq']:g})" for k in kids if k["_seq"] < tax[p]["_seq"]]
    rep.check("a parent sorts at its lowest child (no child sorts before it)", before_parent)
    rep.check("siblings have different sort numbers (a tie sorts arbitrarily)", ties, "WARN")
    rep.check("sort numbers by a guess, for review",
              [f"{n}: {t['SequenceSource']}" for n, t in tax.items()
               if t["Rank"] == "species" and int(t["Pictures"] or 0) > 0 and
               t["SequenceSource"] not in ("AviList", "AviList (today's name)")], "WARN")

    # ---- the aliases
    species = {n for n, t in tax.items() if t["Rank"] == "species"}
    sci = [r for r in names if r["Language"] == "sci"]
    rep.check("every alias points to a species in the table",
              [f"{r['Name']} → {r['PointsTo']}" for r in names if r["PointsTo"] not in species])
    rep.check("an alias is never itself a taxon (no species is also an old name)",
              [f"{r['Name']} → {r['PointsTo']}" for r in sci if r["Name"] in tax])
    rep.check("an alias never points to itself",
              [r["Name"] for r in names if r["Name"] == r["PointsTo"]])
    targets = defaultdict(set)
    for r in sci:
        targets[r["Name"]].add(r["PointsTo"])
    rep.check("an old scientific name points to one species only",
              [f"{n} → {', '.join(sorted(v))}" for n, v in targets.items() if len(v) > 1])

    # ---- common names: what the Dutch and English lookups would hold
    for lang, col in (("nl", "DutchNames"), ("en", "EnglishNames")):
        lookup = defaultdict(set)
        for n, t in tax.items():
            if t["Rank"] == "species":
                for name in as_list(t[col]):
                    lookup[name].add(n)
        for r in names:
            if r["Language"] == lang:
                lookup[r["Name"]].add(r["PointsTo"])
        rep.check(f"a {lang.upper()} name finds one species only",
                  [f"'{k}' → {', '.join(sorted(v))}" for k, v in lookup.items() if len(v) > 1], "WARN")
        rep.check(f"every species has a {lang.upper()} name to show",
                  [n for n in species if not as_list(tax[n][col])], "WARN")
    rep.check("every order and family has an English name to show",
              [f"{n} ({t['Rank']})" for n, t in tax.items() if t["Rank"] in ("order", "family")
               and not as_list(t["EnglishNames"])], "WARN")

    # ---- nothing of today's is lost
    pictured = today_pictured(a.master, a.klass)
    alias_of = {r["Name"]: r["PointsTo"] for r in sci}
    lost = [f"{n} ({p} pictures)" for n, p in sorted(pictured.items(), key=lambda x: -x[1])
            if n not in species and n not in alias_of]
    rep.check(f"every pictured species of today is found — as itself or via an alias "
              f"({len(pictured)} checked)", lost)
    total_today = sum(pictured.values())
    total_new = sum(int(tax[n]["Pictures"] or 0) for n in species)
    rep.check("picture count adds up (today = new table)",
              [] if total_today == total_new else [f"today {total_today}, new table {total_new}"])

    print(f"\n{rep.failed} check(s) failed")
    (folder / "check.md").write_text(
        "# Taxonomy table check\n\n" + "\n".join(rep.lines) + f"\n\n{rep.failed} check(s) failed\n",
        encoding="utf-8")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
