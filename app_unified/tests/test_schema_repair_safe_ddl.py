"""_safe_ddl 隔離保證（防 prod 開機 schema 修復連鎖失敗）。

背景：app.py 開機時整批 ALTER 跑在同一個 transaction。PostgreSQL 只要任一句
出錯就把整個 tx 標成 aborted，之後每句都回 'current transaction is aborted'；
原本的 try/except:pass 只吞 Python 例外、沒 rollback，於是後面幾十句 DDL 全被
連坐，最後 commit 一個 aborted tx = 整批 ROLLBACK（新欄位靜默不落地 → prod ISE）。

_safe_ddl 用 begin_nested()（SAVEPOINT）把每句隔離：一句失敗只回滾該句、外層
tx 照常存活，批次可繼續。本檔驗證這個隔離語義（SQLite 也支援 SAVEPOINT）。

注意：SQLite 不會像 PG 那樣「整個 tx aborted」，所以無法在此重現 PG 的連鎖現象；
這裡驗證的是修法的機制面——單句失敗被隔離、前後成功句都存活且能落地。
"""
from app import _safe_ddl
from extensions import db


def test_safe_ddl_returns_bool_and_isolates_failure(app):
    """一句壞語句(False)不會拖垮它前後的好語句(True)，最後都能 commit 落地。"""
    with app.app_context():
        try:
            assert _safe_ddl("CREATE TABLE _ddltest (id INTEGER)") is True
            assert _safe_ddl("INSERT INTO _ddltest VALUES (1)") is True
            # 故意壞掉：語法錯誤 → 應該回 False，且只回滾這一句的 savepoint
            assert _safe_ddl("THIS IS NOT VALID SQL") is False
            # 壞語句之後，session 仍可用、後續好語句照常成功
            assert _safe_ddl("INSERT INTO _ddltest VALUES (2)") is True
            db.session.commit()

            rows = db.session.execute(db.text("SELECT id FROM _ddltest ORDER BY id")).scalars().all()
            # 兩筆成功的都在（前後都沒被壞語句連坐），壞語句沒插入任何東西
            assert rows == [1, 2]
        finally:
            db.session.execute(db.text("DROP TABLE IF EXISTS _ddltest"))
            db.session.commit()


def test_safe_ddl_failure_does_not_abort_session(app):
    """壞語句後 session 沒進入不可用狀態：緊接著的一般 ORM query 仍能跑。"""
    from models import User
    with app.app_context():
        assert _safe_ddl("ALTER TABLE nonexistent_table_xyz ADD COLUMN foo TEXT") is False
        # 若 savepoint 沒正確回滾，這裡在 PG 會炸 'transaction is aborted'
        User.query.limit(1).all()  # 不應丟例外


def test_schema_repair_block_has_no_bare_execute_wrapper():
    """回歸防護：schema 修復區塊必須走 _safe_ddl，不可退回 try/except:pass 包
    db.session.execute 的舊寫法（那會重新引入 PG 連鎖失敗地雷）。"""
    from pathlib import Path
    src = Path(__file__).parent.parent / "app.py"
    text = src.read_text(encoding="utf-8")
    assert "def _safe_ddl(" in text
    assert "begin_nested()" in text
    # 全檔僅剩 _safe_ddl 內部那一句 db.session.execute
    assert text.count("db.session.execute(") == 1, (
        "schema 修復區塊疑似有裸 db.session.execute（未走 _safe_ddl）"
    )
    # 修復區塊語句數量沒被意外砍掉（46 個 call site，drift 時提醒對齊 models.py）
    assert text.count("_safe_ddl(") >= 46
