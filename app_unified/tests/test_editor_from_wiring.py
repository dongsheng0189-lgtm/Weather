"""editor.html 的 `?from=` 接線守門（2026-09-09）。

背景：`?from=` 原本只服務 AI 摘要 review 流程——把「← 返回」鈕重指到摘要，
同時把整頁鎖進 `review-mode`（不可編輯）。建檔追蹤表要接上同一個「返回鈕
指去哪裡」機制（PO 回饋：從追蹤表點進原文，按返回要回到追蹤表，不是主
畫面），但**不可以**沿用 review-mode——使用者點進追蹤命中的原文，八成就是
要編輯它，那正是追蹤存在的目的；誤鎖住編輯是這次要避免的坑。

這裡全部是原始碼層的靜態斷言（跟 test_notes_floating_back.py 同一套慣例，
專案沒有 JS 執行環境可以真的模擬 DOMContentLoaded）。用「切出 `from` 這個
handler 的程式碼區塊」而不是整份原始碼掃字串，是因為 `review-mode` 這個
字串在檔案別處也合法出現（例如 CSS 規則、AI 摘要分支本身）——只掃字串在
不在整份檔案裡，测不出『它是不是被套用到追蹤分支』這件事。
"""
from pathlib import Path

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def _from_handler_block():
    """切出處理 `?from=` 的 DOMContentLoaded handler 完整程式碼（含大括號），
    用簡單的括號計數而不是正規表示式——block 內部本來就有巢狀的
    `if {} else {}`，regex 配不出巢狀結構，配到第一個 `}` 就會提早截斷，
    看起來像是通過但其實只驗證了一半的程式碼。
    """
    src = EDITOR.read_text(encoding='utf-8')
    marker = "var from = new URLSearchParams(location.search).get('from');"
    start = src.index(marker)
    # 往前找到包住這行的 `document.addEventListener('DOMContentLoaded', function() {`
    fn_start = src.rindex('document.addEventListener', 0, start)
    brace_open = src.index('{', fn_start)
    depth = 0
    i = brace_open
    while i < len(src):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return src[fn_start:i + 1]
        i += 1
    raise AssertionError('沒找到 from handler 的結尾大括號，切割邏輯本身壞了')


def test_from_handler_still_whitelists_ai_summary_shape():
    """既有的 AI 摘要形狀 `/n/<id>(#b-<n>)?` 不可以被誤改掉或收窄。"""
    block = _from_handler_block()
    assert r"/^\/n\/\d+(#b-\d+)?$/" in block, (
        '找不到 AI 摘要 from 值的白名單 regex，AI 摘要的 review 連結會被誤擋'
    )


def test_from_handler_admits_tracking_table_shape():
    """建檔追蹤表的 from 值形狀要被放行——不放行的話，追蹤表點過去的原文，
    返回鈕會被開頭的 whitelist 擋掉，整段等於沒接上。"""
    block = _from_handler_block()
    assert r"/^\/n\/tracking\/\d+" in block, (
        '找不到建檔追蹤表 from 值的白名單 regex，追蹤表的返回連結會被擋掉'
    )


def test_ai_summary_branch_still_enters_review_mode():
    """AI 摘要那條路徑必須維持會加上 review-mode（既有行為，不可迴歸）：
    整頁鎖住不可編輯＋浮動鈕改標「← 返回摘要」。"""
    block = _from_handler_block()
    assert "classList.add('review-mode')" in block
    assert '← 返回摘要' in block


def test_tracking_branch_does_not_enter_review_mode():
    """建檔追蹤那條路徑絕對不能加 review-mode——這是本次修正要守住的核心行為：
    使用者從追蹤表點進原文通常就是要編輯它，套用 AI 摘要專用的唯讀鎖會讓
    使用者連編輯都做不了。

    做法：把 handler 從 `else` 開始（建檔追蹤分支）切到 handler 結尾，只在
    這一段裡找 review-mode，AI 摘要分支（`if (isSummary) { ... }`）本來就
    合法含有它，混在一起找會被那份掩蓋掉。
    """
    block = _from_handler_block()
    else_idx = block.index('} else {')
    tracking_branch = block[else_idx:]
    assert 'review-mode' not in tracking_branch, (
        '建檔追蹤分支不可以出現 review-mode —— 那是 AI 摘要 review 流程專用的'
        '唯讀鎖，套到這裡會誤鎖住使用者要編輯的筆記'
    )


def test_tracking_branch_relabels_back_button_distinctly():
    """『返回鈕要說清楚會去哪裡』——建檔追蹤分支要給浮動返回鈕一個跟
    「← 返回摘要」不同的標籤，讓使用者看得出這次返回不是回摘要而是回追蹤表。
    """
    block = _from_handler_block()
    else_idx = block.index('} else {')
    tracking_branch = block[else_idx:]
    assert 'floatBack.textContent' in tracking_branch, (
        '建檔追蹤分支沒有重貼浮動返回鈕的標籤'
    )
    assert '← 返回摘要' not in tracking_branch, (
        '建檔追蹤分支不該沿用「返回摘要」這個 AI 摘要專用的標籤'
    )


def test_search_hit_back_defers_to_btn_back_not_bare_history_back():
    """checklist 命中（含建檔追蹤的 item 型連結）會顯示 search-hit-widget，
    它自己的「← 返回」鈕過去直接呼叫 `window.history.back()`——只在『剛好是
    從上一頁點進來』時碰巧正確（換分頁、重新整理、從別的入口都會失效或走
    錯地方）。改成跟頂部 #btn-back / 浮動返回鈕一致，交給 #btn-back 自己的
    click handler 處理（它會套用 `?from=` 覆蓋、也會先釋放編輯鎖），不是各
    管各的兩套返回邏輯。
    """
    src = EDITOR.read_text(encoding='utf-8')
    marker = "var backBtn = document.getElementById('search-hit-back');"
    start = src.index(marker)
    block = src[start:start + 400]
    assert "getElementById('btn-back')" in block, (
        'search-hit-back 沒有改成鏡像 #btn-back，還是各管各的'
    )
    assert '.click()' in block, (
        'search-hit-back 應該觸發 #btn-back 的 click handler（含鎖釋放與 '
        '?from= 覆蓋），不是繞過它直接 navigate'
    )
