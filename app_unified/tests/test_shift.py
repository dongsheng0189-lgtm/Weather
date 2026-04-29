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
        category=kwargs.get("category", "unclassified"),
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


def test_title_personal_shift_b(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.get("/notes/new?type=note&category=personal&shift=B")
    body = res.get_data(as_text=True)
    assert f"B {u.username}筆記" in body


def test_title_personal_no_shift(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.get("/notes/new?type=note&category=personal&shift=none")
    body = res.get_data(as_text=True)
    assert f"{u.username}筆記" in body
    assert f"A {u.username}筆記" not in body


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
