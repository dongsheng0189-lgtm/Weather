"""Vetting 共用異常：被外部站擋人時 raise。"""


class RateLimited(Exception):
    """外部站回 429 / 503 — 立即停止，不重試。"""
    pass


class Forbidden(Exception):
    """外部站回 403 — 立即停止，不重試。"""
    pass


def check_blocked(response):
    """檢查 response status 是否表示被擋；是就 raise。"""
    if response.status_code in (429, 503):
        raise RateLimited(f"upstream {response.status_code}")
    if response.status_code == 403:
        raise Forbidden("upstream 403")
