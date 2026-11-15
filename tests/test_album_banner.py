"""Banner: a 2000:270 band, kept inside the picture, written as tripbanner.jpg
with the ini pointing at it and the previous banner kept."""
from PIL import Image

from utils.album_banner import BANNER_NAME, banner_box, output_size, set_banner


def test_band_has_banner_shape_and_stays_inside():
    l, t, r, b = banner_box(6000, 4000, 0.5, 0.5, 1.0)
    assert (l, r) == (0, 6000) and abs((r - l) / (b - t) - 2000 / 270) < 0.02
    l, t, r, b = banner_box(6000, 4000, 0.0, 1.0, 0.5)        # pushed into a corner
    assert l == 0 and b == 4000 and r == 3000
    assert output_size((0, 0, 6000, 810)) == (2000, 270)
    assert output_size((0, 0, 1000, 135))[0] == 1000            # never enlarged


def test_band_narrows_for_a_very_wide_picture():
    l, t, r, b = banner_box(10000, 500, 0.5, 0.5, 1.0)
    assert b - t == 500 and r - l < 10000


def test_set_banner_writes_name_ini_and_keeps_old(tmp_path):
    (tmp_path / "virttrip.ini").write_text(
        "[trip_config]\ntrip_banner = my own.jpg\n\n[trip_text]\ntitle_nl = X\n",
        encoding="utf-8")
    (tmp_path / BANNER_NAME).write_bytes(b"an earlier banner")
    img = Image.new("RGB", (3000, 2000), "olive")
    out = set_banner(img, banner_box(3000, 2000, 0.5, 0.3, 1.0), tmp_path,
                     ini_name="virttrip.ini")
    assert Image.open(out).size == (2000, 270)
    assert "trip_banner = tripbanner.jpg" in (tmp_path / "virttrip.ini").read_text("utf-8")
    kept = [p for p in tmp_path.glob("tripbanner-*.jpg")]
    assert len(kept) == 1 and kept[0].read_bytes() == b"an earlier banner"
