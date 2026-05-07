from datetime import datetime, timezone, timedelta
from tw_time import to_tw, tw_iso, tw_str


def test_to_tw_naive_utc_offsets_8h():
    dt = datetime(2026, 5, 7, 15, 30, 0)  # naive UTC
    result = to_tw(dt)
    assert result.hour == 23
    assert result.utcoffset() == timedelta(hours=8)


def test_to_tw_aware_utc_offsets_8h():
    dt = datetime(2026, 5, 7, 15, 30, 0, tzinfo=timezone.utc)
    result = to_tw(dt)
    assert result.hour == 23


def test_to_tw_handles_none():
    assert to_tw(None) is None


def test_tw_iso_includes_plus_8():
    dt = datetime(2026, 5, 7, 15, 30, 0)
    assert tw_iso(dt) == "2026-05-07T23:30:00+08:00"


def test_tw_iso_none_default_fallback_empty_str():
    assert tw_iso(None) == ""


def test_tw_iso_none_explicit_fallback():
    assert tw_iso(None, fallback=None) is None


def test_tw_str_default_fmt():
    dt = datetime(2026, 5, 7, 15, 30, 0)
    assert tw_str(dt) == "2026/05/07 23:30"


def test_tw_str_custom_fmt():
    dt = datetime(2026, 5, 7, 15, 30, 0)
    assert tw_str(dt, "%m%d") == "0507"


def test_tw_str_midnight_crosses_date():
    dt = datetime(2026, 5, 7, 17, 0, 0)  # 17:00 UTC = 01:00 next day TW
    assert tw_str(dt, "%Y-%m-%d") == "2026-05-08"
