"""Tests for auth/routes.py self-service PIN change (/auth/change-pin)."""
import uuid
import pytest

from extensions import db
from models import User, Store, UserLog


def _make_user(pin="1357"):
    """Create a 北店 user with a known 4-digit PIN. Ensures Store 'A' exists."""
    if not Store.query.filter_by(name="A").first():
        db.session.add(Store(name="A"))
        db.session.commit()
    u = User(username=f"u-{uuid.uuid4().hex[:8]}", store="A", role="user")
    u.set_password(pin)
    u.set_features(["notes"])
    db.session.add(u)
    db.session.commit()
    return u


def _login_as(client, user_id):
    import time
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        sess["_last_request_at"] = int(time.time())


def test_change_pin_success(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(pin="1357").id
    _login_as(client, uid)
    r = client.post("/auth/change-pin",
                    json={"current_pin": "1357", "new_pin": "2580", "confirm_pin": "2580"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"
    with app.app_context():
        u = User.query.get(uid)
        assert u.check_password("2580")
        assert not u.check_password("1357")
        assert UserLog.query.filter_by(target_id=uid, action="change_pin").count() == 1


def test_change_pin_wrong_current(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(pin="1357").id
    _login_as(client, uid)
    r = client.post("/auth/change-pin",
                    json={"current_pin": "0000", "new_pin": "2580", "confirm_pin": "2580"})
    assert r.status_code == 400
    assert "目前 PIN" in r.get_json()["message"]
    with app.app_context():
        assert User.query.get(uid).check_password("1357")  # unchanged


def test_change_pin_confirm_mismatch(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(pin="1357").id
    _login_as(client, uid)
    r = client.post("/auth/change-pin",
                    json={"current_pin": "1357", "new_pin": "2580", "confirm_pin": "2581"})
    assert r.status_code == 400
    assert "不一致" in r.get_json()["message"]


def test_change_pin_same_as_current(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(pin="1357").id
    _login_as(client, uid)
    r = client.post("/auth/change-pin",
                    json={"current_pin": "1357", "new_pin": "1357", "confirm_pin": "1357"})
    assert r.status_code == 400
    assert "不能與目前相同" in r.get_json()["message"]


@pytest.mark.parametrize("weak", ["0000", "1111", "1234", "9876", "3210"])
def test_change_pin_weak_rejected(app, weak):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(pin="1357").id
    _login_as(client, uid)
    r = client.post("/auth/change-pin",
                    json={"current_pin": "1357", "new_pin": weak, "confirm_pin": weak})
    assert r.status_code == 400
    assert "過於簡單" in r.get_json()["message"]


@pytest.mark.parametrize("bad", ["123", "12345", "12a4", "abcd", ""])
def test_change_pin_not_4_digits(app, bad):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(pin="1357").id
    _login_as(client, uid)
    r = client.post("/auth/change-pin",
                    json={"current_pin": "1357", "new_pin": bad, "confirm_pin": bad})
    assert r.status_code == 400
    assert "4 位數字" in r.get_json()["message"]


def test_change_pin_requires_login(app):
    client = app.test_client()
    r = client.post("/auth/change-pin",
                    json={"current_pin": "1357", "new_pin": "2580", "confirm_pin": "2580"})
    assert r.status_code in (401, 302)


def test_settings_page_renders(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(pin="1357").id
    _login_as(client, uid)
    r = client.get("/auth/settings")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "修改 PIN" in body
    assert "字體大小" in body
    assert 'id="cp-current"' in body          # PIN 表單存在
    assert 'data-size="small"' in body          # 字體 picker 存在


def test_settings_requires_login(app):
    client = app.test_client()
    r = client.get("/auth/settings")
    assert r.status_code in (401, 302)
