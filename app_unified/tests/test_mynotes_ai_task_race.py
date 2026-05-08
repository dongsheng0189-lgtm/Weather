"""mynotes get_ai_task race-window fix (parallel to notes 5/7 0e9dda7).

Frontend setInterval polls overlap; second poll shouldn't get 「任務不存在」
just because the first one already del'd the entry. Done/error results
should be repeatable within a 30s grace window, then GC'd.
"""
import time
import uuid
import pytest

from mynotes import routes as mn_routes
from models import User


@pytest.fixture(autouse=True)
def _reset_ai_tasks():
    mn_routes._ai_tasks.clear()
    yield
    mn_routes._ai_tasks.clear()


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


def _seed_task(task_id, payload):
    mn_routes._ai_tasks[task_id] = payload


def test_done_task_repeatable_within_grace_window(app, client, _user):
    """第一次 done 讀取後，30 秒內 same task_id 仍能拿到同樣結果。"""
    _login_as(client, _user)
    tid = "tg-" + uuid.uuid4().hex[:8]
    _seed_task(tid, {"status": "done", "result": "summary text"})

    r1 = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r1.status_code == 200
    assert r1.get_json() == {"status": "done", "result": "summary text"}

    r2 = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r2.status_code == 200
    assert r2.get_json() == {"status": "done", "result": "summary text"}


def test_error_task_repeatable_within_grace_window(app, client, _user):
    _login_as(client, _user)
    tid = "te-" + uuid.uuid4().hex[:8]
    _seed_task(tid, {"status": "error", "message": "boom"})

    r1 = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r1.status_code == 200
    r2 = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r2.status_code == 200
    assert r2.get_json() == {"status": "error", "message": "boom"}


def test_done_task_swept_after_grace(app, client, _user, monkeypatch):
    """超過 grace 後 task 自動清掉，再讀 → 404。"""
    _login_as(client, _user)
    tid = "ts-" + uuid.uuid4().hex[:8]
    _seed_task(tid, {"status": "done", "result": "x"})

    base = time.time()
    monkeypatch.setattr(mn_routes.time, "time", lambda: base)
    r1 = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r1.status_code == 200

    monkeypatch.setattr(mn_routes.time, "time", lambda: base + 31)
    r2 = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r2.status_code == 404
    assert tid not in mn_routes._ai_tasks


def test_processing_task_never_marked_for_deletion(app, client, _user):
    """processing 狀態不該被排程清掉，沒 delete_at。"""
    _login_as(client, _user)
    tid = "tp-" + uuid.uuid4().hex[:8]
    _seed_task(tid, {"status": "processing"})

    r = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r.status_code == 200
    assert r.get_json() == {"status": "processing"}
    assert "delete_at" not in mn_routes._ai_tasks[tid]


def test_unknown_task_still_404(app, client, _user):
    _login_as(client, _user)
    r = client.get("/mynotes/api/ai/task/does-not-exist")
    assert r.status_code == 404
    assert r.get_json()["status"] == "error"
