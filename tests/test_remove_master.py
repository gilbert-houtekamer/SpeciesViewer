"""Remove Master zips a master's settings, not its local website (2026-10-18).

The website on this computer is a copy the collection publishes again; for
Taxonomy 3 it made a 4 GB zip and minutes of waiting. Runs in the test
sandbox (conftest): masters.json and the trash are throw-away.
"""
import zipfile

from utils import masters


def test_remove_zips_settings_but_not_the_website():
    entry = masters.create("Remove me")
    folder = masters.folder_of(entry)
    (folder / "config").mkdir(parents=True)
    (folder / "config" / "sv_config.ini").write_text("[master]\n", encoding="utf-8")
    (folder / "website" / "nature").mkdir(parents=True)
    (folder / "website" / "nature" / "big.jpg").write_bytes(b"x" * 1000)

    zip_path = masters.remove("Remove me")

    names = zipfile.ZipFile(zip_path).namelist()
    assert "config/sv_config.ini" in names
    assert not [n for n in names if n.startswith("website")], names
    assert not folder.exists()
    assert "Remove me" not in [m["name"] for m in masters.load()["masters"]]
