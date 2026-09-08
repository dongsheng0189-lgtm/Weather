"""外部來源的 DNS 快取 + stale-on-error。

## 為什麼需要這個

2026-09-08 查明：`npa.gov.tw` 的 3 個 NS 名稱共 **5 個 IP**，其中
`122.146.27.173` 是死的（實測 0/20 全逾時，另一輪測量則是「會回但每次
>500ms」——時好時壞）。遞迴解析器在快取過期、回上游詢問時若挑中那台，
就解不出來 → app 收到 `[Errno -3] EAI_AGAIN` → 使用者看到
「警政署系統暫時無法連線」。2026-09-05 起累積 252 筆。

對照組：`judicial.gov.tw` 與 `mjib.gov.tw` 的權威 NS 全部正常
→ 問題是 `npa.gov.tw` 專屬，不是我們容器的 DNS 體質。

**對方的 DNS 我們修不了，Zeabur 也修不了。** 但對方的 A 記錄長期穩定
（`eze8.npa.gov.tw` 權威 TTL = 3600 秒，IP 數月未變），所以把成功解析過的
結果留著、解析失敗時沿用舊值，就能完全中和對方 DNS 的不穩。

## 設計取捨

- **只作用於白名單網域。** DB / Redis / Zeabur 內網服務名（`service-<id>`）
  的 IP 會隨 pod 重建而變，快取它們反而會製造故障 —— 那些主機一律原樣穿透。
- **正向快取 300 秒。** 遠低於上游權威的 3600 秒 TTL，所以我們的快取只會比
  遞迴解析器**更新鮮**，不會引入額外的過時風險。
- **stale 上限 24 小時。** 沒有可用舊值時照實拋錯，不吞掉真正的故障
  （例如網域真的被撤掉）。
- **仍然每次先嘗試真正解析**，失敗才退回舊值 —— 不是拿快取取代解析。

## 與 gevent 的關係

`install()` 必須在 `monkey.patch_all()` **之後**呼叫（`create_app()` 裡），
這樣被包起來的就是 gevent 那份 `socket.getaddrinfo`（threadpool resolver），
解析時仍會讓出 hub。

`hub_watchdog` / `monitor_ship.ship_event` 走的是
`monkey.get_original("socket", ...)`，取得的是 **patch 前**的原件，
**不會**經過本模組 —— 這是刻意的，那條路徑必須維持完全獨立。
"""
import logging
import socket
import time

logger = logging.getLogger(__name__)

# 只有這些網域（含子網域）會被快取。新增前先想清楚：該主機的 IP 是否穩定？
# 內網服務名絕對不可加入 —— 它們的 IP 會變。
CACHED_DOMAINS = (
    "npa.gov.tw",                 # 警政署（本次事故的當事人）
    "judicial.gov.tw",            # 司法院
    "mjib.gov.tw",                # 法務部調查局
    "r2.cloudflarestorage.com",   # R2：失敗是靜默的（storage.py 整個吞例外）
)

TTL_SECONDS = 300           # 正向快取；遠低於上游 3600 秒 TTL
STALE_MAX_SECONDS = 86400   # 解析失敗時，舊值最多沿用 24 小時

_cache: dict = {}           # key -> (result, stored_at)
_original = None            # patch 前的 socket.getaddrinfo
_resolver = None            # 實際去解析的函式（測試可注入）


def _now() -> float:
    """獨立成函式，測試才能推進時間。"""
    return time.monotonic()


def _is_cached_domain(host) -> bool:
    if not isinstance(host, str):
        return False
    h = host.rstrip(".").lower()
    return any(h == d or h.endswith("." + d) for d in CACHED_DOMAINS)


def reset_cache() -> None:
    _cache.clear()


def cache_size() -> int:
    return len(_cache)


def _lookup(*args, **kwargs):
    host = args[0] if args else kwargs.get("host")

    if not _is_cached_domain(host):
        return _resolver(*args, **kwargs)   # 名單外：完全不碰

    key = (args, tuple(sorted(kwargs.items())))
    now = _now()
    hit = _cache.get(key)

    if hit is not None and now - hit[1] < TTL_SECONDS:
        return hit[0]

    try:
        result = _resolver(*args, **kwargs)
    except Exception as e:
        # ── 這就是整個模組存在的理由 ──
        if hit is not None and now - hit[1] < STALE_MAX_SECONDS:
            logger.warning(
                "dns_cache: %s 解析失敗（%s: %s），沿用 %.0f 秒前的結果",
                host, type(e).__name__, e, now - hit[1],
            )
            return hit[0]
        raise   # 沒有可用舊值 → 照實拋錯，不吞掉真正的故障

    if result:                      # 空結果不算成功，不可拿來當日後的 stale 值
        _cache[key] = (result, now)
    return result


def install(resolver=None) -> None:
    """包住 socket.getaddrinfo。可重複呼叫（不會把自己包在自己外面）。

    必須在 gevent monkey.patch_all() 之後呼叫。
    """
    global _original, _resolver
    if _original is None:
        _original = socket.getaddrinfo
    _resolver = resolver or _original
    if socket.getaddrinfo is not _lookup:
        socket.getaddrinfo = _lookup


def uninstall() -> None:
    """還原（測試用；正式環境不會呼叫）。"""
    global _original, _resolver
    if _original is not None:
        socket.getaddrinfo = _original
        _original = None
        _resolver = None
