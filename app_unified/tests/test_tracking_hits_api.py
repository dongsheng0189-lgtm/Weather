"""命中列的備註與剔除。"""
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


def _setup_one_hit(client, db, store='B店'):
    kw = _uniq('王小明')
    _mk_note(db, store=store, title=f'{kw} 的交接')
    u = _mk_user(db, store=store)
    _login(client, u)
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']
    from models import TrackedKeywordHit
    hit = TrackedKeywordHit.query.filter_by(keyword_id=kid).first()
    return u, kid, hit


def test_set_note_text(client, db):
    u, kid, hit = _setup_one_hit(client, db)
    memo = f'已口頭告誡_{uuid.uuid4().hex[:4]}'

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': memo})

    assert r.status_code == 200
    assert r.get_json()['note_text'] == memo
    db.session.expire_all()
    from models import TrackedKeywordHit
    assert db.session.get(TrackedKeywordHit, hit.id).note_text == memo


def test_note_text_can_be_cleared(client, db):
    u, kid, hit = _setup_one_hit(client, db)
    client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': '先寫一段'})

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': ''})

    assert r.status_code == 200
    assert r.get_json()['note_text'] == ''


def test_exclude_and_restore(client, db):
    u, kid, hit = _setup_one_hit(client, db)
    from models import TrackedKeywordHit

    r = client.post(f'/notes/api/tracking/hits/{hit.id}/exclude',
                    json={'excluded': True})
    assert r.status_code == 200
    assert r.get_json()['excluded'] is True
    db.session.expire_all()
    row = db.session.get(TrackedKeywordHit, hit.id)
    assert row.excluded_at is not None
    assert row.excluded_by == u.id
    assert row.excluded_by_name == u.username

    r = client.post(f'/notes/api/tracking/hits/{hit.id}/exclude',
                    json={'excluded': False})
    assert r.status_code == 200
    assert r.get_json()['excluded'] is False
    db.session.expire_all()
    row = db.session.get(TrackedKeywordHit, hit.id)
    assert row.excluded_at is None
    assert row.excluded_by is None
    assert row.excluded_by_name is None


def test_exclusion_is_soft_delete_row_survives(client, db):
    """剔除不可以真刪 —— 真刪的話下一輪增量掃描會把它抓回來。"""
    u, kid, hit = _setup_one_hit(client, db)
    from models import TrackedKeywordHit
    client.post(f'/notes/api/tracking/hits/{hit.id}/exclude', json={'excluded': True})
    assert db.session.get(TrackedKeywordHit, hit.id) is not None


def test_cross_store_hit_is_forbidden(client, db):
    u, kid, hit = _setup_one_hit(client, db, store='B店')
    intruder = _mk_user(db, store='A店')
    _login(client, intruder)

    assert client.put(f'/notes/api/tracking/hits/{hit.id}/note',
                      json={'text': 'x'}).status_code == 403
    assert client.post(f'/notes/api/tracking/hits/{hit.id}/exclude',
                       json={'excluded': True}).status_code == 403
