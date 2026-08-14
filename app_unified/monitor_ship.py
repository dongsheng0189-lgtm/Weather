"""把診斷事件推到 monitor service。

**這段程式碼的執行情境很特殊，改動前務必讀完：**

它由 hub_watchdog 的真 OS thread 呼叫，時機是 gevent hub 已經被同步工作
卡死的當下。這帶來兩個硬性限制：

1. **只能用 unpatched socket。** monkey.patch_all() 之後 socket 與
   getaddrinfo 都是 gevent 版本，從非 greenlet 的真執行緒去用，會觸發
   "using gevent objects from multiple threads ... crossing thread
   boundaries is not allowed"（prod 2026-05-09 實際發生過）。

2. **只走內網明文 HTTP。** https 得再處理一個同樣被 patch 的 ssl 模組，
   在這個情境下不值得冒險。ingest endpoint 走 Zeabur 內網，不對外公開。

為什麼非得在當下送出：hub 卡住時任何 greenlet 都跑不動，等恢復再送通常
已經太遲 —— gunicorn timeout=60 會 SIGKILL 該 worker，/tmp/app.log 隨容器
重啟清空，證據就永久消失了。
"""
import json
import os
import socket as _stdsocket
from urllib.parse import urlparse

_TIMEOUT = float(os.environ.get("MONITOR_INGEST_TIMEOUT", "3"))


def _socket_factory():
    """取回未被 monkey patch 的 socket class。"""
    try:
        from gevent.monkey import get_original
        return get_original("socket", "socket")
    except (ImportError, AttributeError):
        return _stdsocket.socket


def _getaddrinfo():
    """取回未被 monkey patch 的 getaddrinfo（gevent 有自己的 resolver）。"""
    try:
        from gevent.monkey import get_original
        return get_original("socket", "getaddrinfo")
    except (ImportError, AttributeError):
        return _stdsocket.getaddrinfo


def ship_event(kind: str, summary: str, detail: str = "") -> bool:
    """送出一則事件。成功 True，任何失敗 False —— 絕不拋例外。"""
    url = os.environ.get("MONITOR_INGEST_URL", "").strip()
    token = os.environ.get("MONITOR_INGEST_TOKEN", "").strip()
    if not url or not token:
        return False

    u = urlparse(url)
    if u.scheme != "http":
        return False        # 見模組說明：只支援內網明文 HTTP

    sock = None
    try:
        body = json.dumps({"kind": kind, "severity": "warning",
                           "source": "app-unified",
                           "summary": summary, "detail": detail},
                          ensure_ascii=False).encode("utf-8")
        host = u.hostname
        port = u.port or 80
        path = u.path or "/ingest"

        addrs = _getaddrinfo()(host, port, _stdsocket.AF_INET,
                               _stdsocket.SOCK_STREAM)
        sock = _socket_factory()(_stdsocket.AF_INET, _stdsocket.SOCK_STREAM)
        sock.settimeout(_TIMEOUT)
        sock.connect(addrs[0][4])

        req = (f"POST {path} HTTP/1.1\r\n"
               f"Host: {host}:{port}\r\n"
               f"Authorization: Bearer {token}\r\n"
               "Content-Type: application/json\r\n"
               f"Content-Length: {len(body)}\r\n"
               "Connection: close\r\n\r\n").encode("utf-8")
        sock.sendall(req + body)

        resp = sock.recv(200).decode("latin-1", "replace")
        return " 201" in resp or " 200" in resp
    except Exception:
        return False        # 監控失敗絕不影響服務
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
