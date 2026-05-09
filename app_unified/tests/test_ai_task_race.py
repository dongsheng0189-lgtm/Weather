"""notes/ai/task race-window: result must be repeatable within TTL so that
overlapping setInterval polls don't see 「任務不存在」 right after a
successful done read. Now backed by DB row + expires_at TTL."""
import json
import uuid
from datetime import datetime, timedelta

import pytest

from extensions import db
from models import AiTask, Store, User


@pytest.fixture
def admin_user(app, db_session):
    if not Store.query.filter_by(name="A").first():
        db.session.add(Store(name="A"))
        db.session.commit()
    suffix = uuid.uuid4().hex[:8]
    u = User(username=f"admin-{suffix}", store="A", role="admin")
    u.set_password("x")
    db.session.add(u)
    db.session.commit()
    return u


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _seed_db_task(user_id, task_id, status, result=None, error=None,
                  expires_in_sec=30, age_sec=0):
    now = datetime.utcnow()
    db.session.add(AiTask(
        id=task_id,
        kind="notes",
        user_id=user_id,
        status=status,
        result_json=json.dumps(result) if result is not None else None,
        error_msg=error,
        created_at=now - timedelta(seconds=age_sec),
        updated_at=now - timedelta(seconds=age_sec),
        expires_at=now + timedelta(seconds=expires_in_sec) if expires_in_sec is not None else None,
    ))
    db.session.commit()


def test_done_task_repeatable_within_ttl(app, client, admin_user):
    _login_as(client, admin_user)
    tid = "t-grace-" + uuid.uuid4().hex[:8]
    _seed_db_task(admin_user.id, tid, status="done", result="summary text")

    r1 = client.get(f"/notes/ai/task/{tid}")
    r2 = client.get(f"/notes/ai/task/{tid}")
    assert r1.status_code == r2.status_code == 200
    assert r1.get_json() == r2.get_json() == {"status": "done", "result": "summary text"}


def test_error_task_repeatable_within_ttl(app, client, admin_user):
    _login_as(client, admin_user)
    tid = "t-err-" + uuid.uuid4().hex[:8]
    _seed_db_task(admin_user.id, tid, status="error", error="boom")

    r1 = client.get(f"/notes/ai/task/{tid}")
    r2 = client.get(f"/notes/ai/task/{tid}")
    assert r1.status_code == r2.status_code == 200
    assert r2.get_json() == {"status": "error", "message": "boom"}


def test_done_task_swept_after_ttl(app, client, admin_user):
    """expires_at 過期 → sweep 刪掉、回 404。"""
    _login_as(client, admin_user)
    tid = "t-sweep-" + uuid.uuid4().hex[:8]
    _seed_db_task(admin_user.id, tid, status="done", result="x", expires_in_sec=-1)

    r = client.get(f"/notes/ai/task/{tid}")
    assert r.status_code == 404
    db.session.expire_all()
    assert db.session.get(AiTask, tid) is None


def test_processing_task_returns_processing(app, client, admin_user):
    _login_as(client, admin_user)
    tid = "t-proc-" + uuid.uuid4().hex[:8]
    _seed_db_task(admin_user.id, tid, status="processing", expires_in_sec=None)

    r = client.get(f"/notes/ai/task/{tid}")
    assert r.status_code == 200
    assert r.get_json() == {"status": "processing"}


def test_unknown_task_still_404(app, client, admin_user):
    _login_as(client, admin_user)
    r = client.get("/notes/ai/task/does-not-exist")
    assert r.status_code == 404
    assert r.get_json()["status"] == "error"
