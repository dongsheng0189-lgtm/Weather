"""Tests for Note.shift — 班別 A/B/N + 標題組裝 + filter"""
import uuid
import pytest
from models import User, Note, SHIFT_CHOICES
from extensions import db


def _mk_user(db_session, username=None, store="B"):
    u = User(
        username=username or f"u_{uuid.uuid4().hex[:6]}",
        role="user",
        store=store,
        is_active=True,
    )
    u.set_password("1234")
    db_session.add(u)
    db_session.commit()
    return u


def _mk_note(db_session, user, **kwargs):
    n = Note(
        user_id=user.id,
        author_name=user.username,
        title=kwargs.get("title", "t"),
        content=kwargs.get("content", "c"),
        store=kwargs.get("store", user.store),
        status=kwargs.get("status", "pending"),
        category=kwargs.get("category", "misc"),
        note_type=kwargs.get("note_type", "note"),
        shift=kwargs.get("shift"),
    )
    db_session.add(n)
    db_session.commit()
    return n


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_shift_default_is_none(app, db_session):
    u = _mk_user(db_session)
    n = Note(user_id=u.id, author_name=u.username, title="x", content="y", store=u.store)
    db_session.add(n)
    db_session.commit()
    assert n.shift is None


def test_shift_choices_constant():
    assert SHIFT_CHOICES == ("A", "B", "N")


def test_title_employee_shift_a(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.get("/notes/new?type=note&category=employee&shift=A")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    # default_title 經模板渲染後出現在 input value
    assert "A員工" in body


def test_title_employee_no_shift(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.get("/notes/new?type=note&category=employee&shift=none")
    body = res.get_data(as_text=True)
    assert "員工" in body
    # 不應有 A員工 / B員工 / N員工
    assert "A員工" not in body and "B員工" not in body and "N員工" not in body


def test_title_machine_shift_b(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.get("/notes/new?type=note&category=machine&shift=B")
    body = res.get_data(as_text=True)
    assert "B機台" in body


def test_title_machine_no_shift(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.get("/notes/new?type=note&category=machine&shift=none")
    body = res.get_data(as_text=True)
    assert "機台" in body
    assert "A機台" not in body
    assert "B機台" not in body
    assert "N機台" not in body


def test_title_checklist_employee_shift_n(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.get("/notes/new?type=checklist&category=employee&shift=N")
    body = res.get_data(as_text=True)
    assert "N員工" in body


def test_title_invalid_shift_falls_back_to_none(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.get("/notes/new?type=note&category=employee&shift=XYZ")
    body = res.get_data(as_text=True)
    # 不合法值應回退成「不需要」
    assert "A員工" not in body
    assert "員工" in body


def test_editor_renders_currentShift_for_post_body(app, client, db_session):
    """Regression: editor.html 必須注入 currentShift JS 變數，否則 POST 不帶 shift。"""
    u = _mk_user(db_session)
    _login(client, u)
    res = client.get("/notes/new?type=note&category=employee&shift=A")
    body = res.get_data(as_text=True)
    assert 'var currentShift = "A"' in body


def test_editor_renders_currentShift_null_when_no_shift(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.get("/notes/new?type=note&category=employee&shift=none")
    body = res.get_data(as_text=True)
    assert 'var currentShift = null' in body


def test_create_note_persists_shift(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.post("/notes/api", json={
        "title": "test",
        "content": "x",
        "category": "employee",
        "shift": "A",
    })
    assert res.status_code == 201
    note_id = res.get_json()["id"]
    n = Note.query.get(note_id)
    assert n.shift == "A"


def test_create_note_no_shift_keeps_null(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.post("/notes/api", json={
        "title": "test",
        "content": "x",
        "category": "employee",
    })
    note_id = res.get_json()["id"]
    n = Note.query.get(note_id)
    assert n.shift is None


def test_create_note_invalid_shift_keeps_null(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.post("/notes/api", json={
        "title": "test",
        "content": "x",
        "category": "employee",
        "shift": "XYZ",
    })
    note_id = res.get_json()["id"]
    n = Note.query.get(note_id)
    assert n.shift is None


def test_index_shift_filter_a(app, client, db_session):
    u = _mk_user(db_session, store="B")
    _login(client, u)
    _mk_note(db_session, u, title="alpha-A", category="employee", shift="A")
    _mk_note(db_session, u, title="alpha-B", category="employee", shift="B")
    _mk_note(db_session, u, title="alpha-NONE", category="employee", shift=None)
    res = client.get("/notes/?store=B&category=employee&shift=A&range=all")
    body = res.get_data(as_text=True)
    assert "alpha-A" in body
    assert "alpha-B" not in body
    assert "alpha-NONE" not in body


def test_index_shift_filter_none_means_show_all(app, client, db_session):
    """『不需要』filter = 不過濾班別，A/B/N/NULL 全部都列出"""
    u = _mk_user(db_session, store="B")
    _login(client, u)
    _mk_note(db_session, u, title="bravo-A", category="employee", shift="A")
    _mk_note(db_session, u, title="bravo-B", category="employee", shift="B")
    _mk_note(db_session, u, title="bravo-NULL", category="employee", shift=None)
    # 不帶 shift 或 shift=none → 顯示全部
    for url in ["/notes/?store=B&category=employee&range=all",
                "/notes/?store=B&category=employee&shift=none&range=all"]:
        body = client.get(url).get_data(as_text=True)
        assert "bravo-A" in body, f"missing A in {url}"
        assert "bravo-B" in body, f"missing B in {url}"
        assert "bravo-NULL" in body, f"missing NULL in {url}"
