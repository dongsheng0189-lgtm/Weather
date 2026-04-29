"""Tests for vetting blueprint routes + feature gate."""
import base64
import uuid
import pytest
from unittest.mock import MagicMock
from models import User
from extensions import db, limiter


@pytest.fixture(autouse=True)
def _reset_limiter(app):
    """Clear flask-limiter state between tests (avoid cross-test rate limits)."""
    with app.app_context():
        limiter.reset()
    yield


def _mk_user(db_session, username=None, role="user", root_admin=False):
    target_name = "admin" if root_admin else (username or f"u_{uuid.uuid4().hex[:6]}")
    # Reuse existing 'admin' user if already created by a prior test
    existing = User.query.filter_by(username=target_name).first()
    if existing:
        return existing
    u = User(
        username=target_name,
        role=role,
        store="B",
        is_active=True,
    )
    u.set_password("1234")
    db_session.add(u)
    db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_vetting_index_requires_auth(client):
    res = client.get("/vetting/")
    # 未登入會被 flask-login 導去 login
    assert res.status_code in (302, 401)


def test_vetting_index_404_for_non_admin(app, client, db_session):
    u = _mk_user(db_session)  # 非 admin
    _login(client, u)
    res = client.get("/vetting/")
    assert res.status_code == 404


def test_vetting_index_200_for_root_admin(app, client, db_session):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.get("/vetting/")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "人員查詢" in body
    # 表單只有姓名一個 input
    assert 'id="vet-name"' in body


def test_vetting_index_404_for_super_admin_role(app, client, db_session):
    """super_admin 角色但 username 不是 'admin' → 不能進。"""
    u = _mk_user(db_session, username="boss", role="super_admin")
    _login(client, u)
    res = client.get("/vetting/")
    assert res.status_code == 404


def test_vetting_index_404_for_admin_role(app, client, db_session):
    """admin 角色但 username 不是 'admin' → 不能進。"""
    u = _mk_user(db_session, username="manager", role="admin")
    _login(client, u)
    res = client.get("/vetting/")
    assert res.status_code == 404


def test_vetting_nav_hidden_from_super_admin_role(app, client, db_session):
    """nav 內不會出現「人員查詢」連結給 super_admin（非 root admin）。"""
    u = _mk_user(db_session, username="boss2", role="super_admin")
    _login(client, u)
    res = client.get("/notes/")  # 任一其他可進入的頁面
    body = res.get_data(as_text=True)
    # nav 不該有人員查詢的連結（emoji 或 label）
    assert "人員查詢" not in body
    assert "/vetting/" not in body


def test_vetting_nav_visible_to_root_admin(app, client, db_session):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.get("/notes/")
    body = res.get_data(as_text=True)
    assert "人員查詢" in body


def test_start_endpoint_returns_query_id_and_captcha(app, client, db_session, monkeypatch):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)

    fake_npa_session = MagicMock()
    fake_tokens = {"_csrf": "abc", "_csrf_header": "X-CSRF-TOKEN", "_key": "k123"}

    def fake_npa_home(sess=None):
        return fake_tokens, fake_npa_session
    monkeypatch.setattr("vetting.routes.npa.fetch_home_state", fake_npa_home)
    monkeypatch.setattr("vetting.routes.npa.fetch_auth_token",
                        lambda sess, tok: "JWT_FAKE")
    monkeypatch.setattr("vetting.routes.npa.fetch_captcha_bytes",
                        lambda sess: b"\xff\xd8\xff\xe0FAKE_JPEG")
    # 司法院 + MJIB 模擬無事發生
    monkeypatch.setattr("vetting.routes.judicial.fetch_home_state",
                        lambda sess=None: ({"form_action": "x", "hidden_fields": {}}, MagicMock()))
    monkeypatch.setattr("vetting.routes.judicial.submit_search",
                        lambda sess, state, name: [])
    monkeypatch.setattr("vetting.routes.mjib.search_by_name",
                        lambda name, session=None: [])
    monkeypatch.setattr("vetting.routes.throttle_sleep", lambda: None)

    res = client.post("/vetting/start", json={"name": "王小明", "id": "A123456789"})
    assert res.status_code == 200
    data = res.get_json()
    assert "query_id" in data
    assert "captcha_b64" in data
    base64.b64decode(data["captcha_b64"])  # 合法 base64


def test_start_missing_params_400(app, client, db_session):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    # 缺 id
    res = client.post("/vetting/start", json={"name": "王"})
    assert res.status_code == 400
    # 缺 name
    res = client.post("/vetting/start", json={"id": "A123"})
    assert res.status_code == 400


def test_submit_endpoint_returns_three_segments(app, client, db_session, monkeypatch):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    from vetting.routes import _put_state
    qid = _put_state({
        "npa_session": MagicMock(),
        "npa_tokens": {"_csrf": "x", "_csrf_header": "h", "_key": "k"},
        "npa_jwt": "jwt",
        "name": "王", "id_no": "A123",
        "judicial": [{"subject": "詐欺", "case_no": "n", "date": "d",
                      "court": "c", "detail_url": "u",
                      "keyword_hits": ["詐欺"], "masked_names": []}],
        "mjib": [{"name": "王某", "eng_name": "", "masked_id": "A12***",
                  "birth": "1990", "photo_url": "", "detail_url": ""}],
    })
    monkeypatch.setattr("vetting.routes.npa.submit_query",
                        lambda *a, **kw: [{"name": "王", "crime": "竊盜", "detail_url": ""}])

    res = client.post("/vetting/submit",
                      json={"query_id": qid, "captcha": "ABCD"})
    assert res.status_code == 200
    data = res.get_json()
    assert "judicial" in data and len(data["judicial"]) == 1
    assert "mjib" in data and len(data["mjib"]) == 1
    assert "npa" in data and len(data["npa"]) == 1


def test_submit_expired_query_id(app, client, db_session):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.post("/vetting/submit",
                      json={"query_id": "nonexistent", "captcha": "x"})
    assert res.status_code == 410
    assert res.get_json()["error"] == "query_expired"


def test_submit_returns_429_when_npa_ratelimits(app, client, db_session, monkeypatch):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    from vetting.routes import _put_state
    from vetting.errors import RateLimited
    qid = _put_state({
        "npa_session": MagicMock(),
        "npa_tokens": {"_csrf": "x", "_csrf_header": "h", "_key": "k"},
        "npa_jwt": "jwt",
        "name": "王", "id_no": "A123",
        "judicial": [], "mjib": [],
    })

    def raise_rl(*a, **kw):
        raise RateLimited("upstream 429")
    monkeypatch.setattr("vetting.routes.npa.submit_query", raise_rl)
    res = client.post("/vetting/submit",
                      json={"query_id": qid, "captcha": "x"})
    assert res.status_code == 429
    assert res.get_json()["error"] == "rate_limited"


def test_submit_captcha_invalid(app, client, db_session, monkeypatch):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    from vetting.routes import _put_state
    qid = _put_state({
        "npa_session": MagicMock(),
        "npa_tokens": {"_csrf": "x", "_csrf_header": "h", "_key": "k"},
        "npa_jwt": "jwt",
        "name": "王", "id_no": "A123",
        "judicial": [], "mjib": [],
    })

    def raise_invalid(*a, **kw):
        raise ValueError("captcha_invalid")
    monkeypatch.setattr("vetting.routes.npa.submit_query", raise_invalid)

    res = client.post("/vetting/submit",
                      json={"query_id": qid, "captcha": "WRONG"})
    assert res.status_code == 400
    assert res.get_json()["error"] == "captcha_invalid"
