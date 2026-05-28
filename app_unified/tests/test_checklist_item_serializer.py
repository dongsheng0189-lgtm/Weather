"""F11: _checklist_item_json exposes 4 authorship fields + updated_at."""
import uuid
from datetime import datetime

import pytest


def _make_user(db_session, name_hint="alice"):
    from models import User
    suffix = uuid.uuid4().hex[:6]
    u = User(username=f"{name_hint}-{suffix}", store="ST", role="user")
    u.set_password("pw")
    db_session.add(u)
    db_session.commit()
    return u


def _make_note(db_session, user_id):
    from models import Note
    note = Note(title="", content="", store="ST", category="employee",
                note_type="checklist", user_id=user_id)
    db_session.add(note)
    db_session.commit()
    return note


def test_serializer_includes_authorship_fields(app, db_session):
    from models import ChecklistItem
    from notes.routes import _checklist_item_json
    u = _make_user(db_session, "alice")
    note = _make_note(db_session, u.id)
    item = ChecklistItem(
        note_id=note.id, text="t",
        created_by=u.id, created_by_name=u.username,
        updated_by=u.id, updated_by_name=u.username,
    )
    db_session.add(item)
    db_session.commit()
    d = _checklist_item_json(item)
    assert d["created_by_name"] == u.username
    assert d["updated_by_name"] == u.username
    assert "created_at" in d
    assert "updated_at" in d
    assert isinstance(d["created_at"], str)
    assert isinstance(d["updated_at"], str)


def test_serializer_check_action_yields_equal_updated_at_and_checked_at(app, db_session):
    """After a check action, updated_at and checked_at MUST be equal in serialized JSON
    so frontend `(完成)` suffix detection works."""
    from models import ChecklistItem
    from notes.routes import _checklist_item_json
    u = _make_user(db_session, "alice")
    note = _make_note(db_session, u.id)
    now = datetime.utcnow()
    item = ChecklistItem(
        note_id=note.id, text="t",
        created_by=u.id, created_by_name=u.username,
        updated_by=u.id, updated_by_name=u.username,
        is_checked=True,
        checked_at=now,
        checked_by=u.id, checked_by_name=u.username,
        updated_at=now,
    )
    db_session.add(item)
    db_session.commit()
    d = _checklist_item_json(item)
    assert d["updated_at"] == d["checked_at"], (
        f"check serializer must yield equal updated_at/checked_at, "
        f"got {d['updated_at']} vs {d['checked_at']}"
    )
