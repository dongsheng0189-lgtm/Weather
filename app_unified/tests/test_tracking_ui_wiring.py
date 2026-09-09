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


def test_unscoped_super_admin_gets_hint_even_when_own_store_has_a_table(client, db):
    """未限定店別的 super_admin，即使自己店已有同關鍵字的追蹤表，也只能看到提示。

    這是三分支「先判 tracking_scoped、再判 tracking_existing_id」順序的守門。
    順序若被調回「先判 existing」，這個狀態就會渲染出「開啟追蹤表」——連到一張
    只含 B 店資料的表，而畫面上的搜尋結果橫跨 A/B/C 店。那正是本功能兩段式流程
    要避免的「搜尋結果 ≠ 追蹤表」錯配，而且它還會把提示整個蓋掉，使用者永遠不會
    知道自己的檢視沒有限定店別。

    這個狀態是唯一能分辨兩種分支順序的狀態，其他測試都沒有同時湊齊
    「未限定店別」＋「自己店已有表」這兩個條件。
    """
    kw = _uniq('王小明')
    _mk_store(db, 'B店')
    _mk_note(db, store='C店', title=f'{kw} 的交接')   # 畫面上看到的是 C 店的命中
    admin = _mk_super_admin(db, store='B店')
    _login(client, admin)

    # 不帶 ?store= 建表 → effective_store() 落到 admin 自己的 B店
    r = client.post('/notes/api/tracking', json={'keyword': kw})
    assert r.status_code == 200, r.get_data(as_text=True)
    from models import TrackedKeyword
    assert db.session.get(TrackedKeyword, r.get_json()['id']).store == 'B店'

    html = client.get(f'/notes/?q={kw}').get_data(as_text=True)

    assert '開啟追蹤表' not in html, (
        '未限定店別時不可以提供「開啟追蹤表」—— 那張表只有 B店 的資料，'
        '畫面上的結果卻橫跨多店'
    )
    assert not CREATE_BUTTON_RE.search(html)
    assert '請先在上方選一間店別' in html, (
        '既有表不可以把「請先選店」的提示蓋掉，否則使用者不會知道檢視未限定店別'
    )


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


# ─── Fix 2：super_admin 看別店的清單頁時，開啟／刪除都要帶上 store ───
#
# tracking_list.html 早於 index.html 那次「_createUrl / _detailUrl 一律帶
# store」的修正而寫成，一直沒被回頭補上：detail 連結用的是不帶 store 的
# url_for，刪除 fetch 也是不帶 store 的字面路徑。兩個端點都靠 effective_store()
# 解析店別，對 super_admin 而言沒有 ?store= 就會落到 current_user.store（甚至
# None）——不是畫面上正在看的那個店，於是這裡建的表 super_admin 自己建完，
# 下一步（開啟／刪除）就先撞 403。

def test_super_admin_list_detail_link_carries_store(client, db):
    """列表頁的『開啟』連結要帶上 store，super_admin 才點得進去。"""
    kw = _uniq('王小明')
    _mk_store(db, 'A店')
    owner = _mk_user(db, store='A店')
    _login(client, owner)
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']

    admin = _mk_super_admin(db, store=None)
    _login(client, admin)

    html = client.get('/notes/tracking/?store=A店').get_data(as_text=True)

    from flask import url_for
    with client.application.test_request_context():
        detail_url = _short(url_for('notes.tracking_detail_page', kid=kid, store='A店'))
    assert detail_url in html, (
        f'列表頁的連結沒有帶上 ?store=A店：期望片段 {detail_url!r}'
    )

    # 證明「有沒有帶 store」不是巧合：沒帶會 403，模板產生的那個連結（帶了）
    # 才進得去。
    assert client.get(f'/notes/tracking/{kid}').status_code == 403
    assert client.get(f'/notes/tracking/{kid}?store=A店').status_code == 200


def test_super_admin_list_delete_fetch_carries_store(client, db):
    """列表頁的刪除 fetch 要帶上 store，super_admin 才刪得掉別店的表。"""
    kw = _uniq('王小明')
    _mk_store(db, 'A店')
    owner = _mk_user(db, store='A店')
    _login(client, owner)
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']

    admin = _mk_super_admin(db, store=None)
    _login(client, admin)

    html = client.get('/notes/tracking/?store=A店').get_data(as_text=True)
    expected_store_js = 'var SCOPED_STORE = ' + _json.dumps('A店') + ';'
    assert expected_store_js in html, (
        f'刪除用的 SCOPED_STORE 沒有等於畫面正在看的 A店：期望片段 '
        f'{expected_store_js!r}'
    )
    assert "delUrl += '?store=' + encodeURIComponent(SCOPED_STORE)" in html, (
        '刪除的 fetch URL 沒有依 SCOPED_STORE 接上 ?store='
    )

    # 證明這條 query string 不是裝飾：沒帶 store 一樣會 403，帶了才能真的刪掉
    # ——跟模板實際會送出的請求一致。
    assert client.delete(f'/notes/api/tracking/{kid}').status_code == 403
    from models import TrackedKeyword
    assert db.session.get(TrackedKeyword, kid) is not None
    assert client.delete(f'/notes/api/tracking/{kid}?store=A店').status_code == 200
    assert db.session.get(TrackedKeyword, kid) is None


# ─── Fix 4：詳細頁本身開得進去，但頁面裡發出去的備註／剔除／還原 fetch ───
# 都沒有一起帶上 store —— 跟 Fix 2 是同一類缺陷（super_admin 沒有 ?store=
# 就會被 effective_store() 落回 current_user.store，一律 403），只是這次是
# 「頁面本身進得去，頁面裡的動作進不去」，之前的 Fix 2 只補了列表頁的連結
# 跟刪除，沒有把詳細頁自己發的三個 fetch 一起補上。

def test_super_admin_detail_memo_fetch_carries_store(client, db):
    """詳細頁的備註 fetch（`{{ url_for(...) }}` 內嵌整條路徑）要帶上這張表
    真正的店別，super_admin 才存得進去。"""
    from flask import url_for
    kw = _uniq('王小明')
    _mk_store(db, 'A店')
    owner = _mk_user(db, store='A店')
    _login(client, owner)
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']

    admin = _mk_super_admin(db, store=None)
    _login(client, admin)

    html = client.get(f'/notes/tracking/{kid}?store=A店').get_data(as_text=True)

    with client.application.test_request_context():
        expected_url = _short(url_for('notes.set_tracking_memo', kid=kid, store='A店'))
    assert f"fetch('{expected_url}'" in html, (
        f'備註存檔的 fetch 路徑沒有帶上這張表的店別：期望片段 '
        f"fetch('{expected_url}'"
    )

    # 證明這條 query string 不是裝飾：沒帶 store 一樣 403，帶了才存得進去。
    assert client.put(f'/notes/api/tracking/{kid}/memo',
                       json={'text': 'x'}).status_code == 403
    r = client.put(f'/notes/api/tracking/{kid}/memo?store=A店', json={'text': 'x'})
    assert r.status_code == 200


def test_super_admin_detail_exclude_fetch_carries_store(client, db):
    """詳細頁『不相關』／『已剔除→還原』的 fetch（JS 拼 data-hit 字串）也要
    帶上這張表真正的店別，super_admin 才操作得動。"""
    kw = _uniq('王小明')
    _mk_store(db, 'A店')
    owner = _mk_user(db, store='A店')
    _mk_note(db, store='A店', title=f'{kw} 的交接')
    _login(client, owner)
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']

    admin = _mk_super_admin(db, store=None)
    _login(client, admin)

    html = client.get(f'/notes/tracking/{kid}?store=A店').get_data(as_text=True)

    expected_store_js = 'var SCOPED_STORE = ' + _json.dumps('A店') + ';'
    assert expected_store_js in html, (
        f'剔除／還原用的 SCOPED_STORE 沒有等於這張表的 A店：期望片段 '
        f'{expected_store_js!r}'
    )
    assert "?store=' + encodeURIComponent(SCOPED_STORE)" in html, (
        '剔除／還原的 fetch URL 沒有依 SCOPED_STORE 接上 ?store='
    )

    from models import TrackedKeywordHit
    hit = TrackedKeywordHit.query.filter_by(keyword_id=kid).first()
    assert hit is not None

    # 證明不是裝飾：沒帶 store 就是 403，帶了才剔除得掉。
    assert client.post(f'/notes/api/tracking/hits/{hit.id}/exclude',
                        json={'excluded': True}).status_code == 403
    r = client.post(f'/notes/api/tracking/hits/{hit.id}/exclude?store=A店',
                     json={'excluded': True})
    assert r.status_code == 200


# ─── Fix 5：『→ 原文』要帶著關鍵字跳轉、且返回要回得到這張表 ───
#
# PO 手動測試回饋（兩點）：(1) 按下建檔進去原文，返回卻跑到主畫面，應該回到
# 上一頁（這張追蹤表）才能繼續看下一筆；(2) 目前點進原文會顯示整篇，應該
# 跳轉到有關鍵字的地方。這裡驗證『→ 原文』連結本身有沒有把這兩件事該帶的
# 東西都帶上：q（給 editor.html 的 buildSearchHits 高亮/跳轉）、from（指回
# 這張表，給 editor.html 的返回鈕）、item 型命中另外帶 #item-<id> 錨點
# （直接跳到那個項目，不只是整篇文字裡有 mark）。

def test_original_link_note_hit_carries_keyword_and_from(client, db):
    from flask import url_for
    kw = _uniq('王小明')
    n = _mk_note(db, title=f'{kw} 的交接')
    _login(client, _mk_user(db))
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']

    html = client.get(f'/notes/tracking/{kid}').get_data(as_text=True)

    from_url = f'/n/tracking/{kid}?store=B店'
    with client.application.test_request_context():
        expected = _short(url_for('notes.edit_note', note_id=n.id, q=kw,
                                   **{'from': from_url}))
    # 模板用 {{ url_for(...) }} 輸出、Jinja 會自動 HTML-escape —— 這條連結
    # 有兩個 query 參數（q 跟 from）要用 & 接起來，跟只有單一參數的
    # tracking_list.html 那幾條連結不一樣，& 在頁面上會被轉成 &amp;。
    expected = expected.replace('&', '&amp;')
    assert expected in html, (
        f'note 型命中的「→ 原文」連結沒帶對 q/from：期望片段 {expected!r}'
    )


def test_original_link_item_hit_carries_item_anchor(client, db):
    from flask import url_for
    from models import Note, ChecklistItem
    kw = _uniq('王小明')
    n = Note(store='B店', title='日報', content='', note_type='checklist')
    db.session.add(n)
    db.session.commit()
    item = ChecklistItem(note_id=n.id, text=f'{kw} 遲到')
    db.session.add(item)
    db.session.commit()
    _login(client, _mk_user(db))
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']

    html = client.get(f'/notes/tracking/{kid}').get_data(as_text=True)

    from_url = f'/n/tracking/{kid}?store=B店'
    with client.application.test_request_context():
        expected = _short(url_for('notes.edit_note', note_id=n.id, q=kw,
                                   _anchor=f'item-{item.id}',
                                   **{'from': from_url}))
    expected = expected.replace('&', '&amp;')   # 理由同上一個測試
    assert expected in html, (
        f'item 型命中的「→ 原文」連結沒帶對 q/from/#item 錨點：期望片段 {expected!r}'
    )
    # 反向斷言：不能誤用母筆記 id 組錨點（那會跳到不存在的項目、或巧合跳到
    # 別的項目）。
    wrong_anchor = f'#item-{n.id}'
    if n.id != item.id:
        assert wrong_anchor not in expected


def test_detail_page_memo_textarea_has_maxlength_from_server_constant(client, db):
    """maxlength 要跟伺服器端 MEMO_MAX_LEN 引用同一個數字，不能各自寫一份、
    改一邊忘了改另一邊——不一致的話，使用者在前端還打得下去，一送出卻被
    伺服器 400 擋下，畫面上只會看到不知道為什麼的「未儲存，請重試」。"""
    from notes.tracking import MEMO_MAX_LEN
    kw = _uniq('王小明')
    _mk_note(db, title=f'{kw} 的交接')
    _login(client, _mk_user(db))
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']

    html = client.get(f'/notes/tracking/{kid}').get_data(as_text=True)

    assert f'maxlength="{MEMO_MAX_LEN}"' in html, (
        f'備註欄位缺少 maxlength="{MEMO_MAX_LEN}"，或跟伺服器端常數對不上'
    )
    # 使用者要在打到超過上限「之前」就看得到限制，不是送出被 400 擋下才知道。
    assert str(MEMO_MAX_LEN) in html.split('placeholder=')[1][:40], (
        '提示文字（placeholder）看不到字數上限'
    )


def test_detail_page_memo_fetch_matches_url_for_this_table(client, db):
    """備註改成整張表一欄後（2026-09-09），fetch 路徑要對到
    notes.set_tracking_memo 這支以 kid（不是 hid）為參數的路由，而且要用
    『這一頁自己的』kid——模板是直接用 `{{ url_for(...) }}` 內嵌整條路徑
    （不像剔除那樣靠 JS 在瀏覽器端拼字串接 data-hit），所以正確性只能靠真的
    渲染這一頁、比對頁面上實際出現的字面路徑來驗證；靜態掃模板原始碼看不出
    kid 有沒有被代換對，也看不出這裡是否不小心抄成 notes.set_hit_note 這種
    已經不存在的舊路由名。

    刻意建兩張表，只驗證『這一頁』的 kid 出現在 fetch 裡、另一張表的 kid
    不出現——避免『路徑裡剛好有一個數字看起來像 url_for 產生的』這種巧合
    通過。"""
    from flask import url_for
    kw_a = _uniq('王小明')
    kw_b = _uniq('陳小華')
    _login(client, _mk_user(db))
    kid_a = client.post('/notes/api/tracking', json={'keyword': kw_a}).get_json()['id']
    kid_b = client.post('/notes/api/tracking', json={'keyword': kw_b}).get_json()['id']

    html = client.get(f'/notes/tracking/{kid_a}').get_data(as_text=True)

    with client.application.test_request_context():
        # Fix（本次修正）：這張表所在的店別（預設 _mk_user 的 'B店'）一律帶上
        # ?store=，理由見下面的 Fix 4 測試群組 —— super_admin 看別店的詳細頁時
        # 若漏帶，備註／剔除都會 403。
        expected_url = _short(url_for('notes.set_tracking_memo', kid=kid_a, store='B店'))
        other_url = _short(url_for('notes.set_tracking_memo', kid=kid_b, store='B店'))
    assert f"fetch('{expected_url}'" in html, (
        f'備註存檔的 fetch 路徑跟這一頁（kid={kid_a}）對不上：期望片段 '
        f"fetch('{expected_url}'"
    )
    assert other_url not in html, (
        f'不該出現在另一張表（kid={kid_b}）的路徑，卻出現在這一頁'
    )
