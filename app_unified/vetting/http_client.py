"""HTTP 真人化 headers + 節流 helper（給 vetting 各 source 共用）。"""
import random
import time

import requests

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

_THROTTLE_MIN_S = 0.5
_THROTTLE_MAX_S = 1.2


def make_session() -> requests.Session:
    """回傳預設帶真人 headers 的 Session。"""
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    return s


def throttle_sleep():
    """連續對外請求之間呼叫，隨機 sleep 模擬人手點擊。"""
    time.sleep(random.uniform(_THROTTLE_MIN_S, _THROTTLE_MAX_S))
