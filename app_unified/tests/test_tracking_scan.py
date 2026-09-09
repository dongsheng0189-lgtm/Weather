"""建檔追蹤的掃描引擎：完整掃描、增量、剔除不復活、跨店隔離。"""
import time
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


def test_full_scan_sets_watermark(db, monkeypatch):
    """水位必須記錄「掃描開始」而非「結束」的時刻（再扣掉 lookback）——見
    tracking_scan.py 模組註解。如果誤植成結束時間，掃描期間才發生的異動會被
    下一輪的 `updated_at > watermark` 跳過，是這個功能唯一不能接受的失敗模式。

    做法：monkeypatch `_target_ids`（掃描中段一定會呼叫）讓它真的睡一段可觀測
    的時間，藉此讓「開始」與「結束」在時間軸上明顯分開，再比對水位落在哪一邊。
    """
    import notes.tracking_scan as scan_mod
    from notes.tracking_scan import scan_tracked_keyword, WATERMARK_LOOKBACK_SECONDS

    t = _mk_tracked(db, f'王{uuid.uuid4().hex[:6]}')
    assert t.scanned_through is None

    real_target_ids = scan_mod._target_ids
    SLEEP = 0.3

    def _slow_target_ids(*a, **kw):
        time.sleep(SLEEP)
        return real_target_ids(*a, **kw)

    monkeypatch.setattr(scan_mod, '_target_ids', _slow_target_ids)

    before = datetime.utcnow()
    scan_tracked_keyword(t)
    after = datetime.utcnow()

    assert isinstance(t.scanned_through, datetime)
    assert (after - before).total_seconds() >= SLEEP  # 確認掃描真的花了可量測的時間

    start_based = before - timedelta(seconds=WATERMARK_LOOKBACK_SECONDS)
    end_based = after - timedelta(seconds=WATERMARK_LOOKBACK_SECONDS)
    margin = timedelta(seconds=0.1)

    # 正確實作：水位貼著「開始」，落在 before-lookback 附近的小容差內。
    assert abs(t.scanned_through - start_based) < margin, (
        f'水位 {t.scanned_through} 離掃描開始時刻 {start_based} 太遠，'
        '疑似記錯成別的時間點'
    )
    # 若誤植成「結束時間 - lookback」，水位會落在 end_based 附近，
    # 與 start_based 至少差了 SLEEP 秒 —— 遠超上面的容差，這條斷言會抓到它。
    assert t.scanned_through < end_based - margin, (
        f'水位 {t.scanned_through} 太接近掃描結束時刻 {end_based}，'
        '疑似記錄了「結束」而非「開始」的時間'
    )


def test_scan_skips_soft_deleted(db):
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    dn = _mk_note(db, title=f'{kw} 已刪筆記', deleted=True)
    parent_of_deleted_item = _mk_note(db, title='存活的筆記')  # 只是用來掛已刪項目，本身不該被斷言
    _mk_item(db, parent_of_deleted_item, f'{kw} 已刪項目', deleted=True)
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
    掃描會把整個 worker 卡住（hub_watchdog 會告警、gunicorn timeout=60 會 SIGKILL）。

    這裡刻意斷言「讓出次數」而非只斷言「有讓出過」：只斷言 `assert calls` 的話，
    一個完全不分批、用 `Note.query.all()` + `ChecklistItem.query.all()` 撈全部、
    最後補一次 `gevent.sleep(0)` 收尾的實作也會通過——那正是這個功能最初要杜絕
    的寫法。用 SCAN_BATCH=2、1 則符合的 note + 5 則符合的 item：
    note 那批 ceil(1/2)=1 次、item 那批 ceil(5/2)=3 次，總共應該讓出 4 次。

    用專屬的店名隔離：`_target_ids` 撈的是「整間店」的 note/item id（不只是命中
    關鍵字的），若沿用其他測試共用的預設店名 'B店'，本檔案裡在此之前所有測試
    寫進同一個 session-scoped app/DB 的資料都會被一起掃進來，批次數就不再是
    可預期的常數。
    """
    import notes.tracking_scan as scan_mod
    store = f'測試店_{uuid.uuid4().hex[:6]}'
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, store=store, title=f'{kw} 的日報')
    for _ in range(5):
        _mk_item(db, n, f'{kw} 出現')
    t = _mk_tracked(db, kw, store=store)

    calls = []
    monkeypatch.setattr(scan_mod, 'SCAN_BATCH', 2)
    monkeypatch.setattr(scan_mod.gevent, 'sleep', lambda *a: calls.append(a))

    added = scan_mod.scan_tracked_keyword(t)

    assert added == 6  # 1 note + 5 items，確認批次真的有掃到全部資料
    assert len(calls) == 4, (
        f'應該每批次讓出一次 hub（note 1 批 + item 3 批 = 4 次），實際 {len(calls)} 次；'
        '把批次收斂成一次或整批塞進單一 .all() 都會讓這個數字掉下來'
    )


def test_persist_survives_duplicate_insert_race(db):
    """證明 `_persist` 的 SAVEPOINT 包法真的擋得住重複插入，不會拖垮整個 session。

    原本想透過公開入口 `scan_tracked_keyword` 重現「兩個 process 同時掃到同一個
    keyword」的競速，但做不到：`scan_tracked_keyword` 一開始就會把該 keyword
    「已存在的命中」全部查進 `seen` 集合，所以只要目標的 hit 已經在 DB 裡，
    這次掃描的 `seen` 一定會包含它、根本不會走到插入那段程式碼，也就永遠不會
    真的去撞 `uq_tracked_hit_target`。

    競速真正發生的時間點是：process A 已經 commit 了那筆 hit，但 process B
    的 `seen` 集合是在那之前查出來的快照，還沒看到它。這裡直接呼叫 `_persist`
    並刻意給一個「漏掉剛剛已存在那筆」的 `seen` 集合，來模擬這個時間差——這就
    是本測試在做的事。若沒有 `begin_nested()`（SAVEPOINT）包住每筆插入，這裡
    會直接讓 IntegrityError 冒出來、整個 session（連帶水位的 commit）都保不住。
    """
    from models import TrackedKeywordHit
    from notes.tracking_scan import _persist

    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title=f'{kw} 的交接')
    t = _mk_tracked(db, kw)

    # 模擬「別的 process 已經先插入並 commit 了這筆 hit」
    db.session.add(TrackedKeywordHit(keyword_id=t.id, target_type='note', target_id=n.id))
    db.session.commit()

    # 這個 process 的 seen 集合是空的 —— 還沒看到別人剛插入的那筆，正是競速的寫照
    seen = set()
    records = [('note', n.id, [n.title, n.content])]

    added = _persist(t, records, seen)  # 不應該讓例外冒出來

    assert added == 0  # 這筆是別人記的，不算這次新增
    rows = TrackedKeywordHit.query.filter_by(
        keyword_id=t.id, target_type='note', target_id=n.id).all()
    assert len(rows) == 1  # 沒有被重複插入

    # session 沒有被那次 IntegrityError 拖垮 —— 之後還能正常查詢/寫入
    db.session.query(TrackedKeywordHit).count()


def test_checklist_edit_route_bumps_updated_at(app, client, db_session):
    """守門測試：保護建檔追蹤（notes/tracking_scan.py）增量掃描依賴的假設——
    「項目文字被改 ⇒ ChecklistItem.updated_at 會被 bump」。

    `ChecklistItem.updated_at` 沒有宣告 `onupdate=`，完全靠 routes.py 手動賦值
    （`update_checklist_item` 裡的 `item.updated_at = now`）。這條依賴目前完全
    沒人守：如果日後有人改動這條路由、不小心漏掉這行賦值，建檔追蹤的增量掃描
    會開始「靜默」漏看被編輯過的項目（`updated_at` 沒變 ⇒ 永遠不會落進
    `updated_at > watermark` 的範圍），而且整個測試套件不會有任何測試失敗——
    這個測試就是唯一會抓到它的地方。
    """
    from models import User, Note, ChecklistItem

    uname = f'tracker_{uuid.uuid4().hex[:6]}'
    u = User(username=uname, store='B店')
    u.set_password('1234')
    db_session.add(u)
    db_session.commit()

    n = Note(store='B店', title='日報', content='', note_type='note')
    db_session.add(n)
    db_session.commit()
    item = ChecklistItem(note_id=n.id, text='原始文字')
    db_session.add(item)
    db_session.commit()
    item_id = item.id
    original_updated_at = item.updated_at

    with client.session_transaction() as sess:
        sess['_user_id'] = str(u.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())

    time.sleep(0.01)  # 確保 updated_at 若真的被 bump，時間戳會可觀測地往後
    resp = client.patch(f'/notes/api/items/{item_id}', json={'text': '改過的文字'})
    assert resp.status_code == 200

    updated_item = ChecklistItem.query.get(item_id)
    assert updated_item.text == '改過的文字'
    assert updated_item.updated_at > original_updated_at, (
        '編輯項目文字沒有 bump updated_at —— 建檔追蹤的增量掃描會從此看不到這類編輯'
    )
