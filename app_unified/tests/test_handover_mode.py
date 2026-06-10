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


def test_logout_clears_handover_flag(app):
    """共用裝置：登出必須清掉 _handover，避免下一個人繼承延長。"""
    client = app.test_client()
    with app.app_context():
        uid = _make_user(role="admin").id
    _login(client, uid)
    client.post("/auth/handover/start")
    with client.session_transaction() as s:
        assert s.get("_handover") is True
    client.get("/auth/logout")
    with client.session_transaction() as s:
        assert not s.get("_handover")


from pathlib import Path

_BASE = Path(__file__).parent.parent / 'templates' / 'base.html'


def test_base_has_handover_banner():
    src = _BASE.read_text(encoding='utf-8')
    assert 'id="handover-banner"' in src
    assert 'handover_active' in src


def test_base_nav_has_handover_toggle_and_js():
    src = _BASE.read_text(encoding='utf-8')
    assert 'startHandover()' in src
    assert 'function startHandover()' in src
    assert 'function endHandover()' in src


def test_base_wires_endpoints_and_idle_extension():
    src = _BASE.read_text(encoding='utf-8')
    assert '/auth/handover/start' in src
    assert '/auth/handover/end' in src
    assert "window.location.href='/auth/logout'" in src
    assert 'IDLE_LIMIT_MS = 15 * 60 * 1000' in src
