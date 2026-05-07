"""Race condition fix for AI task polling: result must be repeatable within
a grace window so that overlapping setInterval polls don't see 「任務不存在」
right after a successful done read."""
import time
import uuid
import pytest

from notes import routes as notes_routes
from models import Store, User
from extensions import db


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


def _seed_task(task_id, payload):
    notes_routes._ai_tasks[task_id] = payload


def test_done_task_repeatable_within_grace_window(app, client, admin_user):
    _login_as(client, admin_user)
    tid = "t-grace-" + uuid.uuid4().hex[:8]
    _seed_task(tid, {"status": "done", "result": "summary text"})

    r1 = client.get(f"/notes/ai/task/{tid}")
    assert r1.status_code == 200
    assert r1.get_json() == {"status": "done", "result": "summary text"}

    r2 = client.get(f"/notes/ai/task/{tid}")
    assert r2.status_code == 200
    assert r2.get_json() == {"status": "done", "result": "summary text"}


def test_error_task_repeatable_within_grace_window(app, client, admin_user):
    _login_as(client, admin_user)
    tid = "t-err-" + uuid.uuid4().hex[:8]
    _seed_task(tid, {"status": "error", "message": "boom"})

    r1 = client.get(f"/notes/ai/task/{tid}")
    assert r1.status_code == 200
    assert r1.get_json() == {"status": "error", "message": "boom"}

    r2 = client.get(f"/notes/ai/task/{tid}")
    assert r2.status_code == 200
    assert r2.get_json() == {"status": "error", "message": "boom"}


def test_done_task_swept_after_grace(app, client, admin_user, monkeypatch):
    _login_as(client, admin_user)
    tid = "t-sweep-" + uuid.uuid4().hex[:8]
    _seed_task(tid, {"status": "done", "result": "x"})

    base = time.time()
    monkeypatch.setattr(notes_routes.time, "time", lambda: base)
    r1 = client.get(f"/notes/ai/task/{tid}")
    assert r1.status_code == 200

    monkeypatch.setattr(notes_routes.time, "time", lambda: base + 31)
    r2 = client.get(f"/notes/ai/task/{tid}")
    assert r2.status_code == 404
    assert tid not in notes_routes._ai_tasks


def test_processing_task_never_marked_for_deletion(app, client, admin_user):
    _login_as(client, admin_user)
    tid = "t-proc-" + uuid.uuid4().hex[:8]
    _seed_task(tid, {"status": "processing"})

    r = client.get(f"/notes/ai/task/{tid}")
    assert r.status_code == 200
    assert r.get_json() == {"status": "processing"}
    assert "delete_at" not in notes_routes._ai_tasks[tid]


def test_unknown_task_still_404(app, client, admin_user):
    _login_as(client, admin_user)
    r = client.get("/notes/ai/task/does-not-exist")
    assert r.status_code == 404
    assert r.get_json()["status"] == "error"
