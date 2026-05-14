"""公告新增 / 編輯頁面必須掛 EditKeepalive，避免 5 分鐘 idle 把使用者踢回 weather。

User 5/13 回報：在公告編輯頁按儲存前切到別頁就被 logout，原因是這兩個頁面
沒掛 `static/js/edit_keepalive.js` + `EditKeepalive.init()`，session
被 `_enforce_session_idle` 5 分鐘規則清掉。
"""
import json as _json

import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _make_user(db, role, store=None, username=None):
    from models import User
    u = User(
        username=username,
        role=role,
        store=store,
        features=_json.dumps(["notes"]),
    )
    u.set_password("0000")
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_new_announcement_page_loads_keepalive(client, db):
    a = _make_user(db, "admin", store="B店", username="adm_keepalive_new")
    _login(client, a)
    r = client.get("/notes/announcement/new")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "edit_keepalive.js" in html, "缺 edit_keepalive.js script tag"
    assert "EditKeepalive.init()" in html, "缺 EditKeepalive.init() 呼叫"


def test_edit_announcement_page_loads_keepalive(client, db):
    a = _make_user(db, "admin", store="B店", username="adm_keepalive_edit")
    _login(client, a)
    r = client.post("/notes/announcement/api",
                    json={"title": "KP_TEST", "content": "x", "store": "B店"})
    assert r.status_code == 201
    ann_id = r.get_json()["id"]

    r = client.get(f"/notes/announcement/{ann_id}/edit")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "edit_keepalive.js" in html, "缺 edit_keepalive.js script tag"
    assert "EditKeepalive.init()" in html, "缺 EditKeepalive.init() 呼叫"
