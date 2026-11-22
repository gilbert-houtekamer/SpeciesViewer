"""engine.backup_copies: only old data_files / data_pictures copies go."""
from engine.backup_copies import plan, apply


def _touch(p, text="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_keeps_newest_per_kind_and_folder_and_never_species(tmp_path):
    act = tmp_path / "metadata" / "active" / "backup"
    st = tmp_path / "admin" / "status" / "backup"
    for d in ("2025-07-06", "2025-07-08", "2026-05-01", "2026-06-20"):
        _touch(act / f"{d}_data_files.csv")
        _touch(act / f"{d}_data_pictures.csv")
        _touch(act / f"{d}_species.csv")
        _touch(act / f"{d}_speciescollection.csv")
    _touch(act / "2026-11-21" / "data_files.csv.gz")          # a daily backup folder
    _touch(st / "data_files.20260519-231616.csv")
    _touch(st / "20261018-114442_data_files.csv")
    _touch(st / "data_files.20261121-183014.csv")
    _touch(st / "data_files.20261017-132847.csv")
    _touch(st / "20261121-184907_species.csv")

    gone = {it["path"].relative_to(tmp_path).as_posix() for it in plan(tmp_path, keep=3)}

    assert gone == {
        "metadata/active/backup/2025-07-06_data_files.csv",
        "metadata/active/backup/2025-07-06_data_pictures.csv",
        "admin/status/backup/data_files.20260519-231616.csv",
    }


def test_apply_removes_exactly_the_plan(tmp_path):
    act = tmp_path / "metadata" / "active" / "backup"
    for d in ("2025-01-01", "2025-02-01"):
        _touch(act / f"{d}_data_files.csv", "abc")
    items = plan(tmp_path, keep=1)
    assert apply(items) == (1, 3)
    assert sorted(p.name for p in act.iterdir()) == ["2025-02-01_data_files.csv"]


def test_nothing_to_do(tmp_path):
    assert plan(tmp_path) == []


def test_evidence_protected_until_archived(tmp_path):
    from engine.backup_copies import archive_evidence, archive_dir
    act = tmp_path / "metadata" / "active" / "backup"
    for d in ("2026-06-20", "2026-07-07", "2026-10-01", "2026-11-01", "2026-11-21"):
        _touch(act / f"{d}_data_files.csv", f"rows of {d}\n")
    # keep=1: four would go, but the two in the window are not archived yet
    gone = {it["path"].name for it in plan(tmp_path, keep=1)}
    assert gone == {"2026-10-01_data_files.csv", "2026-11-01_data_files.csv"}

    n, _size, problems = archive_evidence(tmp_path, progress=lambda _m: None)
    assert (n, problems) == (2, [])
    import gzip
    with gzip.open(archive_dir(tmp_path) / "2026-06-20_data_files.csv.gz", "rt") as fh:
        assert fh.read() == "rows of 2026-06-20\n"

    gone = {it["path"].name for it in plan(tmp_path, keep=1)}
    assert "2026-06-20_data_files.csv" in gone and len(gone) == 4
