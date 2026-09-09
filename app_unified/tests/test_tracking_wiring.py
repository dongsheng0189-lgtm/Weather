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


def test_memo_and_exclude_paths_match_url_for(app):
    """tracking_detail.html 裡兩個 fetch 的路徑字面值，要對得上目前的路由。"""
    with app.test_request_context():
        note_url = url_for('notes.set_hit_note', hid=999999)
        exclude_url = url_for('notes.toggle_hit_exclude', hid=999999)

    detail_src = DETAIL.read_text(encoding='utf-8')

    note_prefix, note_suffix = _split_on_placeholder(note_url, 999999)
    expected_note_js = _expected_js_concat(note_prefix, 'ta.dataset.hit', note_suffix)
    assert expected_note_js in detail_src, (
        f'備註存檔的路徑跟 notes.set_hit_note 的路由對不上：期望片段 {expected_note_js!r}'
    )

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
