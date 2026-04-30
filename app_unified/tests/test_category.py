"""Tests for Note.category — 類別分類 + 篩選器重構"""
import uuid
import pytest
from models import User, Note, CATEGORY_CHOICES, CATEGORY_LABELS, CATEGORY_CREATABLE
from extensions import db


def _mk_user(db_session, username=None, role="user", store="B", active=True):
    u = User(
        username=username or f"u_{uuid.uuid4().hex[:6]}",
        role=role,
        store=store,
        is_active=active,
    )
    u.set_password("1234")
    db_session.add(u)
    db_session.commit()
    return u


def _mk_note(db_session, user, category="misc", **kwargs):
    n = Note(
        user_id=user.id,
        author_name=user.username,
        title=kwargs.get("title", "t"),
        content=kwargs.get("content", "c"),
        store=kwargs.get("store", user.store),
        status=kwargs.get("status", "pending"),
        category=category,
        note_type=kwargs.get("note_type", "note"),
    )
    db_session.add(n)
    db_session.commit()
    return n


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_category_default_is_misc(app, db_session):
    u = _mk_user(db_session)
    n = Note(user_id=u.id, author_name=u.username, title="x", content="y", store=u.store)
    db_session.add(n)
    db_session.commit()
    assert n.category == "misc"


def test_category_constants_defined():
    assert CATEGORY_CHOICES == ["employee", "customer", "machine", "misc"]
    assert CATEGORY_LABELS["employee"] == "員工"
    assert CATEGORY_LABELS["customer"] == "客人"
    assert CATEGORY_LABELS["machine"] == "機台"
    assert CATEGORY_LABELS["misc"] == "雜項"
    assert "personal" not in CATEGORY_LABELS
    assert "unclassified" not in CATEGORY_LABELS
    assert CATEGORY_CREATABLE == ["employee", "customer", "machine", "misc"]


def test_new_note_with_category_employee(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.get("/notes/new?type=note&category=employee")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # default_title 應為 "M/D 員工"（不帶 username）
    assert "員工" in html
    # 找 title-input 的 value，驗證不含 username
    import re
    m = re.search(r'id="title-input"[^>]*value="([^"]*)"', html)
    assert m, "找不到 title-input"
    title_val = m.group(1)
    assert "員工" in title_val
    assert u.username not in title_val, f"employee 類別不該帶 username，但 title={title_val!r}"


def test_new_note_with_category_machine(app, client, db_session):
    u = _mk_user(db_session, username="alice")
    _login(client, u)
    r = client.get("/notes/new?type=note&category=machine")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "alice筆記" in html


def test_new_checklist_with_category_customer(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.get("/notes/new?type=checklist&category=customer")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "客人" in html


def test_new_note_without_category(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.get("/notes/new?type=note")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # fallback misc → 帶 username（沿用舊 default）
    assert u.username in html


def test_new_note_invalid_category(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.get("/notes/new?type=note&category=hacker")
    assert r.status_code == 200
    # 防禦 fallback misc → 帶 username
    html = r.get_data(as_text=True)
    assert u.username in html


def test_post_api_accepts_valid_category(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.post("/notes/api", json={
        "title": "T", "content": "C",
        "category": "employee", "status": "pending", "note_type": "note",
    })
    assert r.status_code == 201
    nid = r.get_json()["id"]
    db_session.expire_all()
    n = Note.query.get(nid)
    assert n.category == "employee"


def test_post_api_rejects_invalid_category_fallback(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.post("/notes/api", json={
        "title": "T", "content": "C",
        "category": "hacker", "status": "pending", "note_type": "note",
    })
    assert r.status_code == 201  # fallback misc, not 400
    nid = r.get_json()["id"]
    db_session.expire_all()
    assert Note.query.get(nid).category == "misc"


def test_get_api_returns_category(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    n = _mk_note(db_session, u, category="customer")
    r = client.get(f"/notes/api/{n.id}")
    assert r.status_code == 200
    assert r.get_json()["category"] == "customer"


def test_put_api_updates_category(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    n = _mk_note(db_session, u, category="misc")
    r = client.put(f"/notes/api/{n.id}", json={"category": "employee"})
    assert r.status_code == 200
    db_session.expire_all()
    assert Note.query.get(n.id).category == "employee"


def test_filter_by_category(app, client, db_session):
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    _mk_note(db_session, u, category="employee", title="emp_note_uniqXYZ")
    _mk_note(db_session, u, category="customer", title="cust_note_uniqXYZ")
    _mk_note(db_session, u, category="misc", title="misc_note_uniqXYZ")
    r = client.get("/notes/?category=employee&range=30d")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "emp_note_uniqXYZ" in html
    assert "cust_note_uniqXYZ" not in html
    assert "misc_note_uniqXYZ" not in html


def test_filter_misc_visible(app, client, db_session):
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    _mk_note(db_session, u, category="misc", title="old_note_abc")
    r = client.get("/notes/?category=misc&range=30d")
    assert r.status_code == 200
    assert "old_note_abc" in r.get_data(as_text=True)


def test_filter_keyword_exclusive(app, client, db_session):
    """有 keyword 時,category/status/range 全部忽略"""
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    _mk_note(db_session, u, category="employee", title="hello_world_A", content="x")
    _mk_note(db_session, u, category="customer", title="hello_universe_B", content="y")
    r = client.get("/notes/?q=hello&category=employee")
    html = r.get_data(as_text=True)
    assert "hello_world_A" in html
    assert "hello_universe_B" in html


def test_filter_category_and_status_combined(app, client, db_session):
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    _mk_note(db_session, u, category="employee", status="pending", title="noteA")
    _mk_note(db_session, u, category="employee", status="resolved", title="noteB")
    _mk_note(db_session, u, category="customer", status="pending", title="noteC")
    r = client.get("/notes/?category=employee&status=pending&range=30d")
    html = r.get_data(as_text=True)
    assert "noteA" in html
    assert "noteB" not in html
    assert "noteC" not in html


def test_filter_cascade_category_link_resets_time(app, client, db_session):
    """category tab href 應該 reset range (不帶 range 或 range=)"""
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    r = client.get("/notes/?category=employee&range=7d&status=pending")
    html = r.get_data(as_text=True)
    import re
    # 找 category=customer 的 tab href
    m = re.search(r'href="([^"]*category=customer[^"]*)"', html)
    assert m, "找不到 category=customer tab"
    href = m.group(1)
    # href 應該不含 range=7d 也不含 status=pending
    assert "range=7d" not in href
    assert "status=pending" not in href


def test_filter_cascade_time_link_preserves_category_resets_status(app, client, db_session):
    """range tab href 應維持 category 但 reset status"""
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    r = client.get("/notes/?category=employee&range=today&status=pending")
    html = r.get_data(as_text=True)
    import re
    m = re.search(r'href="([^"]*range=3d[^"]*)"', html)
    assert m, "找不到 range=3d tab"
    href = m.group(1)
    assert "category=employee" in href
    assert "status=pending" not in href
