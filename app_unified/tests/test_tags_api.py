"""Tag API tests."""
import json as _json
import uuid
import pytest


def _uniq(prefix='T'):
    return f'{prefix}_{uuid.uuid4().hex[:6]}'


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_user(db, role='user', store='B店', username=None):
    from models import User
    u = User(username=username or _uniq(role),
             role=role, store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def test_create_tag_ok(client, db):
    u = _mk_user(db, username=_uniq('u'))
    _login(client, u)
    name = _uniq('重要')
    r = client.post('/notes/api/tags',
                    json={'name': name, 'color': '#E74C3C'})
    assert r.status_code == 200
    data = r.get_json()
    assert data['tag']['name'] == name
    assert data['tag']['fg'] == '#fff'
    assert data['tag']['pinned'] is False


def test_create_tag_rejects_bad_color(client, db):
    u = _mk_user(db, username=_uniq('u'))
    _login(client, u)
    r = client.post('/notes/api/tags', json={'name': _uniq('X'), 'color': '#000000'})
    assert r.status_code == 400


def test_create_tag_rejects_duplicate(client, db):
    u = _mk_user(db, username=_uniq('u'))
    _login(client, u)
    name = _uniq('D')
    client.post('/notes/api/tags', json={'name': name, 'color': '#E74C3C'})
    r = client.post('/notes/api/tags', json={'name': name, 'color': '#27AE60'})
    assert r.status_code == 400


def test_list_tags_per_store_only(client, db):
    from models import Tag
    u_b = _mk_user(db, store='B店', username=_uniq('ub'))
    u_z = _mk_user(db, store='Z店', username=_uniq('uz'))
    btag = _uniq('Btag')
    ztag = _uniq('Ztag')
    db.session.add(Tag(store='B店', name=btag, color='#E74C3C'))
    db.session.add(Tag(store='Z店', name=ztag, color='#27AE60'))
    db.session.commit()
    _login(client, u_b)
    r = client.get('/notes/api/tags')
    names = [t['name'] for t in r.get_json()['tags']]
    assert btag in names and ztag not in names


def test_update_tag(client, db):
    u = _mk_user(db, username=_uniq('u'))
    _login(client, u)
    r = client.post('/notes/api/tags', json={'name': _uniq('A'), 'color': '#E74C3C'})
    tid = r.get_json()['tag']['id']
    new_name = _uniq('B')
    r2 = client.patch(f'/notes/api/tags/{tid}', json={'name': new_name})
    assert r2.status_code == 200
    assert r2.get_json()['tag']['name'] == new_name


def test_delete_tag_returns_cascade_count(client, db):
    from models import Tag, Note, NoteTag
    u = _mk_user(db, username=_uniq('u'))
    n = Note(user_id=u.id, store='B店', title='n', content='', note_type='regular')
    t = Tag(store='B店', name=_uniq('X'), color='#E74C3C')
    db.session.add_all([n, t])
    db.session.commit()
    db.session.add(NoteTag(note_id=n.id, tag_id=t.id))
    db.session.commit()
    _login(client, u)
    r = client.delete(f'/notes/api/tags/{t.id}')
    assert r.status_code == 200
    assert r.get_json()['cascaded'] == 1


def test_pin_tag(client, db):
    u = _mk_user(db, username=_uniq('u'))
    _login(client, u)
    r = client.post('/notes/api/tags', json={'name': _uniq('P'), 'color': '#E74C3C'})
    tid = r.get_json()['tag']['id']
    r2 = client.post(f'/notes/api/tags/{tid}/pin')
    assert r2.status_code == 200
    assert r2.get_json()['tag']['pinned'] is True


def test_pin_limit_3(client, db):
    # 用唯一 store 避免跟其他 test 的 pinned tag 共用 store 撞 limit
    store = f'PSx_{uuid.uuid4().hex[:4]}'
    u = _mk_user(db, store=store, username=_uniq('u'))
    _login(client, u)
    ids = []
    for i in range(4):
        r = client.post('/notes/api/tags',
                        json={'name': _uniq(f'P{i}'), 'color': '#E74C3C'})
        ids.append(r.get_json()['tag']['id'])
    for tid in ids[:3]:
        assert client.post(f'/notes/api/tags/{tid}/pin').status_code == 200
    r = client.post(f'/notes/api/tags/{ids[3]}/pin')
    assert r.status_code == 400
    assert '釘選已滿' in r.get_json()['error']


def test_unpin_then_pin_new(client, db):
    store = f'PSy_{uuid.uuid4().hex[:4]}'
    u = _mk_user(db, store=store, username=_uniq('u'))
    _login(client, u)
    ids = []
    for i in range(4):
        r = client.post('/notes/api/tags',
                        json={'name': _uniq(f'U{i}'), 'color': '#E74C3C'})
        ids.append(r.get_json()['tag']['id'])
    for tid in ids[:3]:
        client.post(f'/notes/api/tags/{tid}/pin')
    client.delete(f'/notes/api/tags/{ids[0]}/pin')
    r = client.post(f'/notes/api/tags/{ids[3]}/pin')
    assert r.status_code == 200


def test_set_note_tags_replace_semantics(client, db):
    from models import Tag, Note
    u = _mk_user(db, username=_uniq('u'))
    n = Note(user_id=u.id, store='B店', title='n', content='', note_type='regular')
    t1 = Tag(store='B店', name=_uniq('T1'), color='#E74C3C')
    t2 = Tag(store='B店', name=_uniq('T2'), color='#27AE60')
    t3 = Tag(store='B店', name=_uniq('T3'), color='#3498DB')
    db.session.add_all([n, t1, t2, t3])
    db.session.commit()
    _login(client, u)
    r = client.put(f'/notes/api/{n.id}/tags', json={'tag_ids': [t1.id, t2.id]})
    assert sorted(r.get_json()['tag_ids']) == sorted([t1.id, t2.id])
    r = client.put(f'/notes/api/{n.id}/tags', json={'tag_ids': [t3.id]})
    assert r.get_json()['tag_ids'] == [t3.id]


def test_set_note_tags_rejects_over_5(client, db):
    from models import Tag, Note
    u = _mk_user(db, username=_uniq('u'))
    n = Note(user_id=u.id, store='B店', title='n', content='', note_type='regular')
    ts = [Tag(store='B店', name=_uniq(f'M{i}'), color='#E74C3C') for i in range(6)]
    db.session.add(n)
    db.session.add_all(ts)
    db.session.commit()
    _login(client, u)
    r = client.put(f'/notes/api/{n.id}/tags',
                   json={'tag_ids': [t.id for t in ts]})
    assert r.status_code == 400


def test_set_note_tags_rejects_cross_store(client, db):
    from models import Tag, Note
    u = _mk_user(db, store='B店', username=_uniq('u'))
    n = Note(user_id=u.id, store='B店', title='n', content='', note_type='regular')
    t = Tag(store='Z店', name=_uniq('Zt'), color='#E74C3C')
    db.session.add_all([n, t])
    db.session.commit()
    _login(client, u)
    r = client.put(f'/notes/api/{n.id}/tags', json={'tag_ids': [t.id]})
    assert r.status_code == 400


def test_set_item_tags_replace_semantics(client, db):
    from models import Tag, Note, ChecklistItem
    u = _mk_user(db, username=_uniq('u'))
    n = Note(user_id=u.id, store='B店', title='n', content='', note_type='checklist')
    db.session.add(n)
    db.session.commit()
    it = ChecklistItem(note_id=n.id, text='補貨', is_checked=False)
    t1 = Tag(store='B店', name=_uniq('IT1'), color='#E74C3C')
    db.session.add_all([it, t1])
    db.session.commit()
    _login(client, u)
    r = client.put(f'/notes/api/items/{it.id}/tags', json={'tag_ids': [t1.id]})
    assert r.status_code == 200
    assert r.get_json()['tag_ids'] == [t1.id]


def test_update_cross_store_forbidden(client, db):
    from models import Tag
    u = _mk_user(db, store='B店', username=_uniq('u'))
    z_name = _uniq('Ztag')
    db.session.add(Tag(store='Z店', name=z_name, color='#E74C3C'))
    db.session.commit()
    t = Tag.query.filter_by(store='Z店', name=z_name).first()
    _login(client, u)
    r = client.patch(f'/notes/api/tags/{t.id}', json={'name': _uniq('Y')})
    assert r.status_code == 403
