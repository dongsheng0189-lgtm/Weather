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


def _mk_note(db_session, user, category="unclassified", **kwargs):
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


def test_category_default_is_unclassified(app, db_session):
    u = _mk_user(db_session)
    n = Note(user_id=u.id, author_name=u.username, title="x", content="y", store=u.store)
    db_session.add(n)
    db_session.commit()
    assert n.category == "unclassified"


def test_category_constants_defined():
    assert CATEGORY_CHOICES == ["employee", "customer", "misc", "personal", "unclassified"]
    assert CATEGORY_LABELS["employee"] == "員工"
    assert CATEGORY_LABELS["customer"] == "客人"
    assert CATEGORY_LABELS["misc"] == "雜事"
    assert CATEGORY_LABELS["personal"] == "個人"
    assert CATEGORY_LABELS["unclassified"] == "未分類"
    assert CATEGORY_CREATABLE == ["employee", "customer", "misc", "personal"]


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


def test_new_note_with_category_personal(app, client, db_session):
    u = _mk_user(db_session, username="alice")
    _login(client, u)
    r = client.get("/notes/new?type=note&category=personal")
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
    # fallback unclassified → 帶 username（沿用舊 default）
    assert u.username in html


def test_new_note_invalid_category(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.get("/notes/new?type=note&category=hacker")
    assert r.status_code == 200
    # 防禦 fallback unclassified → 帶 username
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
    assert r.status_code == 201  # fallback unclassified, not 400
    nid = r.get_json()["id"]
    db_session.expire_all()
    assert Note.query.get(nid).category == "unclassified"


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
    n = _mk_note(db_session, u, category="unclassified")
    r = client.put(f"/notes/api/{n.id}", json={"category": "misc"})
    assert r.status_code == 200
    db_session.expire_all()
    assert Note.query.get(n.id).category == "misc"
