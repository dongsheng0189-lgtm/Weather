"""Tests for admin/routes.py::set_role.

Role and module-permissions (features) are fully DECOUPLED: changing a user's
role must never add or remove any feature. Module permissions are managed only
through the root-admin "設定模組權限" UI (set_user_features, require_root_admin).

Regression: a 店員 was demoted admin→user and silently lost "vetting" (人員查詢
變 404) because the old set_role side-effect stripped it. That must never happen
again.
"""
import json
import uuid
import pytest

from extensions import db
from models import Store, User


@pytest.fixture
def admin_user(app):
    """Create a fresh super_admin per test (uuid-suffixed username).

    Overrides the conftest-level admin_user fixture for this module so we can
    return a plain dict (id/username/password) and avoid DetachedInstanceError
    when the test re-queries inside its own app context.
    """
    with app.app_context():
        if not Store.query.filter_by(name="A").first():
            db.session.add(Store(name="A"))
            db.session.commit()
        suffix = uuid.uuid4().hex[:8]
        u = User(username=f"super-{suffix}", store="A", role="super_admin")
        u.set_password("x")
        u.set_features(["notes", "vetting"])
        db.session.add(u)
        db.session.commit()
        return {"id": u.id, "username": u.username, "password": "x"}


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user["id"])
        sess["_fresh"] = True


def _make_target(role="user", features=None):
    u = User(username=f"target-{uuid.uuid4().hex[:8]}", store="A", role=role)
    u.set_password("x")
    u.set_features(features if features is not None else ["notes"])
    db.session.add(u)
    db.session.commit()
    return u


def test_set_role_changes_role(app, admin_user):
    """Sanity: set-role still updates the role itself."""
    client = app.test_client()
    _login_as(client, admin_user)
    with app.app_context():
        target = _make_target(role="user", features=["notes"])
        tid = target.id
    r = client.post(f"/admin/users/{tid}/set-role", json={"role": "admin"})
    assert r.status_code == 200
    with app.app_context():
        assert User.query.get(tid).role == "admin"


def test_promote_user_to_admin_does_not_touch_features(app, admin_user):
    """user → admin: features untouched — vetting is NOT auto-added."""
    client = app.test_client()
    _login_as(client, admin_user)
    with app.app_context():
        target = _make_target(role="user", features=["notes"])
        tid = target.id
    r = client.post(f"/admin/users/{tid}/set-role", json={"role": "admin"})
    assert r.status_code == 200
    with app.app_context():
        feats = json.loads(User.query.get(tid).features)
        assert feats == ["notes"]  # 完全不變


def test_promote_to_super_admin_does_not_touch_features(app, admin_user):
    """user(features=[]) → super_admin: still empty — nothing auto-added."""
    client = app.test_client()
    _login_as(client, admin_user)
    with app.app_context():
        target = _make_target(role="user", features=[])
        tid = target.id
    r = client.post(f"/admin/users/{tid}/set-role", json={"role": "super_admin"})
    assert r.status_code == 200
    with app.app_context():
        feats = json.loads(User.query.get(tid).features)
        assert feats == []  # 完全不變


def test_demote_admin_to_user_keeps_vetting(app, admin_user):
    """admin → user: vetting (and all features) preserved.

    This is the 茹佳 regression — demotion must NOT strip 人員查詢.
    """
    client = app.test_client()
    _login_as(client, admin_user)
    with app.app_context():
        target = _make_target(role="admin", features=["notes", "vetting", "mynotes"])
        tid = target.id
    r = client.post(f"/admin/users/{tid}/set-role", json={"role": "user"})
    assert r.status_code == 200
    with app.app_context():
        feats = json.loads(User.query.get(tid).features)
        assert "vetting" in feats  # 不再被拔掉
        assert "notes" in feats
        assert "mynotes" in feats
