"""Tests for admin/routes.py::set_role auto-features sync (Task 9)."""
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


def test_promote_user_to_admin_adds_vetting(app, admin_user):
    """user → admin: features 補 vetting（notes 已有）。"""
    client = app.test_client()
    _login_as(client, admin_user)
    with app.app_context():
        target = _make_target(role="user", features=["notes"])
        tid = target.id
    r = client.post(f"/admin/users/{tid}/set-role", json={"role": "admin"})
    assert r.status_code == 200
    with app.app_context():
        t2 = User.query.get(tid)
        feats = json.loads(t2.features)
        assert "notes" in feats
        assert "vetting" in feats


def test_promote_user_to_super_admin_adds_both(app, admin_user):
    """user(features=[]) → super_admin: 補 notes + vetting。"""
    client = app.test_client()
    _login_as(client, admin_user)
    with app.app_context():
        target = _make_target(role="user", features=[])  # 空 features 起步
        tid = target.id
    r = client.post(f"/admin/users/{tid}/set-role", json={"role": "super_admin"})
    assert r.status_code == 200
    with app.app_context():
        t2 = User.query.get(tid)
        feats = json.loads(t2.features)
        assert "notes" in feats
        assert "vetting" in feats


def test_demote_admin_to_user_removes_vetting_keeps_notes(app, admin_user):
    """admin → user: 移除 vetting、保留 notes（mynotes 也保留 — 不在自動 sync 範圍）。"""
    client = app.test_client()
    _login_as(client, admin_user)
    with app.app_context():
        target = _make_target(role="admin", features=["notes", "vetting", "mynotes"])
        tid = target.id
    r = client.post(f"/admin/users/{tid}/set-role", json={"role": "user"})
    assert r.status_code == 200
    with app.app_context():
        t2 = User.query.get(tid)
        feats = json.loads(t2.features)
        assert "notes" in feats
        assert "vetting" not in feats  # 移除
        assert "mynotes" in feats  # 不在自動 sync 範圍 → 保留
