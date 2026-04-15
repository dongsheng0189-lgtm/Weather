"""Admin rename user + cascade to TrustedDevice.device_name"""
import uuid
import pytest
from models import User, TrustedDevice, UserLog
from extensions import db


def _mk_user(db_session, username=None, role="user", store=None, active=True):
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


def _mk_device(db_session, user, device_name):
    d = TrustedDevice(
        fingerprint=f"fp_{uuid.uuid4().hex[:8]}",
        device_name=device_name,
        user_id=user.id,
        is_approved=True,
    )
    db_session.add(d)
    db_session.commit()
    return d


def _login(client, user, pin="1234"):
    """Bypass real auth (6-tap/face) and seed Flask-Login session directly."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_admin_rename_cascades_device_name(app, client, db_session):
    admin = _mk_user(db_session, username=f"admin_{uuid.uuid4().hex[:6]}", role="admin", store="B")
    admin.set_password("0000")
    db_session.commit()
    target = _mk_user(db_session, username="alice", store="B")
    d1 = _mk_device(db_session, target, device_name="alice")
    d2 = _mk_device(db_session, target, device_name="alice")
    _login(client, admin, pin="0000")

    r = client.post(f"/admin/users/{target.id}/rename", json={"new_username": "alice2"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["status"] == "ok"

    db_session.expire_all()
    assert User.query.get(target.id).username == "alice2"
    assert TrustedDevice.query.get(d1.id).device_name == "alice2"
    assert TrustedDevice.query.get(d2.id).device_name == "alice2"

    log = UserLog.query.filter_by(target_id=target.id, action="rename").first()
    assert log is not None
    assert "alice" in (log.detail or "") and "alice2" in (log.detail or "")


def test_admin_cannot_rename_other_store(app, client, db_session):
    admin = _mk_user(db_session, username=f"admin_{uuid.uuid4().hex[:6]}", role="admin", store="B")
    admin.set_password("0000")
    db_session.commit()
    target = _mk_user(db_session, username="bob", store="C")
    _login(client, admin, pin="0000")

    r = client.post(f"/admin/users/{target.id}/rename", json={"new_username": "bob2"})
    assert r.status_code == 403
    db_session.expire_all()
    assert User.query.get(target.id).username == "bob"


def test_super_admin_rename_any_store(app, client, db_session):
    sa = _mk_user(db_session, username=f"sa_{uuid.uuid4().hex[:6]}", role="super_admin", store=None)
    sa.set_password("0000")
    db_session.commit()
    target = _mk_user(db_session, username="carol", store="C")
    _login(client, sa, pin="0000")

    r = client.post(f"/admin/users/{target.id}/rename", json={"new_username": "carol2"})
    assert r.status_code == 200
    db_session.expire_all()
    assert User.query.get(target.id).username == "carol2"


def test_rename_conflict_returns_409(app, client, db_session):
    admin = _mk_user(db_session, username=f"admin_{uuid.uuid4().hex[:6]}", role="admin", store="B")
    admin.set_password("0000")
    db_session.commit()
    _mk_user(db_session, username="taken", store="B")
    target = _mk_user(db_session, username="dave", store="B")
    _login(client, admin, pin="0000")

    r = client.post(f"/admin/users/{target.id}/rename", json={"new_username": "taken"})
    assert r.status_code == 409
    db_session.expire_all()
    assert User.query.get(target.id).username == "dave"


def test_rename_empty_returns_400(app, client, db_session):
    admin = _mk_user(db_session, username=f"admin_{uuid.uuid4().hex[:6]}", role="admin", store="B")
    admin.set_password("0000")
    db_session.commit()
    target = _mk_user(db_session, username="eve", store="B")
    _login(client, admin, pin="0000")

    r = client.post(f"/admin/users/{target.id}/rename", json={"new_username": "   "})
    assert r.status_code == 400


def test_cannot_rename_self(app, client, db_session):
    admin = _mk_user(db_session, username=f"admin_{uuid.uuid4().hex[:6]}", role="admin", store="B")
    admin.set_password("0000")
    db_session.commit()
    _login(client, admin, pin="0000")

    r = client.post(f"/admin/users/{admin.id}/rename", json={"new_username": "admin_renamed"})
    assert r.status_code == 403


def test_rename_noop_same_name(app, client, db_session):
    admin = _mk_user(db_session, username=f"admin_{uuid.uuid4().hex[:6]}", role="admin", store="B")
    admin.set_password("0000")
    db_session.commit()
    target = _mk_user(db_session, username="frank", store="B")
    _login(client, admin, pin="0000")

    r = client.post(f"/admin/users/{target.id}/rename", json={"new_username": "frank"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_non_admin_forbidden(app, client, db_session):
    user = _mk_user(db_session, username=f"user_{uuid.uuid4().hex[:6]}", store="B")
    target = _mk_user(db_session, username="gina", store="B")
    _login(client, user)

    r = client.post(f"/admin/users/{target.id}/rename", json={"new_username": "gina2"})
    assert r.status_code in (302, 401, 403)
