# trip_registry_html.py: render the trip registry as a local, private
# management console — a summary index page plus one drill-down page per trip
# that still has pictures missing from the master.
#
# The drill-down answers the question that decides whether a local repo is safe
# to delete: of the pictures NOT yet in the master, which are of a species the
# master does not have (a real loss) vs. extra angles of species already kept.
#
# Output is plain static HTML (no JS) written under a local folder on the master
# (NOT published). Open index.html in a browser.

import os
import html
import logging
from datetime import datetime

logger = logging.getLogger("speciesviewer")

_VERDICT_COLORS = {
    "SAFE to delete":          "#bfe6b0",
    "safe (have all species)": "#e3f0b8",
    "no local data":           "#e9e9e9",
    "not in master":           "#f3d2c9",
}


def _verdict_color(verdict):
    if verdict.startswith("keep"):
        return "#ffe39a"
    return _VERDICT_COLORS.get(verdict, "#ffffff")


def _safe_filename(name):
    keep = "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)
    return keep or "trip"


_CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222;background:#fafafa}
h1{font-size:20px;margin:0 0 4px} h2{font-size:16px;margin:24px 0 8px}
.meta{color:#666;font-size:12px;margin-bottom:16px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}
th,td{border:1px solid #ddd;padding:5px 8px;text-align:left}
th{background:#f0f0f0;vertical-align:bottom;white-space:normal}
th.n{width:64px} td.n{text-align:right}
tr:hover{background:#f6f9ff}
.verdict{font-weight:600;white-space:nowrap}
a{color:#1a5fb4;text-decoration:none} a:hover{text-decoration:underline}
.new{background:#fff4f0} .dup{background:#f7faf0}
.tag{font-size:11px;padding:1px 6px;border-radius:8px}
.tag.new{background:#e7503a;color:#fff} .tag.dup{background:#7aa83a;color:#fff}
.back{font-size:12px}
.nothumb{color:#bbb}
.sub{color:#777;font-size:11px}
td.absent{color:#ccc;text-align:center}
.legend div{margin:3px 0;font-size:12px}
.swatch{display:inline-block;width:14px;height:14px;border:1px solid #999;
  vertical-align:middle;margin-right:6px}
.taxon{color:#555;white-space:nowrap}
/* images are off by default so the grouping stays readable */
.inline-thumb{display:none;height:120px;border:1px solid #ccc}
body.show-images .inline-thumb{display:block}
tr[data-thumb]{cursor:zoom-in}
.toolbar{margin:14px 0}
.toolbar button{font-size:13px;padding:6px 14px;cursor:pointer;border:1px solid #aaa;
  border-radius:5px;background:#fff}
.toolbar button:hover{background:#eef4ff}
.hint{color:#888;font-size:12px;margin-left:10px}
/* floating hover preview (only when images are not pinned inline) */
#preview{position:fixed;top:14px;right:14px;display:none;z-index:100;background:#fff;
  border:2px solid #1a5fb4;padding:3px;box-shadow:0 6px 20px rgba(0,0,0,.3)}
#preview img{max-height:72vh;max-width:44vw;display:block}
"""

# Tiny vanilla-JS: hover any picture row to peek at it in a corner pane; the
# toolbar button pins all thumbnails inline (and suppresses the hover peek).
_SCRIPT = """
<script>
(function(){
  var pv=document.getElementById('preview'), img=pv?pv.querySelector('img'):null;
  document.addEventListener('mouseover',function(e){
    var tr=e.target.closest&&e.target.closest('tr[data-thumb]');
    if(tr && img && !document.body.classList.contains('show-images')){
      img.src=tr.getAttribute('data-thumb'); pv.style.display='block';
    }
  });
  document.addEventListener('mouseout',function(e){
    if(pv && e.target.closest && e.target.closest('tr[data-thumb]')) pv.style.display='none';
  });
  window.toggleImages=function(btn){
    var on=document.body.classList.toggle('show-images');
    btn.textContent= on ? 'Hide images' : 'Show images';
    if(on && pv) pv.style.display='none';
  };
})();
</script>
"""


def _page(title, body):
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
            f"<body>{body}</body></html>")


def _e(v):
    return html.escape(str(v))


def render_console(rows, out_dir, machine="", thumb_map=None):
    """Write index.html + per-trip drill-down pages into out_dir. Returns the
    index path. Expects rows produced by build_registry (carrying _missing).
    thumb_map maps UPN -> relative thumbnail path (optional)."""
    thumb_map = thumb_map or {}
    os.makedirs(out_dir, exist_ok=True)
    has_page = {}
    for row in rows:
        missing = row.get("_missing", [])
        if missing:
            fn = _safe_filename(row["trip_name"]) + ".html"
            # disambiguate same-named trips (e.g. a stray "- Copy")
            base, n = fn[:-5], 1
            while fn in has_page.values() and has_page.get(id(row)) != fn:
                n += 1
                fn = f"{base}_{n}.html"
            has_page[id(row)] = fn
            _write_trip_page(os.path.join(out_dir, fn), row, missing, thumb_map)

    index_path = os.path.join(out_dir, "index.html")
    _write_index(index_path, rows, machine, has_page)
    return index_path


# Per-machine role colours: green = done/safe, amber = CAUTION (unique field
# work, copy before deleting), blue = active, purple = virtual keeper.
_ROLE_COLOR = {
    "Complete (in master)":   "#bfe6b0",
    "Superseded — delete":    "#dceec6",
    "Copy to primary first":  "#ffcf99",
    "Active (in progress)":   "#dfe9ff",
    "Active (not in master)": "#dfe9ff",
    "Virtual (keep)":         "#e7dcff",
    "Ongoing (keep)":         "#9fe7df",
    "no local data":          "#eeeeee",
}
_OVERALL_COLOR = {"Integrated": "#bfe6b0", "Active": "#dfe9ff",
                  "Virtual (keep)": "#e7dcff", "Ongoing (keep)": "#9fe7df"}

_ROLE_LEGEND = [
    ("Complete (in master)", "the authoritative copy, fully backed up"),
    ("Superseded — delete", "leftover fragment, nothing the master lacks — safe to delete"),
    ("Copy to primary first", "holds species not yet upstream — copy to BigBoy before deleting"),
    ("Active (in progress)", "still being worked, new species not yet in master"),
    ("Virtual (keep)", "virtual trip — tiny footprint, keep the blogs"),
    ("Ongoing (keep)", "open-ended collection with no date range — permanent keeper"),
]


def render_merged(by_trip, machines, out_path, console_dir):
    """Integrated cross-machine view. Picks the most-complete (primary) copy per
    trip, labels each machine's copy with its role, and derives an overall.
    Trip names link to the LOCAL machine's drill-down when present."""
    import engine.trip_registry as tr
    mheads = "".join(f"<th>{_e(m)}</th>" for m in machines)
    head = f"<th>Trip</th><th>Type</th>{mheads}<th>Overall</th><th>Range</th>"
    trs = []
    for trip in sorted(by_trip):
        recs = by_trip[trip]
        primary_m, overall, roles = tr.classify_trip(recs)
        any_row = next(iter(recs.values()))
        page = _safe_filename(trip) + ".html"
        name = (f"<a href='{_e(page)}'>{_e(trip)}</a>"
                if os.path.exists(os.path.join(console_dir, page)) else _e(trip))
        cells = [f"<td>{name}</td>", f"<td>{_e(any_row.get('type',''))}</td>"]
        for m in machines:
            cells.append(_machine_cell(recs.get(m), roles.get(m), m == primary_m))
        cells.append(f"<td style='background:{_OVERALL_COLOR.get(overall,'#fff')}'>"
                     f"<b>{_e(overall)}</b> <span class='sub'>(primary: {_e(primary_m)})</span></td>")
        cells.append(f"<td>{_e(_trip_range(recs))}</td>")
        trs.append("<tr>" + "".join(cells) + "</tr>")
    legend = "".join(
        f"<div><span class='swatch' style='background:{_ROLE_COLOR.get(r,'#fff')}'></span>"
        f"<b>{_e(r)}</b> &mdash; {_e(d)}</div>" for r, d in _ROLE_LEGEND)
    body = (f"<h1>Trip registry &mdash; all machines</h1>"
            f"<div class='meta'>generated {datetime.now():%Y-%m-%d %H:%M} &middot; "
            f"machines: {_e(', '.join(machines))} &middot; "
            f"<a href='index.html'>this machine only</a></div>"
            f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(trs)}</tbody></table>"
            f"<h2>Legend</h2><div class='legend'>{legend}</div>")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(_page("Trip registry — all machines", body))
    return out_path


def _machine_cell(row, role, is_primary):
    if not row:
        return "<td class='absent'>&mdash;</td>"
    bits = [f"{row.get('local_pics','')} pics"]
    miss = row.get("local_missing", "")
    if miss not in ("", "0"):
        bits.append(f"{miss} miss")
    nsp = row.get("new_species", "")
    if nsp not in ("", "0"):
        bits.append(f"{nsp} new sp")
    star = " &#9733;" if is_primary else ""
    return (f"<td style='background:{_ROLE_COLOR.get(role,'#fff')}'>"
            f"<b>{_e(role)}</b>{star}<br><span class='sub'>{_e(', '.join(bits))}</span></td>")


def _trip_range(recs):
    froms = [r.get("fromdate", "") for r in recs.values() if r.get("fromdate")]
    tos = [r.get("todate", "") for r in recs.values() if r.get("todate")]
    if not froms and not tos:
        return ""
    return f"{min(froms) if froms else ''}..{max(tos) if tos else ''}".strip(".")


def _write_index(path, rows, machine, has_page):
    # (label, is_numeric) — descriptive labels wrap onto two lines.
    head = [("Trip", False), ("Type", False), ("Days", True), ("Committed", True),
            ("Local Pictures", True), ("In Master", True), ("Missing from Master", True),
            ("New Species", True), ("Verdict", False), ("Status", False), ("Range", False)]
    th = "".join(f"<th class='{'n' if n else ''}'>{_e(h)}</th>" for h, n in head)
    trs = []
    for row in rows:
        name = _e(row["trip_name"])
        page = has_page.get(id(row))
        name_cell = f"<a href='{_e(page)}'>{name}</a>" if page else name
        rng = f"{row['fromdate']}..{row['todate']}".strip(".")
        verdict = row["delete_local"]
        cells = [
            f"<td>{name_cell}</td>",
            f"<td>{_e(row['type'])}</td>",
            f"<td class='n'>{_e(row['num_days'])}</td>",
            f"<td class='n'>{_e(row['days_committed'])}</td>",
            f"<td class='n'>{_e(row['local_pics'])}</td>",
            f"<td class='n'>{_e(row['local_in_master'])}</td>",
            f"<td class='n'>{_e(row['local_missing'])}</td>",
            f"<td class='n'>{_e(row['new_species'])}</td>",
            f"<td class='verdict' style='background:{_verdict_color(verdict)}'>{_e(verdict)}</td>",
            f"<td>{_e(row['status'])}</td>",
            f"<td>{_e(rng)}</td>",
        ]
        trs.append("<tr>" + "".join(cells) + "</tr>")
    body = (f"<h1>Trip registry &mdash; {_e(machine)}</h1>"
            f"<div class='meta'>generated {datetime.now():%Y-%m-%d %H:%M} &middot; "
            f"{len(rows)} trips &middot; verdict is by UniquePictureName match against the master</div>"
            f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_page(f"Trip registry — {machine}", body))


def _taxon(s):
    """Strip the eBird-style numeric sort prefix (e.g. '040 passeriformes')."""
    return s.lstrip("0123456789 ").capitalize()


def _missing_table(items, thumb_map):
    # Taxonomic order so related species cluster — the grouping the user wants
    # to stay visible even with images off.
    items = sorted(items, key=lambda m: (
        m.get("phylum", ""), m.get("cls", ""),
        _taxon(m.get("order", "")), _taxon(m.get("family", "")), m["sci"]))
    head = ["", "Phylum", "Class", "Order", "Family", "Scientific", "English", "Dutch", "Image"]
    th = "".join(f"<th>{_e(h)}</th>" for h in head)
    trs = []
    for m in items:
        cls = "new" if m["is_new"] else "dup"
        tag = "NEW" if m["is_new"] else "have"
        thumb = thumb_map.get(m["upn"])
        data = f" data-thumb='{_e(thumb)}'" if thumb else ""
        img = (f"<a href='{_e(thumb)}' target='_blank'>"
               f"<img class='inline-thumb' src='{_e(thumb)}' loading='lazy'></a>"
               if thumb else "<span class='nothumb'>—</span>")
        trs.append(
            f"<tr class='{cls}'{data}>"
            f"<td><span class='tag {cls}'>{tag}</span></td>"
            f"<td class='taxon'>{_e(_taxon(m.get('phylum','')))}</td>"
            f"<td class='taxon'>{_e(_taxon(m.get('cls','')))}</td>"
            f"<td class='taxon'>{_e(_taxon(m.get('order','')))}</td>"
            f"<td class='taxon'>{_e(_taxon(m.get('family','')))}</td>"
            f"<td><i>{_e(m['sci'])}</i></td>"
            f"<td>{_e(m['eng'])}</td>"
            f"<td>{_e(m['nl'])}</td>"
            f"<td>{img}</td></tr>")
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"


def _write_trip_page(path, row, missing, thumb_map):
    new = [m for m in missing if m["is_new"]]
    dup = [m for m in missing if not m["is_new"]]
    n_new_species = len({m["sci"].lower() for m in new})
    n_dup_species = len({m["sci"].lower() for m in dup})
    rng = f"{row['fromdate']}..{row['todate']}".strip(".")
    have_thumbs = any(m["upn"] in thumb_map for m in missing)
    toolbar = ("<div class='toolbar'><button onclick='toggleImages(this)'>Show images</button>"
               "<span class='hint'>or hover a row to preview</span></div>" if have_thumbs else "")
    body = [f"<div class='back'><a href='index.html'>&larr; back to console</a></div>",
            f"<h1>{_e(row['trip_name'])} &mdash; {_e(row['local_missing'])} pictures not in master</h1>",
            f"<div class='meta'>{_e(row['type'])} &middot; {_e(rng)} &middot; "
            f"local {_e(row['local_pics'])} pictures / in master {_e(row['local_in_master'])} &middot; "
            f"verdict: <b>{_e(row['delete_local'])}</b> &middot; {_e(row['repo_path'])}</div>",
            toolbar]
    if new:
        body.append(f"<h2>New species &mdash; not in the master "
                    f"({n_new_species} species, {len(new)} pictures) — would be lost on delete</h2>")
        body.append(_missing_table(new, thumb_map))
    if dup:
        body.append(f"<h2>Species already in the master "
                    f"({n_dup_species} species, {len(dup)} pictures) — extra pictures, low risk</h2>")
        body.append(_missing_table(dup, thumb_map))
    if not missing:
        body.append("<p>Nothing missing.</p>")
    if have_thumbs:
        body.append("<div id='preview'><img alt=''></div>")
        body.append(_SCRIPT)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_page(row["trip_name"], "".join(body)))
