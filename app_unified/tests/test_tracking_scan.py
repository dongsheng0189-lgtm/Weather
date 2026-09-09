"""建檔追蹤的掃描引擎：完整掃描、增量、剔除不復活、跨店隔離。"""
import uuid
from datetime import datetime, timedelta
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_note(db, store='B店', title='日報', content='', note_type='note',
             is_ai_summary=False, deleted=False):
    from models import Note
    n = Note(store=store, title=title, content=content, note_type=note_type,
             is_ai_summary=is_ai_summary,
             deleted_at=datetime.utcnow() if deleted else None)
    db.session.add(n)
    db.session.commit()
    return n


def _mk_item(db, note, text, deleted=False):
    from models import ChecklistItem
    i = ChecklistItem(note_id=note.id, text=text,
                      deleted_at=datetime.utcnow() if deleted else None)
    db.session.add(i)
    db.session.commit()
    return i


def _mk_tracked(db, keyword, store='B店'):
    from models import TrackedKeyword
    t = TrackedKeyword(store=store, keyword=keyword, created_by_name='tester')
    db.session.add(t)
    db.session.commit()
    return t


def _hits(db, tracked, include_excluded=False):
    from models import TrackedKeywordHit
    q = TrackedKeywordHit.query.filter_by(keyword_id=tracked.id)
    if not include_excluded:
        q = q.filter(TrackedKeywordHit.excluded_at.is_(None))
    return q.all()


def test_full_scan_finds_note_and_item(db):
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title=f'{kw} 的交接', content='')
    other = _mk_note(db, title='與此無關')
    _mk_item(db, other, f'{kw} 遲到 10 分鐘')
    _mk_item(db, other, '大家都準時')
    t = _mk_tracked(db, kw)

    added = scan_tracked_keyword(t)

    assert added == 2
    got = {(h.target_type, h.target_id) for h in _hits(db, t)}
    assert ('note', n.id) in got
    assert len(got) == 2


def test_full_scan_matches_note_content_not_only_title(db):
    """有些事寫在本文、不在項目裡。"""
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title='一般日報', content=f'另外 {kw} 今天請假')
    t = _mk_tracked(db, kw)

    scan_tracked_keyword(t)

    assert [(h.target_type, h.target_id) for h in _hits(db, t)] == [('note', n.id)]


def test_full_scan_sets_watermark(db):
    from notes.tracking_scan import scan_tracked_keyword
    t = _mk_tracked(db, f'王{uuid.uuid4().hex[:6]}')
    assert t.scanned_through is None

    scan_tracked_keyword(t)

    assert isinstance(t.scanned_through, datetime)


def test_scan_skips_soft_deleted(db):
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    dn = _mk_note(db, title=f'{kw} 已刪筆記', deleted=True)
    live = _mk_note(db, title='存活的筆記')
    _mk_item(db, live, f'{kw} 已刪項目', deleted=True)
    t = _mk_tracked(db, kw)

    scan_tracked_keyword(t)

    assert _hits(db, t) == []
    assert dn.id  # 資料還在，只是不該被掃到


def test_scan_skips_ai_summary_notes(db):
    """AI 摘要是其他 note 的彙整，會與原 note 重複命中（與搜尋的處理一致）。"""
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    _mk_note(db, title=f'{kw} 的摘要', is_ai_summary=True)
    t = _mk_tracked(db, kw)

    scan_tracked_keyword(t)

    assert _hits(db, t) == []


def test_scan_is_store_scoped(db):
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    _mk_note(db, store='A店', title=f'{kw} 在 A 店')
    t = _mk_tracked(db, kw, store='B店')

    scan_tracked_keyword(t)

    assert _hits(db, t) == []


def test_incremental_scan_picks_up_new_item_without_duplicating(db):
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title=f'{kw} 的交接')
    t = _mk_tracked(db, kw)
    assert scan_tracked_keyword(t) == 1

    _mk_item(db, n, f'{kw} 又遲到')
    added = scan_tracked_keyword(t)

    assert added == 1                     # 只新增了那一筆
    assert len(_hits(db, t)) == 2         # 舊的沒有被重複插入


def test_incremental_scan_catches_edited_item(db):
    """既有項目被編輯後才開始含關鍵字 —— 水位用 updated_at（而非最大 id）的理由。"""
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title='一般日報')
    item = _mk_item(db, n, '某人遲到')
    t = _mk_tracked(db, kw)
    assert scan_tracked_keyword(t) == 0

    item.text = f'{kw} 遲到'
    item.updated_at = datetime.utcnow() + timedelta(seconds=1)
    db.session.commit()

    assert scan_tracked_keyword(t) == 1
    assert [(h.target_type, h.target_id) for h in _hits(db, t)] == [('item', item.id)]


def test_excluded_hit_is_not_resurrected_by_rescan(db):
    """剔除用軟刪除的唯一理由：真刪的話下一輪會再抓回來。"""
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title=f'{kw} 的交接')
    t = _mk_tracked(db, kw)
    scan_tracked_keyword(t)

    h = _hits(db, t)[0]
    h.excluded_at = datetime.utcnow()
    h.excluded_by_name = 'tester'
    db.session.commit()

    # 讓原始資料重新落入水位範圍，模擬它又被編輯過
    n.updated_at = datetime.utcnow() + timedelta(seconds=1)
    db.session.commit()

    assert scan_tracked_keyword(t) == 0
    assert _hits(db, t) == []                        # 頁面上看不到
    assert len(_hits(db, t, include_excluded=True)) == 1   # 剔除紀錄仍在


def test_scan_yields_to_hub_between_batches(db, monkeypatch):
    """掃描是同步解密迴圈，跑在 gevent worker 上。不讓出 hub 的話，大店的完整
    掃描會把整個 worker 卡住（hub_watchdog 會告警、gunicorn timeout=60 會 SIGKILL）。"""
    import notes.tracking_scan as scan_mod
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title='一般日報')
    for _ in range(5):
        _mk_item(db, n, f'{kw} 出現')
    t = _mk_tracked(db, kw)

    calls = []
    monkeypatch.setattr(scan_mod, 'SCAN_BATCH', 2)
    monkeypatch.setattr(scan_mod.gevent, 'sleep', lambda *a: calls.append(a))

    scan_tracked = scan_mod.scan_tracked_keyword
    scan_tracked(t)

    assert calls, 'scan 沒有在批次之間讓出 hub'
