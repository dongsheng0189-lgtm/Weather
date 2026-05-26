"""Filter panel collapsible wrapper markup."""
import json as _json
import uuid
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_user(db):
    from models import User
    u = User(username=f'u_fp_{uuid.uuid4().hex[:6]}', role='user', store='B店',
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def test_filter_panel_wrapper_present(client, db):
    u = _mk_user(db)
    _login(client, u)
    html = client.get('/notes/').get_data(as_text=True)
    assert 'data-section="filters"' in html
    assert '進階篩選' in html


def test_filter_panel_contains_existing_filter_rows(client, db):
    u = _mk_user(db)
    _login(client, u)
    html = client.get('/notes/').get_data(as_text=True)
    assert 'range-tabs' in html or '時間' in html
    assert '種類' in html
    assert '班別' in html
