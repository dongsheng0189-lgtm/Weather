"""Tests for vetting blueprint routes + feature gate."""
import uuid
import pytest
from unittest.mock import MagicMock
from models import User
from extensions import db


def _mk_user(db_session, username=None, role="user", root_admin=False):
    u = User(
        username="admin" if root_admin else (username or f"u_{uuid.uuid4().hex[:6]}"),
        role=role,
        store="B",
        is_active=True,
    )
    u.set_password("1234")
    db_session.add(u)
    db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_vetting_index_requires_auth(client):
    res = client.get("/vetting/")
    # 未登入會被 flask-login 導去 login
    assert res.status_code in (302, 401)


def test_vetting_index_404_for_non_admin(app, client, db_session):
    u = _mk_user(db_session)  # 非 admin
    _login(client, u)
    res = client.get("/vetting/")
    assert res.status_code == 404


def test_vetting_index_200_for_root_admin(app, client, db_session):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.get("/vetting/")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "人員查詢" in body
    # 表單只有姓名一個 input
    assert 'id="vet-name"' in body
