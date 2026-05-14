"""vetting 15-min idle grace + EditKeepalive 掛載驗證。

User 設計：查詢結果含判例 / 社群連結、user 可能停 10+ 分鐘閱讀。預設 5 分鐘
session idle 太短會踢人，30 分鐘（mynotes 等級）又太鬆。折衷 15 分鐘、不分
mobile/desktop（user 明確指定）。
"""
import json as _json
import time

import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _make_vetting_user(db, username):
    from models import User
    u = User(
        username=username,
        role="user",
        store="A店",
        features=_json.dumps(["vetting", "notes"]),
    )
    u.set_password("0000")
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_last_request_at"] = int(time.time())


def test_vetting_page_loads_keepalive(client, db):
    u = _make_vetting_user(db, "vet_kp_load")
    _login(client, u)
    r = client.get("/vetting/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "edit_keepalive.js" in html
    assert "EditKeepalive.init()" in html


def test_vetting_request_sets_grace_until(client, db):
    u = _make_vetting_user(db, "vet_grace_set")
    _login(client, u)
    before = int(time.time())
    r = client.get("/vetting/")
    assert r.status_code == 200
    with client.session_transaction() as sess:
        until = sess.get("_vetting_grace_until")
    assert until is not None
    # grace 為 now + 15min；允許 ±5s 寬限應對 test 內 monotonic 漂移
    expected = before + 15 * 60
    assert abs(until - expected) <= 5, f"_vetting_grace_until={until}, expected~{expected}"


def test_vetting_grace_allows_14min_idle(client, db):
    """在 vetting grace 內、即使最後 request 是 14 分鐘前也不踢出。"""
    u = _make_vetting_user(db, "vet_grace_14")
    _login(client, u)
    now = int(time.time())
    with client.session_transaction() as sess:
        sess["_last_request_at"] = now - 14 * 60
        sess["_vetting_grace_until"] = now + 60  # 還在 grace 內
    r = client.get("/weather/")
    # /weather 是登入頁、200/302 都算還在登入；user 沒被 logout
    assert r.status_code in (200, 302)
    with client.session_transaction() as sess:
        assert sess.get("_user_id") is not None, "在 vetting grace 內被誤踢出"


def test_vetting_grace_expired_kicks_user_after_16min(client, db):
    """超過 15 分鐘 idle 仍會踢出（即使曾進 vetting）。"""
    u = _make_vetting_user(db, "vet_grace_expired")
    _login(client, u)
    now = int(time.time())
    with client.session_transaction() as sess:
        sess["_last_request_at"] = now - 16 * 60
        sess["_vetting_grace_until"] = now - 60  # grace 已過期
    client.get("/weather/")
    with client.session_transaction() as sess:
        assert sess.get("_user_id") is None, "超過 15 min 該被踢"


def test_no_vetting_grace_keeps_5min_rule(client, db):
    """從未進過 vetting → 6 分鐘 idle 走 5 分鐘規則踢出（regression guard）。"""
    u = _make_vetting_user(db, "vet_no_grace")
    _login(client, u)
    now = int(time.time())
    with client.session_transaction() as sess:
        sess["_last_request_at"] = now - 6 * 60
        # 不設任何 grace key
    client.get("/weather/")
    with client.session_transaction() as sess:
        assert sess.get("_user_id") is None, "5 min 規則該踢"


def test_vetting_grace_applies_to_mobile_ua(client, db):
    """user 明確指定 vetting 15 min 不分裝置；mobile UA 也享 grace。"""
    u = _make_vetting_user(db, "vet_mobile")
    _login(client, u)
    r = client.get("/vetting/", headers={
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    })
    assert r.status_code == 200
    with client.session_transaction() as sess:
        assert sess.get("_vetting_grace_until") is not None, "mobile 也該有 vetting grace"
