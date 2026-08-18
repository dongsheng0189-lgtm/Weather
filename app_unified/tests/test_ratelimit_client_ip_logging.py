"""限流取證用的 log(2026-08-18)。

背景:`/auth/verify` 的 `20 per minute` 用 `get_remote_address` 計算,而 app 跑在
Zeabur ingress 後面且沒裝 ProxyFix → `remote_addr` 恆為 ingress IP,額度全體共用。
在動手修之前,要先從 prod 拿到兩個事實:

  1. `remote_addr` 是不是真的每次都同一個 IP(→ 證實共用)
  2. 真實的 `X-Forwarded-For` 長什麼樣(→ 決定 ProxyFix 的 x_for 要設幾)
  3. 429 到底有沒有被觸發過(→ 證實使用者真的撞到,而非只是理論風險)

這些測試守著那幾行 log,避免日後被當成雜訊清掉。
"""
import logging

from flask import request
from werkzeug.exceptions import TooManyRequests


def test_verify_logs_remote_addr_and_forwarded_for(client, caplog):
    """/auth/verify 每次進來都要記下 remote_addr 與原始 X-Forwarded-For。"""
    with caplog.at_level(logging.WARNING):
        client.post(
            '/auth/verify',
            json={'pin': '9999'},
            headers={'X-Forwarded-For': '203.0.113.7, 10.1.2.3'},
            environ_base={'REMOTE_ADDR': '10.0.0.5'},
        )

    assert '10.0.0.5' in caplog.text, 'remote_addr 沒被記下來'
    assert '203.0.113.7, 10.1.2.3' in caplog.text, 'X-Forwarded-For 原始值沒被記下來'


def test_verify_logs_forwarded_for_absence_explicitly(client, caplog):
    """沒有 X-Forwarded-For 時也要看得出來(空字串不能與「沒記」混淆)。"""
    with caplog.at_level(logging.WARNING):
        client.post(
            '/auth/verify',
            json={'pin': '9999'},
            environ_base={'REMOTE_ADDR': '10.0.0.5'},
        )

    assert 'xff=(none)' in caplog.text


def test_rate_limit_429_is_logged_with_client_evidence(app, caplog):
    """被限流時要留一筆 log,否則 prod 完全看不出限流有沒有在擋人。"""
    with app.test_request_context(
        '/auth/verify',
        method='POST',
        headers={'X-Forwarded-For': '203.0.113.7'},
        environ_base={'REMOTE_ADDR': '10.0.0.5'},
    ):
        with caplog.at_level(logging.WARNING):
            app.handle_http_exception(TooManyRequests())

    assert 'rate limited' in caplog.text.lower(), '429 沒有留下 log'
    assert '/auth/verify' in caplog.text, '沒記下是哪條路徑被限流'
    assert '10.0.0.5' in caplog.text, '429 log 沒記 remote_addr'
    assert '203.0.113.7' in caplog.text, '429 log 沒記 X-Forwarded-For'


def test_429_ships_aggregated_event_to_monitor(app, monkeypatch):
    """第一次命中就要送出，且送出的東西不含 IP。"""
    import app as app_module

    sent = []
    monkeypatch.setattr(app_module, "ship_event_from_request_async",
                        lambda kind, summary, detail="": sent.append(
                            (kind, summary, detail)))
    # 用全新的彙總器，避免被同一個 process 內其他測試的計數污染
    import ratelimit_forensics
    monkeypatch.setattr(ratelimit_forensics, "_default",
                        ratelimit_forensics.HitAggregator(salt="t"))

    with app.test_request_context(
        '/auth/verify', method='POST',
        headers={'X-Forwarded-For': '203.0.113.7'},
        environ_base={'REMOTE_ADDR': '10.0.0.5'},
    ):
        app.handle_http_exception(TooManyRequests())

    assert len(sent) == 1, '429 沒有推給 monitor'
    kind, summary, detail = sent[0]
    assert kind == 'rate_limit'
    assert '/auth/verify' in summary
    assert '203.0.113.7' not in summary + detail, '真實 IP 不可送進 monitor'
    assert '10.0.0.5' not in summary + detail, '真實 IP 不可送進 monitor'


def test_429_within_window_ships_only_once(app, monkeypatch):
    import app as app_module

    sent = []
    monkeypatch.setattr(app_module, "ship_event_from_request_async",
                        lambda kind, summary, detail="": sent.append(summary))
    import ratelimit_forensics
    monkeypatch.setattr(ratelimit_forensics, "_default",
                        ratelimit_forensics.HitAggregator(salt="t"))

    for _ in range(5):
        with app.test_request_context(
            '/auth/verify', method='POST',
            environ_base={'REMOTE_ADDR': '10.0.0.5'},
        ):
            app.handle_http_exception(TooManyRequests())

    assert len(sent) == 1, '同一視窗內應只送一則，避免尖峰時火上加油'


def test_429_bucket_key_uses_route_template_not_raw_path(app, monkeypatch):
    """帶變數段的路由（如 /vetting/whitelist/<int:eid>）不同 eid 要落進同一個 bucket。

    若彙總器誤用 request.path 當 key，每個不同 eid 都是全新 bucket，會讓「同一
    視窗只送一次」的節流形同虛設、_buckets 也會無上限成長。這裡用既有的
    /vetting/whitelist/<int:eid> 路由（POST，見 vetting/routes.py 的
    whitelist_update）驗證：先送 eid=1 觸發一次上報，再送 eid=2 —— 應該被
    視為同一視窗內的第二次命中而不再上報。
    """
    import app as app_module

    sent = []
    monkeypatch.setattr(app_module, "ship_event_from_request_async",
                        lambda kind, summary, detail="": sent.append(summary))
    import ratelimit_forensics
    monkeypatch.setattr(ratelimit_forensics, "_default",
                        ratelimit_forensics.HitAggregator(salt="t"))

    with app.test_request_context(
        '/vetting/whitelist/1', method='POST',
        environ_base={'REMOTE_ADDR': '10.0.0.5'},
    ):
        assert request.url_rule is not None, \
            'fixture 假設失敗：/vetting/whitelist/<int:eid> 應該要 match 到 url_rule'
        app.handle_http_exception(TooManyRequests())

    with app.test_request_context(
        '/vetting/whitelist/2', method='POST',
        environ_base={'REMOTE_ADDR': '10.0.0.5'},
    ):
        app.handle_http_exception(TooManyRequests())

    assert len(sent) == 1, (
        '不同 eid 的請求被當成不同 bucket —— bucket key 疑似仍在用 '
        'request.path 而非路由樣板'
    )
    assert '/vetting/whitelist/<int:eid>' in sent[0]


def test_429_shipping_failure_does_not_break_the_response(app, monkeypatch):
    """監控壞掉不能變成使用者的 500。"""
    import app as app_module

    def boom(*a, **kw):
        raise RuntimeError("monitor down")

    monkeypatch.setattr(app_module, "ship_event_from_request_async", boom)
    import ratelimit_forensics
    monkeypatch.setattr(ratelimit_forensics, "_default",
                        ratelimit_forensics.HitAggregator(salt="t"))

    with app.test_request_context(
        '/auth/verify', method='POST',
        headers={'Accept': 'application/json'},
        environ_base={'REMOTE_ADDR': '10.0.0.5'},
    ):
        resp = app.handle_http_exception(TooManyRequests())

    body, status = resp
    assert status == 429
    assert body.get_json()['error'] == 'rate_limited'
