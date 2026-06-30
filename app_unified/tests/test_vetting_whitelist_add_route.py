"""新增白名單端點：權限、格式、去重。"""
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


def test_granted_user_can_add(app, client, db_session):
    u = _mk(db_session, features=["vetting", "vetting_whitelist_add"])
    _login(client, u)
    res = client.post("/vetting/whitelist/add", json={"phone": "0912-345-678"})
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"


def test_duplicate_returns_exists(app, client, db_session):
    u = _mk(db_session, features=["vetting", "vetting_whitelist_add"])
    _login(client, u)
    client.post("/vetting/whitelist/add", json={"phone": "0922333444"})
    res = client.post("/vetting/whitelist/add", json={"phone": "0922-333-444"})
    assert res.status_code == 200
    assert res.get_json()["status"] == "exists"


def test_invalid_phone_400(app, client, db_session):
    u = _mk(db_session, features=["vetting", "vetting_whitelist_add"])
    _login(client, u)
    res = client.post("/vetting/whitelist/add", json={"phone": "nope"})
    assert res.status_code == 400


def test_user_without_permission_404(app, client, db_session):
    """有 vetting 但沒 vetting_whitelist_add → 404。"""
    u = _mk(db_session, features=["vetting"])
    _login(client, u)
    res = client.post("/vetting/whitelist/add", json={"phone": "0912345678"})
    assert res.status_code == 404


def test_user_without_vetting_404(app, client, db_session):
    """連 vetting 都沒有 → before_request gate 先擋 404。"""
    u = _mk(db_session, features=["notes"])
    _login(client, u)
    res = client.post("/vetting/whitelist/add", json={"phone": "0912345678"})
    assert res.status_code == 404
