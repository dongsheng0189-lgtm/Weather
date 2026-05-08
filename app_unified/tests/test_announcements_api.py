"""Announcement API integration tests — uses Flask test client + DB fixtures."""
import json
import pytest


@pytest.fixture
def db(db_session):
    """Provide the SQLAlchemy `db` extension (with active app context via db_session)."""
    from extensions import db as _db
    return _db


def _make_user(db, role, store=None, username=None, features=None):
    from models import User
    import json as _json
    if features is None:
        features = ["notes"]
    u = User(
        username=username or f"{role}_{store or 'na'}_{id(role)}",
        role=role,
        store=store,
        features=_json.dumps(features),
    )
    u.set_password("0000")
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    """flask-login 直接 session 設定（避開人臉驗證）。"""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


# ── POST /notes/announcement/api ─────────────────────────────

def test_post_user_forbidden(client, db):
    u = _make_user(db, "user", store="B店", username="u_post")
    _login(client, u)
    r = client.post("/notes/announcement/api",
                    json={"title": "hi", "content": "x", "store": "B店"})
    assert r.status_code == 403


def test_post_admin_own_store_ok(client, db):
    a = _make_user(db, "admin", store="B店", username="adm_post_ok")
    _login(client, a)
    r = client.post("/notes/announcement/api",
                    json={"title": "B店清潔", "content": "x", "store": "B店"})
    assert r.status_code == 201
    body = r.get_json()
    assert body["store"] == "B店"
    assert body["is_global"] is False
    assert body["author_name"] == a.username


def test_post_admin_global_forbidden(client, db):
    a = _make_user(db, "admin", store="B店", username="adm_post_global")
    _login(client, a)
    r = client.post("/notes/announcement/api",
                    json={"title": "x", "content": "x", "store": None})
    assert r.status_code == 403


def test_post_admin_other_store_forbidden(client, db):
    a = _make_user(db, "admin", store="B店", username="adm_post_other")
    _login(client, a)
    r = client.post("/notes/announcement/api",
                    json={"title": "x", "content": "x", "store": "C店"})
    assert r.status_code == 403


def test_post_super_admin_global_ok(client, db):
    sa = _make_user(db, "super_admin", store=None, username="sa_post_global")
    _login(client, sa)
    r = client.post("/notes/announcement/api",
                    json={"title": "全店公告", "content": "x", "store": None})
    assert r.status_code == 201
    body = r.get_json()
    assert body["is_global"] is True
    assert body["store"] is None


def test_post_super_admin_any_store_ok(client, db):
    sa = _make_user(db, "super_admin", store=None, username="sa_post_any")
    _login(client, sa)
    r = client.post("/notes/announcement/api",
                    json={"title": "C店公告", "content": "x", "store": "C店"})
    assert r.status_code == 201
    assert r.get_json()["store"] == "C店"


def test_post_requires_title(client, db):
    sa = _make_user(db, "super_admin", username="sa_post_title")
    _login(client, sa)
    r = client.post("/notes/announcement/api",
                    json={"title": "", "content": "x", "store": None})
    assert r.status_code == 400


def test_post_store_empty_string_rejected(client, db):
    sa = _make_user(db, "super_admin", username="sa_post_empty")
    _login(client, sa)
    r = client.post("/notes/announcement/api",
                    json={"title": "x", "content": "x", "store": ""})
    assert r.status_code == 400


def test_post_store_whitespace_only_rejected(client, db):
    sa = _make_user(db, "super_admin", username="sa_post_ws")
    _login(client, sa)
    r = client.post("/notes/announcement/api",
                    json={"title": "x", "content": "x", "store": "   "})
    assert r.status_code == 400


def test_post_store_non_string_rejected(client, db):
    sa = _make_user(db, "super_admin", username="sa_post_nonstr")
    _login(client, sa)
    r = client.post("/notes/announcement/api",
                    json={"title": "x", "content": "x", "store": 123})
    assert r.status_code == 400


def test_post_store_with_trailing_space_normalized(client, db):
    """trailing/leading whitespace on store is stripped, then validated."""
    sa = _make_user(db, "super_admin", username="sa_post_strip")
    _login(client, sa)
    r = client.post("/notes/announcement/api",
                    json={"title": "x", "content": "x", "store": "B店  "})
    assert r.status_code == 201
    body = r.get_json()
    assert body["store"] == "B店"  # stripped


# ── GET /notes/announcement/api (list) ────────────────────────

def test_list_user_sees_global_and_own(client, db):
    sa = _make_user(db, "super_admin", username="sa_list_seed")
    u_b = _make_user(db, "user", store="B店", username="u_list_b")

    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "全店", "content": "x", "store": None})
    client.post("/notes/announcement/api",
                json={"title": "B店", "content": "x", "store": "B店"})
    client.post("/notes/announcement/api",
                json={"title": "C店", "content": "x", "store": "C店"})

    _login(client, u_b)
    r = client.get("/notes/announcement/api")
    assert r.status_code == 200
    items = r.get_json()["items"]
    titles = [it["title"] for it in items]
    assert "全店" in titles
    assert "B店" in titles
    assert "C店" not in titles


def test_list_super_admin_sees_all(client, db):
    sa = _make_user(db, "super_admin", username="sa_list_all")
    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "T1", "content": "x", "store": None})
    client.post("/notes/announcement/api",
                json={"title": "T2", "content": "x", "store": "B店"})
    client.post("/notes/announcement/api",
                json={"title": "T3", "content": "x", "store": "C店"})

    r = client.get("/notes/announcement/api")
    assert r.status_code == 200
    titles = [it["title"] for it in r.get_json()["items"]]
    assert {"T1", "T2", "T3"}.issubset(set(titles))


def test_list_orders_by_created_at_desc(client, db):
    import time
    sa = _make_user(db, "super_admin", username="sa_list_order")
    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "FIRST", "content": "x", "store": None})
    time.sleep(0.05)
    client.post("/notes/announcement/api",
                json={"title": "SECOND", "content": "x", "store": None})
    time.sleep(0.05)
    client.post("/notes/announcement/api",
                json={"title": "THIRD", "content": "x", "store": None})

    r = client.get("/notes/announcement/api")
    titles = [it["title"] for it in r.get_json()["items"]]
    # 最新建立的在最上面
    assert titles.index("THIRD") < titles.index("SECOND") < titles.index("FIRST")


# ── GET /notes/announcement/api/<id> ─────────────────────────

def test_get_single_visible(client, db):
    sa = _make_user(db, "super_admin", username="sa_get_seed")
    u_b = _make_user(db, "user", store="B店", username="u_get_b")

    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "B店公告", "content": "x", "store": "B店"})
    aid = rc.get_json()["id"]

    _login(client, u_b)
    r = client.get(f"/notes/announcement/api/{aid}")
    assert r.status_code == 200
    assert r.get_json()["title"] == "B店公告"


def test_get_single_invisible_returns_404(client, db):
    sa = _make_user(db, "super_admin", username="sa_get_inv_seed")
    u_b = _make_user(db, "user", store="B店", username="u_get_inv")

    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "C店公告", "content": "x", "store": "C店"})
    aid = rc.get_json()["id"]

    _login(client, u_b)
    r = client.get(f"/notes/announcement/api/{aid}")
    assert r.status_code == 404


def test_get_single_not_found(client, db):
    sa = _make_user(db, "super_admin", username="sa_get_404")
    _login(client, sa)
    r = client.get("/notes/announcement/api/999999")
    assert r.status_code == 404


# ── PUT /notes/announcement/api/<id> ─────────────────────────

def test_put_super_admin_can_edit_anything(client, db):
    sa = _make_user(db, "super_admin", username="sa_put_any")
    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "old", "content": "x", "store": "C店"})
    aid = rc.get_json()["id"]

    r = client.put(f"/notes/announcement/api/{aid}",
                   json={"title": "new", "content": "y", "store": "C店"})
    assert r.status_code == 200
    assert r.get_json()["title"] == "new"


def test_put_admin_cannot_edit_other_store(client, db):
    sa = _make_user(db, "super_admin", username="sa_put_seed")
    adm_b = _make_user(db, "admin", store="B店", username="adm_put_b")

    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "C店", "content": "x", "store": "C店"})
    aid = rc.get_json()["id"]

    _login(client, adm_b)
    r = client.put(f"/notes/announcement/api/{aid}",
                   json={"title": "hijack", "content": "x", "store": "C店"})
    # admin (B店) 不可修改 C店 公告 → 看不見 → 404
    assert r.status_code == 404


def test_put_admin_cannot_change_global(client, db):
    sa = _make_user(db, "super_admin", username="sa_put_global_seed")
    adm_b = _make_user(db, "admin", store="B店", username="adm_put_global")

    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "全店", "content": "x", "store": None})
    aid = rc.get_json()["id"]

    _login(client, adm_b)
    r = client.put(f"/notes/announcement/api/{aid}",
                   json={"title": "x", "content": "x", "store": None})
    assert r.status_code == 403  # 看得到（visibility 通過）但 modify 失敗


def test_put_admin_cannot_change_store_to_other(client, db):
    adm_b = _make_user(db, "admin", store="B店", username="adm_put_change")
    _login(client, adm_b)
    rc = client.post("/notes/announcement/api",
                     json={"title": "B店", "content": "x", "store": "B店"})
    aid = rc.get_json()["id"]

    r = client.put(f"/notes/announcement/api/{aid}",
                   json={"title": "B店", "content": "x", "store": "C店"})
    assert r.status_code == 403  # _can_target_store("C店") 失敗


# ── DELETE /notes/announcement/api/<id> ───────────────────────

def test_delete_super_admin_ok(client, db):
    sa = _make_user(db, "super_admin", username="sa_del")
    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "to delete", "content": "x", "store": None})
    aid = rc.get_json()["id"]

    r = client.delete(f"/notes/announcement/api/{aid}")
    assert r.status_code == 200

    r2 = client.get(f"/notes/announcement/api/{aid}")
    assert r2.status_code == 404


def test_delete_admin_other_store_forbidden(client, db):
    sa = _make_user(db, "super_admin", username="sa_del_seed")
    adm_b = _make_user(db, "admin", store="B店", username="adm_del_b")

    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "C店", "content": "x", "store": "C店"})
    aid = rc.get_json()["id"]

    _login(client, adm_b)
    r = client.delete(f"/notes/announcement/api/{aid}")
    assert r.status_code == 404  # 看不見


def test_delete_user_forbidden(client, db):
    sa = _make_user(db, "super_admin", username="sa_del_user_seed")
    u = _make_user(db, "user", store="B店", username="u_del")

    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "B店", "content": "x", "store": "B店"})
    aid = rc.get_json()["id"]

    _login(client, u)
    r = client.delete(f"/notes/announcement/api/{aid}")
    assert r.status_code == 403  # 看得到但無權限刪
