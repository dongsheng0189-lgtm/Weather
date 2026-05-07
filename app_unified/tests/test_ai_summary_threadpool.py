"""Worker-recycle root-cause fix verification.

Background: 在 gevent worker 直接跑 sentence-transformers encode + DBSCAN 會 block
整個 reactor，導致 /healthz readiness probe 超時，Zeabur 殺 pod 重啟，
module-level _ai_tasks dict 被清空，前端輪詢拿到「任務不存在」。

修法：CPU-bound 部份 (summarize_local) 改走 gevent.get_hub().threadpool.apply()
在 real OS thread 執行，當前 greenlet 雖等結果但會 yield hub 給 /healthz
與其他 request。
"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from notes import routes as notes_routes
from models import Note, Store, User


@pytest.fixture
def _store_a(app, db_session):
    from extensions import db
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


def _call_run_local_summary(task_id, app, summarize_return="FAKE_SUMMARY", apply_side_effect=None):
    """Helper：mock 掉 summarize_local 跟 gevent.get_hub.threadpool.apply 後呼叫 _run_local_summary。

    apply_side_effect: callable(fn, args=, kwds=) → 自訂 pool.apply 行為。
                       預設行為 = 直接呼叫 fn(*args)，但 fn 會被 mock 成 summarize_return。
    回傳：(captured_pool_apply_call_args, _ai_tasks 內該 task_id 的內容)
    """
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

    return captured["calls"], notes_routes._ai_tasks.get(task_id)


def test_run_local_summary_dispatches_summarize_via_threadpool(app, db_session, _seeded_notes):
    """根因守則：summarize_local 必須走 threadpool.apply，否則會 block gevent reactor。"""
    task_id = "tp-" + uuid.uuid4().hex[:8]
    notes_routes._ai_tasks.clear()

    calls, task = _call_run_local_summary(task_id, app, summarize_return="ok")

    assert len(calls) == 1, "threadpool.apply 必須恰好被呼叫一次"
    fn, args, kwds = calls[0]
    assert fn.__name__ == "summarize_local", \
        "派到 threadpool 的應該是 summarize_local；目前是 %r" % (fn,)

    assert task == {"status": "done", "result": "ok"}


def test_run_local_summary_stores_error_status_on_exception(app, db_session, _seeded_notes):
    """exception path：error 寫進 _ai_tasks，不能讓 task 卡在 processing。"""
    task_id = "err-" + uuid.uuid4().hex[:8]
    notes_routes._ai_tasks.clear()

    def boom(fn, args=(), kwds=None):
        raise RuntimeError("simulated CPU work failure")

    _, task = _call_run_local_summary(task_id, app, apply_side_effect=boom)

    assert task is not None
    assert task["status"] == "error"
    assert "simulated CPU work failure" in task["message"]


def test_run_local_summary_eager_loads_relationships(app, db_session, _seeded_notes):
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
    notes_routes._ai_tasks.clear()

    _, task = _call_run_local_summary(
        task_id, app, summarize_return="ok", apply_side_effect=check_relationships
    )

    assert task["status"] == "done"
    assert inspected["unloaded_per_note"], "至少要查到一筆 note"
    for unloaded in inspected["unloaded_per_note"]:
        assert "author" not in unloaded, \
            "Note.author 必須事先 joinedload，否則 thread 內 lazy load 會炸"
        assert "checklist_items" not in unloaded, \
            "Note.checklist_items 必須事先 joinedload，否則 thread 內 lazy load 會炸"
