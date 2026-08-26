#!/usr/bin/env python3
"""Build the reference manual into ONE self-contained HTML page.

That page is two things at once:

  * a light interactive viewer — a browser tab with a sticky chapter/section
    sidebar and a filter box, which beats scrolling Markdown in an editor, and
  * the PDF source — Ctrl+P → "Save as PDF" gives a single document with each
    chapter starting on a new page and the sidebar dropped.

No third-party dependencies: the Markdown converter below handles exactly the
subset the manual uses (headings, tables, fenced code, blockquotes, lists,
rules, and inline code/bold/italic/links). That is a deliberate trade — the
manual is written to the converter, and a missing feature shows up as visibly
wrong output rather than a silent misrender.

Mermaid diagrams are emitted as <pre class="mermaid"> and drawn by mermaid.js
from a CDN. With no network they degrade to a readable code block; pass
--no-mermaid to skip the script tag entirely.

Usage:
    python tools/svr_manual_build.py                # build, print the path
    python tools/svr_manual_build.py --open         # build and open in browser
    python tools/svr_manual_build.py --out X.html   # somewhere else
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import webbrowser
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANUAL_DIR = REPO / "docs" / "manual"
DEFAULT_OUT = MANUAL_DIR / "SpeciesViewer_Manual.html"

MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"

# Vendored copy of mermaid.js. When this file exists the build INLINES it, and
# the page needs no network at all — which is the point: the manual is read on
# aeroplanes. Fetch it once with --vendor-mermaid while you have internet.
# Not tracked in git (it is a 3 MB third-party blob, MIT licensed).
MERMAID_LOCAL = MANUAL_DIR / "_assets" / "mermaid.min.js"

# The review marker. A blockquote starting with it is a note from whoever is
# reading the manual, not manual prose — rendered loud, listed in the sidebar,
# and extractable with --feedback. Both "> GH: …" and "> **GH:** …" are taken.
REVIEW_MARK = re.compile(r"^\**\s*GH\s*\**\s*:\s*\**\s*", re.IGNORECASE)

# Notes found during a build, in document order: (anchor id, preview text).
_NOTES: list[tuple[str, str]] = []


def _first_words(text: str, limit: int = 60) -> str:
    flat = " ".join(REVIEW_MARK.sub("", text.lstrip(), count=1).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# ── chapter order ────────────────────────────────────────────────────────────

def chapter_files(manual_dir: Path) -> list[Path]:
    """README first, then the numbered chapters, then the appendices.

    Derived from the filenames rather than a hard-coded list, so a new chapter
    is picked up by being named in the pattern — nothing to keep in sync.
    """
    files = sorted(p for p in manual_dir.glob("*.md"))
    readme = [p for p in files if p.stem.lower() == "readme"]
    numbered = [p for p in files if re.match(r"^\d", p.stem)]
    appendix = [p for p in files if p.stem.lower().startswith("appendix")]
    rest = [p for p in files if p not in readme + numbered + appendix]
    return readme + numbered + rest + appendix


def slugify(text: str) -> str:
    """GitHub-compatible heading slug, so links written for GitHub still work."""
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s)
    return s.strip("-")


# ── inline markdown ──────────────────────────────────────────────────────────

_CODE_TOKEN = "\x00CODE{}\x00"


def inline(text: str, link_rewriter=None) -> str:
    """Inline conversion. Code spans are pulled out first so their contents are
    never treated as markup, then put back at the end."""
    codes: list[str] = []

    def stash(m):
        codes.append(html.escape(m.group(1)))
        return _CODE_TOKEN.format(len(codes) - 1)

    text = re.sub(r"`([^`]+)`", stash, text)
    text = html.escape(text)

    def link(m):
        label, href = m.group(1), m.group(2)
        if link_rewriter:
            href = link_rewriter(href)
        return f'<a href="{html.escape(href, quote=True)}">{label}</a>'

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)

    for i, c in enumerate(codes):
        text = text.replace(_CODE_TOKEN.format(i), f"<code>{c}</code>")
    return text


# ── block markdown ───────────────────────────────────────────────────────────

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^```\s*(\w*)\s*$")
# Alignment cells can be as short as ":-:" (one dash), which GitHub accepts —
# so this takes "-+", not "-{2,}". Only ever tested against the line AFTER a
# row containing "|", so a loose match cannot swallow a list item.
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_LIST_ITEM = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.*)$")


def split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def convert(md: str, link_rewriter=None, heading_cb=None) -> str:
    """Markdown → HTML for the subset the manual uses."""
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # fenced code / mermaid
        m = _FENCE.match(line)
        if m:
            lang = m.group(1)
            i += 1
            body: list[str] = []
            while i < n and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            code = "\n".join(body)
            if lang == "mermaid":
                out.append(f'<pre class="mermaid">{html.escape(code)}</pre>')
            else:
                cls = f' class="language-{lang}"' if lang else ""
                out.append(f"<pre><code{cls}>{html.escape(code)}</code></pre>")
            continue

        # heading
        m = _HEADING.match(line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            anchor = heading_cb(level, text) if heading_cb else slugify(text)
            out.append(
                f'<h{level} id="{anchor}">'
                f'<a class="anchor" href="#{anchor}">#</a>'
                f"{inline(text, link_rewriter)}</h{level}>"
            )
            i += 1
            continue

        # horizontal rule
        if re.match(r"^\s*([-*_])\s*\1\s*\1[\s\-*_]*$", line):
            out.append("<hr>")
            i += 1
            continue

        # table: a row followed by a separator row
        if "|" in line and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            header = split_row(line)
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(split_row(lines[i]))
                i += 1
            th = "".join(f"<th>{inline(c, link_rewriter)}</th>" for c in header)
            trs = []
            for r in rows:
                tds = "".join(f"<td>{inline(c, link_rewriter)}</td>" for c in r)
                trs.append(f"<tr>{tds}</tr>")
            out.append(
                '<div class="table-wrap"><table>'
                f"<thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody>"
                "</table></div>"
            )
            continue

        # blockquote — collect, strip the markers, convert recursively.
        # A quote whose first line starts with the review marker (GH:) is a
        # reader's note, not manual prose: it gets its own class so it shouts.
        if line.startswith(">"):
            body = []
            while i < n and (lines[i].startswith(">") or
                             (lines[i].strip() and body and not _looks_like_block(lines[i]))):
                body.append(re.sub(r"^>\s?", "", lines[i]))
                i += 1
            joined = "\n".join(body)
            if REVIEW_MARK.match(joined.lstrip()):
                note_cb = heading_cb  # unused; kept for symmetry
                nid = f"ghnote-{len(_NOTES)+1}"
                _NOTES.append((nid, _first_words(joined)))
                out.append(f'<blockquote class="ghnote" id="{nid}">'
                           + convert(REVIEW_MARK.sub("", joined.lstrip(), count=1),
                                     link_rewriter)
                           + "</blockquote>")
            else:
                out.append("<blockquote>"
                           + convert(joined, link_rewriter)
                           + "</blockquote>")
            continue

        # list
        m = _LIST_ITEM.match(line)
        if m:
            block, i = _collect_list(lines, i)
            out.append(_render_list(block, link_rewriter))
            continue

        # blank
        if not line.strip():
            i += 1
            continue

        # paragraph — runs until a blank line or the start of another block
        para = [line]
        i += 1
        while i < n and lines[i].strip() and not _looks_like_block(lines[i]):
            para.append(lines[i])
            i += 1
        out.append(f"<p>{inline(' '.join(x.strip() for x in para), link_rewriter)}</p>")

    return "\n".join(out)


def _looks_like_block(line: str) -> bool:
    return bool(
        _HEADING.match(line)
        or _FENCE.match(line)
        or _LIST_ITEM.match(line)
        or line.startswith(">")
        or re.match(r"^\s*([-*_])\s*\1\s*\1[\s\-*_]*$", line)
    )


def _collect_list(lines: list[str], i: int):
    """Gather a list block: consecutive items plus their wrapped continuation
    lines. Returns [(indent, marker, text), ...]."""
    items = []
    n = len(lines)
    while i < n:
        m = _LIST_ITEM.match(lines[i])
        if m:
            indent = len(m.group(1))
            items.append([indent, m.group(2), m.group(3)])
            i += 1
            # continuation lines: indented, non-blank, not a new block
            while i < n and lines[i].strip() and not _looks_like_block(lines[i]):
                items[-1][2] += " " + lines[i].strip()
                i += 1
        elif not lines[i].strip():
            # a blank line ends the list unless another item follows
            if i + 1 < n and _LIST_ITEM.match(lines[i + 1]):
                i += 1
            else:
                break
        else:
            break
    return items, i


def _render_list(items, link_rewriter) -> str:
    """Render with indent-based nesting."""
    html_parts: list[str] = []
    stack: list[tuple[int, str]] = []   # (indent, tag)

    for indent, marker, text in items:
        tag = "ol" if marker[0].isdigit() else "ul"
        while stack and indent < stack[-1][0]:
            html_parts.append(f"</{stack.pop()[1]}>")
        if not stack or indent > stack[-1][0]:
            stack.append((indent, tag))
            html_parts.append(f"<{tag}>")
        html_parts.append(f"<li>{inline(text, link_rewriter)}</li>")

    while stack:
        html_parts.append(f"</{stack.pop()[1]}>")
    return "".join(html_parts)


# ── page assembly ────────────────────────────────────────────────────────────

CSS = """
:root{
  --ink:#1b2320; --ink-soft:#5a6763; --line:#dfe4e1;
  --bg:#ffffff; --surface:#f7f9f8; --accent:#6d8764; --accent-soft:#eef2ec;
  --code-bg:#f2f4f2;
}
@media (prefers-color-scheme: dark){
  :root{ --ink:#e6ebe8; --ink-soft:#9aa8a3; --line:#333c38;
         --bg:#161a18; --surface:#1e2422; --accent:#9ab88f;
         --accent-soft:#232b27; --code-bg:#202724; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;}
#layout{display:flex;align-items:flex-start}

/* sidebar */
#side{position:sticky;top:0;height:100vh;overflow-y:auto;flex:0 0 300px;
  background:var(--surface);border-right:1px solid var(--line);padding:16px 12px}
#side h1{font-size:15px;margin:0 0 4px;color:var(--accent)}
#side .sub{font-size:11px;color:var(--ink-soft);margin-bottom:12px}
#filter{width:100%;padding:6px 8px;border:1px solid var(--line);border-radius:6px;
  background:var(--bg);color:var(--ink);font-size:13px;margin-bottom:10px}
#toc a{display:block;text-decoration:none;color:var(--ink);padding:2px 6px;
  border-radius:4px;font-size:13px;line-height:1.4}
#toc a:hover{background:var(--accent-soft)}
#toc a.lvl1{font-weight:700;margin-top:10px;color:var(--accent)}
#toc a.lvl2{padding-left:16px;font-size:12.5px}
#toc a.lvl3{padding-left:30px;font-size:12px;color:var(--ink-soft)}
#toc a.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
#toc a.hide{display:none}

/* content */
main{flex:1 1 auto;max-width:60rem;padding:28px 40px 120px;min-width:0}
h1,h2,h3,h4{line-height:1.25;margin:1.6em 0 .5em}
h1{font-size:1.9rem;border-bottom:2px solid var(--accent);padding-bottom:.25em}
h2{font-size:1.35rem;border-bottom:1px solid var(--line);padding-bottom:.2em}
h3{font-size:1.1rem}
h4{font-size:1rem;color:var(--ink-soft)}
a{color:var(--accent)}
a.anchor{opacity:0;margin-left:-1em;padding-right:.3em;text-decoration:none;
  font-weight:400;color:var(--ink-soft)}
h1:hover .anchor,h2:hover .anchor,h3:hover .anchor{opacity:1}
code{background:var(--code-bg);padding:1px 5px;border-radius:4px;
  font:0.87em/1.5 Consolas,"SF Mono",Menlo,monospace}
pre{background:var(--code-bg);padding:12px 14px;border-radius:8px;overflow-x:auto;
  border:1px solid var(--line)}
pre code{background:none;padding:0}
blockquote{margin:1.2em 0;padding:.6em 1em;border-left:4px solid var(--accent);
  background:var(--accent-soft);border-radius:0 6px 6px 0}
blockquote > :first-child{margin-top:0} blockquote > :last-child{margin-bottom:0}
.table-wrap{overflow-x:auto;margin:1.1em 0}
table{border-collapse:collapse;width:100%;font-size:.92rem}
th,td{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
th{background:var(--accent-soft);font-weight:700}
tbody tr:nth-child(even){background:var(--surface)}
hr{border:0;border-top:1px solid var(--line);margin:2em 0}
.chapter{scroll-margin-top:12px}
pre.mermaid{background:none;border:1px dashed var(--line);text-align:center}

/* reader's notes — deliberately loud, and never mistakable for manual prose */
blockquote.ghnote{
  border-left:6px solid #d94f2b;background:#fff3ee;color:#3a1a10;
  position:relative;padding:1.1em 1em .7em;margin:1.4em 0;
  border-radius:0 8px 8px 0;box-shadow:0 1px 4px rgba(217,79,43,.18);
  scroll-margin-top:16px;
}
blockquote.ghnote::before{
  content:"GH";position:absolute;top:-10px;left:-6px;
  background:#d94f2b;color:#fff;font:700 10px/1 sans-serif;
  letter-spacing:.09em;padding:4px 7px;border-radius:4px;
}
blockquote.ghnote code{background:rgba(217,79,43,.13)}
blockquote.ghnote a{color:#a8331a}
@media (prefers-color-scheme: dark){
  blockquote.ghnote{background:#2e1710;color:#f6ddd4;
    border-left-color:#e26a47;box-shadow:none}
  blockquote.ghnote a{color:#f0a08a}
}
#notes{margin:0 0 12px;padding:8px;border:1px solid #d94f2b;border-radius:6px;
  background:rgba(217,79,43,.07)}
#notes b{display:block;font-size:11px;letter-spacing:.06em;color:#d94f2b;
  text-transform:uppercase;margin-bottom:4px}
#notes a{display:block;font-size:12px;color:var(--ink);text-decoration:none;
  padding:2px 4px;border-radius:3px;line-height:1.35}
#notes a:hover{background:rgba(217,79,43,.15)}

/* print → PDF */
@media print{
  #side{display:none}
  main{max-width:none;padding:0}
  body{font-size:10.5pt}
  .chapter{break-before:page}
  .chapter:first-of-type{break-before:auto}
  h1,h2,h3{break-after:avoid}
  table,pre,blockquote,.table-wrap{break-inside:avoid}
  a{color:inherit;text-decoration:none}
  a.anchor{display:none}
  thead{display:table-header-group}
}
@page{margin:16mm 14mm}
"""

JS = """
const toc=document.getElementById('toc');
const links=[...toc.querySelectorAll('a')];
document.getElementById('filter').addEventListener('input',e=>{
  const q=e.target.value.trim().toLowerCase();
  links.forEach(a=>a.classList.toggle('hide', q && !a.textContent.toLowerCase().includes(q)));
});
const heads=[...document.querySelectorAll('main h1,main h2,main h3')];
const byId=Object.fromEntries(links.map(a=>[a.getAttribute('href').slice(1),a]));
const obs=new IntersectionObserver(es=>{
  es.forEach(en=>{
    if(!en.isIntersecting) return;
    links.forEach(a=>a.classList.remove('on'));
    const a=byId[en.target.id];
    if(a){a.classList.add('on');
      if(a.offsetTop<toc.scrollTop||a.offsetTop>toc.scrollTop+toc.clientHeight-40)
        toc.scrollTop=a.offsetTop-toc.clientHeight/2;}
  });
},{rootMargin:'0px 0px -75% 0px'});
heads.forEach(h=>obs.observe(h));
"""


def build(manual_dir: Path, out: Path, mermaid: bool = True) -> Path:
    files = chapter_files(manual_dir)
    if not files:
        raise SystemExit(f"No .md files found in {manual_dir}")
    _NOTES.clear()

    # Map "other-chapter.md" → its chapter slug, so cross-file links become
    # in-page jumps. Anchors are namespaced per chapter because several
    # chapters share heading names ("Make the Blogs Clickable").
    chapter_slug = {p.name: p.stem.lower() for p in files}

    toc: list[tuple[int, str, str]] = []
    bodies: list[str] = []

    for p in files:
        cslug = chapter_slug[p.name]

        def rewrite(href, _c=cslug):
            if href.startswith(("http://", "https://", "mailto:")):
                return href
            if href.startswith("#"):
                return f"#{_c}--{href[1:]}"
            target, _, frag = href.partition("#")
            if target in chapter_slug:
                t = chapter_slug[target]
                return f"#{t}--{frag}" if frag else f"#{t}"
            return href      # ../ links stay relative to the manual folder

        def heading(level, text, _c=cslug):
            anchor = f"{_c}" if level == 1 else f"{_c}--{slugify(text)}"
            if level <= 3:
                toc.append((level, anchor, text))
            return anchor

        md = p.read_text(encoding="utf-8")
        bodies.append(f'<section class="chapter">'
                      + convert(md, rewrite, heading) + "</section>")

    toc_html = "".join(
        f'<a class="lvl{lvl}" href="#{anchor}">{html.escape(text)}</a>'
        for lvl, anchor, text in toc
    )

    notes_html = ""
    if _NOTES:
        items = "".join(f'<a href="#{nid}">{html.escape(preview)}</a>'
                        for nid, preview in _NOTES)
        notes_html = (f'<div id="notes"><b>{len(_NOTES)} note'
                      f'{"s" if len(_NOTES) != 1 else ""}</b>{items}</div>')

    init = ('<script>if(window.mermaid){mermaid.initialize({startOnLoad:true,'
            'theme:matchMedia("(prefers-color-scheme: dark)").matches?"dark":"neutral"});}'
            '</script>')
    if not mermaid:
        mermaid_tag = ""
    elif MERMAID_LOCAL.is_file():
        # Inlined: one file, no network, works on a plane.
        mermaid_tag = (f"<script>{MERMAID_LOCAL.read_text(encoding='utf-8')}</script>"
                       + init)
    else:
        mermaid_tag = f'<script src="{MERMAID_CDN}"></script>' + init

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SpeciesViewer — Reference Manual</title>
<style>{CSS}</style></head>
<body><div id="layout">
<nav id="side">
  <h1>SpeciesViewer</h1>
  <div class="sub">Reference Manual · built {date.today().isoformat()}</div>
  <input id="filter" type="search" placeholder="Filter sections…" autocomplete="off">
  {notes_html}
  <div id="toc">{toc_html}</div>
</nav>
<main>{''.join(bodies)}</main>
</div>
<script>{JS}</script>
{mermaid_tag}
</body></html>
"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


def vendor_mermaid(dest: Path = MERMAID_LOCAL) -> Path:
    """Download mermaid.js once, so later builds need no network.

    Stdlib only — this script stays dependency-free. Run it while you have
    internet; every build after that inlines the result."""
    from urllib.request import urlopen

    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Fetching {MERMAID_CDN} …")
    with urlopen(MERMAID_CDN, timeout=60) as r:
        data = r.read()
    text = data.decode("utf-8")
    if "mermaid" not in text[:4000] and "mermaid" not in text[-4000:]:
        raise SystemExit("That download does not look like mermaid.js — aborting.")
    if "</script" in text.lower():
        # Would break out of the inline <script> element it gets embedded in.
        raise SystemExit("Refusing to vendor: the file contains a closing script tag.")
    dest.write_text(text, encoding="utf-8")
    print(f"Vendored {len(data)/1024/1024:.1f} MB → {dest}")
    print("Builds from now on inline it — the page works with no network.")
    return dest


def extract_feedback(manual_dir: Path) -> int:
    """Print every review note as file:line + text, in reading order.

    The point of the marker is that the notes are findable without reading the
    manual again — this is that, and it is also what a reviewer hands back."""
    total = 0
    for p in chapter_files(manual_dir):
        lines = p.read_text(encoding="utf-8").split("\n")
        found: list[tuple[int, str]] = []
        i = 0
        in_fence = False
        while i < len(lines):
            # Skip fenced code — the convention is documented with an example
            # inside a fence, and that example is not a note.
            if lines[i].startswith("```"):
                in_fence = not in_fence
                i += 1
                continue
            if in_fence:
                i += 1
                continue
            stripped = re.sub(r"^>\s?", "", lines[i]) if lines[i].startswith(">") else None
            if stripped is not None and REVIEW_MARK.match(stripped.lstrip()):
                start = i + 1
                body = [REVIEW_MARK.sub("", stripped.lstrip(), count=1)]
                i += 1
                while i < len(lines) and lines[i].startswith(">"):
                    body.append(re.sub(r"^>\s?", "", lines[i]))
                    i += 1
                found.append((start, " ".join(" ".join(body).split())))
                continue
            i += 1
        if found:
            print(f"\n{p.relative_to(manual_dir.parent.parent)}")
            for ln, text in found:
                print(f"  {p.name}:{ln}")
                for chunk in _wrap(text, 76):
                    print(f"      {chunk}")
            total += len(found)
    print(f"\n{total} note{'s' if total != 1 else ''} found."
          if total else "No notes found (marker: '> GH: …').")
    return total


def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def main(argv=None):
    # A Windows console defaults to cp1252, which cannot encode the arrows and
    # dashes this script prints — and an UnicodeEncodeError on a status line
    # would abort a build that had already succeeded.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manual-dir", type=Path, default=MANUAL_DIR)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--no-mermaid", action="store_true",
                    help="omit the mermaid script tag (diagrams stay as code)")
    ap.add_argument("--open", action="store_true", help="open the result in a browser")
    ap.add_argument("--feedback", action="store_true",
                    help="list the review notes (> GH: …) with file:line, and exit")
    ap.add_argument("--vendor-mermaid", action="store_true",
                    help="download mermaid.js once so later builds need no network")
    a = ap.parse_args(argv)

    if a.feedback:
        extract_feedback(a.manual_dir)
        return 0

    if a.vendor_mermaid:
        vendor_mermaid()

    out = build(a.manual_dir, a.out, mermaid=not a.no_mermaid)
    n = len(chapter_files(a.manual_dir))
    size = out.stat().st_size / 1024
    note = f", {len(_NOTES)} note{'s' if len(_NOTES) != 1 else ''}" if _NOTES else ""
    print(f"Built {out}  ({n} chapters, {size:.0f} KB{note})")
    if not a.no_mermaid:
        print("Diagrams: " + ("inlined — works offline." if MERMAID_LOCAL.is_file()
                              else "loaded from a CDN — run --vendor-mermaid to go offline."))
    print("For a PDF: open it and print to PDF — each chapter starts a new page.")
    if a.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
