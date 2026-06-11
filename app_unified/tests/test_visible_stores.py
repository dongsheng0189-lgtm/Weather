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
