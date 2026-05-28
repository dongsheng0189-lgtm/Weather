"""F11: ChecklistItem authorship fields populated correctly per mutation type."""
import time
import uuid
from datetime import datetime

import pytest


def _login(client, user):
    """Bypass Flask-Login: set session cookie directly."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_last_request_at"] = int(time.time())


def _make_user(db_session, name_hint="alice"):
    from models import User
    suffix = uuid.uuid4().hex[:6]
    u = User(username=f"{name_hint}-{suffix}", store="ST-test", role="user")
    u.set_password("pw")
    db_session.add(u)
    db_session.commit()
    return u


def _make_note(db_session, user_id):
    from models import Note
    note = Note(
        title="checklist",
        content="",
        store="ST-test",
        category="employee",
        note_type="checklist",
        user_id=user_id,
    )
    db_session.add(note)
    db_session.commit()
    return note


def _make_item(db_session, note_id, creator_id, creator_name):
    """Pre-seed an item owned by `creator` (so we can test mutations by a different user)."""
    from models import ChecklistItem
    item = ChecklistItem(
        note_id=note_id,
        text="task",
        created_by=creator_id,
        created_by_name=creator_name,
        updated_by=creator_id,
        updated_by_name=creator_name,
    )
    db_session.add(item)
    db_session.commit()
    return item


# ───────────────────────── POST create ──────────────────────────

def test_post_create_item_sets_all_four_fields(app, db_session):
    from models import ChecklistItem
    alice = _make_user(db_session, "alice")
    note = _make_note(db_session, alice.id)

    client = app.test_client()
    _login(client, alice)
    r = client.post(f"/notes/api/{note.id}/items", json={"text": "item1"})
    assert r.status_code == 201, r.get_data(as_text=True)
    item_id = r.get_json()["item"]["id"]

    item = db_session.get(ChecklistItem, item_id)
    assert item.created_by == alice.id
    assert item.created_by_name == alice.username
    assert item.updated_by == alice.id
    assert item.updated_by_name == alice.username
    # At creation, updated_at must equal created_at (same now value)
    assert item.updated_at == item.created_at


# ───────────────────────── PATCH text ──────────────────────────

def test_patch_text_sets_updated_by_keeps_created_by(app, db_session):
    from models import ChecklistItem
    bob = _make_user(db_session, "bob")
    alice = _make_user(db_session, "alice")
    note = _make_note(db_session, bob.id)
    item = _make_item(db_session, note.id, bob.id, bob.username)

    client = app.test_client()
    _login(client, alice)
    r = client.patch(f"/notes/api/items/{item.id}", json={"text": "edited"})
    assert r.status_code == 200, r.get_data(as_text=True)

    db_session.expire(item)
    assert item.text == "edited"
    assert item.updated_by == alice.id
    assert item.updated_by_name == alice.username
    assert item.created_by == bob.id
    assert item.created_by_name == bob.username
    assert item.checked_at is None


# ─────────────────────── PATCH is_checked=True ──────────────────

def test_patch_is_checked_true_makes_updated_at_equal_checked_at(app, db_session):
    """KEY: check action sets updated_at == checked_at (shared now) — UI relies on this for '(完成)' suffix."""
    from models import ChecklistItem
    bob = _make_user(db_session, "bob")
    alice = _make_user(db_session, "alice")
    note = _make_note(db_session, bob.id)
    item = _make_item(db_session, note.id, bob.id, bob.username)

    client = app.test_client()
    _login(client, alice)
    r = client.patch(f"/notes/api/items/{item.id}", json={"is_checked": True})
    assert r.status_code == 200, r.get_data(as_text=True)

    db_session.expire(item)
    assert item.is_checked is True
    assert item.updated_by == alice.id
    assert item.updated_by_name == alice.username
    assert item.checked_by == alice.id
    assert item.checked_by_name == alice.username
    assert item.checked_at == item.updated_at, (
        f"check action must set checked_at == updated_at (got {item.checked_at} vs {item.updated_at})"
    )


# ─────────────────────── PATCH is_checked=False ─────────────────

def test_patch_is_checked_false_bumps_updated_by_clears_checked_at(app, db_session):
    from models import ChecklistItem
    bob = _make_user(db_session, "bob")
    alice = _make_user(db_session, "alice")
    note = _make_note(db_session, bob.id)
    item = _make_item(db_session, note.id, bob.id, bob.username)
    item.is_checked = True
    item.checked_at = datetime.utcnow()
    item.checked_by = bob.id
    item.checked_by_name = bob.username
    db_session.commit()

    client = app.test_client()
    _login(client, alice)
    r = client.patch(f"/notes/api/items/{item.id}", json={"is_checked": False})
    assert r.status_code == 200, r.get_data(as_text=True)

    db_session.expire(item)
    assert item.is_checked is False
    assert item.checked_at is None
    # uncheck bumps updated_by
    assert item.updated_by == alice.id
    assert item.updated_by_name == alice.username


# ─────────────────────── PATCH text + is_checked combined ───────

def test_patch_text_and_check_in_same_request(app, db_session):
    from models import ChecklistItem
    bob = _make_user(db_session, "bob")
    alice = _make_user(db_session, "alice")
    note = _make_note(db_session, bob.id)
    item = _make_item(db_session, note.id, bob.id, bob.username)

    client = app.test_client()
    _login(client, alice)
    r = client.patch(
        f"/notes/api/items/{item.id}", json={"text": "edited", "is_checked": True}
    )
    assert r.status_code == 200, r.get_data(as_text=True)

    db_session.expire(item)
    assert item.text == "edited"
    assert item.is_checked is True
    assert item.updated_by == alice.id
    assert item.checked_by == alice.id
    # MUST be same now
    assert item.checked_at == item.updated_at


# ───────── text edit AFTER check: updated_at > checked_at ──────

def test_text_edit_after_check_preserves_old_checked_at(app, db_session):
    """If checked, then text-edited later: updated_at > checked_at → UI shows '修改' (no '(完成)')."""
    from models import ChecklistItem
    bob = _make_user(db_session, "bob")
    alice = _make_user(db_session, "alice")
    note = _make_note(db_session, bob.id)
    item = _make_item(db_session, note.id, bob.id, bob.username)
    # bob checks
    check_time = datetime.utcnow()
    item.is_checked = True
    item.checked_at = check_time
    item.checked_by = bob.id
    item.checked_by_name = bob.username
    item.updated_at = check_time
    item.updated_by = bob.id
    item.updated_by_name = bob.username
    db_session.commit()

    time.sleep(0.05)

    client = app.test_client()
    _login(client, alice)
    r = client.patch(f"/notes/api/items/{item.id}", json={"text": "edited later"})
    assert r.status_code == 200, r.get_data(as_text=True)

    db_session.expire(item)
    assert item.text == "edited later"
    assert item.updated_at > item.checked_at
    assert item.updated_by == alice.id
    assert item.checked_at == check_time
    assert item.is_checked is True


# ──────── attachment add/delete bumps item updated_by ────────

def test_post_attachment_for_item_bumps_item_updated_by(app, db_session):
    """Attaching a file directly via DB + checking the route uses live updated_by logic.
    Skips upload integration (R2 mocked but storage.upload_attachment returns None for empty key)."""
    from models import ChecklistItem, NoteAttachment
    import io
    bob = _make_user(db_session, "bob")
    alice = _make_user(db_session, "alice")
    note = _make_note(db_session, bob.id)
    item = _make_item(db_session, note.id, bob.id, bob.username)

    client = app.test_client()
    _login(client, alice)
    data = {
        "note_id": str(note.id),
        "item_id": str(item.id),
        "file": (io.BytesIO(b"fake png bytes"), "photo.png", "image/png"),
    }
    r = client.post("/notes/api/attachments/upload", data=data, content_type="multipart/form-data")
    if r.status_code == 503:
        pytest.skip("storage not configured in test env (upload_attachment returned None)")
    assert r.status_code in (200, 201), r.get_data(as_text=True)

    db_session.expire(item)
    assert item.updated_by == alice.id
    assert item.updated_by_name == alice.username


def test_delete_attachment_for_item_bumps_item_updated_by(app, db_session):
    from models import ChecklistItem, NoteAttachment
    bob = _make_user(db_session, "bob")
    alice = _make_user(db_session, "alice")
    note = _make_note(db_session, bob.id)
    item = _make_item(db_session, note.id, bob.id, bob.username)
    att = NoteAttachment(
        note_id=note.id,
        checklist_item_id=item.id,
        user_id=bob.id,
        object_key="dev/fake.png",
        filename="photo.png",
        content_type="image/png",
        file_size=12345,
    )
    db_session.add(att)
    db_session.commit()

    client = app.test_client()
    _login(client, alice)
    r = client.delete(f"/notes/api/attachments/{att.id}")
    assert r.status_code == 200, r.get_data(as_text=True)

    db_session.expire(item)
    assert item.updated_by == alice.id
    assert item.updated_by_name == alice.username
