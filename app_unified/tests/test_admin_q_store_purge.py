"""Backend tests for Q-store purge endpoints.

Spec: docs/superpowers/specs/2026-05-13-q-store-purge-button-design.md
TEMP: q-store-purge — remove this whole test file when feature is taken out.
"""
import uuid
import pytest
from unittest.mock import MagicMock, patch
from models import User, Store, Note, NoteAttachment, UserLog
from extensions import db


def _clean_test_data(db_session):
    """Clear all Note and NoteAttachment rows to isolate tests."""
    NoteAttachment.query.delete()
    Note.query.delete()
    db_session.commit()


def _mk_user(db_session, username=None, role="user", root_admin=False, store="B"):
    target = "admin" if root_admin else (username or f"u_{uuid.uuid4().hex[:6]}")
    existing = User.query.filter_by(username=target).first()
    if existing:
        return existing
    u = User(username=target, role=role, store=store, is_active=True)
    u.set_password("1234")
    db_session.add(u)
    db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


# ── Auth gate ────────────────────────────────────────────────────


def test_preview_404_for_anonymous(client):
    """Anonymous (not logged in) → preview redirects to login."""
    res = client.get("/admin/q-store/notes-preview")
    assert res.status_code in (302, 401)


def test_preview_404_for_user_role(app, client, db_session):
    """user role → 404 (require_root_admin guard)."""
    u = _mk_user(db_session)  # default role=user
    _login(client, u)
    res = client.get("/admin/q-store/notes-preview")
    assert res.status_code == 404


def test_preview_404_for_super_admin(app, client, db_session):
    """super_admin role (not username='admin') → 404."""
    u = _mk_user(db_session, username="boss", role="super_admin")
    _login(client, u)
    res = client.get("/admin/q-store/notes-preview")
    assert res.status_code == 404


def test_preview_200_for_root_admin(app, client, db_session):
    """username='admin' seed → 200 + json with both keys."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.get("/admin/q-store/notes-preview")
    assert res.status_code == 200
    data = res.get_json()
    assert "notes_count" in data
    assert "attachments_count" in data
    assert isinstance(data["notes_count"], int)
    assert isinstance(data["attachments_count"], int)


# ── Preview correctness ─────────────────────────────────────────


def test_preview_counts_q_notes_only(app, client, db_session):
    """3 Q notes + 5 B notes → preview reports notes_count=3."""
    _clean_test_data(db_session)
    admin = _mk_user(db_session, root_admin=True)
    # Need Store rows for FK validity in some setups; create if missing
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    if not Store.query.filter_by(name="B").first():
        db_session.add(Store(name="B", login_enabled=True))
    db_session.commit()

    for _ in range(3):
        db_session.add(Note(store="Q", title="q-note", content="x"))
    for _ in range(5):
        db_session.add(Note(store="B", title="b-note", content="x"))
    db_session.commit()

    _login(client, admin)
    res = client.get("/admin/q-store/notes-preview")
    data = res.get_json()
    assert data["notes_count"] == 3
    assert data["attachments_count"] == 0


def test_preview_counts_q_attachments(app, client, db_session):
    """2 Q notes, 1 with 2 attachments + 1 with 0 → preview reports 2."""
    _clean_test_data(db_session)
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    db_session.commit()

    n1 = Note(store="Q", title="with-attach", content="x")
    n2 = Note(store="Q", title="no-attach", content="x")
    db_session.add_all([n1, n2])
    db_session.commit()

    a1 = NoteAttachment(
        note_id=n1.id, object_key="q-test/1.jpg",
        filename="1.jpg", content_type="image/jpeg", file_size=100,
    )
    a2 = NoteAttachment(
        note_id=n1.id, object_key="q-test/2.jpg",
        filename="2.jpg", content_type="image/jpeg", file_size=200,
    )
    db_session.add_all([a1, a2])
    db_session.commit()

    _login(client, admin)
    res = client.get("/admin/q-store/notes-preview")
    data = res.get_json()
    assert data["notes_count"] == 2
    assert data["attachments_count"] == 2


def test_preview_counts_zero_when_no_q_data(app, client, db_session):
    """No Q notes at all → preview returns {0, 0}."""
    _clean_test_data(db_session)
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    db_session.commit()

    _login(client, admin)
    res = client.get("/admin/q-store/notes-preview")
    data = res.get_json()
    assert data["notes_count"] == 0
    assert data["attachments_count"] == 0


# ── Purge happy path (with R2 mocked to no-op) ──────────────────


def _mock_r2_client(success_keys, fail_entries=None):
    """Build a MagicMock R2 client whose delete_objects returns deterministic shape."""
    fail_entries = fail_entries or []
    client = MagicMock()
    client.delete_objects.return_value = {
        "Deleted": [{"Key": k} for k in success_keys],
        "Errors": fail_entries,
    }
    return client


def _patch_r2(monkeypatch, mock_client):
    """Replace storage._get_client to return our mock; also set R2_BUCKET_NAME."""
    monkeypatch.setattr("admin.routes._get_r2_client", lambda: mock_client)


def test_purge_404_for_user_role(app, client, db_session, monkeypatch):
    """Non-root-admin POSTing purge → 404."""
    u = _mk_user(db_session)
    _login(client, u)
    res = client.post("/admin/q-store/notes-purge")
    assert res.status_code == 404


def test_purge_deletes_q_notes_only(app, client, db_session, monkeypatch):
    """3 Q notes + 5 B notes → after purge: 0 Q, 5 B remain."""
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    if not Store.query.filter_by(name="B").first():
        db_session.add(Store(name="B", login_enabled=True))
    db_session.commit()

    for _ in range(3):
        db_session.add(Note(store="Q", title="q-note", content="x"))
    for _ in range(5):
        db_session.add(Note(store="B", title="b-note", content="x"))
    db_session.commit()

    _patch_r2(monkeypatch, _mock_r2_client(success_keys=[]))
    _login(client, admin)
    res = client.post("/admin/q-store/notes-purge")
    assert res.status_code == 200
    data = res.get_json()
    assert data["deleted_notes"] == 3

    # Verify DB state
    assert Note.query.filter_by(store="Q").count() == 0
    assert Note.query.filter_by(store="B").count() == 5


def test_purge_writes_user_log(app, client, db_session, monkeypatch):
    """After purge → UserLog has 1 new row with action='purge_store_notes'."""
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    db_session.commit()
    db_session.add(Note(store="Q", title="x", content="x"))
    db_session.commit()

    before = UserLog.query.filter_by(action="purge_store_notes").count()
    _patch_r2(monkeypatch, _mock_r2_client(success_keys=[]))
    _login(client, admin)
    client.post("/admin/q-store/notes-purge")

    after = UserLog.query.filter_by(action="purge_store_notes").count()
    assert after == before + 1
    log = UserLog.query.filter_by(action="purge_store_notes").order_by(UserLog.id.desc()).first()
    assert log.target_name == "store:Q"
    assert log.operator_name == admin.username
    assert log.target_id is None
    assert "notes=1" in (log.detail or "")


def test_purge_returns_correct_response_shape(app, client, db_session, monkeypatch):
    """Response has deleted_notes, deleted_r2_ok, deleted_r2_fail, errors keys."""
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    db_session.commit()

    _patch_r2(monkeypatch, _mock_r2_client(success_keys=[]))
    _login(client, admin)
    res = client.post("/admin/q-store/notes-purge")
    data = res.get_json()
    assert "deleted_notes" in data
    assert "deleted_r2_ok" in data
    assert "deleted_r2_fail" in data
    assert "errors" in data
    assert isinstance(data["errors"], list)
