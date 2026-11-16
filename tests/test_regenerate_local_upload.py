"""Regenerate species page → a website on THIS computer (2026-11-16).

The picture upload counted what it sent with Sftp.FTPmisses, which the local
website (LocalSite) doesn't have — so every regenerate to a local site crashed
after the page and before its pictures, and the page showed a broken image.
"""
from engine import regenerate_species as rs
from engine.localsite import LocalSite


class _Project:
    def __init__(self, d):
        self._d = d

    def getBiotaDir(self):
        return str(self._d)


def test_pictures_reach_a_local_website(tmp_path, monkeypatch):
    src = tmp_path / "master" / "a.jpg"
    src.parent.mkdir()
    src.write_bytes(b"jpg")
    site = tmp_path / "site"
    monkeypatch.setattr(rs, "_pictures_the_page_links", lambda web_dir, tp: ["a.jpg"])
    monkeypatch.setattr(rs, "_master_source", lambda *a: str(src))

    local = LocalSite(site)
    toweb = local.remotePrefix() + "nature/biota/x/"
    sent = rs._upload_species_pictures(_Project(tmp_path), None, local, toweb, "x/", None)
    assert sent == 1
    assert rs._upload_species_pictures(_Project(tmp_path), None, local, toweb, "x/", None) == 0
