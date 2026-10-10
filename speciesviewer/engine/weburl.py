"""The public URL of a published page — one builder, one convention.

The site address was being assembled in three places that each knew a little of it:
`breadcrumb.py:39` (web root + TargetPath + name + '-nl.html'),
`regenerate_species.py:180` (same, per language), and
the Master Species Viewer's regenerate handler (base_url normalisation +
percent-encoding; since the viewer merge it lives in `rating_editor.py`).
None of them could produce a whole clickable URL on its own, so anything outside the
GUI — a report, a worklist, a CSV a human has to act on — had no way to say "here is
the page". This module is that missing piece, and the place the convention now lives.

Percent-encoding is not optional here: every species folder on this site contains
spaces ('006 galliformes'), and a raw space makes a URL that some clients follow and
others silently truncate.
"""

from __future__ import annotations

from urllib.parse import quote

from utils.config import _config


def site_base() -> str:
    """The site root, e.g. 'https://example.org' — no trailing slash.

    Same normalisation the master species viewer does: accept a bare host in the
    config and still emit a real URL."""
    from utils import site_config
    return site_config.site_base()


def web_root() -> str:
    """The master's web root, e.g. '/nature/' — leading and trailing slash."""
    root = _config.get("master", "masterbiotawebroot", fallback="/nature/").strip()
    if not root.startswith("/"):
        root = "/" + root
    if not root.endswith("/"):
        root = root + "/"
    return root


def species_page_path(target_path: str, sciname: str, lang: str = "nl") -> str:
    """Site-relative path of a species page: '/nature/biota/…/<name>-nl.html'.

    `target_path` is the species' TargetPath from the master (it already carries its
    trailing slash, but old rows and hand-edited ones do not always, and a Windows
    backslash shows up often enough to be worth normalising here rather than in each
    caller)."""
    rel = (target_path or "").replace("\\", "/").lstrip("/")
    if rel and not rel.endswith("/"):
        rel += "/"
    return f"{web_root()}{rel}{sciname}-{lang}.html"


def species_page_url(target_path: str, sciname: str, lang: str = "nl") -> str:
    """Full clickable URL of a species page, percent-encoded."""
    return site_base() + quote(species_page_path(target_path, sciname, lang), safe="/")
