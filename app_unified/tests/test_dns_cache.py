"""DNS 快取 + stale-on-error（2026-09-08）。

事故背景：`npa.gov.tw` 的 5 個權威 NS IP 中有一台是死的（`122.146.27.173`），
遞迴解析器挑中它就解不出來 → app 收到 `EAI_AGAIN` → 使用者看到
「警政署系統暫時無法連線」。對方的 DNS 我們修不了，但對方的 A 記錄長期穩定
（TTL 3600），所以把成功解析過的結果留著、解析失敗時沿用，就能完全中和。
"""
import socket

import pytest

import dns_cache


@pytest.fixture(autouse=True)
def _clean():
    """本檔案要自己控制安裝狀態，但不能弄髒別的測試 ——
    create_app() 會做一次全域安裝，測完要還原，否則測試順序會影響結果。"""
    was_installed = socket.getaddrinfo is dns_cache._lookup
    dns_cache.uninstall()
    dns_cache.reset_cache()
    yield
    dns_cache.uninstall()
    dns_cache.reset_cache()
    if was_installed:
        dns_cache.install()


class _Clock:
    """可控時鐘。真實的 time.monotonic() 回的是開機以來的秒數（很大的數），
    測試若直接指定絕對值會算出負的時間差 —— 所以整段測試都用這個。"""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(dns_cache, "_now", c)
    return c


def _fake(result=None, exc=None):
    """假的底層 resolver，記錄呼叫次數。"""
    calls = []

    def resolver(*a, **kw):
        calls.append(a)
        if exc is not None:
            raise exc
        return result

    resolver.calls = calls
    return resolver


NPA = "eze8.npa.gov.tw"
OK = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("210.69.154.116", 443))]
BOOM = socket.gaierror(-3, "Temporary failure in name resolution")


# ── 1. 名單外的主機完全不碰 ─────────────────────────────
def test_unlisted_host_passes_through_and_still_raises():
    """名單外（含 DB / Redis / Zeabur 內網名稱）必須原樣穿透。

    內網服務的 IP 會隨 pod 重建而變，快取它反而會製造故障。
    """
    r = _fake(exc=BOOM)
    dns_cache.install(resolver=r)
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("service-abc123", 8080, 0, socket.SOCK_STREAM)
    assert len(r.calls) == 1


def test_unlisted_host_success_is_not_cached():
    r = _fake(result=OK)
    dns_cache.install(resolver=r)
    for _ in range(3):
        socket.getaddrinfo("db.internal", 5432, 0, socket.SOCK_STREAM)
    assert len(r.calls) == 3, "名單外的主機不該被快取"


# ── 2. 名單內：成功會被快取 ─────────────────────────────
def test_success_is_cached_within_ttl(clock):
    r = _fake(result=OK)
    dns_cache.install(resolver=r)
    a = socket.getaddrinfo(NPA, 443, 0, socket.SOCK_STREAM)
    b = socket.getaddrinfo(NPA, 443, 0, socket.SOCK_STREAM)
    assert a == b == OK
    assert len(r.calls) == 1, "TTL 內第二次應該走快取"


def test_subdomain_matches_listed_domain():
    r = _fake(result=OK)
    dns_cache.install(resolver=r)
    socket.getaddrinfo("judgment.judicial.gov.tw", 443, 0, socket.SOCK_STREAM)
    socket.getaddrinfo("judgment.judicial.gov.tw", 443, 0, socket.SOCK_STREAM)
    assert len(r.calls) == 1


def test_lookalike_domain_does_not_match():
    """`evil-npa.gov.tw` / `npa.gov.tw.evil.com` 都不該被當成名單內。"""
    r = _fake(result=OK)
    dns_cache.install(resolver=r)
    for host in ("evil-npa.gov.tw", "npa.gov.tw.evil.com"):
        socket.getaddrinfo(host, 443, 0, socket.SOCK_STREAM)
        socket.getaddrinfo(host, 443, 0, socket.SOCK_STREAM)
    assert len(r.calls) == 4, "只是長得像的網域不該進快取"


# ── 3. 核心：解析失敗時沿用上次成功的結果 ────────────────
def test_serves_stale_on_resolution_failure(clock):
    """這就是整個修正的重點：對方 DNS 掛掉時，我們照樣拿得到 IP。"""
    good = _fake(result=OK)
    dns_cache.install(resolver=good)
    assert socket.getaddrinfo(NPA, 443, 0, socket.SOCK_STREAM) == OK

    # TTL 過期 → 會重新問；但這次底層壞了
    clock.advance(dns_cache.TTL_SECONDS + 1)
    bad = _fake(exc=BOOM)
    dns_cache.install(resolver=bad)

    got = socket.getaddrinfo(NPA, 443, 0, socket.SOCK_STREAM)
    assert got == OK, "解析失敗時應沿用上次成功的結果，而不是把錯誤丟給使用者"
    assert len(bad.calls) == 1, "仍應先嘗試真正解析，失敗才退回快取"


def test_raises_when_failure_and_nothing_cached():
    """沒有可用的舊值就要照實拋錯 —— 不吞掉真正的故障。"""
    r = _fake(exc=BOOM)
    dns_cache.install(resolver=r)
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo(NPA, 443, 0, socket.SOCK_STREAM)


def test_stale_entry_too_old_is_not_served(clock):
    """舊值也有保存期限，不能拿一個月前的 IP 硬撐。"""
    good = _fake(result=OK)
    dns_cache.install(resolver=good)
    socket.getaddrinfo(NPA, 443, 0, socket.SOCK_STREAM)

    clock.advance(dns_cache.STALE_MAX_SECONDS + 100)
    bad = _fake(exc=BOOM)
    dns_cache.install(resolver=bad)
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo(NPA, 443, 0, socket.SOCK_STREAM)


def test_empty_result_is_not_cached():
    """解析成功但回空清單不算數，不可拿來當日後的 stale 值。"""
    r = _fake(result=[])
    dns_cache.install(resolver=r)
    socket.getaddrinfo(NPA, 443, 0, socket.SOCK_STREAM)
    assert dns_cache.cache_size() == 0


# ── 4. 安裝行為 ────────────────────────────────────────
def test_install_is_idempotent():
    """重複安裝不可把自己包在自己外面（每包一層就多一次 resolver 呼叫）。"""
    r = _fake(result=OK)
    dns_cache.install(resolver=r)
    first = socket.getaddrinfo
    dns_cache.install(resolver=r)
    dns_cache.install(resolver=r)
    assert socket.getaddrinfo is first


def test_uninstall_restores_original():
    original = socket.getaddrinfo
    dns_cache.install(resolver=_fake(result=OK))
    assert socket.getaddrinfo is not original
    dns_cache.uninstall()
    assert socket.getaddrinfo is original
