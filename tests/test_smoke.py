"""Smoke test: does SpeciesViewer hold together? Run from the repository root:

    python -m pytest

Quick (seconds), needs no master, no pictures, no internet and no screen. It
catches the mistakes that break everything at once: a module that no longer
imports (a deleted helper, a typo, a missing dependency), a broken version
string, and missing or damaged files that ship with the program. The deeper
self-tests live in tools/ — see CONTRIBUTING.md.
"""
import csv
import importlib
import re
from pathlib import Path

import pytest

from conftest import REPO, SANDBOX, SV

# Not part of the program: one-off scripts, parked future code, and data.
_SKIP_DIRS = {"one-off", "batch-future", "__pycache__", "assets", "dist", "HTMLtemplates"}
STARTER = SV / "dist" / "installation files" / "initial_speciescollection.csv"


def _program_modules():
    for path in sorted(SV.rglob("*.py")):
        rel = path.relative_to(SV)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        parts = rel.with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            yield ".".join(parts)


def test_settings_are_isolated():
    """Guard for every other test: the app-data folder is the throw-away one."""
    from utils.cache_paths import app_data_dir
    assert SANDBOX in app_data_dir().parents


def test_every_module_imports():
    modules = list(_program_modules())
    assert len(modules) > 100, "the module scan found too few files"
    failures = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as e:        # report them all, not only the first
            failures.append(f"{name}: {type(e).__name__}: {e}")
    assert not failures, "modules that do not import:\n" + "\n".join(failures)


def test_version_is_sane():
    from utils.version import APP_NAME, __version__
    assert APP_NAME == "SpeciesViewer"
    assert re.fullmatch(r"\d+\.\d+\.\d+(\.dev)?", __version__), __version__


@pytest.mark.parametrize("name", ["LICENSE", "NOTICE.md", "README.md"])
def test_public_files_present(name):
    text = (REPO / name).read_text(encoding="utf-8")
    assert len(text) > 500, f"{name} is (nearly) empty"


def test_shipped_assets_present():
    assets = SV / "assets"
    for size in (16, 32, 48, 256):
        assert (assets / "icon" / f"speciesviewer_{size}.png").is_file()
    assert (assets / "icon" / "speciesviewer.ico").is_file()
    assert list((assets / "splash").glob("*.jpg")), "no splash pictures"
    assert (assets / "about_duck.jpg").is_file()
    assert (REPO / "tools" / "Read me first.txt").is_file()


def _starter_rows():
    with open(STARTER, encoding="utf-8", newline="") as f:
        return list(csv.reader(f, delimiter=";"))


def test_starter_species_list_is_a_tree():
    """A new master starts from this list: it must parse, and every row's
    parent ('Up') must itself be in the list."""
    rows = _starter_rows()
    assert rows[0] == ["Level", "ScientificName", "DutchName", "EnglishName", "Up", "Leaves",
                       "Sequence", "SubspeciesOf"]
    body = rows[1:]
    assert len(body) > 1000
    assert all(len(r) == 8 for r in body), "a row without exactly 8 fields"
    names = {r[1] for r in body}
    orphans = [r[1] for r in body if r[4] and r[4] not in names]
    assert not orphans, f"rows whose parent is missing: {orphans[:10]}"
    numbered = [r[1] for r in body if r[1][:1].isdigit() and r[0] != "Level.SPECIES"]
    assert not numbered, f"old numbered names ('040 passeriformes'): {numbered[:10]}"


def test_starter_aliases_point_into_the_list():
    """The alias table shipped beside the list: every alias names a list entry."""
    names = {r[1] for r in _starter_rows()[1:]}
    with open(STARTER.with_name("speciesnames.csv"), encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    assert rows[0] == ["Name", "Language", "PointsTo", "Kind", "Source"]
    stray = [r for r in rows[1:] if r[2] not in names]
    assert not stray, stray[:10]


def test_starter_species_list_has_no_garbled_names():
    """UTF-8 read as Windows-1252 leaves 'â€™' and 'Ã' behind."""
    garbled = [r[1:4] for r in _starter_rows()
               if any(bad in ";".join(r) for bad in ("â€", "Ã", "\ufffd"))]
    assert not garbled, garbled


def test_templates_link_with_forward_slashes():
    """A web address uses '/'; a Windows '\' in a template link breaks the
    tile pictures on some browsers and every server (found 2026-10-18)."""
    link = re.compile(r'(?:href|src)\s*=\s*"([^"]*)"')
    bad = [(path.name, m.group(1))
           for path in sorted((SV / "HTMLtemplates").glob("*.html"))
           for m in link.finditer(path.read_text(encoding="utf-8", errors="replace"))
           if "\\" in m.group(1)]
    assert not bad, bad[:10]
