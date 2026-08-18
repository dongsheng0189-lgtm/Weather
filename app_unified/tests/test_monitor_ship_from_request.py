"""請求路徑上的寄送：與 ship_event 的限制正好相反。

ship_event 由 hub_watchdog 的真 OS thread 呼叫，必須用 unpatched socket。
這個函式由 greenlet 呼叫，**必須用 patched socket** —— 未被 patch 的
connect/recv 會阻塞整個 gevent hub 最多 3 秒，正是我們在追的那種故障。
為了量測阻塞而製造阻塞，不可接受。

兩個函式的限制相反是這段程式碼最容易被後人「順手統一」而踩爆的地方。
"""
import json

import pytest

import monitor_ship
from tests._monitor_capture import _capture_server

# 這個檔案就是要測 monitor_ship 的「真正寄送行為」（起假的本機 capture
# server 收包），所以整檔退出 conftest.py 的 _reset_ratelimit_forensics
# autouse fixture（該 fixture 會把 ship_event_from_request /
# ship_event_from_request_async 都 stub 成不做事，會讓這裡的斷言全部失敗）。
pytestmark = pytest.mark.no_ratelimit_forensics_reset


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


def test_async_variant_is_noop_when_not_configured(monkeypatch):
    """未設定 env 時 guard clause 直接回 False，不會碰 spawn 或網路，也不拋例外。

    ship_event_from_request_async 本身不檢查 env，一律呼叫 gevent.spawn；
    guard clause 在被 spawn 出去的 ship_event_from_request 裡，要等 hub
    真的排到這個 greenlet 才會執行。這裡特意在 monkeypatch 復原前用
    gevent.sleep(0) 把它排空，否則這個未執行的 greenlet 會遺留到下一個
    測試，等 hub 排到它時讀到的是「當時」的 env（可能已被換成別的測試的
    設定），造成跨測試污染。
    """
    import gevent

    monkeypatch.delenv("MONITOR_INGEST_URL", raising=False)
    monkeypatch.delenv("MONITOR_INGEST_TOKEN", raising=False)
    monitor_ship.ship_event_from_request_async("rate_limit", "x")   # 不應拋例外
    gevent.sleep(0)   # 讓 spawn 出去的 greenlet 在這個測試的 env 下跑完，不留到下一個測試


def test_async_variant_actually_delivers_event(monkeypatch):
    """驗證 ship_event_from_request_async 真的透過 gevent.spawn 把事件送達。

    上面那個「未設定時是 noop」的測試沒有走到 spawn 或網路那一段，容易被
    誤讀成「已驗證非同步送達」。這裡才是真正驗證：起一個假 monitor、設好
    env、呼叫 async 變體，讓 spawn 出去的 greenlet 真的跑完，斷言 payload
    確實送到了。
    """
    import gevent

    port, received = _capture_server()
    monkeypatch.setenv("MONITOR_INGEST_URL", f"http://127.0.0.1:{port}/ingest")
    monkeypatch.setenv("MONITOR_INGEST_TOKEN", "tok-async")

    monitor_ship.ship_event_from_request_async(
        "rate_limit", "async delivery check", "detail-x")

    for _ in range(50):
        if received:
            break
        gevent.sleep(0.05)

    assert received, "spawn 出去的 greenlet 應該要送達事件，卻沒有任何請求進來"
    head, body = received[0]
    assert "Bearer tok-async" in head
    payload = json.loads(body)
    assert payload["kind"] == "rate_limit"
    assert payload["summary"] == "async delivery check"
    assert payload["detail"] == "detail-x"


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
