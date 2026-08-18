"""把診斷事件送到 monitor。

這段程式碼在 hub_watchdog 的**真 OS thread** 上執行，而且是在 gevent hub
已經被卡住的當下。兩個死規則：
  1. 只能用 unpatched socket。碰到被 monkey patch 的 socket 就會踩到
     "crossing thread boundaries is not allowed"（prod 2026-05-09 實錄）。
  2. 絕不能拋例外或卡住。監控失敗不該影響服務。
"""
import time

from tests._monitor_capture import _capture_server


def test_ships_event_with_bearer_token(monkeypatch):
    import monitor_ship
    port, received = _capture_server()
    monkeypatch.setenv("MONITOR_INGEST_URL", f"http://127.0.0.1:{port}/ingest")
    monkeypatch.setenv("MONITOR_INGEST_TOKEN", "tok-123")

    ok = monitor_ship.ship_event("hub_block", "hub blocked 42.0s",
                                 "stack: face_locations")
    assert ok is True

    for _ in range(50):
        if received:
            break
        time.sleep(0.05)
    head, body = received[0]
    assert "POST /ingest HTTP/1.1" in head
    assert "Authorization: Bearer tok-123" in head
    assert "hub blocked 42.0s" in body
    assert "face_locations" in body


def test_returns_false_when_not_configured(monkeypatch):
    """沒設定就安靜跳過 —— 本機開發不該因此噴錯。"""
    import monitor_ship
    monkeypatch.delenv("MONITOR_INGEST_URL", raising=False)
    assert monitor_ship.ship_event("hub_block", "x") is False


def test_never_raises_when_monitor_is_down(monkeypatch):
    """monitor 掛掉時 app-unified 必須毫髮無傷。"""
    import monitor_ship
    monkeypatch.setenv("MONITOR_INGEST_URL", "http://127.0.0.1:1/ingest")
    monkeypatch.setenv("MONITOR_INGEST_TOKEN", "t")
    assert monitor_ship.ship_event("hub_block", "x") is False


def test_rejects_https_url(monkeypatch):
    """只支援內網明文 HTTP。https 需要處理被 patch 的 ssl 模組，
    在真 OS thread 上不安全 —— 與其默默出錯，不如明確拒絕。"""
    import monitor_ship
    monkeypatch.setenv("MONITOR_INGEST_URL", "https://example.com/ingest")
    monkeypatch.setenv("MONITOR_INGEST_TOKEN", "t")
    assert monitor_ship.ship_event("hub_block", "x") is False


def test_uses_unpatched_socket():
    """核心保證：不得使用被 gevent monkey patch 的 socket。"""
    import monitor_ship
    from gevent.monkey import get_original
    assert monitor_ship._socket_factory() is get_original("socket", "socket")
    assert monitor_ship._getaddrinfo() is get_original("socket", "getaddrinfo")
