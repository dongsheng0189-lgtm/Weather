"""mynotes get_ai_task — DB-backed task state, replaces former in-memory dict.

Done/error results stay readable until expires_at TTL (~30s) so overlapping
setInterval polls in the frontend don't get 「任務不存在」 right after the
first done read; after expiry → 404. Implementation now lives in DB so it
also survives cross-worker routing (workers=2 since 5/9).
"""
import json
import uuid
from datetime import datetime, timedelta

import pytest

from extensions import db
from models import AiTask, User


@pytest.fixture
def _user(app, db_session):
    suffix = uuid.uuid4().hex[:8]
    u = User(username=f"u-{suffix}")
    u.set_password("x")
    u.set_features(["mynotes"])
    db_session.add(u)
    db_session.commit()
    return u


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _seed_db_task(user_id, task_id, status, result=None, error=None,
                  expires_in_sec=30, age_sec=0):
    now = datetime.utcnow()
    db.session.add(AiTask(
        id=task_id,
        kind="mynotes",
        user_id=user_id,
        status=status,
        result_json=json.dumps(result) if result is not None else None,
        error_msg=error,
        created_at=now - timedelta(seconds=age_sec),
        updated_at=now - timedelta(seconds=age_sec),
        expires_at=now + timedelta(seconds=expires_in_sec) if expires_in_sec is not None else None,
    ))
    db.session.commit()


def test_done_task_repeatable_within_ttl(app, client, _user):
    _login_as(client, _user)
    tid = "tg-" + uuid.uuid4().hex[:8]
    _seed_db_task(_user.id, tid, status="done", result="summary text")

    r1 = client.get(f"/mynotes/api/ai/task/{tid}")
    r2 = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r1.status_code == r2.status_code == 200
    assert r1.get_json() == r2.get_json() == {"status": "done", "result": "summary text"}


def test_error_task_repeatable_within_ttl(app, client, _user):
    _login_as(client, _user)
    tid = "te-" + uuid.uuid4().hex[:8]
    _seed_db_task(_user.id, tid, status="error", error="boom")

    r1 = client.get(f"/mynotes/api/ai/task/{tid}")
    r2 = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r1.status_code == r2.status_code == 200
    assert r2.get_json() == {"status": "error", "message": "boom"}


def test_done_swept_after_ttl(app, client, _user):
    """expires_at 過期 → sweep 刪掉、回 404。"""
    _login_as(client, _user)
    tid = "ts-" + uuid.uuid4().hex[:8]
    # expires_at 在 1 秒前
    _seed_db_task(_user.id, tid, status="done", result="x", expires_in_sec=-1)

    r = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r.status_code == 404

    db.session.expire_all()
    assert db.session.get(AiTask, tid) is None


def test_processing_task_returns_processing(app, client, _user):
    _login_as(client, _user)
    tid = "tp-" + uuid.uuid4().hex[:8]
    _seed_db_task(_user.id, tid, status="processing", expires_in_sec=None)

    r = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r.status_code == 200
    assert r.get_json() == {"status": "processing"}


def test_unknown_task_returns_404(app, client, _user):
    _login_as(client, _user)
    r = client.get("/mynotes/api/ai/task/does-not-exist")
    assert r.status_code == 404
    assert r.get_json()["status"] == "error"


def test_notes_kind_not_visible_via_mynotes_endpoint(app, client, _user):
    """blueprint 隔離：notes kind 的 task 不能透過 /mynotes/api/ai/task 拿到。"""
    _login_as(client, _user)
    tid = "tx-" + uuid.uuid4().hex[:8]
    db.session.add(AiTask(
        id=tid, kind="notes", user_id=_user.id, status="done",
        result_json='"leak"', expires_at=datetime.utcnow() + timedelta(seconds=30),
    ))
    db.session.commit()

    r = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r.status_code == 404
