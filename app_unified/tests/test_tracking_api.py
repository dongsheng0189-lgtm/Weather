"""建檔追蹤 API：建立／列表／刪除／權限。"""
import json as _json
import uuid
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _uniq(prefix='T'):
    return f'{prefix}_{uuid.uuid4().hex[:6]}'


def _mk_user(db, role='user', store='B店'):
    from models import User
    u = User(username=_uniq(role), role=role, store=store,
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


def test_create_tracking_runs_full_scan(client, db):
    kw = _uniq('王小明')
    _mk_note(db, title=f'{kw} 的交接')
    u = _mk_user(db)
    _login(client, u)

    r = client.post('/notes/api/tracking', json={'keyword': kw})

    assert r.status_code == 200
    data = r.get_json()
    assert data['hit_count'] == 1
    from models import TrackedKeyword, TrackedKeywordHit
    t = db.session.get(TrackedKeyword, data['id'])
    assert t.keyword == kw
    assert t.store == 'B店'
    assert t.created_by == u.id
    assert t.created_by_name == u.username
    assert TrackedKeywordHit.query.filter_by(keyword_id=t.id).count() == 1


def test_create_tracking_requires_a_store(client, db):
    """建立追蹤表時解析不出店別 → 明確 400，不可靜默用某個預設店別建下去。

    對稱於 list / page 兩條路的 400。少了這條，create 這一路退回靜默 fallback
    時整套測試仍會綠。
    """
    from models import User
    u = User(username=_uniq('nostore'), role='super_admin', store=None,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    _login(client, u)

    r = client.post('/notes/api/tracking', json={'keyword': _uniq('王小明')})

    assert r.status_code == 400
    assert '店別' in r.get_json()['error']


def test_create_tracking_rejects_blank_keyword(client, db):
    u = _mk_user(db)
    _login(client, u)
    r = client.post('/notes/api/tracking', json={'keyword': '   '})
    assert r.status_code == 400
    assert '關鍵字' in r.get_json()['error']


def test_create_tracking_rejects_overlong_keyword(client, db):
    """關鍵字落在 EncryptedText，DB 端沒有長度限制，不擋的話使用者想送多長都
    插得進去。比照 notes/tags.py 的 create_tag（標籤名稱上限 20）：關鍵字通常
    是人名或短短幾個字的描述，20 字綽綽有餘。

    這裡不能用 TrackedKeyword.query.count() == 0 當「沒有建成」的證據——本檔
    測試共用同一個 db_session、不做 per-test rollback（同專案慣例），跑在這條
    測試前面的其他測試早就留下別的資料列，全域計數不會是 0。改成比對這個
    使用者所在店別的筆數，請求前後不變，才是這裡真正要守的事。"""
    from models import TrackedKeyword
    u = _mk_user(db)
    _login(client, u)
    before = TrackedKeyword.query.filter_by(store=u.store).count()

    r = client.post('/notes/api/tracking', json={'keyword': 'x' * 21})

    assert r.status_code == 400
    assert '關鍵字' in r.get_json()['error']
    assert TrackedKeyword.query.filter_by(store=u.store).count() == before


def test_create_tracking_accepts_keyword_at_max_len(client, db):
    """邊界值：剛好 20 字要放行，不可以連合法輸入都被擋。"""
    u = _mk_user(db)
    _login(client, u)
    r = client.post('/notes/api/tracking', json={'keyword': 'x' * 20})
    assert r.status_code == 200


def test_create_tracking_returns_existing_when_duplicate(client, db):
    """同店同關鍵字不重複建表 —— 直接把既有那張回給前端。

    kw 尾巴刻意固定加一個小寫字母（而非只靠 uuid4().hex 隨機出的英文字元）：
    hex 有約 6% 機率全是數字，那樣 `.upper()` 就是 no-op，第二次送出的請求
    只剩下前後空白這一種差異——route 自己的 `.strip()` 就足以讓它通過，測不到
    大小寫折疊這條路徑，變成看隨機數臉色的測試。
    """
    kw = _uniq('王小明') + 'x'
    u = _mk_user(db)
    _login(client, u)
    first = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()

    r = client.post('/notes/api/tracking', json={'keyword': f'  {kw.upper()}  '})

    assert r.status_code == 200
    assert r.get_json()['id'] == first['id']
    assert r.get_json()['existing'] is True


def test_list_tracking_is_store_scoped(client, db):
    from models import TrackedKeyword
    other = TrackedKeyword(store='A店', keyword=_uniq('別店'), created_by_name='x')
    db.session.add(other)
    db.session.commit()
    kw = _uniq('本店')
    u = _mk_user(db, store='B店')
    _login(client, u)
    client.post('/notes/api/tracking', json={'keyword': kw})

    r = client.get('/notes/api/tracking')

    assert r.status_code == 200
    names = [row['keyword'] for row in r.get_json()['tracking']]
    assert kw in names
    assert all('別店' not in n for n in names)


def test_delete_tracking_removes_hits(client, db):
    from models import TrackedKeywordHit
    kw = _uniq('王小明')
    _mk_note(db, title=f'{kw} 的交接')
    u = _mk_user(db)
    _login(client, u)
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']

    r = client.delete(f'/notes/api/tracking/{kid}')

    assert r.status_code == 200
    from models import TrackedKeyword
    assert db.session.get(TrackedKeyword, kid) is None
    assert TrackedKeywordHit.query.filter_by(keyword_id=kid).count() == 0


def test_cross_store_access_is_forbidden(client, db):
    kw = _uniq('王小明')
    owner = _mk_user(db, store='B店')
    _login(client, owner)
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']

    intruder = _mk_user(db, store='A店')
    _login(client, intruder)

    assert client.delete(f'/notes/api/tracking/{kid}').status_code == 403
    assert client.get(f'/notes/tracking/{kid}').status_code == 403


def test_tracking_requires_notes_feature(client, db):
    """未啟用 notes 模組的人看不見這個功能（沿用 blueprint 的模組開關）。"""
    from models import User
    u = User(username=_uniq('nofeat'), role='user', store='B店',
             features=_json.dumps([]))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    _login(client, u)

    assert client.get('/notes/api/tracking').status_code == 404


def test_store_page_lists_tracking(client, db):
    kw = _uniq('王小明')
    u = _mk_user(db)
    _login(client, u)
    client.post('/notes/api/tracking', json={'keyword': kw})

    r = client.get('/notes/tracking/')

    assert r.status_code == 200
    assert kw in r.get_data(as_text=True)


def test_list_tracking_without_store_is_explicit_error_not_empty(client, db):
    """store 為 None（例如沒有本店的 super_admin 忘了帶 ?store=）必須是明確的
    400，不能悄悄回一個跟「本店真的沒有任何追蹤表」外觀一樣的空清單——那正是
    規格明文禁止的「靜默資料消失」模糊地帶。"""
    u = _mk_user(db, role='super_admin', store=None)
    _login(client, u)

    r = client.get('/notes/api/tracking')

    assert r.status_code == 400
    assert '店別' in r.get_json()['error']


def test_tracking_list_page_without_store_is_explicit_error_not_empty(client, db):
    u = _mk_user(db, role='super_admin', store=None)
    _login(client, u)

    r = client.get('/notes/tracking/')

    assert r.status_code == 400
    assert '店別' in r.get_data(as_text=True)


def test_super_admin_store_override_sees_target_store(client, db):
    """_effective_store 的特權那一半：super_admin 帶 ?store= 可以看到指定店的
    追蹤表，之前完全沒有測試涵蓋（tag 那邊的同一條規則也沒有）。"""
    kw = _uniq('王小明')
    owner = _mk_user(db, store='A店')
    _login(client, owner)
    client.post('/notes/api/tracking', json={'keyword': kw})

    admin = _mk_user(db, role='super_admin', store=None)
    _login(client, admin)

    r = client.get('/notes/api/tracking?store=A店')

    assert r.status_code == 200
    names = [row['keyword'] for row in r.get_json()['tracking']]
    assert kw in names


def test_plain_user_store_override_is_ignored(client, db):
    """_effective_store 的越權防護那一半：一般 user 帶 ?store= 一律被忽略，
    仍只看得到自己的店，不能藉此偷看別店。"""
    kw = _uniq('王小明')
    owner = _mk_user(db, store='A店')
    _login(client, owner)
    client.post('/notes/api/tracking', json={'keyword': kw})

    u = _mk_user(db, role='user', store='B店')
    _login(client, u)

    r = client.get('/notes/api/tracking?store=A店')

    assert r.status_code == 200
    names = [row['keyword'] for row in r.get_json()['tracking']]
    assert kw not in names
