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
