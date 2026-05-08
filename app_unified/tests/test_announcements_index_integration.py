"""Notes index 渲染整合測試 — 公告區塊 / 第三按鈕 conditional 顯示。"""
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
        username=username or f"{role}_{store or 'na'}",
        role=role,
        store=store,
        features=_json.dumps(features),
    )
    u.set_password("0000")
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_admin_sees_create_announcement_button(client, db):
    a = _make_user(db, "admin", store="B店", username="adm_idx_btn")
    _login(client, a)
    r = client.get("/notes/")
    assert r.status_code == 200
    assert "新增公告" in r.get_data(as_text=True)


def test_user_does_not_see_create_announcement_button(client, db):
    u = _make_user(db, "user", store="B店", username="u_idx_btn")
    _login(client, u)
    r = client.get("/notes/")
    assert r.status_code == 200
    assert "新增公告" not in r.get_data(as_text=True)


def test_announcements_render_above_notes(client, db):
    sa = _make_user(db, "super_admin", username="sa_idx_seed")
    u = _make_user(db, "user", store="B店", username="u_idx_render")

    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "TEST_ANNOUNCEMENT", "content": "x", "store": None})

    _login(client, u)
    r = client.get("/notes/")
    html = r.get_data(as_text=True)
    assert "TEST_ANNOUNCEMENT" in html
    # announcement-section / announcement-card class 出現在 HTML 中
    assert "announcement-section" in html or "announcement-card" in html


def test_user_does_not_see_other_store_announcement_in_index(client, db):
    sa = _make_user(db, "super_admin", username="sa_idx_other_seed")
    u = _make_user(db, "user", store="B店", username="u_idx_other")

    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "C_STORE_SECRET", "content": "x", "store": "C店"})

    _login(client, u)
    r = client.get("/notes/")
    html = r.get_data(as_text=True)
    assert "C_STORE_SECRET" not in html


def test_admin_sees_edit_button_for_own_store(client, db):
    sa = _make_user(db, "super_admin", username="sa_idx_edit_seed")
    adm_b = _make_user(db, "admin", store="B店", username="adm_idx_edit")

    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "B_OWN", "content": "x", "store": "B店"})
    client.post("/notes/announcement/api",
                json={"title": "C_NOT_MINE", "content": "x", "store": "C店"})

    _login(client, adm_b)
    r = client.get("/notes/")
    html = r.get_data(as_text=True)
    # 看得到自己店公告
    assert "B_OWN" in html
    # 看不到 C 店
    assert "C_NOT_MINE" not in html
