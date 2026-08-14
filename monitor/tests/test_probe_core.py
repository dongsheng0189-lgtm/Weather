"""分層探測：把「連不上」拆成可指認的層。

分段是重點 —— 「TCP 就接不上」和「TCP 通了但 app 不吐 byte」是完全不同的病，
前者要查網路/邊緣，後者要查 app。混在一起就查不下去。
"""
import socket
import threading

from probe_core import probe


def _serve(handler):
    """起一個 localhost server，回傳 port。handler(conn) 決定它怎麼回應。"""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(5)
    port = s.getsockname()[1]
    held = []

    def loop():
        while True:
            try:
                conn, _ = s.accept()
            except OSError:
                return
            held.append(conn)      # 留住連線，否則 GC 會關掉它
            handler(conn)

    threading.Thread(target=loop, daemon=True).start()
    return port


def test_ok_response_is_classified_ok():
    def h(c):
        c.recv(4096)
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        c.close()
    port = _serve(h)
    r = probe("127.0.0.1", "/healthz", port=port, use_tls=False, timeout=3)
    assert r["verdict"] == "ok"
    assert r["code"] == 200


def test_connection_refused_is_conn_fail():
    r = probe("127.0.0.1", "/healthz", port=1, use_tls=False, timeout=3)
    assert r["verdict"] == "conn_fail"


def test_accepts_but_never_replies_is_ttfb_hang():
    """這是 gevent hub 被卡住時從外面看到的樣子 —— 最關鍵的一格。"""
    port = _serve(lambda c: None)          # 收下連線，永不回應
    r = probe("127.0.0.1", "/healthz", port=port, use_tls=False, timeout=1)
    assert r["verdict"] == "ttfb_hang"
    assert r["t_conn"] is not None, "TCP 有接上，必須記錄下來"
    assert r["t_ttfb"] >= 1


def test_5xx_captures_cf_ray_header():
    def h(c):
        c.recv(4096)
        c.sendall(b"HTTP/1.1 502 Bad Gateway\r\nCf-Ray: abc-TPE\r\n"
                  b"Content-Length: 9\r\n\r\nError 522")
        c.close()
    port = _serve(h)
    r = probe("127.0.0.1", "/", port=port, use_tls=False, timeout=3)
    assert r["verdict"] == "http_5xx"
    assert r["cf_ray"] == "abc-TPE"


def test_parses_healthz_pid_and_uptime():
    """app-unified 的 /healthz 回 pid —— pid 變了代表 worker 被重啟過。"""
    body = b'{"status":"ok","pid":4242,"uptime_s":88.5}'

    def h(c):
        c.recv(4096)
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s"
                  % (len(body), body))
        c.close()
    port = _serve(h)
    r = probe("127.0.0.1", "/healthz", port=port, use_tls=False, timeout=3)
    assert r["app"] == {"pid": 4242, "uptime_s": 88.5}


def test_plain_text_healthz_does_not_crash():
    """舊版 /healthz 回純文字 'ok' —— 不能因此爆掉。"""
    def h(c):
        c.recv(4096)
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        c.close()
    port = _serve(h)
    r = probe("127.0.0.1", "/healthz", port=port, use_tls=False, timeout=3)
    assert r["verdict"] == "ok"
    assert r["app"] is None
