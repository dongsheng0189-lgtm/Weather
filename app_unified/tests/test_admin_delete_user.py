"""Tests for admin/routes.py::delete_user — FK cleanup completeness.

Bug (2026-07): 刪除帳號沒反應、帳號還在。root cause 是人員查詢（vetting）三張表
用普通 FK（無 ON DELETE）指向 users.id，而 delete_user 的清理清單漏了它們。被刪
帳號只要查過人員（vetting_query_logs / v_aux_logs 有 row），prod PostgreSQL 就丟
FK 違規 → 整個 transaction rollback → 帳號刪不掉。裝置能刪是因為 TrustedDevice
有清；前端遇 500 時 res.json() 解析 HTML 直接 throw，所以看起來「按了沒反應」。

本機測試用 SQLite，預設「不」強制 FK，正常跑抓不到這個 bug。故 behavioral test
明確 `PRAGMA foreign_keys=ON` 模擬 prod。structural guard test 則防「以後有人加新
表、又忘了更新 delete_user」——見 test_no_unhandled_user_fk_left_behind。
"""
import uuid

import pytest
from sqlalchemy import text

from extensions import db
from models import (
    User, Store,
    VettingQueryLog, IcinfoQueryLog, VettingWhitelistPhone,
)


@pytest.fixture
def admin_user(app):
    with app.app_context():
        if not Store.query.filter_by(name="A").first():
            db.session.add(Store(name="A"))
            db.session.commit()
        u = User(username=f"admin-{uuid.uuid4().hex[:8]}", store="A", role="super_admin")
        u.set_password("x")
        u.set_features(["notes", "vetting"])
        db.session.add(u)
        db.session.commit()
        return {"id": u.id, "username": u.username}


def _login_as(client, user):
    import time
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user["id"])
        sess["_fresh"] = True
        sess["_last_request_at"] = int(time.time())


@pytest.fixture
def _sqlite_fk_on(app):
    """在 SQLite 上強制 FK（模擬 prod PostgreSQL）。conftest 用 StaticPool → 單一
    連線，PRAGMA 設一次即對後續所有 statement 生效。teardown 關回避免污染其他測試。"""
    with app.app_context():
        db.session.execute(text("PRAGMA foreign_keys=ON"))
    yield
    with app.app_context():
        db.session.execute(text("PRAGMA foreign_keys=OFF"))


def test_delete_user_with_vetting_logs_succeeds(app, admin_user, _sqlite_fk_on):
    """被刪帳號查過人員（有 vetting log）時仍能成功刪除，且 log 一併清掉。"""
    client = app.test_client()
    _login_as(client, admin_user)
    with app.app_context():
        target = User(username=f"clerk-{uuid.uuid4().hex[:8]}", store="A", role="user")
        target.set_password("x")
        db.session.add(target)
        db.session.commit()
        tid = target.id
        # 該員工的查詢紀錄
        db.session.add(VettingQueryLog(user_id=tid, queried_name="王小明"))
        db.session.add(IcinfoQueryLog(user_id=tid, queried_uino="A123456789", matched=True))
        # 該員工加的白名單（全店共用，應保留、只斷開建立者）
        wl = VettingWhitelistPhone(created_by=tid)
        wl.phone = "0912345678"
        wl.created_by_name = "clerk"
        db.session.add(wl)
        db.session.commit()

    r = client.delete(f"/admin/users/{tid}")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["status"] == "ok"

    with app.app_context():
        assert User.query.get(tid) is None                                  # 帳號真的沒了
        assert VettingQueryLog.query.filter_by(user_id=tid).count() == 0     # 查詢 log 清掉
        assert IcinfoQueryLog.query.filter_by(user_id=tid).count() == 0
        # 白名單保留、created_by 斷開為 NULL（號碼快照仍在）
        surviving = VettingWhitelistPhone.query.all()
        assert len(surviving) == 1
        assert surviving[0].created_by is None


def test_delete_user_without_vetting_logs_still_works(app, admin_user, _sqlite_fk_on):
    """無 vetting 紀錄的一般帳號刪除不受影響（回歸保護）。"""
    client = app.test_client()
    _login_as(client, admin_user)
    with app.app_context():
        target = User(username=f"plain-{uuid.uuid4().hex[:8]}", store="A", role="user")
        target.set_password("x")
        db.session.add(target)
        db.session.commit()
        tid = target.id

    r = client.delete(f"/admin/users/{tid}")
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        assert User.query.get(tid) is None


# ── 結構守門測試：防「以後加新表忘了更新 delete_user」 ─────────────────────
#
# 規則：任何用普通 FK（無 ON DELETE SET NULL / CASCADE）指向 users.id 的欄位，
# 都必須由 delete_user 手動清理，否則刪帳號會在 prod 踩 FK 違規。新增這種表時，
# 把 (table, column) 加進 _DELETE_USER_HANDLED_FKS 並在 delete_user 補對應清理，
# 否則這個測試會 fail 擋下。
#
# 說明：prod 走 app.py inline ALTER、不跑 alembic，實際約束未必等於 models.py
# 宣告的 ondelete。故這裡對「宣告 SET NULL/CASCADE」的欄位仍信任其自動處理，但
# 對「無 ondelete」的普通 FK 一律要求 delete_user 明確接手——這正是本次 bug 的類型。

_DELETE_USER_HANDLED_FKS = {
    # (table_name, column_name) — 由 delete_user 明確清理/斷開的普通 FK
    ("notes", "updated_by"),
    ("vetting_query_logs", "user_id"),
    ("v_aux_logs", "user_id"),
    ("vetting_whitelist_phones", "created_by"),
}


def test_no_unhandled_user_fk_left_behind(app):
    """列舉所有指向 users.id 的 FK；普通 FK（無 ondelete）必須被 delete_user 接手。"""
    unhandled = []
    with app.app_context():
        for table in db.metadata.tables.values():
            for col in table.columns:
                for fk in col.foreign_keys:
                    if fk.column.table.name != "users":
                        continue
                    ondelete = (fk.ondelete or "").upper()
                    if ondelete in ("SET NULL", "CASCADE"):
                        continue  # DB 層自動處理
                    key = (table.name, col.name)
                    if key not in _DELETE_USER_HANDLED_FKS:
                        unhandled.append(f"{table.name}.{col.name}")
    assert not unhandled, (
        "以下欄位用普通 FK 指向 users.id 但 delete_user 沒清理，刪帳號會在 prod 踩 "
        "FK 違規：\n  " + "\n  ".join(unhandled) + "\n\n"
        "請在 admin/routes.py::delete_user 補上對應清理，並把 (table, column) 加進 "
        "本檔的 _DELETE_USER_HANDLED_FKS。"
    )
