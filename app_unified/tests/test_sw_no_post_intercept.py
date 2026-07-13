"""Service Worker 不可攔截帶 body 的請求（附件上傳「缺少 note_id」真兇）。

2026-07-13 prod log 實錄：
    [note_id-missing] raw note_id=None item_id=None user=250 filename=None
note_id / item_id / filename **全是 None**（不是字串 "null"）＝整個 multipart body
是空的，但 cookie 還在（user 認得出來）。前端絕不會刻意送一個沒檔案的上傳，所以
body 是在送達 server 前掉的。

真兇：static/sw.js 的 fetch handler 沒過濾 method，最後一行 catch-all
`event.respondWith(networkFirst(event.request))` 把**所有** POST（含三條附件上傳路徑：
fetch ×2 + XHR ×1，SW 連 XHR 都會攔）接管下來、自己再 fetch() 一次。request body 是
one-shot stream，SW 冷啟動 / 網路抖動時可能整個掉光 → server 收到 headers+cookie 齊、
body 空的請求 → request.form 解析出空 dict → note_id=None → 400「缺少 note_id」。
天然 flaky，且只在 prod 發（本機不會踩到 SW 生命週期那些狀況）。

修法：SW 只接管 GET。Cache API 本來就只存得了 GET，攔 POST 毫無好處，卻憑空引入
一整類「body 掉了」的失敗模式。
"""
import re
from pathlib import Path

_RAW_SW = (Path(__file__).parent.parent / "static" / "sw.js").read_text(encoding="utf-8")


def _strip_comments(js: str) -> str:
    """剝掉 // 行註解與 /* */ 區塊註解。

    必要：本檔的斷言在找 respondWith / method guard 的**相對位置**，而 sw.js 的註解
    裡本來就會提到 respondWith 這個字（含本次修法的說明）。不剝註解的話 tripwire 會
    被註解文字騙過去 —— 測到的是散文，不是 code。
    """
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"//[^\n]*", "", js)


SW = _strip_comments(_RAW_SW)


def _fetch_handler() -> str:
    """只取 fetch handler 的 code（不含註解）。"""
    return SW[SW.index("addEventListener('fetch'"):]


def test_sw_bails_out_on_non_get_before_any_respond_with():
    """method guard 必須在任何 respondWith 之前就 return，否則帶 body 的請求會被
    SW 接管重送、body 可能掉光（附件上傳變成空 body 的 400）。"""
    handler = _fetch_handler()
    guard = re.search(r"request\.method\s*!==\s*'GET'\s*\)\s*return\s*;", handler)
    assert guard, "sw.js 的 fetch handler 沒有『非 GET 直接 return』的 guard"

    assert guard.start() < handler.index("respondWith"), (
        "method guard 出現在第一個 respondWith 之後 —— POST 仍會被 SW 接管"
    )


def test_sw_fetch_handler_has_no_unguarded_catch_all():
    """回歸防護：guard 一旦被拿掉或往後挪，附件上傳的 body 會再次隨機消失。"""
    handler = _fetch_handler()
    guard_pos = handler.index("return")  # method guard 的 return（handler 內第一個）
    assert "respondWith" not in handler[:guard_pos]
