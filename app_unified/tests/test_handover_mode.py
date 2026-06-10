"""Tests for handover mode（admin-gated 15min sliding idle extension）。"""
import time
import uuid

from extensions import db
from models import User, Store


def _make_user(role="user", pin="1357"):
    if not Store.query.filter_by(name="A").first():
        db.session.add(Store(name="A"))
        db.session.commit()
    u = User(username=f"u-{uuid.uuid4().hex[:8]}", store="A", role=role)
    u.set_password(pin)
    u.set_features(["notes"])
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, uid):
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["_last_request_at"] = int(time.time())


def test_admin_can_start_handover(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(role="admin").id
    _login(client, uid)
    r = client.post("/auth/handover/start")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"
    with client.session_transaction() as s:
        assert s.get("_handover") is True


def test_regular_user_cannot_start_handover(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(role="user").id
    _login(client, uid)
    r = client.post("/auth/handover/start")
    assert r.status_code == 403
    with client.session_transaction() as s:
        assert not s.get("_handover")


def test_start_requires_login(app):
    client = app.test_client()
    r = client.post("/auth/handover/start")
    assert r.status_code in (401, 302)


def test_end_clears_handover(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(role="admin").id
    _login(client, uid)
    client.post("/auth/handover/start")
    r = client.post("/auth/handover/end")
    assert r.status_code == 200
    with client.session_transaction() as s:
        assert not s.get("_handover")


def test_handover_extends_idle_to_15min(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(role="admin").id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["_handover"] = True
        s["_last_request_at"] = int(time.time()) - 6 * 60
    r = client.get("/auth/ping")
    assert r.status_code == 200


def test_handover_still_locks_after_15min(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(role="admin").id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["_handover"] = True
        s["_last_request_at"] = int(time.time()) - 16 * 60
    r = client.get("/auth/ping")
    assert r.status_code == 401


def test_without_handover_locks_at_5min(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(role="admin").id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["_last_request_at"] = int(time.time()) - 6 * 60
    r = client.get("/auth/ping")
    assert r.status_code == 401
