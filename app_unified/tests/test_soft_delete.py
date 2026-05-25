"""Soft delete tests for F1 spec: docs/superpowers/specs/2026-05-25-notes-soft-delete.md"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from extensions import db
from models import User, Note, ChecklistItem, NoteAttachment


def _mk_users(db_session, role="admin"):
    suffix = uuid.uuid4().hex[:6]
    alice = User(username=f"alice_{suffix}", store="A", role=role)
    alice.set_password("x")
    bob = User(username=f"bob_{suffix}", store="A", role=role)
    bob.set_password("x")
    db_session.add_all([alice, bob])
    db_session.commit()
    return alice, bob


def _mk_note(db_session, store="A", note_type="note"):
    n = Note(title="t", content="c", store=store, category="misc", note_type=note_type)
    db_session.add(n)
    db_session.commit()
    return n


def _mk_item(db_session, note, text="x"):
    item = ChecklistItem(note_id=note.id, text=text, order_index=0)
    db_session.add(item)
    db_session.commit()
    return item


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        import time
        sess["_last_request_at"] = int(time.time())


def test_soft_delete_note_sets_deleted_at(app, db_session):
    from notes.routes import soft_delete_note
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    soft_delete_note(n, alice.id)
    db_session.commit()
    db_session.refresh(n)
    assert n.deleted_at is not None
    assert n.deleted_by == alice.id


def test_soft_delete_item_sets_deleted_at(app, db_session):
    from notes.routes import soft_delete_item
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session, note_type="checklist")
    item = _mk_item(db_session, n)
    soft_delete_item(item, alice.id)
    db_session.commit()
    db_session.refresh(item)
    assert item.deleted_at is not None
    assert item.deleted_by == alice.id


def test_delete_note_endpoint_soft_deletes(app, client, db_session):
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    nid = n.id
    _login_as(client, alice)
    res = client.delete(f'/notes/api/{nid}')
    assert res.status_code == 200
    n2 = Note.query.filter_by(id=nid).first()
    assert n2 is not None  # 仍在 DB
    assert n2.deleted_at is not None  # 但被 soft deleted


def test_delete_item_endpoint_soft_deletes(app, client, db_session):
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session, note_type="checklist")
    item = _mk_item(db_session, n)
    iid = item.id
    _login_as(client, alice)
    res = client.delete(f'/notes/api/items/{iid}')
    assert res.status_code == 200
    item2 = ChecklistItem.query.filter_by(id=iid).first()
    assert item2 is not None
    assert item2.deleted_at is not None


def test_soft_deleted_note_not_in_active(app, db_session):
    alice, _ = _mk_users(db_session)
    n1 = _mk_note(db_session)
    n2 = _mk_note(db_session)
    n2.deleted_at = datetime.utcnow()
    n2.deleted_by = alice.id
    db_session.commit()
    active = Note.active().all()
    assert n1 in active
    assert n2 not in active


def test_soft_deleted_item_not_in_active(app, db_session):
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session, note_type="checklist")
    i1 = _mk_item(db_session, n)
    i2 = _mk_item(db_session, n)
    i2.deleted_at = datetime.utcnow()
    db_session.commit()
    active = ChecklistItem.active().filter_by(note_id=n.id).all()
    assert i1 in active
    assert i2 not in active


def test_delete_note_does_not_delete_r2(app, client, db_session):
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    att = NoteAttachment(note_id=n.id, filename="x.jpg",
                          object_key="key1", file_size=100, content_type="image/jpeg")
    db_session.add(att)
    db_session.commit()
    _login_as(client, alice)
    with patch('storage.delete_attachment') as mock_r2:
        res = client.delete(f'/notes/api/{n.id}')
        assert res.status_code == 200
        mock_r2.assert_not_called()


def test_cleanup_soft_deleted_within_24h_no_action(app, db_session):
    from notes.cleanup import cleanup_soft_deleted
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    n.deleted_at = datetime.utcnow() - timedelta(hours=10)
    n.deleted_by = alice.id
    db_session.commit()
    notes_deleted, items_deleted = cleanup_soft_deleted(force=True)
    assert notes_deleted == 0
    assert Note.query.filter_by(id=n.id).first() is not None


def test_cleanup_soft_deleted_after_24h_hard_deletes(app, db_session):
    from notes.cleanup import cleanup_soft_deleted
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    n.deleted_at = datetime.utcnow() - timedelta(hours=25)
    n.deleted_by = alice.id
    db_session.commit()
    nid = n.id
    notes_deleted, _ = cleanup_soft_deleted(force=True)
    assert notes_deleted == 1
    assert Note.query.filter_by(id=nid).first() is None


def test_cleanup_soft_deleted_calls_r2_delete(app, db_session):
    from notes.cleanup import cleanup_soft_deleted
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    att = NoteAttachment(note_id=n.id, filename="x.jpg",
                          object_key="ck1", file_size=100, content_type="image/jpeg")
    db_session.add(att)
    n.deleted_at = datetime.utcnow() - timedelta(hours=25)
    n.deleted_by = alice.id
    db_session.commit()
    with patch('notes.cleanup.delete_attachment') as mock_r2:
        cleanup_soft_deleted(force=True)
        mock_r2.assert_called_once_with("ck1")


def test_restore_note_clears_deleted_at(app, db_session):
    from notes.routes import restore_note
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    n.deleted_at = datetime.utcnow()
    n.deleted_by = alice.id
    db_session.commit()
    restore_note(n)
    db_session.commit()
    assert n.deleted_at is None
    assert n.deleted_by is None


def test_admin_list_deleted_endpoint(app, client, db_session):
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session, store="A")
    n.deleted_at = datetime.utcnow() - timedelta(hours=1)
    n.deleted_by = alice.id
    db_session.commit()
    nid = n.id
    _login_as(client, alice)
    res = client.get('/admin/api/deleted')
    assert res.status_code == 200
    data = res.get_json()
    assert any(x['id'] == nid for x in data['notes'])


def test_admin_restore_note_endpoint(app, client, db_session):
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session, store="A")
    n.deleted_at = datetime.utcnow() - timedelta(hours=1)
    n.deleted_by = alice.id
    db_session.commit()
    nid = n.id
    _login_as(client, alice)
    res = client.post(f'/admin/api/restore/note/{nid}')
    assert res.status_code == 200
    n2 = Note.query.filter_by(id=nid).first()
    assert n2.deleted_at is None


def test_admin_restore_cross_store_rejected_for_admin(app, client, db_session):
    alice, _ = _mk_users(db_session)
    alice.store = "A"
    db_session.commit()
    n_other = _mk_note(db_session, store="B")
    n_other.deleted_at = datetime.utcnow() - timedelta(hours=1)
    n_other.deleted_by = alice.id
    db_session.commit()
    _login_as(client, alice)
    res = client.post(f'/admin/api/restore/note/{n_other.id}')
    assert res.status_code == 403


def test_admin_restore_not_found_returns_404(app, client, db_session):
    alice, _ = _mk_users(db_session)
    _login_as(client, alice)
    res = client.post('/admin/api/restore/note/999999')
    assert res.status_code == 404
