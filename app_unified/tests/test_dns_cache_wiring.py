"""create_app() 必須安裝 DNS 快取 —— 少了這行，dns_cache.py 就是死程式碼。"""
import socket

import dns_cache


def test_create_app_installs_dns_cache(app):
    """用 session 範圍的 app fixture：它已經跑過 create_app()。"""
    assert socket.getaddrinfo is dns_cache._lookup, \
        "create_app() 沒有安裝 DNS 快取（app.py 裡的 dns_cache.install() 掉了？）"


def test_internal_hostnames_are_not_cached():
    """回歸防護：內網服務名的 IP 會隨 pod 重建而變，絕不可進快取名單。

    monitor 的 ingest 位址是 `service-<24 位十六進位>`，DB / Redis 同理。
    """
    for host in ("service-6a7ea9492b4272705cd1b1ce", "localhost",
                 "postgresql.zeabur.internal", "redis"):
        assert not dns_cache._is_cached_domain(host), f"{host} 不該被快取"


def test_external_sources_are_cached():
    for host in ("eze8.npa.gov.tw", "judgment.judicial.gov.tw",
                 "www.mjib.gov.tw"):
        assert dns_cache._is_cached_domain(host), f"{host} 應該要被快取"
