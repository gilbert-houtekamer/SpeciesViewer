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
"""


def _page(title, body):
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
            f"<body>{body}</body></html>")


def _e(v):
    return html.escape(str(v))


def render_console(rows, out_dir, machine=""):
    """Write index.html + per-trip drill-down pages into out_dir. Returns the
    index path. Expects rows produced by build_registry (carrying _missing)."""
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
            _write_trip_page(os.path.join(out_dir, fn), row, missing)

    index_path = os.path.join(out_dir, "index.html")
    _write_index(index_path, rows, machine, has_page)
    return index_path


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


def _missing_table(items):
    head = ["", "Scientific", "English", "Dutch", "Best JPG", "UPN"]
    th = "".join(f"<th>{_e(h)}</th>" for h in head)
    trs = []
    for m in items:
        cls = "new" if m["is_new"] else "dup"
        tag = "NEW" if m["is_new"] else "have"
        trs.append(
            f"<tr class='{cls}'>"
            f"<td><span class='tag {cls}'>{tag}</span></td>"
            f"<td><i>{_e(m['sci'])}</i></td>"
            f"<td>{_e(m['eng'])}</td>"
            f"<td>{_e(m['nl'])}</td>"
            f"<td>{_e(m['jpg'])}</td>"
            f"<td>{_e(m['upn'])}</td></tr>")
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"


def _write_trip_page(path, row, missing):
    new = [m for m in missing if m["is_new"]]
    dup = [m for m in missing if not m["is_new"]]
    n_new_species = len({m["sci"].lower() for m in new})
    n_dup_species = len({m["sci"].lower() for m in dup})
    rng = f"{row['fromdate']}..{row['todate']}".strip(".")
    body = [f"<div class='back'><a href='index.html'>&larr; back to console</a></div>",
            f"<h1>{_e(row['trip_name'])} &mdash; {_e(row['local_missing'])} pictures not in master</h1>",
            f"<div class='meta'>{_e(row['type'])} &middot; {_e(rng)} &middot; "
            f"local {_e(row['local_pics'])} pictures / in master {_e(row['local_in_master'])} &middot; "
            f"verdict: <b>{_e(row['delete_local'])}</b> &middot; {_e(row['repo_path'])}</div>"]
    if new:
        body.append(f"<h2>New species &mdash; not in the master "
                    f"({n_new_species} species, {len(new)} pictures) — would be lost on delete</h2>")
        body.append(_missing_table(new))
    if dup:
        body.append(f"<h2>Species already in the master "
                    f"({n_dup_species} species, {len(dup)} pictures) — extra pictures, low risk</h2>")
        body.append(_missing_table(dup))
    if not missing:
        body.append("<p>Nothing missing.</p>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_page(row["trip_name"], "".join(body)))
