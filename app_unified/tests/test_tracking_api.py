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
    n = _mk_note(db, title=f'{kw} 的交接')
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
    assert n.id


def test_create_tracking_rejects_blank_keyword(client, db):
    u = _mk_user(db)
    _login(client, u)
    r = client.post('/notes/api/tracking', json={'keyword': '   '})
    assert r.status_code == 400
    assert '關鍵字' in r.get_json()['error']


def test_create_tracking_returns_existing_when_duplicate(client, db):
    """同店同關鍵字不重複建表 —— 直接把既有那張回給前端。"""
    kw = _uniq('王小明')
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
