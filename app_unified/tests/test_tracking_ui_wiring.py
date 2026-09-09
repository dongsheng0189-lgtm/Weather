"""入口接線：toolbar 連結、搜尋結果上方的建檔追蹤按鈕。"""
import json as _json
import re
import uuid
import pytest


# 站台的路徑別名（app.py 的 PATH_ALIAS）：回應內容裡的 /notes/ 一律被改寫成短
# 路徑 /n/（目的是讓 Chrome 網址列/autocomplete 看不出功能）。這裡只複製
# `/notes` -> `/n` 這一條給測試用，不是在複製路由本身 —— 路由名稱仍然全部
# 透過 url_for 現算，只有這層「站台級的路徑改寫」沒有 helper 可以呼叫，才需
# 要另外還原。若這個對照表以後改了，這裡要跟著改（跟路由改名不是同一種風險）。
_PATH_ALIAS_NOTES = ('/notes', '/n')


def _short(url):
    long, short = _PATH_ALIAS_NOTES
    assert url.startswith(long), f'{url!r} 預期以 {long!r} 開頭'
    return short + url[len(long):]


CREATE_BUTTON_RE = re.compile(r'<button[^>]*data-role="create-tracking"')


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _uniq(p='T'):
    return f'{p}_{uuid.uuid4().hex[:6]}'


def _mk_user(db, store='B店', role='user'):
    from models import User
    u = User(username=_uniq('u'), role=role, store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _mk_super_admin(db, store=None):
    return _mk_user(db, store=store, role='super_admin')


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def _mk_note(db, store='B店', title='日報'):
    from models import Note
    n = Note(store=store, title=title, content='', note_type='note')
    db.session.add(n)
    db.session.commit()
    return n


def _mk_store(db, name):
    """建一筆 Store row（get-or-create）—— conftest 的 db_session fixture
    不會自動 seed 任何店，_visible_stores()/_get_stores() 兩者都直接查這張
    表，沒建過的店名永遠不會出現在 `stores in` 判斷式的右手邊（見 Fix 3 的
    說明）。get-or-create 是因為 db_session 不做 per-test rollback（見本檔
    上方 conftest 的 `_clean_vetting_whitelist` 註解），同一個店名（例如
    'C店'）在同個測試檔的多個測試裡各自建一次會撞 Store.name 的唯一約束。"""
    from models import Store
    existing = Store.query.filter_by(name=name).first()
    if existing:
        return existing
    s = Store(name=name)
    db.session.add(s)
    db.session.commit()
    return s


def test_index_has_tracking_entry_link(client, db):
    _login(client, _mk_user(db))
    html = client.get('/notes/').get_data(as_text=True)
    from flask import url_for
    with client.application.test_request_context():
        list_url = _short(url_for('notes.tracking_list_page'))
    # 掃形狀而非裸字串：<a> 標籤要真的指到追蹤列表頁、而且顯示「建檔追蹤」
    # 這個文字 —— 純字串比對 list_url 在頁面上出現，抓不到「連結標的錯了但
    # 剛好有這串文字」的情況。
    assert re.search(
        r'<a[^>]*href="' + re.escape(list_url) + r'"[^>]*>[^<]*建檔追蹤', html
    ), f'toolbar 上找不到指向 {list_url!r} 且顯示「建檔追蹤」的連結'


def test_search_page_offers_create_button(client, db):
    kw = _uniq('王小明')
    _mk_note(db, title=f'{kw} 的交接')
    _login(client, _mk_user(db))

    html = client.get(f'/notes/?q={kw}').get_data(as_text=True)

    # 掃形狀（真的有一顆 <button data-role="create-tracking">），不是掃字串
    # 「data-role="create-tracking"」本身在不在 —— 後者連按鈕被拿掉、字串
    # 換個地方（例如藏進註解或 JS 字面值）都測不出來。
    m = CREATE_BUTTON_RE.search(html)
    assert m, '找不到 <button data-role="create-tracking"> 這顆按鈕'
    # 「建檔追蹤」本身是 toolbar 連結每一頁都會渲染的文字，光比對它在不在整
    # 份 html 裡是空判斷（toolbar 存在就一定為真，不會因為這顆按鈕消失而變
    # 紅）；改成要求它出現在按鈕之後不遠處，確認按鈕本身也帶著這段文字。
    tail = html[m.start():m.start() + 400]
    assert '建檔追蹤' in tail, '按鈕附近沒有「建檔追蹤」文字'


def test_search_page_offers_open_button_when_already_tracked(client, db):
    kw = _uniq('王小明')
    _mk_note(db, title=f'{kw} 的交接')
    _login(client, _mk_user(db))
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']

    html = client.get(f'/notes/?q={kw}').get_data(as_text=True)

    from flask import url_for
    with client.application.test_request_context():
        detail_url = _short(url_for('notes.tracking_detail_page', kid=kid))
    assert detail_url in html
    assert '開啟追蹤表' in html
    assert not CREATE_BUTTON_RE.search(html), (
        '已經有追蹤表了，不應該還看得到建立按鈕（形狀檢查，不只是字串）'
    )


def test_non_search_page_has_no_create_button(client, db):
    """建檔追蹤只從搜尋結果進入 —— 這是「搜尋=暫時 / 建檔=持續」分界的一部分。"""
    _login(client, _mk_user(db))
    html = client.get('/notes/').get_data(as_text=True)
    assert not CREATE_BUTTON_RE.search(html)


# ─── Fix 1：super_admin 的店別範圍要跟頁面一致，不能悄悄落回 current_user.store ───

def test_super_admin_with_store_param_scopes_create_to_that_store(client, db):
    """super_admin 帶 ?store=C店 時，畫面上看的是 C店 的搜尋結果；建立追蹤表
    的請求必須也指名 C店，不能讓 fetch 沒帶 store、悄悄落到
    effective_store() 預設的 current_user.store（這裡故意設成不同的 B店，
    確保測的是「有沒有帶對 store」而不是巧合相符）。

    C店 必須真的是一間存在的 Store row —— routes.py index() 只在
    `store_filter in stores`（stores = _visible_stores()，查自 Store 表）
    才會精確過濾成單一店，否則落到「全部未隱藏店」的多店分支。conftest 不會
    自動 seed 任何 Store，所以這裡自己建，讓這個請求真的走到「已限定單一
    店別」那條路徑，而不是巧合通過（Fix 3：之前這個測試沒建 Store row，
    `'C店' in stores` 恆為 False，實際走的是未限定分支，只是因為當時的
    判斷式改測 current_store 的原始真假值才矇混過關）。"""
    kw = _uniq('王小明')
    _mk_store(db, 'C店')
    _mk_note(db, store='C店', title=f'{kw} 的交接')
    admin = _mk_super_admin(db, store='B店')
    _login(client, admin)

    html = client.get(f'/notes/?store=C店&q={kw}').get_data(as_text=True)

    assert CREATE_BUTTON_RE.search(html), '單一店別已限定，建立按鈕應該出現'
    expected_store_js = 'var SCOPED_STORE = ' + _json.dumps('C店') + ';'
    assert expected_store_js in html, (
        f'建立追蹤表用的 SCOPED_STORE 沒有等於畫面正在看的 C店：期望片段 '
        f'{expected_store_js!r}'
    )


def test_super_admin_with_unknown_store_param_gets_hint_not_button(client, db):
    """super_admin 帶 ?store=<不存在/未建的店名> 時，routes.py index() 的
    `store_filter in stores` 判不到，跟完全沒帶 ?store= 一樣落到「全部未隱藏
    店」的多店分支 —— 這是 Fix 2 要修的那個門：模板原本的判斷式只測
    current_store 的真假值，`?store=亂打的名字` 會被誤判成「已限定」，讓建立
    按鈕出現、create_tracking 拿一個查無此店的名字去建表。這裡故意不建
    'Z店' 這個 Store row，模擬隱藏店/typo/stale 連結三種情況共同的效果。"""
    kw = _uniq('王小明')
    _mk_store(db, 'C店')
    _mk_note(db, store='C店', title=f'{kw} 的交接')
    admin = _mk_super_admin(db, store='B店')
    _login(client, admin)

    html = client.get(f'/notes/?store=Z店&q={kw}').get_data(as_text=True)

    assert not CREATE_BUTTON_RE.search(html), (
        '?store= 帶到一個不在 stores 清單裡的店名，不該出現建立按鈕'
        '（畫面實際上是多店檢視，跟 current_store 的原始字面值無關）'
    )
    assert '請先在上方選一間店別' in html, '缺少引導使用者先選店的提示文字'


def test_super_admin_without_store_param_gets_hint_not_button(client, db):
    """super_admin 沒帶 ?store= 時，搜尋結果橫跨好幾間未隱藏的店（見
    routes.py index() 的 store_filter 分支）。一張追蹤表只能屬於一個店，這
    種情況下沒有「該建在哪一店」的正確答案，所以不該出現建立按鈕，只給提示，
    逼使用者先選店 —— 而不是讓它靜默建到 current_user.store（這裡故意設成
    跟命中筆記不同的 B店，確保它「沒有」被拿來用）。"""
    kw = _uniq('王小明')
    _mk_note(db, store='C店', title=f'{kw} 的交接')
    admin = _mk_super_admin(db, store='B店')
    _login(client, admin)

    html = client.get(f'/notes/?q={kw}').get_data(as_text=True)

    assert not CREATE_BUTTON_RE.search(html), (
        '沒有指定店別時不該出現建立按鈕（會不知道該建在哪一店）'
    )
    assert '請先在上方選一間店別' in html, '缺少引導使用者先選店的提示文字'


def test_plain_user_create_button_unaffected_by_fix1(client, db):
    """一般使用者永遠鎖在自己店（effective_store() 忽略 query string），
    Fix 1(b) 的「未限定店別就隱藏按鈕」規則不該套用到他們身上 —— 按鈕要照
    現行行為出現，且不需要、也不會帶上 ?store=（SCOPED_STORE 應為空）。"""
    kw = _uniq('王小明')
    _mk_note(db, store='B店', title=f'{kw} 的交接')
    _login(client, _mk_user(db, store='B店'))

    html = client.get(f'/notes/?q={kw}').get_data(as_text=True)

    assert CREATE_BUTTON_RE.search(html), '一般使用者的建立按鈕不該被 Fix 1(b) 隱藏'
    assert '請先在上方選一間店別' not in html
    assert "var SCOPED_STORE = '';" not in html  # tojson 用雙引號，不是單引號
    assert 'var SCOPED_STORE = "";' in html
