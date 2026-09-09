"""建檔追蹤前端的接線守門（2026-09-09）。

同專案 test_quick_tag_wiring.py 慣例：原始碼層斷言，不用瀏覽器。

兩份模板把 API 路徑寫死成字串字面值（不是 url_for 產生），JS 端也沒有任何
測試檢查 data-role hook 存不存在。現況剛好對得上路由，但改路由名稱、或有人
手滑刪掉某個 data-role，整個測試套件都不會有任何一個測試變紅 —— 備註、剔除、
刪除三個功能會在使用者眼裡「按了沒反應」，卻沒有任何守門。

路徑的期望值刻意從 `url_for` 現算，不手動抄一份字面值到測試裡 —— 抄字面值的
話，路由被改名時 url_for 和 JS 裡的舊字串會「一起」變得跟現實不符但彼此仍然
相等，測試照樣綠燈、完全失去偵測力。
"""
from pathlib import Path

from flask import url_for

DETAIL = Path(__file__).parent.parent / 'templates' / 'notes' / 'tracking_detail.html'
LIST = Path(__file__).parent.parent / 'templates' / 'notes' / 'tracking_list.html'
INDEX = Path(__file__).parent.parent / 'templates' / 'notes' / 'index.html'


def test_data_role_hooks_present():
    """四個 data-role 是 JS 綁事件的唯一依據，少一個對應功能就整個失效。"""
    detail_src = DETAIL.read_text(encoding='utf-8')
    list_src = LIST.read_text(encoding='utf-8')
    assert 'data-role="memo"' in detail_src, '缺少備註欄位的 data-role hook'
    assert 'data-role="exclude"' in detail_src, '缺少「不相關」按鈕的 data-role hook'
    assert 'data-role="restore-exclude"' in detail_src, (
        '缺少「已剔除」區塊還原按鈕的 data-role hook'
    )
    assert 'data-role="del-tracking"' in list_src, '缺少刪除按鈕的 data-role hook'


def _split_on_placeholder(url, placeholder):
    """把 url_for 產生的路徑，用佔位的 id 值切成「前段」跟「後段」。

    JS 端的寫法是 `'<前段>' + xxx.dataset.yyy + '<後段>'` 這種字串串接，用同一
    組前後段去比對，只要 route 的路徑（不含變動的 id）沒變就會過；路徑一旦
    改名，前段或後段就會連帶變化，跟模板裡寫死的舊字面值對不上，測試會紅。
    """
    marker = str(placeholder)
    assert url.count(marker) == 1, f'佔位值 {marker} 沒有在 {url} 裡剛好出現一次'
    return url.split(marker)


def _expected_js_concat(prefix, var_expr, suffix):
    """組出「前段 + JS 變數 + 後段」的字串串接寫法，id 在路徑最尾端時沒有
    後段可接，JS 端本來就不會多寫一個空字串的 `+ ''`，這裡要跟著省略，
    否則永遠比對不到。"""
    js = "'" + prefix + "' + " + var_expr
    if suffix:
        js += " + '" + suffix + "'"
    return js


def test_exclude_path_matches_url_for(app):
    """tracking_detail.html 的『不相關』fetch 路徑，要對得上目前的路由。

    備註的 fetch 路徑不再走這種「JS 字串串接 + data-hit 佔位」的形狀 ——
    2026-09-09 改版後備註是整張表一欄，一頁只有一個備註框、kid 在渲染當下就
    已知，模板直接用 `{{ url_for(...) }}` 內嵌整條路徑，不需要 JS 端拼字串。
    這種形狀改成用實際渲染出來的頁面驗證，見
    test_tracking_ui_wiring.py::test_detail_page_memo_fetch_matches_url_for_this_table。
    """
    with app.test_request_context():
        exclude_url = url_for('notes.toggle_hit_exclude', hid=999999)

    detail_src = DETAIL.read_text(encoding='utf-8')

    exclude_prefix, exclude_suffix = _split_on_placeholder(exclude_url, 999999)
    expected_exclude_js = _expected_js_concat(exclude_prefix, 'btn.dataset.hit', exclude_suffix)
    assert expected_exclude_js in detail_src, (
        f'「不相關」的路徑跟 notes.toggle_hit_exclude 的路由對不上：'
        f'期望片段 {expected_exclude_js!r}'
    )


def test_delete_path_matches_url_for(app):
    """tracking_list.html 的刪除 fetch 路徑，要對得上目前的路由。"""
    with app.test_request_context():
        delete_url = url_for('notes.delete_tracking', kid=999999)

    list_src = LIST.read_text(encoding='utf-8')

    delete_prefix, delete_suffix = _split_on_placeholder(delete_url, 999999)
    expected_delete_js = _expected_js_concat(delete_prefix, 'btn.dataset.id', delete_suffix)
    assert expected_delete_js in list_src, (
        f'刪除的路徑跟 notes.delete_tracking 的路由對不上：期望片段 {expected_delete_js!r}'
    )


def test_index_create_tracking_fetch_matches_url_for(app):
    """index.html 搜尋結果按鈕的建立 fetch，路徑要對得上 notes.create_tracking。

    Fix 1(a) 之後這個路徑會依 current_store 動態接上 `?store=`（super_admin
    scoped 到單一店別時），所以不再是 fetch() 呼叫裡的字面字串，而是先指定
    給 `_createUrl` 變數、fetch() 再讀這個變數。這裡分兩段驗證：字面值本身
    對得上 url_for，而且真的被 fetch() 使用（不是宣告了卻沒用到的死變數）。
    """
    with app.test_request_context():
        create_url = url_for('notes.create_tracking')

    index_src = INDEX.read_text(encoding='utf-8')

    assert f"var _createUrl = '{create_url}'" in index_src, (
        f'建檔追蹤的建立路徑跟 notes.create_tracking 的路由對不上：期望 {create_url!r}'
    )
    assert 'fetch(_createUrl' in index_src, (
        '_createUrl 沒有被 fetch() 實際使用'
    )


def test_index_redirect_path_matches_url_for(app):
    """index.html 建立成功後的導向路徑，要對得上 notes.tracking_detail_page。

    同上，Fix 1(a) 之後這段路徑也會視 current_store 動態接上 `?store=`，
    先組進 `_detailUrl` 變數再指定給 location.href。
    """
    with app.test_request_context():
        detail_url = url_for('notes.tracking_detail_page', kid=999999)

    index_src = INDEX.read_text(encoding='utf-8')

    detail_prefix, detail_suffix = _split_on_placeholder(detail_url, 999999)
    expected_redirect_js = _expected_js_concat(detail_prefix, 'd.id', detail_suffix)
    assert f'var _detailUrl = {expected_redirect_js}' in index_src, (
        f'建立成功後的導向路徑跟 notes.tracking_detail_page 的路由對不上：'
        f'期望片段 {expected_redirect_js!r}'
    )
    assert 'location.href = _detailUrl' in index_src, (
        '_detailUrl 沒有被 location.href 實際使用'
    )


# ─── B3：備註存檔失敗的可見狀態沒有任何測試守著 ───
#
# 這次改版把備註存檔失敗的處理，從「每一列各自 forEach + closest('.hit-card')
# 找 DOM」整個重寫成「單一元素的 IIFE」，CSS class 也全部改名（改版前後長相
# 完全不同）。但沒有任何既有測試提到 hint 元素、失敗 class、`r.ok`、`.catch`
# ——若 hint 元素或失敗規則被刪掉，不會有任何測試變紅：使用者看著打好的字
# 留在畫面上、以為存了，其實沒有（見這支路由 docstring 對這個風險的說明）。

def _memo_save_block():
    """切出備註存檔的 IIFE（`(function () { ... })();`），用大括號計數而非
    正規表示式（原因同 test_editor_from_wiring.py 的 `_from_handler_block`：
    區塊內有巢狀 `{}`）。

    只在這個範圍內找『失敗狀態』的相關程式碼——不跟下面「不相關」／
    「還原」兩顆按鈕各自的 `r.ok` / `.catch` 混在一起找（那是各自獨立的
    失敗處理，不是這次要守的行為），也避開「不相關」按鈕註解裡提到的字面
    字串 `confirm()`（那段註解在解釋『刻意不用 confirm()』，不是真的呼叫
    它）——不切開範圍找，這裡對『memo 區塊不可以用 alert()/confirm()』的
    檢查會被這段註解文字污染。
    """
    src = DETAIL.read_text(encoding='utf-8')
    marker = "var ta = document.querySelector('[data-role=\"memo\"]');"
    marker_idx = src.index(marker)
    start = src.rindex('(function () {', 0, marker_idx)
    brace_open = src.index('{', start)
    depth = 0
    i = brace_open
    while i < len(src):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError('沒找到備註存檔 IIFE 的結尾大括號，切割邏輯本身壞了')


def test_memo_hint_element_is_rendered():
    """失敗提示的 DOM 掛勾要真的渲染出來，JS 才找得到它去顯示/隱藏。"""
    detail_src = DETAIL.read_text(encoding='utf-8')
    assert 'data-role="memo-hint"' in detail_src, (
        '缺少備註存檔失敗提示的 data-role hook'
    )


def test_memo_save_failure_has_a_css_rule():
    """失敗狀態要有視覺區別，不能跟成功長得一模一樣（這支路由的 docstring
    明講了理由：TrackedKeywordMemoLog 只記真的落地的異動，救不了一次沒送
    出去的請求）。"""
    detail_src = DETAIL.read_text(encoding='utf-8')
    assert '.tracking-memo.save-failed' in detail_src, (
        '缺少備註存檔失敗的 CSS 規則'
    )


def test_memo_save_checks_response_ok_and_has_catch():
    """`r.json()` 對非 2xx 回應（403/404，或 session 過期被導回登入頁的
    HTML）可能安靜地 resolve 或 reject，必須先檢查 `r.ok` 才能信任回應；
    整條 promise chain 也要有 `.catch` 接住網路層本身的失敗（例如斷線讓
    fetch() 直接 reject）——兩者缺一，使用者都可能誤以為存了、其實沒存。"""
    block = _memo_save_block()
    assert 'r.ok' in block, '備註存檔沒有檢查 r.ok'
    assert '.catch(' in block, '備註存檔的 promise chain 沒有 .catch 接住失敗'


def test_memo_save_failure_does_not_use_blocking_dialogs():
    """失敗提示走的是行內視覺變化（`markFailed` 切 CSS class），不能用
    `alert()` / `confirm()`——那種瀏覽器原生 modal 會卡住自動化工具，跟這
    頁面「不相關」按鈕特意避開 `confirm()` 是同一個理由。"""
    block = _memo_save_block()
    assert 'alert(' not in block, '備註存檔失敗不該用 alert() 阻斷式對話框'
    assert 'confirm(' not in block, '備註存檔失敗不該用 confirm() 阻斷式對話框'
