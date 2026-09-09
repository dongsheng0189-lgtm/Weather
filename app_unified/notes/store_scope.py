"""同店範圍解析 + 同店授權檢查 —— 標籤（tags）與建檔追蹤（tracking）共用。

這是一條**授權規則**（誰能看到／操作哪個 store 底下的資料），特意獨立成一個
模組，不掛在 notes/tags.py 或 notes/tracking.py 任一邊底下：兩個模組原本各自
抄了一份一模一樣的邏輯，若店別規則以後要改（例如改成從 session 讀店別、加店
別隱藏旗標、加稽核 hook），很容易改了一邊、另一邊沒改到，卻仍然「看起來」
正常運作——兩份重複的授權邏輯是最容易悄悄分岔的那種 bug。

放在獨立模組而不是併進其中一邊，是因為 notes/tags.py 與 notes/tracking.py
都要 `from notes.routes import notes_bp` 註冊路由；若這份邏輯留在其中一邊、
被另一邊 import，會形成循環 import。本模組刻意不 import notes_bp、也不
import tags.py / tracking.py 的任何東西。
"""
from flask import request, abort
from flask_login import current_user


def effective_store():
    """回傳目前請求要操作的店別。

    super_admin 可用 `?store=` 指定要看哪一店；其他人一律鎖在自己的
    `current_user.store`（忽略 query string，防止越權）。
    """
    if current_user.role == 'super_admin':
        s = request.args.get('store')
        if s:
            return s
    return current_user.store


def require_same_store(row):
    """`row.store` 與目前有效店別不同就 403。

    用 403 而非 404：這是既有的「同店」慣例（沿用 notes/tags.py 原本的行
    為），跟「沒有這個模組權限就 404（看不見即不存在）」是不同層次的規則，
    後者是模組層級的功能開關，這裡是資料列層級的同店授權。
    """
    eff = effective_store()
    if not eff or row.store != eff:
        abort(403)
