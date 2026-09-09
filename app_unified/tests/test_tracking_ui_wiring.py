"""入口接線：toolbar 連結、搜尋結果上方的建檔追蹤按鈕。"""
import json as _json
import uuid
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _uniq(p='T'):
    return f'{p}_{uuid.uuid4().hex[:6]}'


def _mk_user(db, store='B店'):
    from models import User
    u = User(username=_uniq('u'), role='user', store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


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


def test_index_has_tracking_entry_link(client, db):
    _login(client, _mk_user(db))
    html = client.get('/notes/').get_data(as_text=True)
    # app.py 有全站的路徑別名（PATH_ALIAS）：回應內容裡的 /notes/ 一律被改寫成
    # 短路徑 /n/（目的是讓 Chrome 網址列/autocomplete 看不出功能），所以這裡
    # 斷言的是使用者瀏覽器實際會拿到的字串，不是路由註冊時的原始長路徑。
    assert '/n/tracking/' in html
    assert '建檔追蹤' in html


def test_search_page_offers_create_button(client, db):
    kw = _uniq('王小明')
    _mk_note(db, title=f'{kw} 的交接')
    _login(client, _mk_user(db))

    html = client.get(f'/notes/?q={kw}').get_data(as_text=True)

    assert 'data-role="create-tracking"' in html
    assert '建檔追蹤' in html


def test_search_page_offers_open_button_when_already_tracked(client, db):
    kw = _uniq('王小明')
    _mk_note(db, title=f'{kw} 的交接')
    _login(client, _mk_user(db))
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']

    html = client.get(f'/notes/?q={kw}').get_data(as_text=True)

    # 同 test_index_has_tracking_entry_link：回應內容的 /notes/ 已被 PATH_ALIAS
    # 改寫成 /n/。
    assert f'/n/tracking/{kid}' in html
    assert '開啟追蹤表' in html
    assert 'data-role="create-tracking"' not in html


def test_non_search_page_has_no_create_button(client, db):
    """建檔追蹤只從搜尋結果進入 —— 這是「搜尋=暫時 / 建檔=持續」分界的一部分。"""
    _login(client, _mk_user(db))
    html = client.get('/notes/').get_data(as_text=True)
    assert 'data-role="create-tracking"' not in html
