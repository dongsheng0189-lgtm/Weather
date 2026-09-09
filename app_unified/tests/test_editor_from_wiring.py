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
import re
from pathlib import Path

from tests._tracking_from_regex import tracking_from_pattern

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def _matching_close_brace(src, open_brace_idx):
    """給一段原始碼與其中一個 `{` 的 index，回傳跟它配對的 `}` 的 index。

    用括號計數而不是正規表示式——原始碼裡本來就有巢狀 `{}`，regex 配不出
    巢狀結構，配到第一個 `}` 就會提早截斷，看起來像是通過但其實只驗證了
    一半的程式碼。這個 helper 被下面兩個切割函式共用，理由同它們各自的
    docstring。
    """
    depth = 0
    i = open_brace_idx
    while i < len(src):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise AssertionError('沒找到對應的結尾大括號，切割邏輯本身壞了')


def _from_handler_block():
    """切出處理 `?from=` 的 DOMContentLoaded handler 完整程式碼（含大括號）。"""
    src = EDITOR.read_text(encoding='utf-8')
    marker = "var from = new URLSearchParams(location.search).get('from');"
    start = src.index(marker)
    # 往前找到包住這行的 `document.addEventListener('DOMContentLoaded', function() {`
    fn_start = src.rindex('document.addEventListener', 0, start)
    brace_open = src.index('{', fn_start)
    end = _matching_close_brace(src, brace_open)
    return src[fn_start:end + 1]


def _if_isSummary_slice():
    """回傳 `if (isSummary) { ... }` 這個 if 區塊本體，以及緊接在它後面
    `else { ... }` 區塊本體，用大括號計數（共用 `_matching_close_brace`）
    配對，不是正規表示式。

    這裡刻意要求**字面**的 `if (isSummary) {` 存在，而不是只確認 review-mode
    /「← 返回摘要」這兩個字串在整個 handler 裡有出現：光掃字串在不在整份
    block 裡，測不出「一字之差把條件反轉成 `if (!isSummary)`」這種迴歸——
    反轉條件不會移動任何文字，兩個分支的原始碼在檔案裡的位置完全不變，只
    有『哪個分支在什麼條件下執行』整個相反過來，review-mode 因此可能誤套
    到建檔追蹤分支（見這個測試檔 `test_review_mode_and_summary_label_are_
    scoped_to_if_isSummary_branch` 的說明）。
    """
    block = _from_handler_block()
    marker = 'if (isSummary) {'
    assert marker in block, (
        '找不到字面的 `if (isSummary) {`——分支條件可能被改寫了（例如反轉成 '
        '`if (!isSummary)`），這正是這裡要守住的迴歸'
    )
    if_start = block.index(marker)
    brace_open = if_start + len(marker) - 1  # marker 最後一個字元就是這個 '{'
    if_close = _matching_close_brace(block, brace_open)
    if_slice = block[if_start:if_close + 1]

    tail = block[if_close + 1:]
    m = re.match(r'\s*else\s*\{', tail)
    assert m, (
        '`if (isSummary) { ... }` 區塊後面沒有緊接著 `} else {`——'
        '分支結構被改動了'
    )
    else_open = if_close + 1 + tail.index('{')
    else_close = _matching_close_brace(block, else_open)
    else_slice = block[else_open:else_close + 1]
    return if_slice, else_slice


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


def test_tracking_from_whitelist_rejects_hostile_candidates():
    """A3：這是本次修正唯一放寬過的 open-redirect 白名單（`[^\\/#\\s]+`
    收窄成 `[^\\/#]+`，見 A1），但在此之前完全沒有任何測試斷言這條 regex
    『拒絕』什麼——既有測試（`test_from_handler_admits_tracking_table_shape`
    等）全部只確認合法字面值『存在於原始碼』，regex 真正提供的安全邊界
    （擋掉哪些形狀）完全沒有守門。

    直接對『目前檔案裡實際生效的那條 regex』（`tracking_from_pattern()`
    現讀 editor.html，不是另抄一份字面 pattern）跑一輪表列測試。
    """
    pattern = tracking_from_pattern()

    hostile = [
        # 開頭不是 /n/tracking/ —— protocol-relative，會被瀏覽器當成
        # 「同協定連去 evil.example」的 open redirect。
        '//evil.example',
        # 結尾帶換行——JS 的 $（無 m flag）不認換行前的位置，Python 的
        # $（無 re.MULTILINE）會，這裡驗證的正是 tracking_from_pattern()
        # 用 \Z 修正過這個落差，不是留著會通過。
        '/n/tracking/1\n',
        # 「編碼過的 traversal」：把 .. 編碼成 %2e%2e 躲避可能存在的
        # ".." 子字串過濾，但斜線本身沒有編碼、仍是字面 /——只要 store
        # 值裡混進真正的 / 字元，就該被 [^\/#]+ 擋下，不管前後是不是
        # encode 過。
        '/n/tracking/1?store=%2e%2e/%2e%2e/etc/passwd',
        # javascript: 偽協定——若被誤放行、又被別的程式碼路徑當成可導航的
        # URL 使用，會是一個 XSS 向量。
        'javascript:/n/tracking/1',
        # 「近似命中」：/nx/... 不是 /n/tracking/...，前綴比對若不夠嚴謹
        # （例如漏了 \b 或用錯 startsWith 語意）可能誤放行。
        '/nx/tracking/1',
    ]
    for candidate in hostile:
        assert not pattern.match(candidate), f'不該放行的值卻通過了白名單：{candidate!r}'

    legit = [
        '/n/tracking/1',
        '/n/tracking/1?store=B店',
        '/n/tracking/123?store=台北信義',
        # A1 修的那個形狀：店名內部帶空格——admin/routes.py 建店只
        # strip()＋非空檢查，空格放得進去，這裡必須放行（否則 A1 的
        # 迴歸就在這裡發生，不需要真的去渲染整頁才看得到）。
        '/n/tracking/1?store=台北 信義',
    ]
    for candidate in legit:
        assert pattern.match(candidate), f'合法的值卻被白名單擋下：{candidate!r}'


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


def test_review_mode_and_summary_label_are_scoped_to_if_isSummary_branch():
    """A2 迴歸守門：review-mode 與「← 返回摘要」必須掛在字面的
    `if (isSummary) { ... }` 區塊裡，不能只是整個 handler 裡某處存在這兩個
    字串。

    背景：把 `if (isSummary)` 手滑改成 `if (!isSummary)`，兩條分支執行的
    條件整個反過來（建檔追蹤連結會被鎖進 review-mode），但兩個分支的原始碼
    位置完全沒動。上面 `test_ai_summary_branch_still_enters_review_mode` 只
    掃整個 block 有沒有這兩個字串——條件反轉不影響字串存不存在，還是綠燈；
    `test_tracking_branch_does_not_enter_review_mode` /
    `test_tracking_branch_relabels_back_button_distinctly` 是用
    `block.index('} else {')` 切出「後半段」，這個位置也不因為條件反轉而
    移動，一樣量不到。這裡改成先定位字面的 `if (isSummary) {`，才能真的
    驗證「這兩件事是不是掛在正確的條件上」，而不是「這兩件事有沒有出現在
    檔案裡」。
    """
    if_slice, else_slice = _if_isSummary_slice()
    assert "classList.add('review-mode')" in if_slice, (
        'review-mode 沒有掛在 if (isSummary) 分支裡'
    )
    assert '← 返回摘要' in if_slice, (
        '「← 返回摘要」沒有掛在 if (isSummary) 分支裡'
    )
    assert 'review-mode' not in else_slice, (
        'review-mode 出現在 else（建檔追蹤）分支裡——這是 A2 要擋的迴歸'
    )


def test_search_hit_back_defers_to_float_back_widget_not_bare_history_back():
    """checklist 命中（含建檔追蹤的 item 型連結）會顯示 search-hit-widget，
    它自己的「← 返回」鈕過去直接呼叫 `window.history.back()`——只在『剛好是
    從上一頁點進來』時碰巧正確（換分頁、重新整理、從別的入口都會失效或走
    錯地方）。

    A5：優先交給 #float-back-widget 自己的 click handler，不是 #btn-back——
    真正涵蓋「dirty 時先儲存再返回」（`doSave()` + `waitForInflightWrites()`）
    的是浮動返回鈕；#btn-back 自己的 click handler 只處理 `?from=` 覆蓋跟
    導航前釋放編輯鎖，並不會存檔。checklist 筆記的標題若在按下這顆鈕前被
    改過，鏡像 #btn-back 會讓那筆修改連提示都沒有就消失。
    """
    src = EDITOR.read_text(encoding='utf-8')
    marker = "var backBtn = document.getElementById('search-hit-back');"
    start = src.index(marker)
    block = src[start:start + 500]
    assert "getElementById('float-back-widget')" in block, (
        'search-hit-back 沒有優先交給 #float-back-widget 的 click handler'
        '（它才有「dirty 時先儲存再返回」那套邏輯，#btn-back 自己的 click '
        'handler 不會存檔）'
    )
    fb_idx = block.index("getElementById('float-back-widget')")
    assert '.click()' in block[fb_idx:], (
        'float-back-widget 應該被程式化觸發 click，不是查一下就結束'
    )
