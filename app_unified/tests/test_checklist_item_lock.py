"""Item-level lock tests for F2 spec: docs/superpowers/specs/2026-05-25-checklist-item-lock.md"""
import uuid
from datetime import datetime, timedelta

from extensions import db
from models import User, Note, ChecklistItem


def _mk_users(db_session):
    suffix = uuid.uuid4().hex[:6]
    alice = User(username=f"alice_{suffix}", store="A", role="admin")
    alice.set_password("x")
    bob = User(username=f"bob_{suffix}", store="A", role="admin")
    bob.set_password("x")
    db_session.add_all([alice, bob])
    db_session.commit()
    return alice, bob


def _mk_checklist_note(db_session, store="A"):
    n = Note(title="t", content="", store=store, category="misc", note_type="checklist")
    db_session.add(n)
    db_session.commit()
    return n


def _mk_item(db_session, note, text="x"):
    item = ChecklistItem(note_id=note.id, text=text, order_index=0)
    db_session.add(item)
    db_session.commit()
    return item


def _login_as(client, user):
    """既有 conftest pattern (從 test_notes_lock.py 學)"""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        import time
        sess["_last_request_at"] = int(time.time())


def test_ensure_item_lock_acquires_when_unlocked(app, db_session):
    from notes.routes import _ensure_item_lock_for_mutation
    alice, _bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    ok, locker = _ensure_item_lock_for_mutation(item, alice.id)
    assert ok is True
    assert item.locked_by == alice.id
    assert item.locked_at is not None


def test_ensure_item_lock_passes_when_self_locked(app, db_session):
    from notes.routes import _ensure_item_lock_for_mutation
    alice, _bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow()
    db_session.commit()
    ok, _locker = _ensure_item_lock_for_mutation(item, alice.id)
    assert ok is True


def test_ensure_item_lock_rejects_when_other_locked_fresh(app, db_session):
    from notes.routes import _ensure_item_lock_for_mutation
    alice, bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow()
    db_session.commit()
    ok, locker = _ensure_item_lock_for_mutation(item, bob.id)
    assert ok is False
    assert locker.username == alice.username


def test_ensure_item_lock_takes_over_expired_lock(app, db_session):
    from notes.routes import _ensure_item_lock_for_mutation, LOCK_TTL_SEC
    alice, bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow() - timedelta(seconds=LOCK_TTL_SEC + 60)
    db_session.commit()
    ok, _locker = _ensure_item_lock_for_mutation(item, bob.id)
    assert ok is True
    assert item.locked_by == bob.id


def test_item_lock_heartbeat_acquires(app, client, db_session):
    alice, _bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    iid = item.id
    aid = alice.id
    _login_as(client, alice)
    res = client.post(f'/notes/api/items/{iid}/lock/heartbeat')
    assert res.status_code == 200
    item2 = ChecklistItem.query.get(iid)
    assert item2.locked_by == aid


def test_item_lock_heartbeat_rejects_other_locked(app, client, db_session):
    alice, bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow()
    db_session.commit()
    _login_as(client, bob)
    res = client.post(f'/notes/api/items/{item.id}/lock/heartbeat')
    assert res.status_code == 409
    data = res.get_json()
    assert data['locker'] == alice.username


def test_item_lock_release_clears_lock(app, client, db_session):
    alice, _bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow()
    db_session.commit()
    iid = item.id
    _login_as(client, alice)
    res = client.post(f'/notes/api/items/{iid}/lock/release')
    assert res.status_code == 200
    item2 = ChecklistItem.query.get(iid)
    assert item2.locked_by is None
    assert item2.locked_at is None


def test_patch_item_text_rejects_when_locked_by_other(app, client, db_session):
    alice, bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n, text="original")
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow()
    db_session.commit()
    _login_as(client, bob)
    res = client.patch(f'/notes/api/items/{item.id}', json={"text": "changed"})
    assert res.status_code == 409


def test_patch_item_is_checked_succeeds_even_when_locked(app, client, db_session):
    """is_checked toggle 不檢 lock — 即使別人在編 text、仍可勾選。"""
    alice, bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow()
    db_session.commit()
    iid = item.id
    _login_as(client, bob)
    res = client.patch(f'/notes/api/items/{iid}', json={"is_checked": True})
    assert res.status_code == 200
    item2 = ChecklistItem.query.get(iid)
    assert item2.is_checked is True


def test_delete_item_rejects_when_locked_by_other(app, client, db_session):
    alice, bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow()
    db_session.commit()
    _login_as(client, bob)
    res = client.delete(f'/notes/api/items/{item.id}')
    assert res.status_code == 409


def test_checklist_item_json_shows_locker_to_other(app, client, db_session):
    """別人持鎖時、_checklist_item_json 帶 locked_by_username 給 frontend"""
    alice, bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow()
    db_session.commit()
    nid = n.id
    _login_as(client, bob)
    res = client.get(f'/notes/api/{nid}/items')
    assert res.status_code == 200
    items = res.get_json()['items']
    assert len(items) == 1
    assert items[0]['locked_by_username'] == alice.username


def test_checklist_item_json_hides_locker_to_self(app, client, db_session):
    """自己持鎖時、_checklist_item_json locked_by_username = None"""
    alice, _bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow()
    db_session.commit()
    nid = n.id
    _login_as(client, alice)
    res = client.get(f'/notes/api/{nid}/items')
    items = res.get_json()['items']
    assert items[0]['locked_by_username'] is None
