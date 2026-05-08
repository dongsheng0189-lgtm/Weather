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
