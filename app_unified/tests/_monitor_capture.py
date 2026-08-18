"""測試用的假 monitor server：收一個 HTTP 請求就停，回傳 (port, received)。

兩個測試檔共用（test_monitor_ship.py 與 test_monitor_ship_from_request.py），
命名比照 tests/_r2_fixture.py 的慣例：底線開頭 = 共用 helper，不是測試檔。
"""
import socket
import threading


def _capture_server():
    """起一個假 monitor，回傳 (port, received list)。"""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(5)
    port = s.getsockname()[1]
    received = []

    def loop():
        conn, _ = s.accept()
        data = b""
        conn.settimeout(3)
        try:
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            head, _, body = data.partition(b"\r\n\r\n")
            length = 0
            for line in head.decode("latin-1").splitlines():
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1])
            while len(body) < length:
                body += conn.recv(4096)
            received.append((head.decode("latin-1"), body.decode("utf-8")))
            conn.sendall(b"HTTP/1.1 201 Created\r\nContent-Length: 0\r\n\r\n")
        except OSError:
            pass
        finally:
            conn.close()

    threading.Thread(target=loop, daemon=True).start()
    return port, received
