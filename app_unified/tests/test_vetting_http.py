"""Tests for vetting/http_client.py — 真人 headers + politeness helpers."""
import pytest
from vetting.http_client import make_session, BROWSER_HEADERS, throttle_sleep


def test_make_session_has_browser_headers():
    s = make_session()
    assert "Mozilla/5.0" in s.headers["User-Agent"]
    assert "zh-TW" in s.headers["Accept-Language"]
    assert "python-requests" not in s.headers["User-Agent"]


def test_browser_headers_include_essentials():
    for k in ("User-Agent", "Accept", "Accept-Language", "Accept-Encoding"):
        assert k in BROWSER_HEADERS


def test_throttle_sleep_in_range(monkeypatch):
    """節流 sleep 應該落在 0.5~1.2 秒區間"""
    captured = []
    monkeypatch.setattr("vetting.http_client.time.sleep", lambda s: captured.append(s))
    for _ in range(10):
        throttle_sleep()
    assert all(0.5 <= s <= 1.2 for s in captured)
    assert len(captured) == 10
