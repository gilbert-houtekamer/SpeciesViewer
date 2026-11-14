"""Remove GPS + place: clears exactly what paste GPS + place writes, and reports
honestly. Runs the real exiftool on a temp sidecar; skipped when it is absent.
The master mirror is a no-op here (the sandbox config names no master)."""

import shutil

import pytest


def _have_exiftool():
    try:
        from engine.exif_writer import exiftool_exe
        exe = exiftool_exe()
        return bool(exe) and (shutil.which(exe) or __import__("os").path.exists(exe))
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _have_exiftool(), reason="exiftool not installed")


def _blank_xmp(path):
    path.write_text(
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
        'xmp:Rating="3"/></rdf:RDF></x:xmpmeta>\n<?xpacket end="w"?>',
        encoding="utf-8")


def test_remove_clears_pasted_gps_and_place(tmp_path):
    from gui.geolocate import (write_gps, write_location_batch, batch_gps,
                               read_location_fields, remove_gps_and_place)
    xmp = tmp_path / "P1010001.xmp"
    _blank_xmp(xmp)
    assert write_gps([xmp], -18.766605, 47.398152) == 1
    write_location_batch([str(xmp)], {"Location": "Tsimahandry", "City": "Antananarivo",
                                      "Country": "Madagascar"}, overwrite=True)
    assert batch_gps([xmp])                                    # it is there
    assert read_location_fields(xmp).get("Location") == "Tsimahandry"

    cleared, still = remove_gps_and_place([xmp])

    assert cleared == [str(xmp)] and still == []
    assert batch_gps([xmp]) == {}
    loc = read_location_fields(xmp)
    assert not any(loc.get(k) for k in ("Location", "City", "Country"))
    assert "Rating" in xmp.read_text(encoding="utf-8")         # the rest stays


def test_nothing_to_remove_is_not_an_error(tmp_path):
    from gui.geolocate import remove_gps_and_place
    xmp = tmp_path / "P1010002.xmp"
    _blank_xmp(xmp)
    cleared, still = remove_gps_and_place([xmp])
    assert still == []
    assert remove_gps_and_place([]) == ([], [])
