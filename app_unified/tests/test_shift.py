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
