"""Ch.6 step 2 — build the new taxonomy tables beside today's list.

Read-only on the master. Writes only into --out:
  taxa.csv      one row per taxon (kingdom -> species): GBIF name + placement,
                a sort number among its siblings + where it came from,
                Dutch/English names as lists (first = shown)
  names.csv     alias table: name -> scientific name it points to, and what kind
  changes.csv   one row per species of today's list: what happens to it
  report.md     what would change against today's list, pictured species first
  preview.txt   the whole tree in the new order
  gbif_cache.json  GBIF species-match answers, so a re-run makes no calls

Sort numbers (compared only among siblings):
  birds         AviList Sequence; orders and families at their lowest member
  hand table    kingdoms, phyla, classes and non-bird orders:
                tools/taxonomy_hand_order.csv (edit it; unknown names come after,
                alphabetically)
  book order    odonata / butterflies: today's family and subfamily numbers
  otherwise     alphabetical

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

PREFIX_RE = re.compile(r"^(\d+)\s+")
RANKS = ["kingdom", "phylum", "class", "order", "family", "species"]
HIGHER = RANKS[:-1]
SAME_STATUS = {"ACCEPTED", "DOUBTFUL"}
KEPT = "kept — GBIF synonym"
HAND_TABLE = Path(__file__).with_name("taxonomy_hand_order.csv")
AFTER_HAND = 1000          # unknown siblings sort after the hand-placed ones


def strip(name):
    return PREFIX_RE.sub("", name or "").strip()


def legacy_number(name):
    """The sort number today's list put in front of a name, or None."""
    m = PREFIX_RE.match(name or "")
    return int(m.group(1)) if m else None


def real_family(name):
    """'zygoptera lestidae' / 'nymphalidae satyrinae' -> the family word in it."""
    words = strip(name).split()
    fam = [w for w in words if w.endswith(("idae", "aceae"))]
    return fam[0] if fam else (words[-1] if words else "")


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

    species = []
    for name, r in sc.items():
        if r["Level"] != "Level.SPECIES":
            continue
        ch = chain(name)
        if "kingdom" not in ch:          # landscapes and other topics
            continue
        fam_raw = ch.get("family", "")
        species.append({
            "name": name, "nl": r["DutchName"], "en": r["EnglishName"],
            "kingdom": strip(ch.get("kingdom")), "phylum": strip(ch.get("phylum")),
            "class": strip(ch.get("class")), "order": strip(ch.get("order")),
            "family": real_family(fam_raw), "family_raw": strip(fam_raw),
            "book": legacy_number(fam_raw), "pics": pics.get(name, 0),
        })
    # names of higher taxa, keyed by the name without its number; a fused
    # 'zygoptera lestidae' also names the family 'lestidae'
    higher = {}
    for name, r in sc.items():
        if r["Level"] in ("Level.KINGDOM", "Level.PHYLUM", "Level.CLASS",
                          "Level.ORDER", "Level.FAMILY"):
            higher.setdefault(strip(name), (r["DutchName"], r["EnglishName"]))
            if r["Level"] == "Level.FAMILY" and strip(name).split()[-1] == real_family(name):
                higher.setdefault(real_family(name), (r["DutchName"], r["EnglishName"]))
    return species, higher


def load_hand_table(path=HAND_TABLE):
    return {(r["Rank"], r["Parent"], r["Name"]): (float(r["Sequence"]), r["Source"])
            for r in read_csv(path)}


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
def gbif_match(species, cache_path):
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    todo = [s for s in species if s["name"] not in cache]

    def one(s):
        params = {"name": s["name"], "kingdom": s["kingdom"].capitalize(), "strict": "true"}
        if s["class"] == "aves":
            params["class"] = "Aves"
        q = urllib.parse.urlencode(params)
        for _ in range(3):
            try:
                with urllib.request.urlopen("https://api.gbif.org/v1/species/match?" + q,
                                            timeout=30) as resp:
                    return s["name"], json.load(resp)
            except Exception:
                pass
        return s["name"], None

    if todo:
        print(f"GBIF: {len(todo)} lookups ({len(species) - len(todo)} cached)…", flush=True)
        with ThreadPoolExecutor(8) as ex:
            for i, (name, d) in enumerate(ex.map(one, todo), 1):
                if d is not None:
                    cache[name] = d
                if i % 500 == 0:          # save as we go: an interrupted run resumes
                    cache_path.write_text(json.dumps(cache), encoding="utf-8")
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
        **{k: (d.get(k) or "").lower() for k in ("kingdom", "phylum", "class", "order",
                                                 "family", "genus")},
        "key": d.get("speciesKey") or d.get("usageKey"),
    }


def chain_of(place):
    """[(rank, name)] from kingdom down, skipping empty levels and a name that
    repeats a higher one (GBIF: class Crocodylia, order Crocodylia)."""
    out, seen = [], set()
    for rank in HIGHER:
        name = place.get(rank, "")
        if name and name not in seen:
            out.append((rank, name))
            seen.add(name)
    return out


# ---------------------------------------------------------------- build
def build(species, higher, avi, gbif, hand):
    av_species, av_genus, av_family_en, av_epithet = avi
    rows = {}          # new species name -> row
    names = []         # alias rows
    changes = []       # per old species: what happens to it

    def new_name(s):
        g = gbif_view(gbif.get(s["name"]))
        if not g or not g["species"] or change_kind(s["name"], g["species"]) == "merged":
            return s["name"]    # GBIF merges are never followed: the species stays
        return g["species"]

    # species that exist in the new table under their own name: an epithet guess
    # must never land on one of them (Oxyura ferruginea is not Tadorna ferruginea)
    taken = {new_name(s) for s in species}

    # a species GBIF leaves alone owns its row; one merging into it comes second
    for s in sorted(species, key=lambda s: new_name(s) != s["name"]):
        g = gbif_view(gbif.get(s["name"]))
        today = {k: s[k] for k in HIGHER}
        if g and g["species"]:
            new = g["species"]
            kind = change_kind(s["name"], new)
            if kind == "merged":
                # GBIF calls it a synonym of another species. Never followed (too
                # often wrong: Ochlodes sylvanus -> venata); it stays as it is.
                kind, gbif_target, new = KEPT, new, s["name"]
            place = {k: g[k] for k in HIGHER}
            if not place["order"] and not place["family"]:
                place["order"], place["family"] = today["order"], today["family"]
            elif not place["family"]:
                place["family"] = today["family"]   # GBIF leaves it empty: today's stays
            genus = g["genus"]
        else:
            new, kind, place, genus = s["name"], "not in GBIF", today, s["name"].split()[0]
        if kind != KEPT:
            gbif_target = ""
        bird = place["class"] == "aves"

        # sort number for a bird; other species are numbered per family below
        seq, src, av = None, "", None
        if bird:
            if new in av_species:
                seq, src = av_species[new]["seq"], "AviList"
            elif s["name"] in av_species:
                seq, src = av_species[s["name"]]["seq"], "AviList (today's name)"
            else:
                cands = av_epithet.get((new.split()[-1], place["family"]), []) or \
                        av_epithet.get((s["name"].split()[-1], place["family"]), [])
                cands = [c for c in cands if c["name"] not in taken]
                if len(cands) == 1:
                    seq, src = cands[0]["seq"], "AviList epithet in family — REVIEW"
                elif genus in av_genus:
                    seq, src = av_genus[genus] + 0.5, "after its genus"
                else:
                    src = "none — end of family"
            av = av_species.get(new) or av_species.get(s["name"])

        changes.append({**s, "new": new, "kind": kind, "new_order": place["order"],
                        "new_family": place["family"], "new_class": place["class"],
                        "seq_src": src, "gbif": gbif_target})

        if new in rows:                     # a second of today's species lands here
            row = rows[new]
            row["merged_from"].append(s["name"])
            k = kind + (" — REVIEW" if s["pics"] else "")
            names.append([s["name"], "sci", new, k, "GBIF match"])
            for lang in ("nl", "en"):
                if s[lang] and s[lang] not in row[lang]:
                    row[lang].append(s[lang])
                    names.append([s[lang], lang, new, k, "today's list"])
            row["pics"] += s["pics"]
            if s["book"] is not None and (row["book"] is None or s["book"] < row["book"]):
                row["book"] = s["book"]
            continue

        en = [s["en"]] if s["en"] else []
        if av and av["en"] and av["en"] not in en:
            en.append(av["en"])
            names.append([av["en"], "en", new, "other English name", "AviList"])
        rows[new] = {"name": new, "chain": chain_of(place), "bird": bird,
                     "seq": seq, "seq_src": src, "book": None if bird else s["book"],
                     "book_family": s["family"],
                     "nl": [s["nl"]] if s["nl"] else [], "en": en,
                     "avibase": av["avibase"] if av else "", "gbif_key": g["key"] if g else "",
                     "pics": s["pics"], "merged_from": [],
                     "note": f"GBIF: synonym of {gbif_target}" if gbif_target else ""}
        if gbif_target:
            rows[new]["gbif_key"] = ""      # GBIF's key is the other species'
        if new != s["name"]:
            names.append([s["name"], "sci", new, kind, "GBIF match"])

    for r in rows.values():
        r["parent"] = r["chain"][-1][1] if r["chain"] else ""

    # ---- species order inside each family
    by_family = defaultdict(list)
    for r in rows.values():
        by_family[r["parent"]].append(r)
    for fam, members in by_family.items():
        birds = [r for r in members if r["bird"]]
        if birds:
            known = [r["seq"] for r in birds if r["seq"] is not None]
            for r in birds:
                if r["seq"] is None:
                    r["seq"] = (max(known) + 0.9) if known else 999999.0
            tied = defaultdict(list)       # several after the same genus: alphabetical
            for r in birds:
                tied[r["seq"]].append(r)
            for group in tied.values():
                if len(group) > 1:
                    for i, r in enumerate(sorted(group, key=lambda r: r["name"])):
                        r["seq"] += i * 0.001
        others = sorted((r for r in members if not r["bird"]),
                        key=lambda r: (r["book"] if r["book"] is not None else 0, r["name"]))
        for i, r in enumerate(others, 1):
            r["seq"] = float(i)
            r["seq_src"] = "today's book order, then alphabetical" if r["book"] is not None \
                else "alphabetical"

    # ---- higher taxa
    taxa_hi = {}       # name -> {rank, parent, members, books}
    for r in rows.values():
        parent = ""
        for rank, name in r["chain"]:
            t = taxa_hi.setdefault(name, {"rank": rank, "parent": parent, "seqs": [],
                                          "books": [], "bird": False})
            t["seqs"].append(r["seq"])
            t["bird"] |= r["bird"]
            # a family takes a book number only from species that were in it
            # before — not from one GBIF moved in from another family
            if r["book"] is not None and (rank != "family" or name == r["book_family"]):
                t["books"].append(r["book"])
            parent = name
    siblings = defaultdict(list)
    for name, t in taxa_hi.items():
        siblings[t["parent"]].append(name)
    for parent, kids in siblings.items():
        later = []
        for name in kids:
            t = taxa_hi[name]
            if t["bird"] and t["rank"] in ("order", "family"):
                t["seq"], t["src"] = min(t["seqs"]), "lowest member"
            elif (t["rank"], parent, name) in hand:
                t["seq"], t["src"] = hand[(t["rank"], parent, name)]
            elif t["rank"] == "family" and t["books"]:
                t["seq"], t["src"] = float(min(t["books"])), "today's book order"
            else:
                later.append(name)
        for i, name in enumerate(sorted(later), 1):
            taxa_hi[name]["seq"] = AFTER_HAND + i
            taxa_hi[name]["src"] = "alphabetical"

    taxa = []
    for name, t in taxa_hi.items():
        nl, en = higher.get(name, ("", ""))
        if not en and t["rank"] == "family":
            en = av_family_en.get(name, "")
        taxa.append({"rank": t["rank"], "name": name, "parent": t["parent"],
                     "seq": t["seq"], "seq_src": t["src"],
                     "nl": [nl] if nl else [], "en": [en] if en else [],
                     "avibase": "", "gbif_key": "", "pics": "", "note": ""})
    for r in rows.values():
        taxa.append({"rank": "species", **r})
    return taxa, rows, names, changes


# ---------------------------------------------------------------- output
def write_outputs(out, taxa, rows, names, changes, av_species):
    with open(out / "taxa.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Rank", "ScientificName", "Parent", "Sequence", "SequenceSource",
                    "DutchNames", "EnglishNames", "AvibaseID", "GBIFKey", "Pictures", "Note"])
        for t in sorted(taxa, key=lambda t: (RANKS.index(t["rank"]), t["parent"], t["seq"])):
            w.writerow([t["rank"], t["name"], t["parent"], round(t["seq"], 4), t["seq_src"],
                        t["nl"], t["en"], t["avibase"], t["gbif_key"], t["pics"], t["note"]])
    with open(out / "names.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Name", "Language", "PointsTo", "Kind", "Source"])
        w.writerows(sorted(names, key=lambda n: (n[3], n[0])))

    with open(out / "changes.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Today", "Class", "OrderToday", "FamilyToday", "Kind", "New", "GBIFSynonymOf",
                    "OrderNew", "FamilyNew", "DutchName", "EnglishName", "Pictures", "SequenceSource"])
        for c in sorted(changes, key=lambda c: (c["class"], c["name"])):
            w.writerow([c["name"], c["class"], c["order"], c["family"], c["kind"], c["new"], c["gbif"],
                        c["new_order"], c["new_family"], c["nl"], c["en"], c["pics"], c["seq_src"]])

    # preview: the tree in the new order
    by_parent = defaultdict(list)
    for t in taxa:
        by_parent[t["parent"]].append(t)
    lines = []

    def walk(parent, depth):
        for t in sorted(by_parent.get(parent, []), key=lambda t: t["seq"]):
            label = t["en"][0] if t["en"] else (t["nl"][0] if t["nl"] else "")
            pics = f"  [{t['pics']}]" if t["rank"] == "species" and t["pics"] else ""
            lines.append(f"{'  ' * depth}{t['name']}  — {label}{pics}")
            if t["rank"] != "species":
                walk(t["name"], depth + 1)
    walk("", 0)
    (out / "preview.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # report
    pictured = [c for c in changes if c["pics"]]
    kinds_all, kinds_pic = Counter(c["kind"] for c in changes), Counter(c["kind"] for c in pictured)
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

    L = ["# Step 2 — new taxonomy tables against today's list", "",
         f"Today's list: {len(changes)} species, {len(pictured)} with pictures. "
         f"New table: {len(rows)} species.", "",
         "| class | species | pictured | renamed | kept: GBIF synonym | not in GBIF |",
         "|---|---:|---:|---:|---:|---:|"]
    per_class = defaultdict(list)
    for c in changes:
        per_class[c["class"]].append(c)
    for k, cs in sorted(per_class.items(), key=lambda kv: -len(kv[1])):
        kc = Counter(c["kind"] for c in cs)
        L.append(f"| {k} | {len(cs)} | {sum(1 for c in cs if c['pics'])} | "
                 f"{kc['renamed'] + kc['spelling']} | {kc[KEPT]} | {kc['not in GBIF']} |")
    L += ["", "| what happens | all | with pictures |", "|---|---:|---:|"]
    for k in ("same", "spelling", "renamed", "subspecies → species", KEPT, "not in GBIF"):
        L.append(f"| {k} | {kinds_all[k]} | {kinds_pic[k]} |")

    def table(title, items, cols):
        nonlocal L
        L += ["", f"## {title} ({len(items)})", "", "| " + " | ".join(h for h, _ in cols) + " |",
              "|" + "---|" * len(cols)]
        for c in items:
            L.append("| " + " | ".join(str(fn(c)) for _, fn in cols) + " |")

    name_of = lambda c: c["en"] or c["nl"]
    table("Two of your pictured entries become one (a subspecies folded in) — ask the user", two_into_one,
          [("GBIF species", lambda cs: cs[0]["new"]),
           ("today", lambda cs: ", ".join(f"{c['name']} ({name_of(c)}, {c['pics']})" for c in cs))])
    table("Pictured species GBIF calls a synonym — kept as their own species; ask the user",
          sorted([c for c in pictured if c["kind"] == KEPT], key=lambda c: -c["pics"]),
          [("species", lambda c: c["name"]), ("name", name_of), ("GBIF: synonym of", lambda c: c["gbif"]),
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
          [("species", lambda c: c["name"]), ("class", lambda c: c["class"]),
           ("family", lambda c: c["family"]), ("pictures", lambda c: c["pics"])])
    table("Pictured birds sorted by a guess — review",
          [c for c in pictured if "REVIEW" in c["seq_src"] or "after" in c["seq_src"]
           or "none" in c["seq_src"]],
          [("species", lambda c: c["new"]), ("how", lambda c: c["seq_src"])])
    L += ["", f"## AviList species not in today's list: {len(missing)}", "",
          "(Neither under today's name nor under GBIF's — what a full bird list would add.)", ""]
    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--master", required=True)
    ap.add_argument("--avilist", required=True, help="AviList short .xlsx (or a .csv of it)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    species, higher = load_today(a.master)
    avi = load_avilist(a.avilist)
    gbif = gbif_match(species, out / "gbif_cache.json")
    taxa, rows, names, changes = build(species, higher, avi, gbif, load_hand_table())
    write_outputs(out, taxa, rows, names, changes, avi[0])
    print(f"{len(species)} species today -> {len(rows)} species, {len(taxa)} taxa, "
          f"{len(names)} alias rows. Written to {out}")


if __name__ == "__main__":
    sys.exit(main())
