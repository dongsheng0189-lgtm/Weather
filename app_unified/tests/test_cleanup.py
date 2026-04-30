"""Tests for notes.cleanup.cleanup_resolved_notes and resolved_at lifecycle."""
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest
from extensions import db
from models import Note, NoteAttachment, User


def _mk_note(db_session, status="resolved", resolved_at=None, user_id=None):
    n = Note(
        user_id=user_id,
        author_name="system",
        note_type="note",
        title="t",
        content="c",
        store=None,
        status=status,
        category="misc",
    )
    n.resolved_at = resolved_at
    db_session.add(n)
    db_session.commit()
    return n


def _mk_attachment(db_session, note, object_key):
    a = NoteAttachment(
        note_id=note.id,
        object_key=object_key,
        filename="f.jpg",
        content_type="image/jpeg",
        file_size=10,
    )
    db_session.add(a)
    db_session.commit()
    return a


def _reset_throttle():
    from notes import cleanup
    cleanup._last_run = 0.0


def test_cleanup_deletes_expired_resolved(app, db_session):
    _reset_throttle()
    expired = _mk_note(db_session, resolved_at=datetime.utcnow() - timedelta(days=35))
    recent = _mk_note(db_session, resolved_at=datetime.utcnow() - timedelta(days=10))
    no_ts = _mk_note(db_session, resolved_at=None)

    from notes.cleanup import cleanup_resolved_notes
    with patch("notes.cleanup.delete_attachment"):
        n = cleanup_resolved_notes(force=True)

    assert n == 1
    db_session.expire_all()
    assert Note.query.get(expired.id) is None
    assert Note.query.get(recent.id) is not None
    assert Note.query.get(no_ts.id) is not None


def test_cleanup_removes_r2_attachments(app, db_session):
    _reset_throttle()
    note = _mk_note(db_session, resolved_at=datetime.utcnow() - timedelta(days=35))
    _mk_attachment(db_session, note, "attachments/X/a.jpg")
    _mk_attachment(db_session, note, "attachments/X/b.jpg")

    from notes.cleanup import cleanup_resolved_notes
    with patch("notes.cleanup.delete_attachment") as m:
        cleanup_resolved_notes(force=True)

    keys = {call.args[0] for call in m.call_args_list}
    assert keys == {"attachments/X/a.jpg", "attachments/X/b.jpg"}
    db_session.expire_all()
    assert NoteAttachment.query.filter_by(note_id=note.id).count() == 0


def test_cleanup_ignores_non_resolved(app, db_session):
    _reset_throttle()
    n = _mk_note(db_session, status="pending", resolved_at=datetime.utcnow() - timedelta(days=35))
    from notes.cleanup import cleanup_resolved_notes
    with patch("notes.cleanup.delete_attachment"):
        assert cleanup_resolved_notes(force=True) == 0
    db_session.expire_all()
    assert Note.query.get(n.id) is not None


def test_cleanup_tolerates_r2_failure(app, db_session):
    _reset_throttle()
    note = _mk_note(db_session, resolved_at=datetime.utcnow() - timedelta(days=35))
    _mk_attachment(db_session, note, "attachments/Y/a.jpg")

    from notes.cleanup import cleanup_resolved_notes
    with patch("notes.cleanup.delete_attachment", side_effect=RuntimeError("boom")):
        n = cleanup_resolved_notes(force=True)

    assert n == 1
    db_session.expire_all()
    assert Note.query.get(note.id) is None


def test_cleanup_throttle(app, db_session):
    _reset_throttle()
    _mk_note(db_session, resolved_at=datetime.utcnow() - timedelta(days=35))
    from notes.cleanup import cleanup_resolved_notes
    with patch("notes.cleanup.delete_attachment"):
        first = cleanup_resolved_notes()
        second = cleanup_resolved_notes()
    assert first >= 0
    assert second == 0


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_resolved_at_set_on_transition(app, client, db_session):
    u = User(username=f"u_{uuid.uuid4().hex[:6]}", role="super_admin", store=None)
    u.set_password("0000")
    db_session.add(u); db_session.commit()
    _login(client, u)

    r = client.post("/notes/api", json={"title": "t", "content": "c", "status": "pending"})
    assert r.status_code == 201
    note_id = r.get_json()["id"]

    db_session.expire_all()
    assert Note.query.get(note_id).resolved_at is None

    r = client.put(f"/notes/api/{note_id}", json={"status": "resolved"})
    assert r.status_code == 200
    db_session.expire_all()
    ra = Note.query.get(note_id).resolved_at
    assert ra is not None
    assert abs((datetime.utcnow() - ra).total_seconds()) < 10


def test_resolved_at_cleared_on_unresolve(app, client, db_session):
    u = User(username=f"u_{uuid.uuid4().hex[:6]}", role="super_admin", store=None)
    u.set_password("0000")
    db_session.add(u); db_session.commit()
    _login(client, u)

    r = client.post("/notes/api", json={"title": "t", "content": "c", "status": "resolved"})
    note_id = r.get_json()["id"]
    db_session.expire_all()
    assert Note.query.get(note_id).resolved_at is not None

    r = client.put(f"/notes/api/{note_id}", json={"status": "pending"})
    assert r.status_code == 200
    db_session.expire_all()
    assert Note.query.get(note_id).resolved_at is None
