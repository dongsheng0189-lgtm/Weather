"""請求路徑上的寄送：與 ship_event 的限制正好相反。

ship_event 由 hub_watchdog 的真 OS thread 呼叫，必須用 unpatched socket。
這個函式由 greenlet 呼叫，**必須用 patched socket** —— 未被 patch 的
connect/recv 會阻塞整個 gevent hub 最多 3 秒，正是我們在追的那種故障。
為了量測阻塞而製造阻塞，不可接受。

兩個函式的限制相反是這段程式碼最容易被後人「順手統一」而踩爆的地方。
"""
import json

import monitor_ship
from tests._monitor_capture import _capture_server


def test_posts_event_to_ingest(monkeypatch):
    port, received = _capture_server()
    monkeypatch.setenv("MONITOR_INGEST_URL", f"http://127.0.0.1:{port}/ingest")
    monkeypatch.setenv("MONITOR_INGEST_TOKEN", "tok")

    ok = monitor_ship.ship_event_from_request(
        "rate_limit", "rate limited: /auth/verify ×3, 1 client, pid=1", "xff_hops: 2")

    assert ok is True
    head, body = received[0]
    assert "Bearer tok" in head
    payload = json.loads(body)
    assert payload["kind"] == "rate_limit"
    assert payload["source"] == "app-unified"
    assert payload["detail"] == "xff_hops: 2"


def test_returns_false_when_not_configured(monkeypatch):
    monkeypatch.delenv("MONITOR_INGEST_URL", raising=False)
    monkeypatch.delenv("MONITOR_INGEST_TOKEN", raising=False)
    assert monitor_ship.ship_event_from_request("rate_limit", "x") is False


def test_never_raises_when_nothing_is_listening(monkeypatch):
    """監控失敗絕不影響服務 —— 這裡跑在請求路徑上，拋例外會變成使用者的 500。"""
    monkeypatch.setenv("MONITOR_INGEST_URL", "http://127.0.0.1:1/ingest")
    monkeypatch.setenv("MONITOR_INGEST_TOKEN", "tok")
    assert monitor_ship.ship_event_from_request("rate_limit", "x") is False


def test_async_variant_never_raises_without_gevent_hub(monkeypatch):
    monkeypatch.delenv("MONITOR_INGEST_URL", raising=False)
    monkeypatch.delenv("MONITOR_INGEST_TOKEN", raising=False)
    monitor_ship.ship_event_from_request_async("rate_limit", "x")   # 不應拋例外


def test_ship_event_still_uses_unpatched_socket():
    """回歸防護：既有 ship_event 的死規則不能因為新增函式而被順手改掉。

    這條規則的真實後果（跨執行緒用 gevent 物件會直接爆掉）單元測試本來就
    抓不到 —— 這裡能測的只有「原始碼裡還在用 _socket_factory /
    _getaddrinfo」，不是行為本身。這個斷言很脆弱（改個變數名字就會誤報），
    但它擋住的是「後人把 ship_event 與 ship_event_from_request 順手統一」
    這個真實會在 prod 炸掉的危險，取捨後決定保留。
    """
    import inspect
    src = inspect.getsource(monitor_ship.ship_event)
    assert "_socket_factory" in src
    assert "_getaddrinfo" in src
