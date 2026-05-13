"""Backend tests for Q-store purge endpoints.

Spec: docs/superpowers/specs/2026-05-13-q-store-purge-button-design.md
TEMP: q-store-purge — remove this whole test file when feature is taken out.
"""
import uuid
import pytest
from unittest.mock import MagicMock, patch
from models import User, Store, Note, NoteAttachment, UserLog
from extensions import db


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
