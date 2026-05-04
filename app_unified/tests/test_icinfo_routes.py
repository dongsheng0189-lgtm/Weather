"""Test icinfo routes — features gate + session 行為 + log 寫入."""
from unittest.mock import patch
import json
import uuid
import pytest

from models import User


def _mk_user(db_session, features=None, role="user"):
    """建 user，features 以 list 傳入 (e.g. ['notes', 'v_aux'])."""
    if features is None:
        features = ["notes"]
    uname = f"u_icinfo_{uuid.uuid4().hex[:8]}"
    u = User(
        username=uname,
        role=role,
        is_active=True,
        features=json.dumps(features),
    )
    u.set_password("1234")
    db_session.add(u)
    db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_user_without_v_aux_feature_gets_404(client, app, db_session):
    u = _mk_user(db_session, features=["notes"], role="user")
    _login(client, u)
    r = client.get("/v/aux/c")
    assert r.status_code == 404


def test_admin_with_v_aux_can_get_captcha(client, app, db_session):
    u = _mk_user(db_session, features=["notes", "v_aux"], role="admin")
    _login(client, u)

    with patch("icinfo.routes.fetch_captcha", return_value=(b"\xff\xd8\xff\xe0JPEG", "JS123")):
        r = client.get("/v/aux/c")

    assert r.status_code == 200
    body = r.get_json()
    assert "image" in body and len(body["image"]) > 0
    with client.session_transaction() as s:
        assert s.get("icinfo_jsessionid") == "JS123"


def test_search_without_session_jsessionid_returns_captcha_expired(client, app, db_session):
    u = _mk_user(db_session, features=["v_aux"], role="admin")
    _login(client, u)
    r = client.post("/v/aux/s", json={
        "uino": "A12345678", "issueDate": "2024-01-01",
        "expiryDate": "2026-01-01", "serialNo": "1234567890", "captcha": "abcd",
    })
    assert r.status_code == 200
    assert r.get_json()["error"] == "captcha_expired"


def test_search_success_returns_image_and_writes_log(client, app, db_session):
    u = _mk_user(db_session, features=["v_aux"], role="admin")
    uid = u.id
    _login(client, u)
    with client.session_transaction() as s:
        s["icinfo_jsessionid"] = "JS123"

    fake = {"matched": True, "image": "BASE64IMG", "search_time": "2026-05-04 12:00", "rcode": "SUCCESS"}
    with patch("icinfo.routes.submit_search", return_value=fake):
        r = client.post("/v/aux/s", json={
            "uino": "A12345678", "issueDate": "2024-01-01",
            "expiryDate": "2026-01-01", "serialNo": "1234567890", "captcha": "abcd",
        })

    assert r.status_code == 200
    body = r.get_json()
    assert body["matched"] is True
    assert body["image"] == "BASE64IMG"

    from models import IcinfoQueryLog
    logs = IcinfoQueryLog.query.filter_by(user_id=uid).all()
    assert len(logs) == 1
    assert logs[0].queried_uino == "A12345678"
    assert logs[0].matched is True

    with client.session_transaction() as s:
        assert s.get("icinfo_jsessionid") is None


def test_search_no_match_writes_log_with_matched_false(client, app, db_session):
    u = _mk_user(db_session, features=["v_aux"], role="admin")
    uid = u.id
    _login(client, u)
    with client.session_transaction() as s:
        s["icinfo_jsessionid"] = "JS123"

    fake = {"matched": False, "image": None, "search_time": None, "rcode": "FAIL"}
    with patch("icinfo.routes.submit_search", return_value=fake):
        r = client.post("/v/aux/s", json={
            "uino": "B98765432", "issueDate": "", "expiryDate": "",
            "serialNo": "9876543210", "captcha": "wrong",
        })

    assert r.status_code == 200
    body = r.get_json()
    assert body["matched"] is False
    assert body["image"] is None

    from models import IcinfoQueryLog
    log = IcinfoQueryLog.query.filter_by(user_id=uid).first()
    assert log.matched is False
    assert log.queried_uino == "B98765432"


def test_upstream_error_returns_upstream_error_field(client, app, db_session):
    u = _mk_user(db_session, features=["v_aux"], role="admin")
    uid = u.id
    _login(client, u)
    with client.session_transaction() as s:
        s["icinfo_jsessionid"] = "JS123"

    from icinfo.errors import UpstreamError
    with patch("icinfo.routes.submit_search", side_effect=UpstreamError("timeout")):
        r = client.post("/v/aux/s", json={
            "uino": "C11111111", "issueDate": "", "expiryDate": "",
            "serialNo": "1", "captcha": "x",
        })

    assert r.status_code == 200
    body = r.get_json()
    assert body.get("error") == "upstream"

    from models import IcinfoQueryLog
    assert IcinfoQueryLog.query.filter_by(user_id=uid).count() == 0
