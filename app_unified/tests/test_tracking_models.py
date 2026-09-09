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
