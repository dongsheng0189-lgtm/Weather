"""開機 schema 修復的跨 worker 序列化（防 deadlock）。

背景（2026-07-10 prod db log 實錄）：gunicorn.conf.py `workers = 2`，兩個 worker
開機各跑一次 create_app() → 各跑一次這段 schema 修復 → 兩邊同時執行
`ALTER TABLE notes ALTER COLUMN user_id DROP NOT NULL`，各自要 notes 的
AccessExclusiveLock 又互握前面拿到的鎖 → 07:05:45 deadlock detected，PG 砍掉其中
一個。犧牲者的 tx 被打成 aborted，後面 40+ 句 DDL 全連坐（_safe_ddl 的 SAVEPOINT
隔離讓它不再致命，但 deadlock 本身還是會發生、還是會噴 ERROR）。

修法：進修復區塊前先拿一把 **交易級** advisory lock，同時只有一個 worker 進得去；
另一個排隊等，等到時所有 IF NOT EXISTS 都已是 no-op，秒過。本檔驗證這個接線。
"""
import re
from pathlib import Path

import pytest

from app import _acquire_schema_repair_lock
from extensions import db


SRC = (Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")


def _repair_block() -> str:
    """切出 schema 修復區塊原始碼：從 postgresql gate 到那句收尾的 commit。"""
    start = SRC.index("if 'postgresql' in app.config['SQLALCHEMY_DATABASE_URI']")
    end = SRC.index("db.session.commit()", start)
    return SRC[start:end]


def test_repair_block_is_guarded_by_advisory_lock():
    """修復區塊必須被 advisory lock 擋著跑（否則 2 個 worker 會再 deadlock）。"""
    gate = _repair_block().splitlines()[0]
    assert "_acquire_schema_repair_lock()" in gate, (
        f"schema 修復區塊的 gate 沒接 advisory lock，2 worker 開機會 deadlock：{gate}"
    )


def _lock_sql():
    """helper 實際送出的 advisory lock SQL（只看 SQL 字串，不看註解/docstring）。"""
    return re.findall(r'db\.text\(\s*"([^"]*advisory[^"]*)"', SRC)


def test_lock_is_transaction_scoped_not_session_scoped():
    """必須用 xact 版（tx 結束自動釋放）。session 版一旦漏解鎖，connection 回到
    pool 後鎖還在，會把之後每次部署的 schema 修復永久卡死。"""
    sqls = _lock_sql()
    assert sqls, "找不到 advisory lock 的 SQL"
    for sql in sqls:
        assert "advisory_xact_lock" in sql, (
            f"用了 session 級 advisory lock：漏解鎖會卡死後續所有部署 → {sql}"
        )


def test_lock_never_blocks_indefinitely():
    """必須用 try 版（立刻回 true/false）。會阻塞的 pg_advisory_xact_lock 一旦持鎖
    worker 卡住，等鎖的 worker 會無限期擋在開機被 gunicorn boot timeout 砍掉——而它
    是否受 lock_timeout 約束是 prod-only 行為、本機驗不到，不能賭。"""
    for sql in _lock_sql():
        assert "pg_try_advisory_xact_lock" in sql, (
            f"用了會阻塞的 advisory lock（等待上限無法在本機證明）→ {sql}"
        )


def test_lock_wait_is_bounded_by_retry_loop():
    """等待上限由重試迴圈保證：次數 × 間隔 有限，超過就放棄修復而不是卡在開機。"""
    from app import _SCHEMA_REPAIR_LOCK_TRIES, _SCHEMA_REPAIR_LOCK_SLEEP_S

    assert _SCHEMA_REPAIR_LOCK_TRIES >= 1
    total = _SCHEMA_REPAIR_LOCK_TRIES * _SCHEMA_REPAIR_LOCK_SLEEP_S
    assert 0 < total <= 60, f"等鎖總時長 {total}s 不合理（會拖垮 worker 開機）"


def test_gives_up_after_bounded_retries_when_lock_held(app, monkeypatch):
    """持鎖 worker 一直不放（try lock 永遠回 false）→ 有限次重試後放棄、回 False，
    不會無限迴圈卡死開機；且不能真的睡滿 30 秒。"""
    import app as app_mod

    calls = {"tries": 0, "slept": 0}

    class _FakeResult:
        def scalar(self):
            calls["tries"] += 1
            return False  # 鎖一直被別人拿著

    monkeypatch.setattr(app_mod.db.session, "execute", lambda *a, **k: _FakeResult())
    monkeypatch.setattr(app_mod.time, "sleep", lambda s: calls.__setitem__("slept", calls["slept"] + s))

    with app.app_context():
        assert _acquire_schema_repair_lock() is False

    assert calls["tries"] == app_mod._SCHEMA_REPAIR_LOCK_TRIES
    assert calls["slept"] == (
        app_mod._SCHEMA_REPAIR_LOCK_TRIES * app_mod._SCHEMA_REPAIR_LOCK_SLEEP_S
    )


def test_acquires_when_lock_is_free(app, monkeypatch):
    """拿到鎖 → 回 True（呼叫端才會去跑那批修復 DDL），且不必等待。"""
    import app as app_mod

    class _FakeResult:
        def scalar(self):
            return True

    monkeypatch.setattr(app_mod.db.session, "execute", lambda *a, **k: _FakeResult())
    monkeypatch.setattr(app_mod.time, "sleep", lambda s: pytest.fail("拿到鎖不該還在等"))

    with app.app_context():
        assert _acquire_schema_repair_lock() is True


def test_repair_statements_still_go_through_safe_ddl():
    """回歸防護：修復區塊裡不可有裸 db.session.execute（那會繞過 SAVEPOINT 隔離、
    重新引入 PG 連鎖失敗）。advisory lock 的 execute 在 helper 裡，不在區塊內。"""
    assert "db.session.execute(" not in _repair_block()


def test_acquire_returns_false_and_keeps_session_usable_on_failure(app):
    """拿鎖失敗（非 PG / PG 掛了 / lock_timeout）不可炸開機：回 False 讓呼叫端跳過
    修復，且 session 必須留在乾淨可用狀態（後續 create_app 還要跑 ORM query）。

    SQLite 不認得 lock_timeout/advisory lock → 正好模擬失敗路徑。
    """
    from models import User

    with app.app_context():
        assert _acquire_schema_repair_lock() is False
        # 若失敗路徑沒 rollback，這裡在 PG 會炸 'current transaction is aborted'
        User.query.limit(1).all()  # 不應丟例外
