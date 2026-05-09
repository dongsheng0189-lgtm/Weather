"""DB-backed AI task state tests.

Root cause being fixed: `_ai_tasks = {}` 是 module-level dict、每個 gunicorn
worker process 獨立。5/9 把 workers 1→2 後，POST 寫到 worker A 的 dict、前端
GET polling 路由到 worker B → 404「任務不存在」。Fix：task state 改存 DB，
跨 worker / 跨 pod restart 都活。

These tests exercise the polling endpoint (`/notes/ai/task/<id>`) against
DB-seeded `AiTask` rows — independent from any in-memory dict state.
"""
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
    suffix = uuid.uuid4().hex[:6]
    u = User(username=f"admin-{suffix}", store="A", role="admin")
    u.set_password("x")
    db_session.add(u)
    db_session.commit()
    return u


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _seed_db_task(user_id, task_id, status, result=None, error=None, expires_in_sec=30, age_sec=0):
    """Insert AiTask row directly via DB — simulates cross-worker (writer worker
    finishes then dies / different worker; reader is current request)."""
    now = datetime.utcnow()
    row = AiTask(
        id=task_id,
        kind="notes",
        user_id=user_id,
        status=status,
        result_json=json.dumps(result) if result is not None else None,
        error_msg=error,
        created_at=now - timedelta(seconds=age_sec),
        updated_at=now - timedelta(seconds=age_sec),
        expires_at=now + timedelta(seconds=expires_in_sec) if expires_in_sec is not None else None,
    )
    db.session.add(row)
    db.session.commit()
    return row


def test_get_returns_done_when_task_in_db(app, client, admin_user):
    """Cross-worker happy path：worker A 寫 done 進 DB，worker B (current request)
    讀 DB → 應該回 done + 結果。"""
    _login_as(client, admin_user)
    tid = "tdone-" + uuid.uuid4().hex[:8]
    _seed_db_task(admin_user.id, tid, status="done", result="cluster summary")

    r = client.get(f"/notes/ai/task/{tid}")
    assert r.status_code == 200
    assert r.get_json() == {"status": "done", "result": "cluster summary"}


def test_get_returns_error_when_db_row_has_error(app, client, admin_user):
    _login_as(client, admin_user)
    tid = "terr-" + uuid.uuid4().hex[:8]
    _seed_db_task(admin_user.id, tid, status="error", error="boom")

    r = client.get(f"/notes/ai/task/{tid}")
    assert r.status_code == 200
    assert r.get_json() == {"status": "error", "message": "boom"}


def test_get_returns_processing_when_db_row_processing(app, client, admin_user):
    _login_as(client, admin_user)
    tid = "tproc-" + uuid.uuid4().hex[:8]
    _seed_db_task(admin_user.id, tid, status="processing", expires_in_sec=None)

    r = client.get(f"/notes/ai/task/{tid}")
    assert r.status_code == 200
    assert r.get_json() == {"status": "processing"}


def test_get_404_when_no_db_row(app, client, admin_user):
    _login_as(client, admin_user)
    r = client.get("/notes/ai/task/no-such-id")
    assert r.status_code == 404


def test_stuck_processing_swept_to_error_after_60s(app, client, admin_user):
    """worker SIGKILL (timeout=60) 留下 processing 永遠不變的殭屍 task。
    get_ai_task 進來時應該 sweep：updated_at > 60s 前的 processing → error."""
    _login_as(client, admin_user)
    tid = "tstuck-" + uuid.uuid4().hex[:8]
    _seed_db_task(admin_user.id, tid, status="processing", age_sec=70, expires_in_sec=None)

    r = client.get(f"/notes/ai/task/{tid}")
    body = r.get_json()
    assert body["status"] == "error", body
    assert "逾時" in body["message"], body

    db.session.expire_all()
    row = db.session.get(AiTask, tid)
    assert row is not None
    assert row.status == "error"


def test_recent_processing_not_swept(app, client, admin_user):
    """剛剛開的 processing 別誤殺。"""
    _login_as(client, admin_user)
    tid = "tfresh-" + uuid.uuid4().hex[:8]
    _seed_db_task(admin_user.id, tid, status="processing", age_sec=5, expires_in_sec=None)

    r = client.get(f"/notes/ai/task/{tid}")
    assert r.get_json() == {"status": "processing"}

    db.session.expire_all()
    row = db.session.get(AiTask, tid)
    assert row.status == "processing"


def test_expired_done_swept_and_returns_404(app, client, admin_user):
    """expires_at 過了就應該掃掉 + 404（取代舊 grace window 邏輯）。"""
    _login_as(client, admin_user)
    tid = "texp-" + uuid.uuid4().hex[:8]
    # expires_at 在 1 秒前
    _seed_db_task(admin_user.id, tid, status="done", result="x", expires_in_sec=-1)

    r = client.get(f"/notes/ai/task/{tid}")
    assert r.status_code == 404

    db.session.expire_all()
    row = db.session.get(AiTask, tid)
    assert row is None


def test_done_within_expires_at_repeatable(app, client, admin_user):
    """expires_at 還沒到 → 重複 GET 都拿得到 done（取代舊 30s grace window）。"""
    _login_as(client, admin_user)
    tid = "trep-" + uuid.uuid4().hex[:8]
    _seed_db_task(admin_user.id, tid, status="done", result="abc", expires_in_sec=30)

    r1 = client.get(f"/notes/ai/task/{tid}")
    r2 = client.get(f"/notes/ai/task/{tid}")
    assert r1.get_json() == r2.get_json() == {"status": "done", "result": "abc"}
