"""查詢電話時，命中白名單會在 social.phone 標 is_whitelisted。"""
import uuid
from unittest.mock import MagicMock
import pytest
from models import User
from extensions import db, limiter
from vetting import whitelist
from vetting.routes import _take_state


@pytest.fixture(autouse=True)
def _reset_limiter(app):
    with app.app_context():
        limiter.reset()
    yield


def _mk_root(db_session):
    existing = User.query.filter_by(username="admin").first()
    if existing:
        return existing
    u = User(username="admin", role="user", store="B", is_active=True)
    u.set_password("1234")
    db_session.add(u); db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _mock_npa_judicial_mjib(monkeypatch):
    monkeypatch.setattr("vetting.routes.npa.fetch_home_state",
                        lambda sess=None: ({"_csrf": "x", "_csrf_header": "h", "_key": "k"}, MagicMock()))
    monkeypatch.setattr("vetting.routes.npa.fetch_auth_token", lambda sess, tok: "JWT")
    monkeypatch.setattr("vetting.routes.npa.fetch_captcha_bytes", lambda sess: b"\xff\xd8FAKE")
    monkeypatch.setattr("vetting.routes.judicial.fetch_home_state",
                        lambda sess=None: ({"form_action": "x", "hidden_fields": {}}, MagicMock()))
    monkeypatch.setattr("vetting.routes.judicial.submit_search", lambda sess, state, name: [])
    monkeypatch.setattr("vetting.routes.mjib.search_by_name", lambda name, session=None: [])
    monkeypatch.setattr("vetting.routes.throttle_sleep", lambda: None)


def test_whitelisted_phone_flagged(app, client, db_session, monkeypatch):
    whitelist.add("0912345678")
    u = _mk_root(db_session); _login(client, u)
    _mock_npa_judicial_mjib(monkeypatch)
    res = client.post("/vetting/start", json={"name": "王", "phone": "0912-345-678"})
    assert res.status_code == 200
    entry = _take_state(res.get_json()["query_id"])
    assert entry["social"]["phone"]["is_whitelisted"] is True


def test_non_whitelisted_phone_not_flagged(app, client, db_session, monkeypatch):
    u = _mk_root(db_session); _login(client, u)
    _mock_npa_judicial_mjib(monkeypatch)
    res = client.post("/vetting/start", json={"name": "王", "phone": "0911000000"})
    assert res.status_code == 200
    entry = _take_state(res.get_json()["query_id"])
    assert entry["social"]["phone"]["is_whitelisted"] is False
