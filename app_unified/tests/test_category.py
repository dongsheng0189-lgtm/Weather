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
