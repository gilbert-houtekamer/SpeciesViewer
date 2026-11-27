"""Calibrate the AI's one-name-per-sitting vote, and its certainty, on a day you named.

ONE JOB: for a day measured by AI… (its animal vectors in the AI cache) and
the same day named by you (folder YOURS, e.g. an old Photo Mechanic copy):
how well "neighbouring poses whose animals look alike" means "the same
species" per cosine cut-off (engine.day_ai.ALIKE), whether the vote makes the
names more right, and how often the AI is right per certainty band (for
"probable" / "possible").
INVARIANT: report only — reads the cache and the .xmp sidecars, writes nothing.

    python tools/svr_ai_vote_calibrate.py <AI day folder> <your day folder> <country code>
"""
import sqlite3, os, re, sys
import numpy as np
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "speciesviewer"))
sys.stdout.reconfigure(encoding="utf-8")
if len(sys.argv) != 4:
    sys.exit(__doc__)
DAY, G, REGION = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3].upper()
from utils.config import _config
from db.master_mutate import _read_collection
rows = _read_collection(Path(_config.get("master","masterbiotadatabase",fallback="").strip().strip('"')))
to_sci = {}
for s, r in rows.items():
    for c in ("DutchName","EnglishName","ScientificName"):
        v = (r.get(c) or "").strip().lower()
        if v: to_sci.setdefault(v, s.lower())
def his(stem):
    t = next(G.glob(f"{stem}.[xX][mM][pP]")).read_text(encoding="utf-8", errors="replace")
    m = re.search(r"<dc:subject>(.*?)</dc:subject>", t, re.S)
    sp = {to_sci[k.strip().lower()] for k in re.findall(r"<rdf:li>([^<]*)</rdf:li>", m.group(1)) if k.strip().lower() in to_sci} if m else set()
    d = re.search(r'exif:DateTimeOriginal="([^"+]*)', t)
    return sp, datetime.fromisoformat(d.group(1)) if d else None
con = sqlite3.connect(Path(os.environ["LOCALAPPDATA"])/"SpeciesViewer"/"scores"/"bird_boxes.sqlite")
L = []
for path, box, vec in con.execute("SELECT path, box, vec FROM vectors WHERE path LIKE ? AND box IS NOT NULL AND box != '' ORDER BY path", (str(DAY) + "%",)):
    stem = Path(path).stem.split(" - ")[-1]
    sp, t = his(stem)
    v = np.frombuffer(vec, np.float32); v = v/np.linalg.norm(v)
    L.append((stem, sp, t, v))
print("leaders with an animal:", len(L))
pairs = []
for a, b in zip(L, L[1:]):
    if not a[1] or not b[1] or len(a[1])>1 or len(b[1])>1: continue
    gap = (b[2]-a[2]).total_seconds() if a[2] and b[2] else 1e9
    pairs.append((float(a[3]@b[3]), gap, a[1]==b[1]))
print("usable neighbour pairs:", len(pairs), " same species:", sum(p[2] for p in pairs))
for maxgap in (120, 600):
    print(f"-- gap <= {maxgap} s")
    for T in (0.70, 0.75, 0.80, 0.85, 0.88, 0.90, 0.92, 0.95):
        linked = [p for p in pairs if p[1] <= maxgap and p[0] >= T]
        wrong = sum(not p[2] for p in linked)
        same_all = sum(p[2] for p in pairs if p[1] <= maxgap)
        print(f"  cos >= {T:.2f}: linked {len(linked):4}  of which different species {wrong:3}  "
              f"({wrong/max(1,len(linked)):.1%})  catches {len(linked)-wrong}/{same_all} same-species neighbours")

# ---- names with and without the vote ----
from engine import ai_measure
master = _config.get("master","masterbiotadatabase",fallback="").strip().strip('"')
found = ai_measure.names_many([x[3] for x in L], REGION, master)
top = [(nm[0] if nm else None) for nm, _ in found]
def score(names, label):
    both = ok = 0
    for (stem, sp, t, v), n in zip(L, names):
        if not sp or n is None or n["prob"] * 100 < 45: continue
        both += 1
        mine = to_sci.get((n.get("sci") or "").lower(), (n.get("sci") or "").lower())
        ok += mine in sp or any(s.startswith(mine + " ") for s in sp)   # subspecies of the AI's species = right
    print(f"  {label}: named {both}, right {ok} ({ok/max(1,both):.1%})")
score(top, "each pose on its own")
for T in (0.85, 0.90):
    # chains of look-alike neighbours
    chains, cur = [], [0]
    for i in range(1, len(L)):
        a, b = L[i-1], L[i]
        gap = (b[2]-a[2]).total_seconds() if a[2] and b[2] else 1e9
        if gap <= 120 and float(a[3]@b[3]) >= T: cur.append(i)
        else: chains.append(cur); cur = [i]
    chains.append(cur)
    voted = list(top)
    for ch in chains:
        tally = {}
        for i in ch:
            if top[i] is not None:
                tally[top[i]["sci"].lower()] = tally.get(top[i]["sci"].lower(), 0) + top[i]["prob"]
        if not tally: continue
        win = max(tally, key=tally.get)
        best = max((top[i] for i in ch if top[i] and top[i]["sci"].lower() == win), key=lambda n: n["prob"])
        for i in ch:
            voted[i] = dict(best)
    print(f"  chains at {T}: {len(chains)} (from {len(L)} poses)")
    score(voted, f"voted at {T}")

# ---- mixed sittings, and right-rate per certainty ----
chains, cur = [], [0]
for i in range(1, len(L)):
    a, b = L[i-1], L[i]
    gap = (b[2]-a[2]).total_seconds() if a[2] and b[2] else 1e9
    if gap <= 120 and float(a[3]@b[3]) >= 0.85: cur.append(i)
    else: chains.append(cur); cur = [i]
chains.append(cur)
multi = [c for c in chains if len(c) > 1]
mixed = [c for c in multi if len({top[i]["sci"].lower() for i in c if top[i] and top[i]["prob"] >= .45}) > 1]
print(f"sittings of 2+ poses: {len(multi)}, with mixed sure names: {len(mixed)}")
print("right-rate by the AI's certainty (poses you named):")
for lo, hi in ((0, .25), (.25, .35), (.35, .45), (.45, .55), (.55, .65), (.65, .75), (.75, .85), (.85, .95), (.95, 1.01)):
    n = ok = 0
    for (stem, sp, t, v), x in zip(L, top):
        if not sp or x is None or not (lo <= x["prob"] < hi): continue
        n += 1
        mine = to_sci.get((x.get("sci") or "").lower(), (x.get("sci") or "").lower())
        ok += mine in sp or any(s.startswith(mine + " ") for s in sp)
    print(f"  {lo*100:3.0f}-{min(hi,1)*100:3.0f} %: {n:4} poses, right {ok/max(1,n):.0%}")
