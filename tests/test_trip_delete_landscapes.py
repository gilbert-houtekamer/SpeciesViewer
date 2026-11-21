"""Delete Local Files can keep landscapes (backlog 1.7): a picture the master
files under topics/ is flagged, and execute_scan_delete skips it when asked.
Everything happens in tmp_path."""

import engine.trip_delete_scan as tds


def _setup(tmp_path, monkeypatch):
    master = tmp_path / "master"
    bird = master / "biota" / "aves" / "pica pica" / "DSC_0001.NEF"
    land = master / "topics" / "landscape" / "2026" / "DSC_0002.NEF"
    for f in (bird, land):
        f.parent.mkdir(parents=True)
        f.write_bytes(b"x" * 10)
    day = tmp_path / "pics" / "2026-10-10"
    day.mkdir(parents=True)
    for n in ("DSC_0001.NEF", "DSC_0002.NEF"):
        (day / n).write_bytes(b"x" * 10)
    index = {"by_name": {"dsc_0001.nef": str(bird), "dsc_0002.nef": str(land)},
             "by_upn": {}}
    upns = {"DSC_0001", "DSC_0002"}
    monkeypatch.setattr(tds, "load_master_inventory",
                        lambda _m: (upns, set(index["by_name"]), index))
    monkeypatch.setattr("engine.filecsv.deriveUniquePictureName",
                        lambda fn: fn.rsplit(".", 1)[0])
    return tds.scan_trip(str(tmp_path / "pics"), str(master), ""), day


def test_landscape_is_flagged_and_counted(tmp_path, monkeypatch):
    report, _ = _setup(tmp_path, monkeypatch)
    assert report["totals"]["deletable_files"] == 2
    assert report["totals"]["landscape_pictures"] == 1
    assert tds.deletable_count(report, keep_landscapes=True) == (1, 1)
    assert tds.deletable_count(report, keep_landscapes=False) == (2, 2)


def test_keep_landscapes_leaves_them_on_disk(tmp_path, monkeypatch):
    report, day = _setup(tmp_path, monkeypatch)
    nf, npx, errs = tds.execute_scan_delete(report, keep_landscapes=True)
    assert (nf, npx, errs) == (1, 1, [])
    assert not (day / "DSC_0001.NEF").exists()
    assert (day / "DSC_0002.NEF").exists()


def test_without_the_tick_landscapes_go_too(tmp_path, monkeypatch):
    report, day = _setup(tmp_path, monkeypatch)
    assert tds.execute_scan_delete(report)[0] == 2
    assert not (day / "DSC_0002.NEF").exists()
