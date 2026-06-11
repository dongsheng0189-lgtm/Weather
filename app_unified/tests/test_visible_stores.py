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
