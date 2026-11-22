"""engine.leftover_cleanup: move only real leftovers, restore exactly them."""
import os
import time

from engine import leftover_cleanup as lc


def _touch(p, text="x", age_days=10):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    t = time.time() - age_days * 86400
    os.utime(p, (t, t))
    return p


def _master(tmp_path):
    m = tmp_path / "naturedb"
    rv = m / "metadata" / "review"
    _touch(rv / "2024 Marokko" / "species.csv")
    _touch(rv / "2024 Marokko" / "frozen" / "biota" / "aves" / "a.jpg", "jpg")
    _touch(rv / "2024 Marokko" / "statemachine.ini",
           "[state]\nsv6freeze = True\nsv7writecommit = True\n")
    _touch(rv / "oman" / "frozen" / "biota" / "b.jpg")
    _touch(rv / "oman" / "statemachine.ini",
           "[state]\nsv6freeze = True\nsv7writecommit = False\n")      # unfinished
    _touch(rv / "2026 Brazil" / "frozen" / "biota" / "c.jpg", age_days=0)  # recent
    _touch(rv / "sv_droppool_abc" / "d.jpg")
    _touch(rv / "master_rebuild" / "frozen" / "biota" / "e.html")
    st = m / "admin" / "status"
    _touch(st / "review_20261017-132700" / "r.csv")
    _touch(st / "review_20261121-181727" / "r.csv")
    _touch(st / "preview_20260711-190526" / "p.csv")
    _touch(m / "admin" / "trip_admin.csv")
    return m


def test_plan_takes_only_leftovers(tmp_path):
    m = _master(tmp_path)
    items, kept = lc.plan(m)
    got = {it["path"].relative_to(m).as_posix() for it in items}
    assert got == {
        "metadata/review/2024 Marokko/frozen/biota",
        "metadata/review/sv_droppool_abc",
        "admin/status/review_20261017-132700",
        "admin/status/preview_20260711-190526",
    }
    assert any("oman" in k for k in kept)
    assert any("review_20261121-181727" in k for k in kept)
    assert any("2026 Brazil" in k for k in kept)


def test_apply_moves_and_restore_puts_back(tmp_path):
    m = _master(tmp_path)
    items, _ = lc.plan(m)
    dest, n, _size = lc.apply(m, items)
    assert n == 4
    assert not (m / "metadata/review/2024 Marokko/frozen/biota").exists()
    assert (m / "metadata/review/2024 Marokko/species.csv").exists()      # list stays
    assert (dest / "metadata/review/2024 Marokko/frozen/biota/aves/a.jpg").read_text() == "jpg"

    done, problems = lc.restore(m, lc.batches(m)[-1])
    assert (done, problems) == (4, [])
    assert (m / "metadata/review/2024 Marokko/frozen/biota/aves/a.jpg").read_text() == "jpg"


def test_empty_deletes_only_the_leftover_trash(tmp_path):
    m = _master(tmp_path)
    items, _ = lc.plan(m)
    lc.apply(m, items)
    assert lc.empty(m) > 0
    assert not lc.trash_root(m).exists()
    assert (m / "admin" / "trip_admin.csv").exists()
