"""vetting blueprint 白名單管理端點（GET /vetting/whitelist, POST/DELETE /vetting/whitelist/<id>）。
權限邏輯：同 whitelist_add — 需持有 vetting_whitelist_add 功能，且受 before_request vetting gate 管控。
"""
import uuid
import pytest
from models import User
from extensions import db, limiter


@pytest.fixture(autouse=True)
def _reset_limiter(app):
    with app.app_context():
        limiter.reset()
    yield


def _mk(db_session, root_admin=False, features=None):
    name = "admin" if root_admin else f"u_{uuid.uuid4().hex[:6]}"
    existing = User.query.filter_by(username=name).first()
    if existing:
        if features is not None:
            existing.set_features(features); db_session.commit()
        return existing
    u = User(username=name, role="user", store="B", is_active=True)
    u.set_password("1234")
    if features is not None:
        u.set_features(features)
    db_session.add(u); db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


# ── Granted user (vetting + vetting_whitelist_add) ────────────────────────────

def test_granted_list_returns_entries(app, client, db_session):
    from vetting import whitelist
    with app.app_context():
        whitelist.add("0912345678")
    u = _mk(db_session, features=["vetting", "vetting_whitelist_add"])
    _login(client, u)
    res = client.get("/vetting/whitelist")
    assert res.status_code == 200
    data = res.get_json()
    assert "entries" in data
    phones = [e["phone"] for e in data["entries"]]
    assert "0912345678" in phones


def test_granted_update_ok(app, client, db_session):
    from vetting import whitelist
    with app.app_context():
        whitelist.add("0922333444")
        eid = whitelist.list_all()[0]["id"]
    u = _mk(db_session, features=["vetting", "vetting_whitelist_add"])
    _login(client, u)
    res = client.post(f"/vetting/whitelist/{eid}", json={"phone": "0933444555"})
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"
    with app.app_context():
        assert whitelist.is_whitelisted("0933444555") is True


def test_granted_delete_ok(app, client, db_session):
    from vetting import whitelist
    with app.app_context():
        whitelist.add("0944555666")
        eid = whitelist.list_all()[0]["id"]
    u = _mk(db_session, features=["vetting", "vetting_whitelist_add"])
    _login(client, u)
    res = client.delete(f"/vetting/whitelist/{eid}")
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"
    with app.app_context():
        assert whitelist.is_whitelisted("0944555666") is False


# ── Update with invalid phone → 400 ──────────────────────────────────────────

def test_update_invalid_phone_400(app, client, db_session):
    from vetting import whitelist
    with app.app_context():
        whitelist.add("0955666777")
        eid = whitelist.list_all()[0]["id"]
    u = _mk(db_session, features=["vetting", "vetting_whitelist_add"])
    _login(client, u)
    res = client.post(f"/vetting/whitelist/{eid}", json={"phone": "not-a-phone"})
    assert res.status_code == 400
    body = res.get_json()
    assert body.get("error") == "invalid_phone"


# ── User with vetting but NO whitelist perm → 404 ────────────────────────────

def test_no_whitelist_perm_get_404(app, client, db_session):
    u = _mk(db_session, features=["vetting"])
    _login(client, u)
    assert client.get("/vetting/whitelist").status_code == 404


def test_no_whitelist_perm_post_404(app, client, db_session):
    from vetting import whitelist
    with app.app_context():
        whitelist.add("0966777888")
        eid = whitelist.list_all()[0]["id"]
    u = _mk(db_session, features=["vetting"])
    _login(client, u)
    assert client.post(f"/vetting/whitelist/{eid}", json={"phone": "0977888999"}).status_code == 404


def test_no_whitelist_perm_delete_404(app, client, db_session):
    from vetting import whitelist
    with app.app_context():
        whitelist.add("0977888999")
        eid = whitelist.list_all()[0]["id"]
    u = _mk(db_session, features=["vetting"])
    _login(client, u)
    assert client.delete(f"/vetting/whitelist/{eid}").status_code == 404


# ── User without vetting feature → before_request gate → 404 ─────────────────

def test_no_vetting_feature_get_404(app, client, db_session):
    u = _mk(db_session, features=["notes"])
    _login(client, u)
    assert client.get("/vetting/whitelist").status_code == 404
