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
    _u, _kid, hit = _setup_one_hit(client, db)
    memo = f'已口頭告誡_{uuid.uuid4().hex[:4]}'

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': memo})

    assert r.status_code == 200
    assert r.get_json()['note_text'] == memo
    db.session.expire_all()
    from models import TrackedKeywordHit
    assert db.session.get(TrackedKeywordHit, hit.id).note_text == memo


def test_note_text_can_be_cleared(client, db):
    _u, _kid, hit = _setup_one_hit(client, db)
    client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': '先寫一段'})

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': ''})

    assert r.status_code == 200
    assert r.get_json()['note_text'] == ''
    db.session.expire_all()
    from models import TrackedKeywordHit
    assert db.session.get(TrackedKeywordHit, hit.id).note_text == ''


def test_note_text_missing_key_is_rejected(client, db):
    """PUT 是整份取代，明確傳 '' 才是「清空」；根本沒帶 text 欄位是另一回事，
    備註是寫死的評語、清掉了找不回來，必須擋在 400，不可靜默清空。"""
    _u, _kid, hit = _setup_one_hit(client, db)
    memo = f'已口頭告誡_{uuid.uuid4().hex[:4]}'
    client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': memo})

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={})

    assert r.status_code == 400
    db.session.expire_all()
    from models import TrackedKeywordHit
    assert db.session.get(TrackedKeywordHit, hit.id).note_text == memo


def test_note_text_null_is_rejected(client, db):
    """{"text": null} 跟沒帶欄位是同一種「缺少」，同樣不可被當成清空。"""
    _u, _kid, hit = _setup_one_hit(client, db)
    memo = f'已口頭告誡_{uuid.uuid4().hex[:4]}'
    client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': memo})

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': None})

    assert r.status_code == 400
    db.session.expire_all()
    from models import TrackedKeywordHit
    assert db.session.get(TrackedKeywordHit, hit.id).note_text == memo


def test_note_text_non_string_is_rejected(client, db):
    """數字之類的非字串不可被隱性轉型／coerce，直接 400 讓呼叫端自己修。"""
    _u, _kid, hit = _setup_one_hit(client, db)

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': 5})

    assert r.status_code == 400
    db.session.expire_all()
    from models import TrackedKeywordHit
    assert db.session.get(TrackedKeywordHit, hit.id).note_text is None


def test_exclude_and_restore(client, db):
    u, _kid, hit = _setup_one_hit(client, db)
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


def test_exclude_rejects_truthy_string_false(client, db):
    """字串 "false" 是 truthy，若沿用 truthy 判斷會把「想還原」誤判成「剔除」，
    方向整個反過來、而且是靜默發生。必須要求真布林，非布林一律 400、不生效。"""
    _u, _kid, hit = _setup_one_hit(client, db)
    from models import TrackedKeywordHit

    r = client.post(f'/notes/api/tracking/hits/{hit.id}/exclude',
                    json={'excluded': 'false'})

    assert r.status_code == 400
    db.session.expire_all()
    row = db.session.get(TrackedKeywordHit, hit.id)
    assert row.excluded_at is None


def test_exclusion_is_soft_delete_row_survives(client, db):
    """剔除不可以真刪 —— 真刪的話下一輪增量掃描會把它抓回來。"""
    _u, _kid, hit = _setup_one_hit(client, db)
    from models import TrackedKeywordHit
    r = client.post(f'/notes/api/tracking/hits/{hit.id}/exclude', json={'excluded': True})
    assert r.status_code == 200
    row = db.session.get(TrackedKeywordHit, hit.id)
    assert row is not None
    assert row.excluded_at is not None


def test_cross_store_hit_is_forbidden(client, db):
    _u, _kid, hit = _setup_one_hit(client, db, store='B店')
    intruder = _mk_user(db, store='A店')
    _login(client, intruder)

    assert client.put(f'/notes/api/tracking/hits/{hit.id}/note',
                      json={'text': 'x'}).status_code == 403
    assert client.post(f'/notes/api/tracking/hits/{hit.id}/exclude',
                       json={'excluded': True}).status_code == 403


def test_hit_not_found_is_404(client, db):
    """_get_hit_or_404 的找不到分支：跟跨店的 403 是相鄰分支，各自要有測試守著，
    避免日後改動時把兩者混淆。"""
    _u, _kid, _hit = _setup_one_hit(client, db)
    bogus_id = 9_999_999

    assert client.put(f'/notes/api/tracking/hits/{bogus_id}/note',
                      json={'text': 'x'}).status_code == 404
    assert client.post(f'/notes/api/tracking/hits/{bogus_id}/exclude',
                       json={'excluded': True}).status_code == 404
