"""Ch.6 step 2a — build the new bird taxonomy tables beside today's list.

Read-only on the master. Writes only into --out:
  taxa.csv      one row per taxon (kingdom -> species), GBIF name + placement,
                AviList sort number, Dutch/English names as lists (first = shown)
  names.csv     alias table: name -> scientific name it points to, and what kind
  report.md     what would change against today's list, pictured species first
  preview.txt   the bird tree in the new order
  gbif_cache.json  GBIF species-match answers, so a re-run makes no calls

Rules: docs/plans/taxonomy_order_design.md, "Step 2a".

    python tools/svr_taxonomy_build.py --master E:/Dropbox/naturedb \
        --avilist AviList-v2025-11Jun-short.xlsx --out <folder>
"""
import argparse
import csv
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

PREFIX_RE = re.compile(r"^\d+\s+")
RANKS = ["kingdom", "phylum", "class", "order", "family", "species"]
SAME_STATUS = {"ACCEPTED", "DOUBTFUL"}


def strip(name):
    return PREFIX_RE.sub("", name or "").strip()


def similar(a, b):
    """Same word give or take a Latin ending or a spelling (rueppelli/rueppellii)."""
    return SequenceMatcher(None, a, b).ratio() >= 0.8


def change_kind(old, new):
    """What GBIF did to one of today's species names."""
    o, n = old.split(), new.split()
    if old == new:
        return "same"
    if len(o) > 2 and o[:2] == n[:2]:
        return "subspecies → species"
    if similar(o[-1] if len(o) == 2 else o[1], n[-1]):
        return "spelling" if o[0] == n[0] else "renamed"
    return "merged"


def read_csv(path, delimiter=";"):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter=delimiter))


# ---------------------------------------------------------------- today's list
def load_today(master):
    active = Path(master) / "metadata/active"
    sc = {r["ScientificName"]: r for r in read_csv(active / "speciescollection.csv")}
    pics = {}
    for r in read_csv(active / "species.csv"):
        if r["Level"] == "Level.SPECIES":
            try:
                pics[r["ScientificName"]] = int(r["iPictureCollectionCount"] or 0)
            except ValueError:
                pics[r["ScientificName"]] = 0

    def chain(name):
        out, seen = {}, set()
        while name in sc and name not in seen:
            seen.add(name)
            out[sc[name]["Level"].split(".")[-1].lower()] = name
            name = sc[name]["Up"]
        return out

    birds = []
    for name, r in sc.items():
        if r["Level"] != "Level.SPECIES":
            continue
        ch = chain(name)
        if ch.get("class") != "aves":
            continue
        birds.append({
            "name": name,
            "nl": r["DutchName"], "en": r["EnglishName"],
            "order": strip(ch.get("order")), "family": strip(ch.get("family")),
            "pics": pics.get(name, 0),
        })
    # names of higher taxa, keyed by the name without its number
    higher = {}
    for name, r in sc.items():
        if r["Level"] in ("Level.KINGDOM", "Level.PHYLUM", "Level.CLASS",
                          "Level.ORDER", "Level.FAMILY"):
            higher.setdefault(strip(name), (r["DutchName"], r["EnglishName"]))
    return birds, higher


# ---------------------------------------------------------------- AviList
def load_avilist(path):
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True)
        rows = list(wb[wb.sheetnames[0]].iter_rows(values_only=True))
        hdr = rows[0]
        rows = [dict(zip(hdr, r)) for r in rows[1:]]
    else:
        rows = read_csv(path, delimiter=",")
    species, genus, family_en, by_epithet = {}, {}, {}, defaultdict(list)
    for r in rows:
        rank = (r.get("Taxon_rank") or "").lower()
        name = (r.get("Scientific_name") or "").lower().strip()
        seq = float(r["Sequence"])
        if rank == "species":
            rec = {"name": name, "seq": seq, "en": (r.get("English_name_AviList") or "").lower(),
                   "family": (r.get("Family") or "").lower(), "avibase": r.get("AvibaseID") or ""}
            species[name] = rec
            by_epithet[(name.split()[-1], rec["family"])].append(rec)
        elif rank == "genus":
            genus[name] = seq
        elif rank == "family" and r.get("Family_English_name"):
            family_en[name] = r["Family_English_name"].lower()
    return species, genus, family_en, by_epithet


# ---------------------------------------------------------------- GBIF
def gbif_match(names, cache_path):
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    todo = [n for n in names if n not in cache]

    def one(name):
        q = urllib.parse.urlencode({"name": name, "kingdom": "Animalia", "class": "Aves",
                                    "strict": "true"})
        for _ in range(3):
            try:
                with urllib.request.urlopen("https://api.gbif.org/v1/species/match?" + q,
                                            timeout=30) as resp:
                    return name, json.load(resp)
            except Exception:
                pass
        return name, None

    if todo:
        print(f"GBIF: {len(todo)} lookups ({len(names) - len(todo)} cached)…", flush=True)
        with ThreadPoolExecutor(8) as ex:
            for i, (name, d) in enumerate(ex.map(one, todo), 1):
                if d is not None:
                    cache[name] = d
                if i % 1000 == 0:
                    print(f"  {i}/{len(todo)}", flush=True)
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    return cache


def gbif_view(d):
    """GBIF's answer reduced to what we use; None when GBIF has no species for it."""
    if not d or d.get("matchType") == "NONE" or d.get("rank") not in ("SPECIES", "SUBSPECIES"):
        return None
    return {
        "species": (d.get("species") or "").lower(),
        "status": d.get("status"),
        "kingdom": (d.get("kingdom") or "").lower(), "phylum": (d.get("phylum") or "").lower(),
        "class": (d.get("class") or "").lower(), "order": (d.get("order") or "").lower(),
        "family": (d.get("family") or "").lower(), "genus": (d.get("genus") or "").lower(),
        "key": d.get("speciesKey") or d.get("usageKey"),
    }


# ---------------------------------------------------------------- build
def build(birds, higher, avi, gbif):
    av_species, av_genus, av_family_en, av_epithet = avi
    rows = {}          # new species name -> row
    names = []         # alias rows
    changes = []       # per old species: what happens to it
    def unchanged(b):
        g = gbif_view(gbif.get(b["name"]))
        return not g or g["species"] == b["name"]

    # a species GBIF leaves alone owns its row; one merging into it comes second
    for b in sorted(birds, key=lambda b: not unchanged(b)):
        g = gbif_view(gbif.get(b["name"]))
        if g and g["species"]:
            new = g["species"]
            kind = change_kind(b["name"], new)
            place = {k: g[k] or "" for k in ("kingdom", "phylum", "class", "order", "family")}
            for k, today in (("order", b["order"]), ("family", b["family"])):
                if not place[k]:
                    place[k] = today        # GBIF leaves it empty: today's value stays
            genus = g["genus"]
        else:
            new, kind = b["name"], "not in GBIF"
            place = {"kingdom": "animalia", "phylum": "chordata", "class": "aves",
                     "order": b["order"], "family": b["family"]}
            genus = b["name"].split()[0]

        # sort number
        if new in av_species:
            seq, src = av_species[new]["seq"], "AviList"
        elif b["name"] in av_species:
            seq, src = av_species[b["name"]]["seq"], "AviList (today's name)"
        else:
            cands = av_epithet.get((new.split()[-1], place["family"]), []) or \
                    av_epithet.get((b["name"].split()[-1], place["family"]), [])
            if len(cands) == 1:
                seq, src = cands[0]["seq"], "AviList epithet in family — REVIEW"
            elif genus in av_genus:
                seq, src = av_genus[genus] + 0.5, "after its genus"
            else:
                seq, src = None, "none — end of family"
        av = av_species.get(new) or av_species.get(b["name"])

        changes.append({**b, "new": new, "kind": kind, "new_order": place["order"],
                        "new_family": place["family"], "seq_src": src})

        if new in rows:                     # a second of today's species lands here
            row = rows[new]
            row["merged_from"].append(b["name"])
            k = kind + (" — REVIEW" if b["pics"] else "")
            names.append([b["name"], "sci", new, k, "GBIF match"])
            for lang in ("nl", "en"):
                if b[lang] and b[lang] not in row[lang]:
                    row[lang].append(b[lang])
                    names.append([b[lang], lang, new, k, "today's list"])
            row["pics"] += b["pics"]
            continue

        en = [b["en"]] if b["en"] else []
        if av and av["en"] and av["en"] not in en:
            en.append(av["en"])
            names.append([av["en"], "en", new, "other English name", "AviList"])
        rows[new] = {"name": new, **place, "seq": seq, "seq_src": src,
                     "nl": [b["nl"]] if b["nl"] else [], "en": en,
                     "avibase": av["avibase"] if av else "", "gbif_key": g["key"] if g else "",
                     "pics": b["pics"], "merged_from": []}
        if new != b["name"]:
            names.append([b["name"], "sci", new, kind, "GBIF match"])

    # unsorted species: end of their family
    for row in rows.values():
        if row["seq"] is None:
            fam = [r["seq"] for r in rows.values() if r["family"] == row["family"] and r["seq"] is not None]
            row["seq"] = (max(fam) + 0.9) if fam else 999999.0

    # higher taxa: sort at their lowest member
    taxa = []
    for rank in ("kingdom", "phylum", "class", "order", "family"):
        parent_rank = RANKS[RANKS.index(rank) - 1] if rank != "kingdom" else None
        members = defaultdict(list)
        parents = {}
        for r in rows.values():
            members[r[rank]].append(r["seq"])
            parents[r[rank]] = r[parent_rank] if parent_rank else ""
        for name, seqs in members.items():
            nl, en = higher.get(name, ("", ""))
            if not en and rank == "family":
                en = av_family_en.get(name, "")
            taxa.append({"rank": rank, "name": name, "parent": parents[name],
                         "seq": min(seqs), "seq_src": "lowest member",
                         "nl": [nl] if nl else [], "en": [en] if en else [],
                         "avibase": "", "gbif_key": "", "pics": ""})
    for r in rows.values():
        taxa.append({"rank": "species", "parent": r["family"], **r})
    return taxa, rows, names, changes


# ---------------------------------------------------------------- output
def write_outputs(out, taxa, rows, names, changes, av_species):
    with open(out / "taxa.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Rank", "ScientificName", "Parent", "Sequence", "SequenceSource",
                    "DutchNames", "EnglishNames", "AvibaseID", "GBIFKey", "Pictures"])
        for t in sorted(taxa, key=lambda t: (RANKS.index(t["rank"]), t["seq"])):
            w.writerow([t["rank"], t["name"], t["parent"], f"{t['seq']:g}", t["seq_src"],
                        t["nl"], t["en"], t["avibase"], t["gbif_key"], t["pics"]])
    with open(out / "names.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Name", "Language", "PointsTo", "Kind", "Source"])
        w.writerows(sorted(names, key=lambda n: (n[3], n[0])))

    # preview: the tree in the new order
    by_parent = defaultdict(list)
    for t in taxa:
        by_parent[t["parent"]].append(t)
    lines = []

    def walk(parent, depth):
        for t in sorted(by_parent.get(parent, []), key=lambda t: t["seq"]):
            label = t["en"][0] if t["en"] else ""
            pics = f"  [{t['pics']}]" if t["rank"] == "species" and t["pics"] else ""
            lines.append(f"{'  ' * depth}{t['name']}  — {label}{pics}")
            if t["rank"] != "species":
                walk(t["name"], depth + 1)
    walk("", 0)
    (out / "preview.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # report
    pictured = [c for c in changes if c["pics"]]
    kinds_all, kinds_pic = Counter(c["kind"] for c in changes), Counter(c["kind"] for c in pictured)
    src_pic = Counter(c["seq_src"] for c in pictured)
    fam_moves = [c for c in pictured if c["new_family"] != c["family"]]
    spelled = [c for c in fam_moves if similar(c["family"], c["new_family"])
               and (c["new_order"] == c["order"] or similar(c["order"], c["new_order"]))]
    fam_moves = [c for c in fam_moves if c not in spelled]
    auto = [c for c in fam_moves if c["new_order"] == c["order"]]
    review = [c for c in fam_moves if c["new_order"] != c["order"]]
    landing = defaultdict(list)
    for c in pictured:
        landing[c["new"]].append(c)
    two_into_one = [cs for cs in landing.values() if len(cs) > 1]
    missing = sorted(set(av_species) - {c["new"] for c in changes} - {c["name"] for c in changes})

    L = ["# Step 2a — new bird tables against today's list", "",
         f"Today's list: {len(changes)} bird species, {len(pictured)} with pictures. "
         f"New table: {len(rows)} species.", "",
         "| what happens | all | with pictures |", "|---|---:|---:|"]
    for k in ("same", "spelling", "renamed", "subspecies → species", "merged", "not in GBIF"):
        L.append(f"| {k} | {kinds_all[k]} | {kinds_pic[k]} |")
    L += ["", "## Where the sort number comes from (pictured species)", "",
          "| source | species |", "|---|---:|"]
    L += [f"| {k} | {v} |" for k, v in src_pic.most_common()]

    def table(title, items, cols):
        nonlocal L
        L += ["", f"## {title} ({len(items)})", "", "| " + " | ".join(h for h, _ in cols) + " |",
              "|" + "---|" * len(cols)]
        for c in items:
            L.append("| " + " | ".join(str(fn(c)) for _, fn in cols) + " |")

    table("Two of your pictured species become one — ask the user", two_into_one,
          [("GBIF species", lambda cs: cs[0]["new"]),
           ("today", lambda cs: ", ".join(f"{c['name']} ({c['en']}, {c['pics']})" for c in cs))])
    table("Pictured species merged into another — ask the user",
          sorted([c for c in pictured if c["kind"] == "merged"], key=lambda c: -c["pics"]),
          [("today", lambda c: c["name"]), ("English", lambda c: c["en"]), ("GBIF", lambda c: c["new"]),
           ("pictures", lambda c: c["pics"])])
    table("Pictured species with today's subspecies folded into the species",
          [c for c in pictured if c["kind"] == "subspecies → species"],
          [("today", lambda c: c["name"]), ("GBIF", lambda c: c["new"]), ("pictures", lambda c: c["pics"])])
    table("Pictured species, name spelled differently (Latin ending, spelling)",
          [c for c in pictured if c["kind"] == "spelling"],
          [("today", lambda c: c["name"]), ("GBIF", lambda c: c["new"]), ("pictures", lambda c: c["pics"])])
    table("Pictured species renamed (genus move) — ask the user",
          sorted([c for c in pictured if c["kind"] == "renamed"], key=lambda c: -c["pics"]),
          [("today", lambda c: c["name"]), ("GBIF", lambda c: c["new"]), ("pictures", lambda c: c["pics"])])
    table("Pictured species moving to another ORDER — review", review,
          [("species", lambda c: c["new"]), ("order", lambda c: f"{c['order']} → {c['new_order']}"),
           ("family", lambda c: f"{c['family']} → {c['new_family']}"), ("pictures", lambda c: c["pics"])])
    sp = Counter((f"{c['order']} / {c['family']}", f"{c['new_order']} / {c['new_family']}") for c in spelled)
    L += ["", f"## Order/family only spelled differently by GBIF ({len(spelled)} pictured species)", "",
          "| today → GBIF | species |", "|---|---:|"]
    L += [f"| {a} → {b} | {n} |" for (a, b), n in sp.most_common()]
    moves = Counter((c["family"], c["new_family"]) for c in auto)
    L += ["", f"## Family changes inside the same order — automatic ({len(auto)} pictured species)", "",
          "| family today → new | species |", "|---|---:|"]
    L += [f"| {a} → {b} | {n} |" for (a, b), n in moves.most_common()]
    table("Pictured species not in GBIF — keep today's placement",
          [c for c in pictured if c["kind"] == "not in GBIF"],
          [("species", lambda c: c["name"]), ("family", lambda c: c["family"]), ("pictures", lambda c: c["pics"])])
    table("Pictured species sorted by a guess — review",
          [c for c in pictured if "REVIEW" in c["seq_src"] or "after" in c["seq_src"] or "none" in c["seq_src"]],
          [("species", lambda c: c["new"]), ("how", lambda c: c["seq_src"])])
    L += ["", f"## AviList species not in today's list: {len(missing)}", "",
          "(Neither under today's name nor under GBIF's — what a full list would add.)", ""]
    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--master", required=True)
    ap.add_argument("--avilist", required=True, help="AviList short .xlsx (or a .csv of it)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    birds, higher = load_today(a.master)
    avi = load_avilist(a.avilist)
    gbif = gbif_match([b["name"] for b in birds], out / "gbif_cache.json")
    taxa, rows, names, changes = build(birds, higher, avi, gbif)
    write_outputs(out, taxa, rows, names, changes, avi[0])
    print(f"{len(birds)} birds today -> {len(rows)} species, {len(taxa)} taxa, "
          f"{len(names)} alias rows. Written to {out}")


if __name__ == "__main__":
    sys.exit(main())
