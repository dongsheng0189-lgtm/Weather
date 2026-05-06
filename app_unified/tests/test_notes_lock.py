import uuid
import pytest
from datetime import datetime, timedelta

from extensions import db
from models import User, Note


def _make_users(db_session):
    """Create two unique users (alice, bob) for a single test, isolated by uuid suffix."""
    suffix = uuid.uuid4().hex[:6]
    alice = User(username=f"alice_{suffix}", store="A", role="admin")
    alice.set_password("x")
    bob = User(username=f"bob_{suffix}", store="A", role="admin")
    bob.set_password("x")
    db_session.add_all([alice, bob])
    db_session.commit()
    return alice, bob


def _make_note(db_session, store="A"):
    n = Note(title="t", content="c", store=store, category="misc")
    db_session.add(n)
    db_session.commit()
    return n


def test_ensure_lock_acquires_when_unlocked(app, db_session):
    from notes.routes import _ensure_lock_for_mutation
    alice, _bob = _make_users(db_session)
    n = _make_note(db_session)
    ok, locker = _ensure_lock_for_mutation(n, alice.id)
    assert ok is True
    assert n.locked_by == alice.id
    assert n.locked_at is not None


def test_ensure_lock_passes_when_self_locked(app, db_session):
    from notes.routes import _ensure_lock_for_mutation
    alice, _bob = _make_users(db_session)
    n = _make_note(db_session)
    n.locked_by = alice.id
    n.locked_at = datetime.utcnow()
    db_session.commit()
    ok, locker = _ensure_lock_for_mutation(n, alice.id)
    assert ok is True
    assert n.locked_by == alice.id  # unchanged


def test_ensure_lock_rejects_when_other_locked_fresh(app, db_session):
    from notes.routes import _ensure_lock_for_mutation
    alice, bob = _make_users(db_session)
    n = _make_note(db_session)
    n.locked_by = alice.id
    n.locked_at = datetime.utcnow()
    db_session.commit()
    ok, locker = _ensure_lock_for_mutation(n, bob.id)
    assert ok is False
    assert locker.username == alice.username


def test_ensure_lock_steals_expired_lock(app, db_session):
    from notes.routes import _ensure_lock_for_mutation
    alice, bob = _make_users(db_session)
    n = _make_note(db_session)
    n.locked_by = alice.id
    n.locked_at = datetime.utcnow() - timedelta(minutes=10)  # 過期（>5min）
    db_session.commit()
    ok, locker = _ensure_lock_for_mutation(n, bob.id)
    assert ok is True
    assert n.locked_by == bob.id
