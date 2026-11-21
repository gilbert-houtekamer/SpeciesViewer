"""Where the reference manual's one-page HTML is.

ONE job: return the path of SpeciesViewer_Manual.html for Help ▸ Reference
Manual, or None.

  - Shipped program: <app>/_internal/manual/ (put there by tools/SpeciesViewer.spec).
  - Source checkout: docs/manual/, built by tools/svr_manual_build.py — rebuilt
    here first when any chapter is newer than the page, so a developer never
    reads a stale manual.

INVARIANT: read-only towards the user's data; the only file it may write is the
git-ignored manual page in a source checkout.
"""

import importlib.util
import logging
from pathlib import Path

NAME = "SpeciesViewer_Manual.html"
logger = logging.getLogger("speciesviewer")


def _app_dir() -> Path:
    """speciesviewer/ in a checkout, _internal/ in the shipped build."""
    return Path(__file__).resolve().parent.parent


def _stale(page: Path, manual_dir: Path) -> bool:
    if not page.is_file():
        return True
    built = page.stat().st_mtime
    return any(md.stat().st_mtime > built for md in manual_dir.glob("*.md"))


def _build(tool: Path, manual_dir: Path, page: Path) -> None:
    spec = importlib.util.spec_from_file_location("svr_manual_build", tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.build(manual_dir, page)


def manual_html(build_if_stale: bool = True) -> Path | None:
    shipped = _app_dir() / "manual" / NAME
    if shipped.is_file():
        return shipped
    repo = _app_dir().parent
    manual_dir = repo / "docs" / "manual"
    page = manual_dir / NAME
    tool = repo / "tools" / "svr_manual_build.py"
    if build_if_stale and tool.is_file() and manual_dir.is_dir() and _stale(page, manual_dir):
        try:
            _build(tool, manual_dir, page)
        except Exception as e:          # an old page is better than none
            logger.warning(f"Could not rebuild the reference manual: {e}")
    return page if page.is_file() else None
