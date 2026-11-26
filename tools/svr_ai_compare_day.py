"""Compare the AI's accepted names and colours on a day with your own verdicts.

ONE JOB: for one day read twice — once accepted with AI… (folder AI), once
reviewed by you (folder YOURS, e.g. an old Photo Mechanic copy) — match the
pictures by file name and report how often the species agree and how the
colours cross (your colour against the AI's).
INVARIANT: report only — reads the .xmp sidecars, writes nothing.

    python tools/svr_ai_compare_day.py <AI day folder> <your day folder>

Names are compared as scientific names through the main master's species list
(your Dutch / English / scientific names); keywords not in the list are listed
and ignored.
"""
import re, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "speciesviewer"))
from utils.config import _config
from db.master_mutate import _read_collection

if len(sys.argv) != 3:
    sys.exit(__doc__)
AI, HIS = Path(sys.argv[1]), Path(sys.argv[2])
sys.stdout.reconfigure(encoding="utf-8")
master = Path(_config.get("master", "masterbiotadatabase", fallback="").strip().strip('"'))
rows = _read_collection(master)
to_sci = {}
for sci, r in rows.items():
    for col in ("DutchName", "EnglishName", "ScientificName"):
        v = (r.get(col) or "").strip().lower()
        if v:
            to_sci.setdefault(v, sci.lower())
    to_sci.setdefault(sci.lower(), sci.lower())


def read(xmp: Path):
    t = xmp.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'xmp:Label(?:="([^"]*)"|>([^<]*)<)', t)
    label = (m.group(1) or m.group(2)) if m else ""
    subj = re.search(r"<dc:subject>(.*?)</dc:subject>", t, re.S)
    kws = re.findall(r"<rdf:li>([^<]*)</rdf:li>", subj.group(1)) if subj else []
    sp = [to_sci[k.strip().lower()] for k in kws if k.strip().lower() in to_sci]
    other = [k for k in kws if k.strip().lower() not in to_sci]
    return label, sp, other


def name_of(sci):
    r = rows.get(sci, {})
    return r.get("EnglishName") or r.get("DutchName") or sci


ai, his = {}, {}
for x in (p for p in AI.iterdir() if p.suffix.lower() == ".xmp"):
    ai[x.stem.split(" - ")[-1].upper()] = read(x)
for x in {p.resolve() for p in HIS.iterdir() if p.suffix.lower() == ".xmp"}:
    his[x.stem.upper()] = read(x)
keys = sorted(set(ai) & set(his))
print(f"pictures: AI side {len(ai)}, yours {len(his)}, both {len(keys)}")
unknown_his = Counter(o for k in keys for o in his[k][2])
if unknown_his:
    print("your keywords not in the species list (ignored):", unknown_his.most_common(12))

# ── names ──
same = wrong = ai_only = his_only = neither = 0
wrong_pairs = Counter()
miss_by = Counter()
hit_by = Counter()
for k in keys:
    a, h = set(ai[k][1]), set(his[k][1])
    if a and h:
        if a & h:
            same += 1
            for s in a & h:
                hit_by[s] += 1
        else:
            wrong += 1
            wrong_pairs[(name_of(min(h)), name_of(min(a)))] += 1
    elif a:
        ai_only += 1
    elif h:
        his_only += 1
        for s in h:
            miss_by[s] += 1
    else:
        neither += 1
named_both = same + wrong
print("\nNAMES")
print(f"  both named: {named_both} — same species {same} ({same / max(1, named_both):.1%}), different {wrong}")
print(f"  AI named, you did not: {ai_only}")
print(f"  you named, AI did not (no sure name / not in list): {his_only}")
print(f"  neither: {neither}")
print("  different — yours → AI's (top 25):")
for (h, a), n in wrong_pairs.most_common(25):
    print(f"    {n:4}  {h}  →  {a}")
print("  yours the AI left unnamed (top 15):")
for s, n in miss_by.most_common(15):
    print(f"    {n:4}  {name_of(s)}")

# ── colours ──
print("\nCOLOURS  (rows = yours, columns = AI's)")
cols = ["", "Purple", "Green", "Blue", "Yellow", "Red"]
grid = defaultdict(Counter)
for k in keys:
    grid[his[k][0] or "none"][ai[k][0] or "none"] += 1
ai_cols = sorted({c for g in grid.values() for c in g})
print("  " + "yours\\AI".ljust(10) + "".join(c.rjust(8) for c in ai_cols) + "   total")
for hcol in sorted(grid, key=lambda c: -sum(grid[c].values())):
    print("  " + hcol.ljust(10) + "".join(str(grid[hcol][c]).rjust(8) for c in ai_cols)
          + str(sum(grid[hcol].values())).rjust(8))
GO = {"Blue", "Purple"}
STAY = {"Green", "Yellow", "Red"}
p_go = [k for k in keys if ai[k][0] == "Purple"]
print(f"\n  AI Purple: {len(p_go)} — you also let go (Blue/Purple): "
      f"{sum(his[k][0] in GO for k in p_go)}, you kept (Green/Yellow/Red): "
      f"{sum(his[k][0] in STAY for k in p_go)}")
kept_lost = [k for k in p_go if his[k][0] in ("Yellow", "Red")]
print(f"  of which your Yellow/Red (your best): {len(kept_lost)}  {sorted(kept_lost)[:20]}")
g_ai = [k for k in keys if ai[k][0] == "Green"]
print(f"  AI Green: {len(g_ai)} — you kept: {sum(his[k][0] in STAY for k in g_ai)}, "
      f"you let go: {sum(his[k][0] in GO for k in g_ai)}")
