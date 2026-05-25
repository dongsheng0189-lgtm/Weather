"""Markup smoke tests for F4 weather GPS fast load.

純驗 template 渲染含預期 marker — 不驗 JS runtime 行為。
Runtime 行為走 manual verification flow（spec: docs/superpowers/specs/2026-05-25-weather-gps-fast-load.md）。
"""


def test_weather_page_contains_staleness_chip(client):
    res = client.get('/weather/')
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'staleness-chip' in body, "staleness chip element / class 缺失"


def test_weather_page_gps_timeout_3s(client):
    res = client.get('/weather/')
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'timeout: 3000' in body, "GPS timeout 未降到 3s"
    assert 'timeout: 8000' not in body, "舊的 8s timeout 殘留"


def test_weather_page_localStorage_cache_key(client):
    res = client.get('/weather/')
    body = res.get_data(as_text=True)
    assert 'weather_last_location' in body, "cache key 缺失"


def test_weather_page_cache_max_age_7days(client):
    res = client.get('/weather/')
    body = res.get_data(as_text=True)
    assert '7 * 24 * 3600 * 1000' in body, "cache TTL 7 天 expression 缺失"


def test_weather_page_loading_text_span(client):
    res = client.get('/weather/')
    body = res.get_data(as_text=True)
    assert 'id="loading-text"' in body, "progressive loading-text span 缺失"
