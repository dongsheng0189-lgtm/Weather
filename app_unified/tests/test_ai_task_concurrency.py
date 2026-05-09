"""Concurrency guards for AI summary tasks.

Layered defenses (each must hold even if the others fail):
1. BoundedSemaphore(2) per worker → 3rd concurrent caller gets 429 immediately
2. gevent.with_timeout(45) inside the spawned task → never exceeds gunicorn 60s
3. try/finally release sem → done/error/timeout all release the slot

These tests exercise notes/routes.py helpers directly (not the HTTP endpoint)
to keep them deterministic without hitting the real `gevent.threadpool`.
"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import gevent
import pytest

from extensions import db
from models import AiTask, Note, Store, User
from notes import routes as notes_routes


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


def _drain_sem(sem):
    """Acquire all permits without blocking so the next acquire(blocking=False)
    will fail. Returns count taken so caller can release."""
    taken = 0
    while sem.acquire(blocking=False):
        taken += 1
    return taken


def test_third_concurrent_summary_returns_429(app, client, db_session, admin_user):
    """Sem 限 2：當 sem 已被佔滿，新的 /ai/summary 應該回 429 不 spawn task。"""
    _login_as(client, admin_user)
    # Seed 一筆 note，否則 endpoint 在「近期無筆記」就 return，碰不到 sem 檢查
    n = Note(
        user_id=admin_user.id, author_name=admin_user.username,
        store="A", title="seed", content="x", note_type="note",
        category="employee", status="pending",
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db_session.add(n)
    db_session.commit()
    # 把 sem 全佔滿，模擬已經有 2 個 task 在跑
    taken = _drain_sem(notes_routes._ai_summary_sem)
    assert taken == 2, f"expected sem to start with 2 permits, got {taken}"
    try:
        before = db.session.query(AiTask).count()
        r = client.post("/notes/ai/summary", json={"store": "A", "business_day": "today"})
        after = db.session.query(AiTask).count()

        assert r.status_code == 429, r.get_data(as_text=True)
        assert r.get_json().get("status") == "busy"
        # 沒新增 task row（rejected before _create_ai_task）
        assert after == before, "rejected request must NOT create AiTask row"
    finally:
        for _ in range(taken):
            notes_routes._ai_summary_sem.release()


def test_semaphore_released_after_done(app, db_session, admin_user):
    """task body 跑完後 sem 必須 release。"""
    sem = notes_routes._ai_summary_sem
    # 確保乾淨
    assert sem.acquire(blocking=False)
    sem.release()

    task_id = notes_routes._create_ai_task("notes", admin_user.id)
    assert sem.acquire(blocking=False)  # 模擬 spawn site 已 acquire

    fake_summarize = MagicMock(return_value="ok")
    fake_summarize.__name__ = "summarize_local"
    with patch("notes.ai_local.summarize_local", fake_summarize), \
         patch("gevent.get_hub") as mock_gh:
        mock_pool = MagicMock()
        mock_pool.apply = MagicMock(side_effect=lambda fn, args=(), kwds=None: fn(*args, **(kwds or {})))
        mock_gh.return_value.threadpool = mock_pool

        notes_routes._run_local_summary(
            task_id, app, "A",
            datetime.utcnow() - timedelta(days=1),
            datetime.utcnow() + timedelta(hours=1),
            "「A店」", "今天", "今天",
        )

    db.session.expire_all()
    row = db.session.get(AiTask, task_id)
    assert row.status == "done"
    # sem 釋放：應能再 acquire
    assert sem.acquire(blocking=False), "semaphore must be released after task body completes"
    sem.release()


def test_semaphore_released_after_exception(app, db_session, admin_user):
    """exception path：sem 一樣釋放，task 寫成 error。"""
    sem = notes_routes._ai_summary_sem
    task_id = notes_routes._create_ai_task("notes", admin_user.id)
    assert sem.acquire(blocking=False)

    fake_summarize = MagicMock()
    fake_summarize.__name__ = "summarize_local"
    with patch("notes.ai_local.summarize_local", fake_summarize), \
         patch("gevent.get_hub") as mock_gh:
        def boom(fn, args=(), kwds=None):
            raise RuntimeError("compute failure")
        mock_pool = MagicMock()
        mock_pool.apply = MagicMock(side_effect=boom)
        mock_gh.return_value.threadpool = mock_pool

        notes_routes._run_local_summary(
            task_id, app, "A",
            datetime.utcnow() - timedelta(days=1),
            datetime.utcnow() + timedelta(hours=1),
            "「A店」", "今天", "今天",
        )

    db.session.expire_all()
    row = db.session.get(AiTask, task_id)
    assert row.status == "error"
    assert "compute failure" in (row.error_msg or "")
    assert sem.acquire(blocking=False), "semaphore must be released after exception"
    sem.release()


def test_hard_timeout_records_error_not_exception(app, db_session, admin_user):
    """任務跑超過 _AI_TASK_HARD_TIMEOUT_SEC 必須轉成 error 而不是讓 gunicorn SIGKILL。"""
    sem = notes_routes._ai_summary_sem
    task_id = notes_routes._create_ai_task("notes", admin_user.id)
    assert sem.acquire(blocking=False)

    fake_summarize = MagicMock(return_value="ignored")
    fake_summarize.__name__ = "summarize_local"

    # 直接 mock gevent.with_timeout 拋 Timeout，避免測試真的等 45 秒
    with patch("notes.ai_local.summarize_local", fake_summarize), \
         patch("gevent.get_hub") as mock_gh, \
         patch.object(notes_routes.gevent, "with_timeout") as mock_wt:
        mock_pool = MagicMock()
        mock_pool.apply = MagicMock(return_value="ignored")
        mock_gh.return_value.threadpool = mock_pool
        mock_wt.side_effect = gevent.Timeout(notes_routes._AI_TASK_HARD_TIMEOUT_SEC)

        notes_routes._run_local_summary(
            task_id, app, "A",
            datetime.utcnow() - timedelta(days=1),
            datetime.utcnow() + timedelta(hours=1),
            "「A店」", "今天", "今天",
        )

    db.session.expire_all()
    row = db.session.get(AiTask, task_id)
    assert row.status == "error", row.status
    assert "逾時" in (row.error_msg or ""), row.error_msg
    assert sem.acquire(blocking=False), "semaphore must be released after timeout"
    sem.release()
