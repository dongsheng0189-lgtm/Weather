"""HTTP 真人化 headers + 節流 helper + DNS 瞬斷重試（給 vetting 各 source 共用）。"""
import random
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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

# ── DNS/連線瞬斷重試 ────────────────────────────────────────────
# 2026-09-07 prod 事故：容器對外解析間歇性 EAI_AGAIN
# （`[Errno -3] Temporary failure in name resolution`），requests 預設 0 重試，
# 一個瞬斷就讓使用者看到「警政署系統暫時無法連線」。三家來源都會中，只是
# /start 把 NPA prep 排第一，所以永遠只看得到警政署的錯誤。
#
# ⚠️ `read=False` 不可改成數字：連線錯誤代表請求**還沒送出去**，重送安全，
# 所以 urllib3 的 increment() 對它不檢查 method（POST 也會重試）；但讀取錯誤
# 是**已經送達之後**才失敗，重送等於對 NPA 重複送出查詢／用掉驗證碼。
# read=False 會讓讀取錯誤原樣拋回，維持既有行為。有測試守著。
#
# 時間預算：backoff 只加 0.6 秒（第一次重試不等、第二次等 0.3×2），
# /start 已經很重（NPA 3 個請求 + 司法院最多 15 個詳情頁 + MJIB），
# 不能讓重試把它推過 gunicorn 的 timeout=60。
_RETRY = Retry(
    total=3,
    connect=2,       # 連線/DNS 失敗最多再試 2 次（共 3 次 attempt）
    read=False,      # 讀取錯誤不重試 —— 見上方說明
    backoff_factor=0.3,
)


def make_session() -> requests.Session:
    """回傳預設帶真人 headers、且對 DNS/連線瞬斷會重試的 Session。"""
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    adapter = HTTPAdapter(max_retries=_RETRY)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def throttle_sleep():
    """連續對外請求之間呼叫，隨機 sleep 模擬人手點擊。"""
    time.sleep(random.uniform(_THROTTLE_MIN_S, _THROTTLE_MAX_S))
