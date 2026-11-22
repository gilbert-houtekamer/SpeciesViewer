"""tools/svr_duplicate_raws: only byte-identical RAWs in ONE folder are a group."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "svr_duplicate_raws", Path(__file__).resolve().parent.parent / "tools" / "svr_duplicate_raws.py")
dup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dup)


def _w(p: Path, data: bytes):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_groups_same_folder_identical_only(tmp_path):
    a = tmp_path / "aves" / "passer simplex"
    _w(a / "NA122724.ORF", b"raw-1" * 100)
    _w(a / "NA122724-2.ORF", b"raw-1" * 100)                 # copy
    _w(a / "2024 Marokko-NA122724.ORF", b"raw-1" * 100)      # second copy
    _w(a / "NA122725.ORF", b"raw-2" * 100)                   # same size, other bytes
    _w(a / "NA122724.XMP", b"raw-1" * 100)                   # not a RAW
    b = tmp_path / "aves" / "passer domesticus"
    _w(b / "NA122724.ORF", b"raw-1" * 100)                   # other species: left alone
    groups = dup.identical_groups(tmp_path)
    assert len(groups) == 1
    names = [p.name for p in groups[0]]
    assert names[0] == "NA122724.ORF"                        # shortest name first
    assert sorted(names[1:]) == ["2024 Marokko-NA122724.ORF", "NA122724-2.ORF"]


def test_rank_prefers_page_then_stars_then_plain_name():
    base = {"best": False, "on_page": [], "stars": 0, "jpgs": [], "dop": False, "files": ["x"]}
    plain = dict(base, path=Path("N1.ORF"))
    long_ = dict(base, path=Path("2020 Engeland N1.ORF"))
    assert dup._rank(plain) > dup._rank(long_)
    starred = dict(long_, stars=2)
    assert dup._rank(starred) > dup._rank(plain)
    on_page = dict(plain, on_page=["N1_DxO.jpg"], jpgs=["N1_DxO.jpg"])
    assert dup._rank(on_page) > dup._rank(starred)
