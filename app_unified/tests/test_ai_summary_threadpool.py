"""notes/_run_local_summary spec.

Combined invariants:
- summarize_local 必須走 gevent.threadpool.apply（5/7 7a1465f：CPU-bound C
  extension 不釋 GIL，直接跑會 block reactor、/healthz 失敗、pod 重啟）
- thread 內訪問 SA 物件不能 lazy load（無 Flask app_context）→ 預先 joinedload
- task state 走 DB (AiTask, kind='notes')，跨 worker 都活（5/10 fix）
"""
import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from extensions import db
from models import AiTask, Note, Store, User
from notes import routes as notes_routes


@pytest.fixture
def _store_a(app, db_session):
    if not Store.query.filter_by(name="A").first():
        db.session.add(Store(name="A"))
        db.session.commit()


@pytest.fixture
def _admin(app, db_session, _store_a):
    suffix = uuid.uuid4().hex[:6]
    u = User(username=f"admin-{suffix}", store="A", role="admin")
    u.set_password("x")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def _seeded_notes(app, db_session, _admin):
    """Seed 3 plain notes in store A so the pipeline has data."""
    now = datetime.utcnow()
    notes = []
    for i in range(3):
        n = Note(
            user_id=_admin.id,
            author_name=_admin.username,
            store="A",
            title=f"T{i}",
            content=f"content {i}",
            note_type="note",
            category="employee",
            status="pending",
            updated_at=now,
            created_at=now,
        )
        db_session.add(n)
        notes.append(n)
    db_session.commit()
    return notes


def _seed_processing(user_id, task_id):
    """Pre-create the AiTask row that the spawn site would have created. Also
    acquire the sem so _run_local_summary's finally-release doesn't underflow."""
    db.session.add(AiTask(
        id=task_id, kind="notes", user_id=user_id, status="processing",
    ))
    db.session.commit()
    assert notes_routes._ai_summary_sem.acquire(blocking=False)


def _read_task(task_id):
    db.session.expire_all()
    return db.session.get(AiTask, task_id)


def _call_run_local_summary(task_id, app, summarize_return="FAKE_SUMMARY", apply_side_effect=None):
    """Helper：mock summarize_local + gevent.get_hub.threadpool.apply，
    呼叫 _run_local_summary。回傳 (captured_pool_apply_calls, AiTask row)。"""
    captured = {"calls": []}

    if apply_side_effect is None:
        def default_apply(fn, args=(), kwds=None):
            captured["calls"].append((fn, args, kwds))
            return fn(*args, **(kwds or {}))
        apply_side_effect = default_apply
    else:
        wrapped_user_effect = apply_side_effect
        def wrapping(fn, args=(), kwds=None):
            captured["calls"].append((fn, args, kwds))
            return wrapped_user_effect(fn, args=args, kwds=kwds)
        apply_side_effect = wrapping

    fake_summarize = MagicMock(return_value=summarize_return)
    fake_summarize.__name__ = "summarize_local"

    with patch("notes.ai_local.summarize_local", fake_summarize), \
         patch("gevent.get_hub") as mock_gh:
        mock_pool = MagicMock()
        mock_pool.apply = MagicMock(side_effect=apply_side_effect)
        mock_gh.return_value.threadpool = mock_pool

        notes_routes._run_local_summary(
            task_id, app, "A",
            datetime.utcnow() - timedelta(days=1),
            datetime.utcnow() + timedelta(hours=1),
            "「A店」", "今天", "今天",
        )

    return captured["calls"], _read_task(task_id)


def test_run_local_summary_dispatches_summarize_via_threadpool(app, db_session, _admin, _seeded_notes):
    """根因守則：summarize_local 必須走 threadpool.apply，否則會 block gevent reactor。"""
    task_id = "tp-" + uuid.uuid4().hex[:8]
    _seed_processing(_admin.id, task_id)

    calls, task = _call_run_local_summary(task_id, app, summarize_return="ok")

    assert len(calls) == 1, "threadpool.apply 必須恰好被呼叫一次"
    fn, args, kwds = calls[0]
    assert fn.__name__ == "summarize_local", \
        "派到 threadpool 的應該是 summarize_local；目前是 %r" % (fn,)

    assert task is not None
    assert task.status == "done"
    assert json.loads(task.result_json) == "ok"


def test_run_local_summary_stores_error_status_on_exception(app, db_session, _admin, _seeded_notes):
    """exception path：error 寫進 AiTask，不能讓 task 卡在 processing。"""
    task_id = "err-" + uuid.uuid4().hex[:8]
    _seed_processing(_admin.id, task_id)

    def boom(fn, args=(), kwds=None):
        raise RuntimeError("simulated CPU work failure")

    _, task = _call_run_local_summary(task_id, app, apply_side_effect=boom)

    assert task is not None
    assert task.status == "error"
    assert "simulated CPU work failure" in (task.error_msg or "")


def test_run_local_summary_eager_loads_relationships(app, db_session, _admin, _seeded_notes):
    """thread 內訪問 detached SA 物件 → relationships 必須事先 joinedload。"""
    from sqlalchemy import inspect as sa_inspect

    inspected = {"unloaded_per_note": []}

    def check_relationships(fn, args=(), kwds=None):
        notes_arg = args[0]
        for n in notes_arg:
            state = sa_inspect(n)
            inspected["unloaded_per_note"].append(set(state.unloaded))
        return "ok"

    task_id = "el-" + uuid.uuid4().hex[:8]
    _seed_processing(_admin.id, task_id)

    _, task = _call_run_local_summary(
        task_id, app, summarize_return="ok", apply_side_effect=check_relationships
    )

    assert task.status == "done"
    assert inspected["unloaded_per_note"], "至少要查到一筆 note"
    for unloaded in inspected["unloaded_per_note"]:
        assert "author" not in unloaded, \
            "Note.author 必須事先 joinedload，否則 thread 內 lazy load 會炸"
        assert "checklist_items" not in unloaded, \
            "Note.checklist_items 必須事先 joinedload，否則 thread 內 lazy load 會炸"
