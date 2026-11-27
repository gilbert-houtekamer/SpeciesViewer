"""Compare the AI's accepted names and colours on a day with your own verdicts.

ONE JOB: for one day read twice — once accepted with AI… (folder AI), once
reviewed by you (folder YOURS, e.g. an old Photo Mechanic copy) — match the
pictures by file name and report how often the species agree and how the
colours cross (your colour against the AI's).
INVARIANT: report only — reads the .xmp sidecars, writes nothing.

    python tools/svr_ai_compare_day.py <AI day folder> <your day folder> [--html <page.html>]

--html also writes a page with, per run of neighbouring pictures where the
names differ, your name and the AI's side by side with the pictures (from
SpeciesViewer's preview cache, linked, not copied) — to look at by eye.

Names are compared as scientific names through the main master's species list
(your Dutch / English / scientific names); keywords not in the list are listed
and ignored.
"""
import re, sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "speciesviewer"))
from utils.config import _config
from db.master_mutate import _read_collection

if len(sys.argv) not in (3, 5) or (len(sys.argv) == 5 and sys.argv[3] != "--html"):
    sys.exit(__doc__)
AI, HIS = Path(sys.argv[1]), Path(sys.argv[2])
HTML = Path(sys.argv[4]) if len(sys.argv) == 5 else None
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
    d = re.search(r'exif:DateTimeOriginal(?:="|>)(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)', t)
    when = datetime.fromisoformat(d.group(1)) if d else None
    return label, sp, other, when


def name_of(sci):
    r = rows.get(sci, {})
    return r.get("EnglishName") or r.get("DutchName") or sci


def both_names(sci):
    r = rows.get(sci, {})
    return r.get("EnglishName") or sci, r.get("DutchName") or "", sci


ai, his, ai_stem = {}, {}, {}
for x in (p for p in AI.iterdir() if p.suffix.lower() == ".xmp"):
    ai[x.stem.split(" - ")[-1].upper()] = read(x)
    ai_stem[x.stem.split(" - ")[-1].upper()] = x.stem
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

# ── Yellow / Red: the AI's best picture per species against yours ──
NEAR = 30    # s — an AI Yellow this close to one of your Yellow/Red = (about) the same pose
print("\nYELLOW / RED per species (your names; 'near' = within 30 s of one of your Yellow/Red)")
print(f"  {'species':32} {'your Y':>6} {'your R':>6} {'AI Y':>5} {'same':>5} {'near':>5}")
per = defaultdict(lambda: {"Y": [], "R": [], "AI": []})
for k in keys:
    lab, sp, _o, when = his[k]
    for s_ in sp:
        if lab in ("Yellow", "Red"):
            per[s_][lab[0]].append((k, when))
        if ai[k][0] == "Yellow":
            per[s_]["AI"].append((k, when))
tot = Counter()
missed = []
for s_, d in sorted(per.items(), key=lambda kv: -(len(kv[1]["Y"]) + len(kv[1]["R"]))):
    mine = d["Y"] + d["R"]
    if not mine and not d["AI"]:
        continue
    same = sum(1 for k, _w in d["AI"] if k in {m for m, _ in mine})
    near = sum(1 for k, w in d["AI"] if k not in {m for m, _ in mine} and w and any(
        mw and abs((w - mw).total_seconds()) <= NEAR for _m, mw in mine))
    tot.update(Y=len(d["Y"]), R=len(d["R"]), AI=len(d["AI"]), same=same, near=near)
    if mine and not d["AI"]:
        missed.append(name_of(s_))
    print(f"  {name_of(s_)[:32]:32} {len(d['Y']):6} {len(d['R']):6} {len(d['AI']):5} {same:5} {near:5}")
print(f"  {'TOTAL':32} {tot['Y']:6} {tot['R']:6} {tot['AI']:5} {tot['same']:5} {tot['near']:5}")
print(f"  species with your Yellow/Red but no AI Yellow: {len(missed)} {missed[:15]}")


# ── the page: your name and the AI's side by side ──
def write_page(path: Path):
    import hashlib, html, os
    cache = (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SpeciesViewer" / "preview_cache"
             / hashlib.sha1(os.path.normcase(str(AI)).encode()).hexdigest()[:16])

    def thumb(k):
        for name in (f"{ai_stem[k]}@268.jpg", f"{ai_stem[k]}.jpg"):
            f = cache / name
            if f.exists():
                return f.as_uri()
        return ""

    runs = []           # [(your sci, AI sci, [keys])], neighbours with the same pair
    for k in keys:
        a, h = set(ai[k][1]), set(his[k][1])
        if not a or not h or a & h:
            continue
        pair = (min(h), min(a))
        w = his[k][3]
        if (runs and runs[-1][0] == pair and w and runs[-1][2]
                and (w - his[runs[-1][1][-1]][3]).total_seconds() <= 120):
            runs[-1][1].append(k)
        else:
            runs.append((pair, [k], w))
    sub = lambda mine, its: mine.startswith(its + " ")
    pairs = Counter()
    for pair, ks, _w in runs:
        pairs[pair] += len(ks)
    e = html.escape
    out = ["<!doctype html><meta charset=utf-8><title>AI names vs yours</title><style>",
           "body{font:14px system-ui,sans-serif;background:#1e1f1c;color:#ddd;margin:16px}",
           "table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #444;"
           "padding:6px;vertical-align:top;text-align:left}th{position:sticky;top:0;background:#2a2b27}",
           ".n{width:22%}.d{color:#999}.s{color:#7a7}.pics img{height:150px;margin:2px;border-radius:4px}",
           "a{color:#9cf}</style>",
           f"<h1>{e(AI.name)}: your name ↔ the AI's</h1>",
           f"<p>{sum(pairs.values())} pictures in {len(runs)} runs (neighbouring pictures "
           "within 2 minutes with the same two names). Green = your name is a subspecies of the "
           "AI's species (not really wrong).</p><h2>By pair</h2><table><tr><th>Yours</th>"
           "<th>The AI's</th><th>Pictures</th></tr>"]
    for (mine, its), n in pairs.most_common():
        cls = " class=s" if sub(mine, its) else ""
        out.append(f"<tr{cls}><td>{e(name_of(mine))}</td><td>{e(name_of(its))}</td><td>{n}</td></tr>")
    out.append("</table><h2>Run by run</h2><table><tr><th class=n>Yours</th><th>Pictures</th>"
               "<th class=n>The AI's</th></tr>")
    for (mine, its), ks, w in runs:
        cls = " class=s" if sub(mine, its) else ""
        me, mn, ms = both_names(mine)
        ae, an, as_ = both_names(its)
        imgs = "".join(f'<img src="{thumb(k)}" title="{e(ai_stem[k])} · yours {e(his[k][0] or "none")}'
                       f' · AI {e(ai[k][0] or "none")}">' for k in ks[:6] if thumb(k))
        more = f" <span class=d>+{len(ks) - 6} more</span>" if len(ks) > 6 else ""
        when = w.strftime("%H:%M:%S") if w else ""
        out.append(f"<tr{cls}><td>{e(me)}<br><span class=d>{e(mn)}<br><i>{e(ms)}</i></span></td>"
                   f"<td class=pics><span class=d>{when} · {len(ks)} pictures · {e(ai_stem[ks[0]])}"
                   f"</span><br>{imgs}{more}</td>"
                   f"<td>{e(ae)}<br><span class=d>{e(an)}<br><i>{e(as_)}</i></span></td></tr>")
    out.append("</table>")
    path.write_text("\n".join(out), encoding="utf-8")
    print(f"\npage: {path} ({len(runs)} runs)")


if HTML is not None:
    write_page(HTML)
