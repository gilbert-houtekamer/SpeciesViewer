"""Ch.6 — a review page for the new taxonomy tables.

Read-only. Takes the folder svr_taxonomy_build.py wrote (taxa.csv, changes.csv)
and the master (for today's order) and writes review.html into that folder: a
self-contained page that works offline.

  1. Decisions that are yours, each with a default: pictured species GBIF calls
     a synonym (kept apart), pictured genus moves (followed), today's subspecies
     entries (folded). Choices stay in the browser and download as decisions.csv.
  2. Today's order next to the new one: orders per class, families per order,
     pictured species per family.

    python tools/svr_taxonomy_review.py --tables D:/code/taxonomy_trial \
        --master E:/Dropbox/naturedb
"""
import argparse
import ast
import csv
import html
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PREFIX_RE = re.compile(r"^\d+\s+")
KEPT = "kept — GBIF synonym"
KEPT_CHOICE = "kept — your choice"
MERGED_CHOICE = "merged — your choice"
KEPT_REF = "kept — reference list"


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def strip(name):
    return PREFIX_RE.sub("", name or "").strip()


def real_family(name):
    words = strip(name).split()
    fam = [w for w in words if w.endswith(("idae", "aceae"))]
    return fam[0] if fam else (words[-1] if words else "")


def today_tree(master):
    """Today's children per parent, in today's order (the website sorts the raw
    names, so the number in front decides). Names are given without the number;
    a fused family name is reduced to its family word."""
    sc = read_csv(Path(master) / "metadata/active/speciescollection.csv")
    kids = defaultdict(list)
    level = {}
    for r in sc:
        kids[r["Up"]].append(r["ScientificName"])
        level[r["ScientificName"]] = r["Level"].split(".")[-1].lower()
    clean = lambda n: real_family(n) if level.get(n) == "family" else strip(n)
    # '040 passeriformes' and 'passeriformes', or the subfamilies of one butterfly
    # family, become one name: join their lists in today's order, once each
    tree = {}
    for parent in sorted(kids):
        lst = tree.setdefault(clean(parent), [])
        for n in sorted(kids[parent]):
            if clean(n) not in lst:
                lst.append(clean(n))
    return tree


def new_tree(taxa):
    kids = defaultdict(list)
    for t in taxa:
        kids[t["Parent"]].append(t)
    return {p: [t["ScientificName"] for t in sorted(ts, key=lambda t: float(t["Sequence"]))]
            for p, ts in kids.items()}


def compare(today, new, pictured=None):
    """Two columns for one parent. A name present on both sides is 'moved' when
    its place among the shared names differs."""
    shared = [n for n in today if n in new]
    pos_t = {n: i for i, n in enumerate(shared)}
    pos_n = {n: i for i, n in enumerate([n for n in new if n in pos_t])}

    def mark(n, side):
        if n not in pos_t:
            return "only"
        d = pos_t[n] - pos_n[n]
        return "" if d == 0 else ("up" if d > 0 else "down") if side == "new" else ""
    keep = (lambda n: True) if pictured is None else (lambda n: n in pictured)
    return {"today": [[n, mark(n, "today")] for n in today if keep(n)],
            "new": [[n, mark(n, "new")] for n in new if keep(n)]}


def change_is_genus(c):
    """A kept row whose GBIF name differs only in the genus (or the ending): a
    genus move you chose not to follow, not a GBIF synonym."""
    a, b = c["Today"].split(), (c["GBIFSynonymOf"] or c["New"]).split()
    return len(a) == 2 and len(b) == 2 and a[0] != b[0] and a[1][:4] == b[1][:4]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tables", required=True)
    ap.add_argument("--master", required=True)
    ap.add_argument("--title", default="Species order review")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    folder = Path(a.tables)
    taxa = read_csv(folder / "taxa.csv")
    changes = read_csv(folder / "changes.csv")
    tax = {t["ScientificName"]: t for t in taxa}

    def shown(name):
        t = tax.get(name)
        if not t:
            return ""
        for col in ("EnglishNames", "DutchNames"):
            v = ast.literal_eval(t[col]) if t[col] else []
            if v:
                return v[0]
        return ""

    pic = [c for c in changes if int(c["Pictures"] or 0) > 0]
    decisions = {
        "synonym": [c for c in pic if c["Kind"] in (KEPT, MERGED_CHOICE)
                    or (c["Kind"] == KEPT_CHOICE and not change_is_genus(c))],
        "renamed": [c for c in pic if c["Kind"] == "renamed" or c["Kind"] == KEPT_REF
                    or (c["Kind"] == KEPT_CHOICE and change_is_genus(c))],
        "subspecies": [c for c in pic if c["Kind"] == "subspecies → species"],
    }
    for group in decisions.values():
        group.sort(key=lambda c: -int(c["Pictures"]))
    slim = {k: [{"today": c["Today"],
                 "new": c["GBIFSynonymOf"] if k == "renamed" and c["GBIFSynonymOf"] else c["New"],
                 "gbif": c["GBIFSynonymOf"] or c["New"],
                 "decided": "follow" if c["Kind"] == MERGED_CHOICE else
                            "keep" if c["Kind"] in (KEPT_CHOICE, KEPT_REF) else "",
                 "why": c.get("Decision", ""), "ref": c.get("Reference", ""),
                 "name": c["EnglishName"] or c["DutchName"], "pics": int(c["Pictures"]),
                 "family": c["FamilyNew"]} for c in v] for k, v in decisions.items()}

    # order comparison: classes -> orders -> families -> pictured species
    tt, nt = today_tree(a.master), new_tree(taxa)
    pictured_new = {c["New"] for c in pic}
    pictured_today = {c["Today"] for c in pic}
    ranks = {t["ScientificName"]: t["Rank"] for t in taxa}
    classes = [n for n in tax if ranks[n] == "class"]
    tree = []
    def path_key(n):
        key = []
        while n in tax:
            key.insert(0, float(tax[n]["Sequence"]))
            n = tax[n]["Parent"]
        return key
    for cl in sorted(classes, key=path_key):
        if not any(c["Class"] == cl for c in pic):
            continue
        node = {"name": cl, "label": shown(cl), "cmp": compare(tt.get(cl, []), nt.get(cl, [])),
                "kids": []}
        for o in nt.get(cl, []):
            if ranks.get(o) == "family":            # no order level (reptiles)
                continue
            fams = nt.get(o, [])
            fam_nodes = []
            for f in fams:
                sp_today = [s for s in tt.get(f, []) if s in pictured_today]
                sp_new = [s for s in nt.get(f, []) if s in pictured_new]
                if sp_new or sp_today:
                    fam_nodes.append({"name": f, "label": shown(f),
                                      "cmp": compare(tt.get(f, []), nt.get(f, []),
                                                     pictured_today | pictured_new)})
            if fam_nodes:
                node["kids"].append({"name": o, "label": shown(o),
                                     "cmp": compare(tt.get(o, []), fams), "kids": fam_nodes})
        tree.append(node)

    counts = defaultdict(int)
    for c in changes:
        counts[c["Kind"]] += 1
    data = {"decisions": slim, "tree": tree, "labels": {n: shown(n) for n in tax},
            "counts": counts, "total": len(changes), "pictured": len(pic)}
    page = PAGE.replace("Species order review", html.escape(a.title)).replace("/*DATA*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    (folder / "review.html").write_text(page, encoding="utf-8")
    print(f"review.html written: {sum(len(v) for v in slim.values())} decisions, "
          f"{len(tree)} classes. Open {folder / 'review.html'}")


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Species order review</title>
<style>
:root{--bg:#fbfaf7;--fg:#1f2421;--muted:#6b716c;--line:#e2e0d9;--card:#ffffff;
 --accent:#2f6b4f;--up:#1f6fb2;--down:#b4532a;--only:#8a5cb8;--hl:#f3efe2}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#171a18;--fg:#e8e9e4;
 --muted:#9aa29c;--line:#2e332f;--card:#1f2320;--accent:#7cc19d;--up:#7fb6e8;--down:#e59a76;
 --only:#c7a3ea;--hl:#2a2e25}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 8px}
.sub{color:var(--muted);margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}
.counts{display:flex;flex-wrap:wrap;gap:8px}.counts span{background:var(--hl);border-radius:6px;padding:3px 9px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums}
i{font-style:italic}.muted{color:var(--muted)}
label.opt{display:block;white-space:nowrap;cursor:pointer}
tr.changed{background:var(--hl)}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:8px 0}
button{font:inherit;padding:7px 14px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:var(--card);cursor:pointer}
button.ghost{background:transparent;color:var(--accent)}
details{border:1px solid var(--line);border-radius:8px;margin:6px 0;background:var(--card)}
details>summary{cursor:pointer;padding:8px 12px;font-weight:600}
details>.body{padding:4px 12px 12px}
details details{margin-left:14px}
.cmp{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.cmp ol{margin:4px 0;padding-left:28px}.cmp li{padding:1px 0}
.cmp h4{margin:4px 0;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.up{color:var(--up)}.down{color:var(--down)}.only{color:var(--only)}
.tag{font-size:12px;margin-left:6px}
.mine{font-size:12px;color:var(--accent)}
.legend span{margin-right:14px}
@media (max-width:640px){.cmp{grid-template-columns:1fr}label.opt{white-space:normal}}
</style></head><body><main>
<h1>Species order review</h1>
<p class="sub">What the new species list would change in your collection. Nothing has been changed yet —
this page only shows it, and lets you choose where a choice is yours.</p>
<div class="card"><div id="summary"></div></div>

<h2>1 · Your choices</h2>
<p class="sub">Each row has a default. Change only what you disagree with. Your choices are remembered
in this browser; <b>Download choices</b> saves them as a file for the next step.</p>
<div class="bar"><button id="dl">Download choices</button>
<button class="ghost" id="reset">Back to the defaults</button><span id="nchanged" class="muted"></span></div>
<div id="decisions"></div>

<h2>2 · Today's order next to the new one</h2>
<p class="sub">Open a class, an order or a family. Left is today's order, right the new one; only species with
pictures are listed.</p>
<p class="legend muted"><span class="up">▲ earlier than today</span><span class="down">▼ later than today</span>
<span class="only">◆ only on this side</span></p>
<div id="tree"></div>
</main>
<script>
const D = /*DATA*/null;
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const KEY = "sv-taxonomy-review-v1";
let choices = {};
try { choices = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(choices)); } catch (e) {} };

const GROUPS = [
 {key:"synonym", title:"GBIF calls these a synonym of another species",
  intro:"They stay as their own species unless you choose to merge them. GBIF is often wrong here.",
  opts:[["keep","Keep as its own species"],["follow",r=>"Merge into "+r.gbif]], def:"keep",
  cols:r=>[`<i>${esc(r.today)}</i><br><span class="muted">${esc(r.name)}</span>`,
           `<i>${esc(r.gbif)}</i><br><span class="muted">${esc(D.labels[r.gbif]||"")}</span>`]},
 {key:"renamed", title:"New genus name",
  intro:"Same bird or insect, a new genus. Followed unless you choose to keep today's name.",
  opts:[["follow",r=>"Use "+r.new],["keep","Keep today's name"]], def:"follow",
  cols:r=>[`<i>${esc(r.today)}</i><br><span class="muted">${esc(r.name)}</span>`,`<i>${esc(r.new)}</i>`]},
 {key:"subspecies", title:"Your subspecies entries",
  intro:"Folded into the species, unless you want to keep the subspecies as its own entry.",
  opts:[["follow",r=>"Fold into "+r.new],["keep","Keep as its own entry"]], def:"follow",
  cols:r=>[`<i>${esc(r.today)}</i><br><span class="muted">${esc(r.name)}</span>`,`<i>${esc(r.new)}</i>`]},
];

function summary(){
  const c = D.counts, n = k => (c[k]||0).toLocaleString();
  document.getElementById("summary").innerHTML =
   `<p style="margin:0 0 8px">${D.total.toLocaleString()} species in today's list, ${D.pictured.toLocaleString()} with pictures.</p>
   <div class="counts"><span>unchanged ${n("same")}</span><span>new genus ${n("renamed")}</span>
   <span>spelling only ${n("spelling")}</span><span>GBIF synonym, kept ${n("kept — GBIF synonym")}</span>
   <span>subspecies folded ${n("subspecies → species")}</span><span>not in GBIF ${n("not in GBIF")}</span></div>`;
}

function decisions(){
  let out = "";
  for (const g of GROUPS){
    const rows = D.decisions[g.key];
    out += `<div class="card"><h3 style="margin:0 0 4px">${esc(g.title)} <span class="muted">(${rows.length})</span></h3>
     <p class="muted" style="margin:0 0 8px">${esc(g.intro)}</p><table><thead><tr><th>Today</th>
     <th>${g.key==="synonym"?"GBIF says":"New"}</th><th class="num">Pictures</th><th>Your choice</th></tr></thead><tbody>`;
    for (const r of rows){
      const def = r.decided || g.def, id = g.key+":"+r.today, cur = choices[id] || def;
      const [a,b] = g.cols(r);
      const opts = g.opts.map(([v,l]) => `<label class="opt"><input type="radio" name="${esc(id)}" value="${v}"
        ${cur===v?"checked":""}> ${esc(typeof l==="function"?l(r):l)}</label>`).join("");
      const why = r.ref && !r.why ? `<br><span class="mine">${esc(r.ref)}</span>`
        : r.decided ? `<br><span class="mine">your decision${r.why ? ": "+esc(r.why) : ""}</span>` : "";
      out += `<tr data-id="${esc(id)}" data-def="${def}" class="${cur!==def?"changed":""}"><td>${a}${why}</td><td>${b}</td>
        <td class="num">${r.pics}</td><td>${opts}</td></tr>`;
    }
    out += "</tbody></table></div>";
  }
  const el = document.getElementById("decisions");
  el.innerHTML = out;
  el.querySelectorAll("input[type=radio]").forEach(inp => inp.addEventListener("change", e => {
    const tr = e.target.closest("tr");
    if (e.target.value === tr.dataset.def) delete choices[tr.dataset.id]; else choices[tr.dataset.id] = e.target.value;
    tr.classList.toggle("changed", !!choices[tr.dataset.id]);
    save(); count();
  }));
  count();
}
function count(){
  const n = Object.keys(choices).length;
  document.getElementById("nchanged").textContent = n ? `${n} changed from the default` : "All defaults";
}

function download(){
  const lines = ["Group;Today;Choice"];
  for (const g of GROUPS) for (const r of D.decisions[g.key]){
    const id = g.key+":"+r.today;
    lines.push([g.key, r.today, choices[id] || r.decided || g.def].join(";"));
  }
  const blob = new Blob([lines.join("\n")+"\n"], {type:"text/csv"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "decisions.csv"; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function cmp(c){
  const li = (n,m) => `<li><i>${esc(n)}</i> <span class="muted">${esc(D.labels[n]||"")}</span>${
     m==="up"?'<span class="tag up">▲</span>':m==="down"?'<span class="tag down">▼</span>':m==="only"?'<span class="tag only">◆</span>':""}</li>`;
  return `<div class="cmp"><div><h4>Today</h4><ol>${c.today.map(([n,m])=>li(n,m)).join("")||'<li class="muted">—</li>'}</ol></div>
   <div><h4>New</h4><ol>${c.new.map(([n,m])=>li(n,m)).join("")}</ol></div></div>`;
}
function moved(c){ return c.new.filter(([,m])=>m).length + c.today.filter(([,m])=>m==="only").length; }
function node(n, depth){
  const m = moved(n.cmp), badge = m ? ` <span class="muted">· ${m} changed</span>` : "";
  const kids = (n.kids||[]).map(k => node(k, depth+1)).join("");
  return `<details><summary><i>${esc(n.name)}</i> <span class="muted">${esc(n.label)}</span>${badge}</summary>
   <div class="body">${cmp(n.cmp)}${kids}</div></details>`;
}

summary(); decisions();
document.getElementById("tree").innerHTML = D.tree.map(n => node(n,0)).join("");
document.getElementById("dl").addEventListener("click", download);
document.getElementById("reset").addEventListener("click", () => { choices = {}; save(); decisions(); });
</script></body></html>
"""

if __name__ == "__main__":
    sys.exit(main())
