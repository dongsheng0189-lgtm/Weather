"""HTTP wrapper for 移民署晶片居留證查詢 API。

對外 URL 偽裝在 /v/aux/，但內部模組保留 icinfo 命名（不洩漏給使用者）。
"""
import logging
import time
import requests

from vetting.http_client import BROWSER_HEADERS, throttle_sleep
from icinfo.errors import UpstreamError

logger = logging.getLogger(__name__)


_BASE = "https://niaicinfo.immigration.gov.tw/icinfo-frontend"
_CAPTCHA_URL = f"{_BASE}/api/captcha"
_SEARCH_URL = f"{_BASE}/api/search"
_TIMEOUT = 15


def fetch_captcha() -> tuple[bytes, str]:
    """打移民署 /api/captcha，回 (jpeg_bytes, jsessionid)."""
    ts = int(time.time() * 1000)
    url = f"{_CAPTCHA_URL}?{ts}"
    headers = {**BROWSER_HEADERS, "Referer": f"{_BASE}/zh"}
    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise UpstreamError(f"captcha fetch failed: {e}")

    jsessionid = resp.cookies.get("JSESSIONID")
    if not jsessionid:
        raise UpstreamError("captcha response missing JSESSIONID")

    return resp.content, jsessionid


def submit_search(payload: dict, jsessionid: str) -> dict:
    """送查詢，回 dict.

    payload: {uino, issueDate, expiryDate, serialNo, captcha}
    回傳：{matched: bool, image: str|None, search_time: str|None, rcode: str}
    """
    throttle_sleep()  # 模擬人手節流
    headers = {
        **BROWSER_HEADERS,
        "Content-Type": "application/json",
        "Referer": f"{_BASE}/zh",
    }

    try:
        resp = requests.post(
            _SEARCH_URL,
            json=payload,
            cookies={"JSESSIONID": jsessionid},
            headers=headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise UpstreamError(f"search failed: {e}")

    try:
        data = resp.json()
    except ValueError as e:
        raise UpstreamError(f"search response not json: {e}")

    rcode = data.get("rcode")
    body = data.get("data") or {}
    # 上游成功 code 是 "0000"（page chunk 看到 x.SUCCESS 常數名，實際 string value 是 "0000"）
    # 保險起見也接受 "SUCCESS" 字面值，以防上游改回常數名
    matched = rcode in ("0000", "SUCCESS")

    return {
        "matched": matched,
        "image": body.get("image") if matched else None,
        "search_time": body.get("searchTime"),
        "rcode": rcode,  # 給 log debug 用
    }
