"""可視店家（super_admin per-user 店家過濾）測試。"""
import json as _json
import uuid
import pytest


def _uniq(p='x'):
    return f'{p}_{uuid.uuid4().hex[:6]}'


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk(db, role='super_admin', store=None):
    from models import User
    u = User(username=_uniq('u'), role=role, store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    import time
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())


def test_hidden_stores_roundtrip(db):
    u = _mk(db)
    assert u.get_hidden_stores() == []
    u.set_hidden_stores(['B', 'C'])
    db.session.commit()
    assert sorted(u.get_hidden_stores()) == ['B', 'C']


def test_visible_super_admin_subtracts_hidden(db):
    u = _mk(db, role='super_admin')
    u.set_hidden_stores(['C'])
    db.session.commit()
    assert u.visible_store_names(['B', 'C', 'E']) == ['B', 'E']


def test_visible_new_store_auto_visible(db):
    u = _mk(db, role='super_admin')
    u.set_hidden_stores(['C'])
    db.session.commit()
    assert 'NEW' in u.visible_store_names(['B', 'C', 'NEW'])


def test_visible_admin_only_own_store(db):
    u = _mk(db, role='admin', store='B')
    u.set_hidden_stores(['B', 'E'])
    db.session.commit()
    assert u.visible_store_names(['B', 'C', 'E']) == ['B']


def test_visible_user_no_store_empty(db):
    u = _mk(db, role='user', store=None)
    assert u.visible_store_names(['B', 'C']) == []


def test_list_stores_includes_visible_flag(client, db):
    from models import Store
    s1, s2 = _uniq('S'), _uniq('S')
    db.session.add_all([Store(name=s1), Store(name=s2)])
    db.session.commit()
    su = _mk(db, role='super_admin')
    su.set_hidden_stores([s2])
    db.session.commit()
    _login(client, su)
    rows = client.get('/admin/stores').get_json()
    by = {r['name']: r for r in rows}
    assert by[s1]['visible'] is True
    assert by[s2]['visible'] is False


def test_toggle_visible_super_admin(client, db):
    from models import Store
    s1, s2 = _uniq('S'), _uniq('S')
    db.session.add_all([Store(name=s1), Store(name=s2)])
    db.session.commit()
    su = _mk(db, role='super_admin')
    _login(client, su)
    r = client.post(f'/admin/stores/{s2}/toggle-visible')
    assert r.get_json()['visible'] is False
    su2 = client.get('/admin/stores').get_json()
    assert {x['name']: x['visible'] for x in su2}[s2] is False
    r2 = client.post(f'/admin/stores/{s2}/toggle-visible')
    assert r2.get_json()['visible'] is True


def test_toggle_visible_admin_forbidden(client, db):
    from models import Store
    sname = _uniq('S')
    db.session.add(Store(name=sname))
    db.session.commit()
    a = _mk(db, role='admin', store=sname)
    _login(client, a)
    assert client.post(f'/admin/stores/{sname}/toggle-visible').status_code == 403


def test_toggle_visible_cannot_hide_last(client, db):
    from models import Store
    sname = _uniq('S')
    db.session.add(Store(name=sname))
    db.session.commit()
    su = _mk(db, role='super_admin')
    _login(client, su)
    # Hide all other stores first so sname is the only visible one
    all_others = [s.name for s in Store.query.all() if s.name != sname]
    su.set_hidden_stores(all_others)
    db.session.commit()
    r = client.post(f'/admin/stores/{sname}/toggle-visible')
    assert r.status_code == 400
    assert '至少' in r.get_json().get('message', '')


def test_dashboard_has_visible_column(client, db):
    su = _mk(db, role='super_admin')
    _login(client, su)
    html = client.get('/admin/dashboard').get_data(as_text=True)
    assert '可視</th>' in html
    assert 'toggleStoreVisible' in html
    assert 'IS_SUPER_ADMIN' in html


def test_notes_super_admin_hidden_store_excluded(client, db):
    from models import Store, Note
    sb, sc = _uniq('SB'), _uniq('SC')
    db.session.add_all([Store(name=sb), Store(name=sc)])
    db.session.commit()
    su = _mk(db, role='super_admin')
    nb = Note(user_id=su.id, store=sb, title=_uniq('NOTE_B'),
              content='', note_type='regular')
    nc = Note(user_id=su.id, store=sc, title=_uniq('NOTE_C'),
              content='', note_type='regular')
    db.session.add_all([nb, nc])
    db.session.commit()
    su.set_hidden_stores([sc])
    db.session.commit()
    _login(client, su)
    # 無 ?store：只看可視店（不含已隱藏的 sc）
    html = client.get('/notes/?range=all').get_data(as_text=True)
    assert nb.title in html
    assert nc.title not in html


def test_notes_super_admin_hidden_store_param_ignored(client, db):
    from models import Store, Note
    sc = _uniq('SC')
    db.session.add(Store(name=sc))
    db.session.commit()
    su = _mk(db, role='super_admin')
    nc = Note(user_id=su.id, store=sc, title=_uniq('ONLY_C'),
              content='', note_type='regular')
    db.session.add(nc)
    db.session.commit()
    su.set_hidden_stores([sc])
    db.session.commit()
    _login(client, su)
    # 顯式 ?store=<已隱藏> → 忽略、回退可視全集，仍看不到 sc
    html = client.get(f'/notes/?store={sc}&range=all').get_data(as_text=True)
    assert nc.title not in html


def test_notes_super_admin_null_store_note_visible_after_hiding(client, db):
    """super_admin 隱藏某店後，store=NULL 的筆記仍應看得到（不被 NOT IN 濾掉）。"""
    from models import Store, Note
    sc = _uniq('SC')
    db.session.add(Store(name=sc))
    db.session.commit()
    su = _mk(db, role='super_admin')
    nn = Note(user_id=su.id, store=None, title=_uniq('NULL_NOTE'),
              content='', note_type='regular')
    db.session.add(nn)
    db.session.commit()
    su.set_hidden_stores([sc])   # 隱藏一間 → 觸發 NOT IN
    db.session.commit()
    _login(client, su)
    html = client.get('/notes/?range=all').get_data(as_text=True)
    assert nn.title in html      # NULL-store 筆記仍可見
