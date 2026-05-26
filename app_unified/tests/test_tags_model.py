"""Tag system model tests."""
import uuid
import pytest


def _uniq(prefix='T'):
    return f'{prefix}_{uuid.uuid4().hex[:6]}'


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def test_tag_create_minimal(db):
    from models import Tag
    t = Tag(store='B店', name=_uniq('重要'), color='#E74C3C')
    db.session.add(t)
    db.session.commit()
    assert t.id > 0
    assert t.pinned is False


def test_tag_unique_per_store(db):
    from models import Tag
    name = _uniq('UQ')
    db.session.add(Tag(store='B店', name=name, color='#E74C3C'))
    db.session.commit()
    db.session.add(Tag(store='B店', name=name, color='#27AE60'))
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


def test_tag_same_name_different_store_ok(db):
    from models import Tag
    name = _uniq('XS')
    db.session.add(Tag(store='B店', name=name, color='#E74C3C'))
    db.session.add(Tag(store='Z店', name=name, color='#E74C3C'))
    db.session.commit()


def test_note_tag_cascade_on_tag_delete(db):
    from models import Tag, Note, NoteTag, User
    import json as _json
    u = User(username=_uniq('um'), store='B店', role='user', features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    n = Note(user_id=u.id, store='B店', title='x', content='y', note_type='regular')
    t = Tag(store='B店', name=_uniq('Tcasc'), color='#E74C3C')
    db.session.add_all([n, t])
    db.session.commit()
    db.session.add(NoteTag(note_id=n.id, tag_id=t.id))
    db.session.commit()
    db.session.delete(t)
    db.session.commit()
    assert db.session.get(Note, n.id) is not None
    assert NoteTag.query.filter_by(note_id=n.id).first() is None
