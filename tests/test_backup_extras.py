"""master_health.backup_extras: the small only-copy files go into the daily backup."""
import gzip

from db.master_health import backup_extras, EXTRA_DIR


def test_extras_gzipped_missing_ones_skipped(tmp_path):
    master = tmp_path / "naturedb"
    (master / "admin").mkdir(parents=True)
    (master / "metadata" / "active").mkdir(parents=True)
    (master / "admin" / "trip_admin.csv").write_text("trip;root\nkreta;x\n", encoding="utf-8")
    (master / "admin" / "TransCache.csv").write_text("a;b\n", encoding="utf-8")
    (master / "metadata" / "active" / "speciesnames.csv").write_text("n\n", encoding="utf-8")
    dest = tmp_path / "backup" / "2026-11-22"

    got = backup_extras(master, dest)

    assert set(got) == {"trip_admin.csv", "TransCache.csv", "speciesnames.csv"}
    with gzip.open(dest / EXTRA_DIR / "trip_admin.csv.gz", "rt", encoding="utf-8") as fh:
        assert fh.read() == "trip;root\nkreta;x\n"
    # the sources are left exactly as they were
    assert (master / "admin" / "trip_admin.csv").read_text(encoding="utf-8") == "trip;root\nkreta;x\n"


def test_no_extras_writes_nothing(tmp_path):
    dest = tmp_path / "backup" / "day"
    assert backup_extras(tmp_path / "empty", dest) == {}
    assert not (dest / EXTRA_DIR).exists()
