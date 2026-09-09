"""建檔追蹤的資料模型。"""
import uuid
import pytest
from datetime import datetime


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_tracked(db, store='B店', keyword=None):
    from models import TrackedKeyword
    t = TrackedKeyword(store=store,
                       keyword=keyword or f'王小明_{uuid.uuid4().hex[:6]}',
                       created_by_name='tester')
    db.session.add(t)
    db.session.commit()
    return t


def test_tracked_keyword_roundtrip(db):
    from models import TrackedKeyword
    kw = f'王小明_{uuid.uuid4().hex[:6]}'
    t = _mk_tracked(db, keyword=kw)
    got = db.session.get(TrackedKeyword, t.id)
    assert got.keyword == kw          # EncryptedText：Python 端拿到明文
    assert got.store == 'B店'
    assert got.scanned_through is None  # 尚未掃過


def test_keyword_column_is_encrypted_at_rest(db):
    """keyword 可能是員工姓名（個資），DB 裡不可以是明文。"""
    from models import TrackedKeyword
    kw = f'極機密姓名_{uuid.uuid4().hex[:6]}'
    t = _mk_tracked(db, keyword=kw)
    raw = db.session.execute(
        db.text('SELECT keyword FROM tracked_keywords WHERE id = :i'),
        {'i': t.id},
    ).scalar()
    assert kw not in (raw or '')


def test_store_column_has_no_length_limit(db):
    """store 要跟 Note.store（Text，無長度上限）比對，欄位型別不該帶長度上限。

    這裡刻意檢查欄位型別本身，而不是插入一個超長字串看會不會噴例外——後者
    在本機 SQLite 上永遠不會噴（SQLite 不強制 VARCHAR 長度上限），測試會是
    巧合通過、完全沒有偵測力。真正會炸的是 prod PostgreSQL：String(8) 底下，
    一個 9 個字的店名（admin/routes.py 建店時沒做長度驗證）插進去就是
    StringDataRightTruncation（500），而這張表還沒部署過，改起來沒有遷移
    成本。"""
    from models import TrackedKeyword
    col_type = TrackedKeyword.__table__.columns['store'].type
    assert getattr(col_type, 'length', None) is None, (
        f'store 欄位型別 {col_type!r} 帶有長度上限'
    )


def test_hit_unique_per_target(db):
    from models import TrackedKeywordHit
    t = _mk_tracked(db)
    db.session.add(TrackedKeywordHit(keyword_id=t.id, target_type='item', target_id=5))
    db.session.commit()
    db.session.add(TrackedKeywordHit(keyword_id=t.id, target_type='item', target_id=5))
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


def test_same_target_id_across_types_is_allowed(db):
    """note 5 與 item 5 是不同的東西，不可以互相擋。"""
    from models import TrackedKeywordHit
    t = _mk_tracked(db)
    db.session.add(TrackedKeywordHit(keyword_id=t.id, target_type='note', target_id=5))
    db.session.add(TrackedKeywordHit(keyword_id=t.id, target_type='item', target_id=5))
    db.session.commit()
    assert TrackedKeywordHit.query.filter_by(keyword_id=t.id).count() == 2


def test_deleting_tracked_keyword_cascades_hits(db):
    from models import TrackedKeyword, TrackedKeywordHit
    t = _mk_tracked(db)
    db.session.add(TrackedKeywordHit(keyword_id=t.id, target_type='item', target_id=1))
    db.session.commit()
    kid = t.id
    db.session.delete(db.session.get(TrackedKeyword, kid))
    db.session.commit()
    assert TrackedKeywordHit.query.filter_by(keyword_id=kid).count() == 0


def test_hit_note_text_is_encrypted_at_rest(db):
    """備註是使用者對人的評語，同樣屬個資。"""
    from models import TrackedKeywordHit
    t = _mk_tracked(db)
    memo = f'已口頭告誡_{uuid.uuid4().hex[:6]}'
    h = TrackedKeywordHit(keyword_id=t.id, target_type='item', target_id=42,
                          note_text=memo)
    db.session.add(h)
    db.session.commit()
    raw = db.session.execute(
        db.text('SELECT note_text FROM tracked_keyword_hits WHERE id = :i'),
        {'i': h.id},
    ).scalar()
    assert memo not in (raw or '')


def test_exclusion_fields_default_null(db):
    from models import TrackedKeywordHit
    t = _mk_tracked(db)
    h = TrackedKeywordHit(keyword_id=t.id, target_type='note', target_id=1)
    db.session.add(h)
    db.session.commit()
    assert h.excluded_at is None
    assert h.excluded_by_name is None
    assert isinstance(h.matched_at, datetime)


# ─── TrackedKeywordMemoLog：備註異動紀錄（append-only） ───

def _mk_hit(db, t=None, target_id=1):
    from models import TrackedKeywordHit
    t = t or _mk_tracked(db)
    h = TrackedKeywordHit(keyword_id=t.id, target_type='item', target_id=target_id)
    db.session.add(h)
    db.session.commit()
    return h


def test_memo_log_columns_are_encrypted_at_rest(db):
    """old_text / new_text 都是對人的書面評語，兩欄都必須是密文。"""
    from models import TrackedKeywordMemoLog
    h = _mk_hit(db)
    old = f'舊備註_{uuid.uuid4().hex[:6]}'
    new = f'新備註_{uuid.uuid4().hex[:6]}'
    log = TrackedKeywordMemoLog(hit_id=h.id, old_text=old, new_text=new,
                                changed_by_name='tester')
    db.session.add(log)
    db.session.commit()
    raw_old, raw_new = db.session.execute(
        db.text('SELECT old_text, new_text FROM tracked_keyword_memo_logs WHERE id = :i'),
        {'i': log.id},
    ).fetchone()
    assert old not in (raw_old or '')
    assert new not in (raw_new or '')
    # 明文仍可從 ORM 端讀回
    got = db.session.get(TrackedKeywordMemoLog, log.id)
    assert got.old_text == old
    assert got.new_text == new


def test_memo_log_survives_and_cascades_with_its_hit(client, db):
    """刪掉命中列（進而刪掉整張追蹤表）要連帶清掉它的備註異動紀錄。

    刻意打真正的 `DELETE /notes/api/tracking/<kid>` 路由，而不是直接
    `db.session.delete(...)`。後者只證明 ORM 的 cascade 設定正確、恰好跟
    production 今天的路由寫法一致，但若哪天 `delete_tracking` 改成 bulk
    `query.delete()`，這個測試在本機 SQLite 仍然會綠燈——本機
    `PRAGMA foreign_keys` 預設是關的，`ondelete='CASCADE'` 在這裡完全不
    生效（PostgreSQL 才會真的 cascade），命中列與備註 log 會被靜默留下孤兒
    列，測試卻測不出來。改成打路由，測的才是 production 實際會走的路徑。
    """
    import json as _json
    from models import User, TrackedKeyword, TrackedKeywordHit, TrackedKeywordMemoLog

    t = _mk_tracked(db)
    h = _mk_hit(db, t=t)
    db.session.add(TrackedKeywordMemoLog(hit_id=h.id, old_text='a', new_text='b',
                                         changed_by_name='tester'))
    db.session.commit()
    hid = h.id
    kid = t.id

    u = User(username=f'u_{uuid.uuid4().hex[:6]}', role='user', store=t.store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(u.id)
        sess['_fresh'] = True

    r = client.delete(f'/notes/api/tracking/{kid}')

    assert r.status_code == 200
    assert db.session.get(TrackedKeyword, kid) is None
    assert TrackedKeywordHit.query.filter_by(keyword_id=kid).count() == 0
    assert TrackedKeywordMemoLog.query.filter_by(hit_id=hid).count() == 0


def test_memo_log_changed_by_name_is_a_snapshot(db):
    """寫入者的帳號快照要跟 excluded_by_name 同一套模式，帳號被刪也留得住名字。"""
    from models import TrackedKeywordMemoLog
    h = _mk_hit(db)
    log = TrackedKeywordMemoLog(hit_id=h.id, old_text=None, new_text='x',
                                changed_by_name='snapshot_tester')
    db.session.add(log)
    db.session.commit()
    got = db.session.get(TrackedKeywordMemoLog, log.id)
    assert got.changed_by_name == 'snapshot_tester'
    assert got.changed_by is None
    assert isinstance(got.changed_at, datetime)


# ─── 結構守門：users 指向欄位必須是真 FK（CLAUDE.md 硬規則） ───
#
# 這個 feature 的較早階段把 changed_by 寫成裸 `db.Column(db.Integer)`（無
# ForeignKey/ondelete），review 後才補上真 FK。上面那個 snapshot 測試完全
# 測不出這個退化——它只驗證「有寫入 changed_by_name 快照」跟「changed_by 這次
# 沒被設」，兩者在裸 Integer 底下一樣會通過。這裡直接查 table metadata 斷言
# 約束本身，而不是插入資料看會不會拋例外：本機 SQLite 預設 `PRAGMA
# foreign_keys` 是關的，ondelete=CASCADE/SET NULL 不會被強制，插入測試在本機
# 永遠通過、沒有偵測力（同 test_store_column_has_no_length_limit 的理由）。

def _fk_ondelete(column, target_table):
    """column 若有一個指向 target_table 的 FK，回傳其 ondelete（大寫）；
    沒有這樣的 FK 就回 None（含「有 FK 但沒宣告 ondelete」的情形）。"""
    for fk in column.foreign_keys:
        if fk.column.table.name == target_table:
            return (fk.ondelete or '').upper()
    return None


def test_memo_log_changed_by_has_real_fk_with_set_null(db):
    from models import TrackedKeywordMemoLog
    col = TrackedKeywordMemoLog.__table__.columns['changed_by']
    assert _fk_ondelete(col, 'users') == 'SET NULL', (
        'TrackedKeywordMemoLog.changed_by 必須是指向 users.id、'
        "ondelete='SET NULL' 的真 FK，不可退化成裸 Integer"
    )


def test_memo_log_hit_id_has_real_fk_with_cascade(db):
    from models import TrackedKeywordMemoLog
    col = TrackedKeywordMemoLog.__table__.columns['hit_id']
    assert _fk_ondelete(col, 'tracked_keyword_hits') == 'CASCADE', (
        'TrackedKeywordMemoLog.hit_id 必須是指向 tracked_keyword_hits.id、'
        "ondelete='CASCADE' 的真 FK，不可退化成裸 Integer"
    )


def test_tracked_keyword_created_by_has_real_fk_with_set_null(db):
    """同一條硬規則，套用到這個 feature 另外兩個 users 指向欄位——它們同樣
    沒有任何測試守著。"""
    from models import TrackedKeyword
    col = TrackedKeyword.__table__.columns['created_by']
    assert _fk_ondelete(col, 'users') == 'SET NULL', (
        'TrackedKeyword.created_by 必須是指向 users.id、'
        "ondelete='SET NULL' 的真 FK"
    )


def test_tracked_keyword_hit_excluded_by_has_real_fk_with_set_null(db):
    from models import TrackedKeywordHit
    col = TrackedKeywordHit.__table__.columns['excluded_by']
    assert _fk_ondelete(col, 'users') == 'SET NULL', (
        'TrackedKeywordHit.excluded_by 必須是指向 users.id、'
        "ondelete='SET NULL' 的真 FK"
    )
