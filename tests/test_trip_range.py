"""Trip dates vs. the days a trip has read: a planned range wider than the days
is fine; a day outside it is found, and the proposal only ever widens."""

from engine.trip_range import check_range, trip_days, set_trip_dates


def test_wider_planned_range_is_fine():
    # Madagascar: planned to 12 Nov, home early on 7 Nov
    assert check_range("2026-10-30", "2026-11-12", ["2026-10-30", "2026-11-07"]) is None
    assert check_range("2026-10-30", "2026-11-12", []) is None


def test_day_outside_widens_and_keeps_the_plan():
    got = check_range("2026-10-30", "2026-11-12", ["2026-10-29", "2026-11-01"])
    assert got["outside"] == ["2026-10-29"]
    assert got["widened"] == ("2026-10-29", "2026-11-12")
    assert got["days_only"] is None              # some days are inside: no narrowing


def test_range_missing_every_day_also_offers_the_days():
    # QA trip "MS": created with future default dates
    got = check_range("2026-11-28", "2026-12-12",
                      ["2026-10-30", "2026-10-31", "2026-11-01"])
    assert got["widened"] == ("2026-10-30", "2026-12-12")
    assert got["days_only"] == ("2026-10-30", "2026-11-01")


def test_no_stored_range():
    got = check_range("", "", ["2026-08-14", "2026-08-16"])
    assert got["widened"] == ("2026-08-14", "2026-08-16")


def test_days_are_read_days_and_dates_written_in_place(tmp_path):
    review = tmp_path / "metadata" / "review"
    (review / "2026-08-14").mkdir(parents=True)
    (review / "2026-08-14" / "data_files.csv").write_text("x", encoding="utf-8")
    (review / "2026-08-15").mkdir()                 # not read yet: not counted
    (review / "notaday").mkdir()
    assert trip_days(tmp_path) == ["2026-08-14"]

    ini = tmp_path / "trip.ini"
    ini.write_text("[trip_config]\n; a comment\nfromdate = 2026-11-28\n"
                   "todate = 2026-12-12\n\n[trip_text]\ntitle_nl = Zuid\n", encoding="utf-8")
    assert set_trip_dates(tmp_path, "2026-08-14", "2026-12-12")
    text = ini.read_text(encoding="utf-8")
    assert "fromdate = 2026-08-14" in text and "todate = 2026-12-12" in text
    assert "; a comment" in text and "title_nl = Zuid" in text
