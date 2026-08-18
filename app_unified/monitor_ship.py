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


# ---------------------------------------------------------------------------
# 以下是請求路徑專用的寄送，限制與上面的 ship_event 正好相反 —— 別把兩者合併。
# ---------------------------------------------------------------------------

def ship_event_from_request(kind: str, summary: str, detail: str = "") -> bool:
    """從**請求路徑上的 greenlet** 送出事件。成功 True，任何失敗 False。

    與 ship_event() 的差別，改動前務必讀懂：

      ship_event()               本函式
      ─────────────────────      ─────────────────────
      呼叫者：真 OS thread        呼叫者：greenlet
      必須 unpatched socket      必須 **patched** socket
      （跨執行緒用 gevent 物件    （未 patch 的 connect/recv 會阻塞整個
        會直接爆掉）               hub 最多 3 秒，等於自己製造故障）

    所以這裡刻意用 urllib（已被 monkey patch → 會正常讓出 hub），
    而**不是**重用上面那套 _socket_factory / _getaddrinfo。
    """
    import urllib.error
    import urllib.request

    url = os.environ.get("MONITOR_INGEST_URL", "").strip()
    token = os.environ.get("MONITOR_INGEST_TOKEN", "").strip()
    if not url or not token:
        return False

    try:
        body = json.dumps({"kind": kind, "severity": "warning",
                           "source": "app-unified",
                           "summary": summary, "detail": detail},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status in (200, 201)
    except Exception:
        return False        # 監控失敗絕不影響服務


def ship_event_from_request_async(kind: str, summary: str, detail: str = "") -> None:
    """背景送出，不讓使用者的請求等待網路往返。

    沒有 gevent（單元測試環境）時退回同步呼叫；該情境下未設定 env 會立刻回
    False，不會真的碰網路。
    """
    try:
        import gevent
        gevent.spawn(ship_event_from_request, kind, summary, detail)
    except Exception:
        try:
            ship_event_from_request(kind, summary, detail)
        except Exception:
            pass
