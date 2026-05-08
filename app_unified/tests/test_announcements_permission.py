"""Pure permission helpers — no Flask app required."""
import pytest
from unittest.mock import MagicMock

from notes.announcement_routes import (
    _can_create,
    _can_target_store,
    _can_modify,
    _can_view,
)


# ── _can_create ──────────────────────────────────────────────

def test_can_create_super_admin(monkeypatch):
    user = MagicMock()
    user.is_admin = MagicMock(return_value=True)
    user.is_super_admin = MagicMock(return_value=True)
    user.is_authenticated = True
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_create() is True


def test_can_create_admin(monkeypatch):
    user = MagicMock()
    user.is_admin = MagicMock(return_value=True)
    user.is_super_admin = MagicMock(return_value=False)
    user.is_authenticated = True
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_create() is True


def test_can_create_user(monkeypatch):
    user = MagicMock()
    user.is_admin = MagicMock(return_value=False)
    user.is_super_admin = MagicMock(return_value=False)
    user.is_authenticated = True
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_create() is False


# ── _can_target_store ─────────────────────────────────────────

def test_can_target_store_super_admin_any(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=True)
    user.is_admin = MagicMock(return_value=True)
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_target_store(None) is True       # 全部
    assert _can_target_store("B店") is True      # 任一店
    assert _can_target_store("C店") is True


def test_can_target_store_admin_own_only(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=False)
    user.is_admin = MagicMock(return_value=True)
    user.store = "B店"
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_target_store("B店") is True
    assert _can_target_store("C店") is False    # 別店
    assert _can_target_store(None) is False     # 全部 (admin 不能)


def test_can_target_store_user_never(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=False)
    user.is_admin = MagicMock(return_value=False)
    user.store = "B店"
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_target_store("B店") is False
    assert _can_target_store(None) is False


# ── _can_modify / _can_view ──────────────────────────────────

def _make_announcement(store):
    a = MagicMock()
    a.store = store
    return a


def test_can_modify_super_admin_anything(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=True)
    user.is_admin = MagicMock(return_value=True)
    user.store = "B店"
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_modify(_make_announcement(None)) is True
    assert _can_modify(_make_announcement("C店")) is True


def test_can_modify_admin_own_store_only(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=False)
    user.is_admin = MagicMock(return_value=True)
    user.store = "B店"
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_modify(_make_announcement("B店")) is True
    assert _can_modify(_make_announcement("C店")) is False
    assert _can_modify(_make_announcement(None)) is False  # admin 不能改全部


def test_can_modify_user_never(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=False)
    user.is_admin = MagicMock(return_value=False)
    user.store = "B店"
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_modify(_make_announcement("B店")) is False


def test_can_view_super_admin_anything(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=True)
    user.store = "B店"
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_view(_make_announcement(None)) is True
    assert _can_view(_make_announcement("C店")) is True


def test_can_view_global_or_own(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=False)
    user.store = "B店"
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_view(_make_announcement(None)) is True   # 全部
    assert _can_view(_make_announcement("B店")) is True  # 自己店
    assert _can_view(_make_announcement("C店")) is False # 別店
