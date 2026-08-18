"""429 取證：把限流命中彙總成可送往 monitor 的事件。

本模組**不做任何 I/O**——只負責「決定要不要送」與「把資料降維成不含個資的形狀」，
寄送由呼叫端（app.py 的 429 handler）負責。這樣核心邏輯可以完全用假時鐘測。

為什麼不送真實 IP：monitor 的 mon_events 沒有欄位加密，其前提是「結構上不含個資」
（見 monitor/models.py）。我們要回答的問題只有「是不是同一個來源」與「XFF 有幾層」，
雜湊與計數就夠，不必破壞那個前提。

為什麼第一次命中就立刻送：若等滿視窗才送，20:00 爆一批、之後整晚沒有 429 的話，
那批會永遠卡在緩衝區——而那正是我們唯一想看到的事件。詳見
docs/superpowers/specs/2026-08-18-ratelimit-forensics-design.md。
"""
import hashlib
import os
from collections import Counter

WINDOW_SECONDS = 60.0


def _hop_count(xff):
    """X-Forwarded-For 的層數。日後 ProxyFix 的 x_for 就照這個數字設。"""
    if not xff:
        return 0
    return len([p for p in xff.split(",") if p.strip()])


class HitAggregator:
    """每個 path 一組計數；每個 path 每個視窗最多回報一次。

    gevent 是協作式排程，下面的讀改寫之間不會被搶佔，因此不需要鎖。
    """

    def __init__(self, salt: str = "", window_seconds: float = WINDOW_SECONDS):
        self._salt = salt
        self._window = window_seconds
        self._buckets = {}

    def _client_hash(self, remote_addr):
        raw = "{}|{}".format(self._salt, remote_addr or "(none)")
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6]

    def record(self, path, remote_addr, xff, now):
        """記一次 429。回傳要送出的 payload，或 None（同視窗內只累計）。"""
        bucket = self._buckets.get(path)
        if bucket is None:
            bucket = {"count": 0, "clients": Counter(),
                      "hops": set(), "last_ship": None}
            self._buckets[path] = bucket

        bucket["count"] += 1
        bucket["clients"][self._client_hash(remote_addr)] += 1
        bucket["hops"].add(_hop_count(xff))

        last_ship = bucket["last_ship"]
        if last_ship is not None and (now - last_ship) < self._window:
            return None

        payload = _format(path, bucket, now, last_ship)
        bucket["count"] = 0
        bucket["clients"] = Counter()
        bucket["hops"] = set()
        bucket["last_ship"] = now
        return payload


def _format(path, bucket, now, last_ship):
    clients = bucket["clients"]
    n = len(clients)
    summary = "rate limited: {} ×{}, {} client{}, pid={}".format(
        path, bucket["count"], n, "s" if n != 1 else "", os.getpid())
    if last_ship is None:
        window_line = "window: first report"
    else:
        window_line = "window: {:.1f}s since last report".format(now - last_ship)
    clients_line = "clients: " + ", ".join(
        "{}={}".format(h, c) for h, c in clients.most_common())
    hops_line = "xff_hops: " + ",".join(str(h) for h in sorted(bucket["hops"]))
    return {"kind": "rate_limit",
            "summary": summary[:500],
            "detail": "\n".join([window_line, clients_line, hops_line])}


_default = None


def default_aggregator():
    """全 process 共用一個。salt 用 SECRET_KEY：固定值 → 雜湊跨重啟、跨 worker 可比對。"""
    global _default
    if _default is None:
        _default = HitAggregator(salt=os.environ.get("SECRET_KEY", ""))
    return _default
