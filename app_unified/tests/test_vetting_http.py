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


# ─────────────────────────────────────────────────────────────
# DNS 瞬斷重試（2026-09-07 事故：prod 容器 EAI_AGAIN 間歇性解析失敗，
# 使用者看到「警政署系統暫時無法連線」。session 原本 0 重試，一個瞬斷就中斷查詢。）
# ─────────────────────────────────────────────────────────────
import socket

import requests


def _count_getaddrinfo_calls(monkeypatch, exc):
    """讓 getaddrinfo 一律拋 exc，回傳呼叫次數的 list（每次 attempt 一次）。"""
    calls = []

    def boom(*a, **kw):
        calls.append(a)
        raise exc

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    return calls


def test_dns_failure_is_retried(monkeypatch):
    """EAI_AGAIN（-3, Temporary failure in name resolution）要重試，不是一次就放棄。

    這是 2026-09-07 prod 事故的實際錯誤：叢集 DNS 間歇沒回應，
    urllib3 預設 0 重試 → 使用者直接看到查詢失敗。
    """
    calls = _count_getaddrinfo_calls(
        monkeypatch, socket.gaierror(-3, "Temporary failure in name resolution"))
    s = make_session()
    with pytest.raises(requests.exceptions.ConnectionError):
        s.get("https://eze8.npa.gov.tw/NpaE8ServerRWD/CE_Query.jsp", timeout=1)
    assert len(calls) >= 3, f"DNS 只嘗試了 {len(calls)} 次，沒有重試"


def test_dns_failure_is_retried_for_post(monkeypatch):
    """POST 也要重試 —— DNS 失敗時請求根本沒送出去，重送不會造成重複查詢。"""
    calls = _count_getaddrinfo_calls(
        monkeypatch, socket.gaierror(-3, "Temporary failure in name resolution"))
    s = make_session()
    with pytest.raises(requests.exceptions.ConnectionError):
        s.post("https://eze8.npa.gov.tw/NpaE8ServerRWD/doCEQuery",
               data={"QS_NAME": "x"}, timeout=1)
    assert len(calls) >= 3, f"POST 的 DNS 只嘗試了 {len(calls)} 次，沒有重試"


def test_read_errors_are_not_retried():
    """讀取逾時**不可**重試：請求已經送到 NPA，重送等於重複送出查詢／驗證碼。

    urllib3 的 increment() 對連線錯誤不檢查 method（安全，因為沒送出去），
    但讀取錯誤會走 `read is False → reraise` 這條，所以 read 必須是 False。
    """
    s = make_session()
    for prefix in ("https://", "http://"):
        retries = s.get_adapter(prefix + "x").max_retries
        assert retries.read is False, f"{prefix} 的 read 重試沒有關掉：{retries.read!r}"
        assert retries.connect and retries.connect > 0, \
            f"{prefix} 沒有設定連線重試：{retries.connect!r}"
