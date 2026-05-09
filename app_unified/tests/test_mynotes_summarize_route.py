"""mynotes/api/<id>/summarize route + threadpool dispatch + DB write.

Test pattern 跟 notes/test_ai_summary_threadpool.py 一致：直接呼叫
`_run_summary(task_id, app, note_id)` 而非透過 route + gevent.spawn，
因為 test 環境沒 monkey-patch gevent reactor，spawn 出去的 greenlet
不會自動跑完。route 層另用一個輕量測試覆蓋 POST 立即回應的部份。

Task state 走 DB (AiTask, kind='mynotes')；測試直接讀 DB row。
"""
import json
import uuid
from unittest.mock import patch, MagicMock

import pytest

from extensions import db
from models import AiTask, PrivateNote, User
from mynotes import routes as mn_routes


@pytest.fixture
def _user(app, db_session):
    suffix = uuid.uuid4().hex[:6]
    u = User(username=f"u-{suffix}")
    u.set_password("x")
    u.set_features(["mynotes"])
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def _meeting_note(app, db_session, _user):
    n = PrivateNote(
        user_id=_user.id,
        title="5/7 月會",
        content="店務:\n1. 外牆修補\n   結論：請廠商評估",
        note_type="meeting",
    )
    db_session.add(n)
    db_session.commit()
    return n


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _seed_processing_task(user_id, task_id):
    """Pre-create the AiTask row that the spawn site would have created. Also
    acquire the sem so _run_summary's finally-release doesn't underflow."""
    db.session.add(AiTask(
        id=task_id, kind="mynotes", user_id=user_id, status="processing",
    ))
    db.session.commit()
    assert mn_routes._mynotes_ai_sem.acquire(blocking=False)


def _read_task(task_id):
    db.session.expire_all()
    return db.session.get(AiTask, task_id)


def _run_with_fake_threadpool(task_id, app, note_id, summarize_return="FAKE_SUMMARY",
                               apply_side_effect=None):
    """Helper：mock 掉 summarize_meeting 與 gevent.threadpool.apply，
    直接呼叫 _run_summary。回傳 (captured_apply_calls, AiTask row)。
    """
    captured = {"calls": []}

    if apply_side_effect is None:
        def default_apply(fn, args=(), kwds=None):
            captured["calls"].append((fn, args, kwds))
            return fn(*args, **(kwds or {}))
        apply_side_effect = default_apply
    else:
        wrapped = apply_side_effect
        def tracked(fn, args=(), kwds=None):
            captured["calls"].append((fn, args, kwds))
            return wrapped(fn, args=args, kwds=kwds)
        apply_side_effect = tracked

    fake_summarize = MagicMock(return_value=summarize_return)
    fake_summarize.__name__ = "summarize_meeting"

    with patch("mynotes.ai_local.summarize_meeting", fake_summarize), \
         patch("gevent.get_hub") as mock_gh:
        mock_pool = MagicMock(apply=MagicMock(side_effect=apply_side_effect))
        mock_gh.return_value.threadpool = mock_pool

        mn_routes._run_summary(task_id, app, note_id)

    return captured["calls"], _read_task(task_id)


def test_run_summary_dispatches_via_threadpool(app, db_session, _user, _meeting_note):
    """Spec 守則：summarize_meeting 必須走 threadpool.apply（不能直接 call）。"""
    tid = "t-" + uuid.uuid4().hex[:8]
    _seed_processing_task(_user.id, tid)

    calls, task = _run_with_fake_threadpool(tid, app, _meeting_note.id)

    assert len(calls) == 1
    fn, args, kwds = calls[0]
    assert fn.__name__ == "summarize_meeting"
    assert args == ("5/7 月會", "店務:\n1. 外牆修補\n   結論：請廠商評估")
    assert task is not None
    assert task.status == "done"
    assert json.loads(task.result_json) == "FAKE_SUMMARY"


def test_run_summary_writes_ai_summary_field(app, db_session, _user, _meeting_note):
    """Pipeline 結果要寫進 PrivateNote.ai_summary 才能讓前端讀出來。"""
    tid = "t-" + uuid.uuid4().hex[:8]
    _seed_processing_task(_user.id, tid)

    _run_with_fake_threadpool(tid, app, _meeting_note.id, summarize_return="WRITTEN")

    db_session.expire_all()
    n = db_session.get(PrivateNote, _meeting_note.id)
    assert n.ai_summary == "WRITTEN"


def test_run_summary_handles_pipeline_exception(app, db_session, _user, _meeting_note):
    """Threadpool 內 raise 時 task 應寫成 error 而不是讓 greenlet 炸掉。"""
    tid = "t-" + uuid.uuid4().hex[:8]
    _seed_processing_task(_user.id, tid)

    def boom(fn, args=(), kwds=None):
        raise RuntimeError("simulated parser failure")

    _calls, task = _run_with_fake_threadpool(
        tid, app, _meeting_note.id, apply_side_effect=boom
    )
    assert task.status == "error"
    assert "simulated parser failure" in (task.error_msg or "")


def test_run_summary_missing_note_returns_error(app, db_session, _user):
    """note_id 不存在時 task 標 error，不應 raise 出來。"""
    tid = "t-" + uuid.uuid4().hex[:8]
    _seed_processing_task(_user.id, tid)

    _calls, task = _run_with_fake_threadpool(tid, app, 999_999)
    assert task.status == "error"
    assert "筆記不存在" in (task.error_msg or "")


def test_summarize_route_rejects_non_meeting(app, client, db_session, _user):
    """todo_note / todo_checklist 不能跑 AI 摘要 → 400。"""
    n = PrivateNote(
        user_id=_user.id,
        title="待辦",
        content="x",
        note_type="todo_note",
    )
    db_session.add(n)
    db_session.commit()

    _login_as(client, _user)
    r = client.post(f"/mynotes/api/{n.id}/summarize")
    assert r.status_code == 400
    assert "僅會議紀錄可摘要" in r.get_json()["message"]


def test_summarize_route_returns_task_id_for_meeting(app, client, db_session, _user, _meeting_note):
    """Route 層立即回應：accepted + task_id；底層 spawn 不在這裡驗。"""
    _login_as(client, _user)
    # 防 gevent.spawn 真的把 greenlet 跑起來 → 用 mock 攔住，只驗 spawn 被叫
    with patch("gevent.spawn") as mock_spawn:
        r = client.post(f"/mynotes/api/{_meeting_note.id}/summarize")
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "accepted"
        assert isinstance(body["task_id"], str) and len(body["task_id"]) >= 8
        # spawn 必須被叫，第一個 arg 是 _run_summary，後續傳 task_id, app, note_id
        assert mock_spawn.called
        spawn_args = mock_spawn.call_args.args
        assert spawn_args[0] is mn_routes._run_summary
        assert spawn_args[1] == body["task_id"]
        assert spawn_args[2] is not None  # 解 proxy 後的 Flask app obj
        assert spawn_args[3] == _meeting_note.id  # task_id, app, note_id
        # AiTask row 已建立、status=processing
        row = db.session.get(AiTask, body["task_id"])
        assert row is not None and row.status == "processing"
        assert row.kind == "mynotes"
    # 釋放 sem（route 已 acquire 但 spawn 被 mock，沒進 _run_summary 的 finally）
    mn_routes._mynotes_ai_sem.release()
