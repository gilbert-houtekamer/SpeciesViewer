"""The camera-clock check compares with this computer's zone ON the pictures'
dates: August pictures read in November are summer time, not a wrong clock."""
from datetime import datetime, timedelta

from gui.sdcard_read import computer_offset_on


def _amsterdam(d: datetime) -> timedelta:      # summer +2 from April to October
    return timedelta(hours=2 if 4 <= d.month <= 10 else 1)


def test_summer_pictures_get_summer_time_whenever_they_are_read():
    tz = computer_offset_on(["2023-08-03", "2023-08-04"], _amsterdam)
    assert tz.utcoffset(None) == timedelta(hours=2)
    tz = computer_offset_on(["2024-01-10"], _amsterdam)
    assert tz.utcoffset(None) == timedelta(hours=1)


def test_a_card_across_the_change_goes_with_most_days_and_skips_unknown():
    tz = computer_offset_on(["2023-10-30", "2023-10-31", "2023-11-01", "unknown"],
                            _amsterdam)
    assert tz.utcoffset(None) == timedelta(hours=2)
    assert computer_offset_on(["unknown"], _amsterdam) is None


def test_the_real_computer_zone_on_a_date():
    d = datetime(2023, 8, 3, 12)
    assert (computer_offset_on(["2023-08-03"]).utcoffset(None)
            == d.astimezone().utcoffset())
