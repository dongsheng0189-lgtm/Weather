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
