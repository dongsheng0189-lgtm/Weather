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
             is_ai_summary=False, deleted=False, updated_at=None):
    """updated_at 給定時，created_at 與 updated_at 一起回填成該時刻——用來讓
    測試資料「看起來是很久以前建立、也很久沒動過」，藉此避開
    WATERMARK_LOOKBACK_SECONDS 造成的假陽性（見 tracking_scan.py 模組註解與
    test_incremental_scan_catches_edited_item 的說明）。"""
    from models import Note
    n = Note(store=store, title=title, content=content, note_type=note_type,
             is_ai_summary=is_ai_summary,
             deleted_at=datetime.utcnow() if deleted else None)
    if updated_at is not None:
        n.created_at = updated_at
        n.updated_at = updated_at
    db.session.add(n)
    db.session.commit()
    return n


def _mk_item(db, note, text, deleted=False, updated_at=None):
    from models import ChecklistItem
    i = ChecklistItem(note_id=note.id, text=text,
                      deleted_at=datetime.utcnow() if deleted else None)
    if updated_at is not None:
        i.created_at = updated_at
        i.updated_at = updated_at
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
    SLEEP = 0.6

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
    # 容差取 SLEEP 的固定比例（而非寫死的絕對秒數）：原本硬編 0.1s 對 0.3s 的
    # sleep 只有 3 倍緩衝，吃重的 CI runner 排程抖動就可能超過，測試就會 flaky。
    # 用比例維持同樣的判別力——start_based 與 end_based 永遠相差整個 SLEEP，
    # 只要 margin 明顯小於 SLEEP，兩種實作（記開始 vs 記結束）就分得開。
    margin = timedelta(seconds=SLEEP / 3)

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
    """軟刪除要連項目一起測——只靠已刪除筆記本身、或掛在存活筆記下的已刪項目，
    都測不到 `item_q` 的 `Note.deleted_at.is_(None)` 那個 join 條件：後者砍掉
    的話，掛在「筆記本身已刪、項目本身沒刪」下的項目就會被漏抓進來，除非有
    一筆這樣的資料存在。"""
    from notes.tracking_scan import scan_tracked_keyword
    from models import Note
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    dn = _mk_note(db, title=f'{kw} 已刪筆記', deleted=True)
    _mk_item(db, dn, f'{kw} 掛在已刪筆記下的項目')  # 項目本身沒刪，靠 join 的 Note.deleted_at 擋
    parent_of_deleted_item = _mk_note(db, title='存活的筆記')  # 只是用來掛已刪項目，本身不該被斷言
    _mk_item(db, parent_of_deleted_item, f'{kw} 已刪項目', deleted=True)
    t = _mk_tracked(db, kw)

    scan_tracked_keyword(t)

    assert _hits(db, t) == []
    # `assert dn.id` 對一筆已 commit 的 row 永遠成立，驗證不到「資料還在」；
    # 改成真的重新查一次，證明軟刪除沒有真的把資料弄丟。
    assert Note.query.get(dn.id) is not None, '資料還在，只是不該被掃到'


def test_scan_skips_ai_summary_notes(db):
    """AI 摘要是其他 note 的彙整，會與原 note 重複命中（與搜尋的處理一致）。

    連項目一起測：只在摘要筆記本身放關鍵字，測不到 `item_q` 的
    `Note.is_ai_summary.isnot(True)` 那個 join 條件——摘要筆記底下若掛著項目
    （砍掉這個條件也不會讓任何既有測試失敗），項目一樣不該被抓進來。"""
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    sn = _mk_note(db, title=f'{kw} 的摘要', is_ai_summary=True)
    _mk_item(db, sn, f'{kw} 項目也在摘要筆記下')
    t = _mk_tracked(db, kw)

    scan_tracked_keyword(t)

    assert _hits(db, t) == []


def test_scan_is_store_scoped(db):
    """跨店隔離要連項目一起測——只測 note 的話，`_target_ids` 裡 item 查詢那段
    `Note.store == tracked.store` 被砍掉也不會有任何測試失敗。"""
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    a_note = _mk_note(db, store='A店', title=f'{kw} 在 A 店')
    _mk_item(db, a_note, f'{kw} 項目也在 A 店')
    t = _mk_tracked(db, kw, store='B店')

    scan_tracked_keyword(t)

    assert _hits(db, t) == []


def test_incremental_scan_picks_up_new_item_without_duplicating(db, monkeypatch):
    """`without_duplicating` 那一半原本就有斷言（`added`／`_hits` 數量），但
    `incremental` 那一半沒有：一個完全忽略 `scanned_through`、每次都全掃整間
    店的實作，一樣會讓 `added == 1`（新命中）、`len(_hits) == 2`（`seen`
    集合擋掉重複計數）——名字承諾的「增量」根本沒被驗到。

    做法：monkeypatch `_target_ids` 側錄它每次回傳的 id 數量。多建一些不含
    關鍵字、但存在於同一間店的「雜訊」筆記，讓「全掃 vs 增量」的規模差異可被
    觀察到；第二輪掃描若真的有用上水位，應該只被交付「水位之後」異動的那 1
    筆項目，而不是像第一輪一樣把整間店的 note 都重新撈一次。

    用專屬店名隔離：`_target_ids` 撈的是整間店的 note/item id，若沿用本檔案
    到處共用的預設店名 'B店'，其他測試留在同一個 session-scoped DB 裡的資料
    會混進雜訊筆記的計數，讓 `first_total`/`second_total` 不再是可預期的
    常數（見 test_scan_yields_to_hub_between_batches 的說明，這裡是同一個
    地雷）。"""
    import notes.tracking_scan as scan_mod
    from notes.tracking_scan import scan_tracked_keyword, WATERMARK_LOOKBACK_SECONDS
    store = f'測試店_{uuid.uuid4().hex[:6]}'
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    # 所有初始資料的 updated_at 都回填到一小時前，遠遠超出
    # WATERMARK_LOOKBACK_SECONDS——不這樣做的話，剛建立、還沒編輯過的資料本來
    # 就落在第一次掃描水位的 lookback 窗口內，第二輪（增量）掃描時這些「雜訊」
    # 筆記還是會被 `_target_ids` 重新撈出來，讓「增量遠小於全掃」這個斷言失真
    # （這正是本檔案其他測試已踩過的坑，見模組內 test_incremental_scan_
    # catches_edited_item 的說明）。
    old = datetime.utcnow() - timedelta(hours=1)
    n = _mk_note(db, store=store, title=f'{kw} 的交接', updated_at=old)
    for _ in range(20):
        _mk_note(db, store=store, title='不相關的日報', updated_at=old)  # 雜訊：讓「全掃」的規模明顯大於「增量」
    t = _mk_tracked(db, kw, store=store)

    real_target_ids = scan_mod._target_ids
    calls = []

    def _recording_target_ids(*a, **kw_):
        note_ids, item_ids = real_target_ids(*a, **kw_)
        calls.append((len(note_ids), len(item_ids)))
        return note_ids, item_ids

    monkeypatch.setattr(scan_mod, '_target_ids', _recording_target_ids)

    assert scan_tracked_keyword(t) == 1
    first_total = sum(calls[0])

    _mk_item(db, n, f'{kw} 又遲到')
    added = scan_tracked_keyword(t)

    assert added == 1                     # 只新增了那一筆
    assert len(_hits(db, t)) == 2         # 舊的沒有被重複插入

    second_total = sum(calls[1])
    # 真正的增量：第二輪只該被交付水位之後異動的那 1 筆項目，遠小於第一輪
    # （全掃整店，含 21 筆 note）。若實作忽略 scanned_through、每次都全掃，
    # 第二輪會回傳跟第一輪同量級（甚至更多）的 id，這兩條斷言都會抓到。
    assert second_total < first_total, (
        f'第二輪（增量）交給 _target_ids 的資料量（{second_total}）應該遠小於'
        f'第一輪（全掃，{first_total}）；否則代表 scanned_through 沒被拿來過濾，'
        '每次都在全掃'
    )
    assert second_total <= 1, (
        f'增量掃描應該只被交付「水位之後」異動的那 1 筆項目，實際拿到 '
        f'{second_total} 筆'
    )


def test_incremental_scan_catches_edited_item(db):
    """既有項目被編輯後才開始含關鍵字 —— 水位用 updated_at（而非 created_at／
    最大 id）的理由。

    項目的初始 created_at/updated_at 刻意回填到一小時前，遠遠超出
    WATERMARK_LOOKBACK_SECONDS（預設 5 秒）的回退範圍；不這樣做的話，剛
    建立、還沒編輯過的資料本來就落在第一次掃描水位的 lookback 窗口內，這裡
    要測的「位移」就變成無意義的雜訊——一個誤植成
    `ChecklistItem.created_at > since` 的實作，會因為 created_at 本身就在
    窗口內而照樣通過這個測試，等於完全沒測到「用 updated_at 過濾」這件事。

    編輯時只推進 updated_at、created_at 維持在一小時前不動，且位移量刻意
    超過 lookback 一秒以上（而非只有 1 秒）：這樣「落進第二輪掃描範圍」是真
    的跨過水位，不是恰好被 lookback 蓋住的巧合。用 created_at 過濾的誤植
    版本，created_at 從頭到尾都停在一小時前，不會落進第二輪的範圍，這個
    測試就會如預期失敗。
    """
    from notes.tracking_scan import scan_tracked_keyword, WATERMARK_LOOKBACK_SECONDS
    from models import ChecklistItem
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    old = datetime.utcnow() - timedelta(hours=1)
    n = _mk_note(db, title='一般日報', updated_at=old)
    item = _mk_item(db, n, '某人遲到', updated_at=old)
    t = _mk_tracked(db, kw)
    assert scan_tracked_keyword(t) == 0

    # Fix 5（scan 會 expunge 掃過的物件）之後，item 已經是 detached 狀態，
    # 直接改它、commit 不會有任何效果——必須重新取得一個 attached 的實例。
    item = ChecklistItem.query.get(item.id)
    item.text = f'{kw} 遲到'
    item.updated_at = datetime.utcnow() + timedelta(seconds=WATERMARK_LOOKBACK_SECONDS + 1)
    db.session.commit()

    assert scan_tracked_keyword(t) == 1
    assert [(h.target_type, h.target_id) for h in _hits(db, t)] == [('item', item.id)]


def test_excluded_hit_is_not_resurrected_by_rescan(db, monkeypatch):
    """剔除用軟刪除的唯一理由：真刪的話下一輪會再抓回來。

    初始 created_at/updated_at 回填到一小時前（理由同
    test_incremental_scan_catches_edited_item），之後把 updated_at 往前推
    超過 lookback 一秒以上，確保它是真的重新跨過水位，不是本來就落在
    lookback 窗口內的巧合。

    只比對最終結果（DB 有沒有多一筆）不夠：唯一約束 uq_tracked_hit_target
    在「seen 集合根本沒擋到」的情況下，靠 IntegrityError 事後補救，讓下面
    兩種退化都能矇混過關而不被任何測試發現：
    (a) seen 查詢加了 `.filter(excluded_at.is_(None))`——復活是被 DB 擋下，
        不是被程式擋下；
    (b) seen 查詢退回 `TrackedKeywordHit.query.filter_by(...).all()`（整列
        ORM 物件）——`(target_type, target_id) in seen` 的 tuple 比對永遠不
        成立，seen 形同虛設，每次重掃都會嘗試重新插入、每次都被
        IntegrityError 悄悄吞掉。
    所以額外監看「有沒有嘗試建構要插入的 TrackedKeywordHit」，確保復活是被
    程式的 seen 集合擋下、根本沒走到插入那段，而不是被資料庫的唯一約束事後
    擋下。
    """
    from notes.tracking_scan import scan_tracked_keyword, WATERMARK_LOOKBACK_SECONDS
    from models import TrackedKeywordHit, Note
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    old = datetime.utcnow() - timedelta(hours=1)
    n = _mk_note(db, title=f'{kw} 的交接', updated_at=old)
    t = _mk_tracked(db, kw)
    scan_tracked_keyword(t)

    h = _hits(db, t)[0]
    h.excluded_at = datetime.utcnow()
    h.excluded_by_name = 'tester'
    db.session.commit()

    # 讓原始資料重新落入水位範圍，模擬它又被編輯過（位移量超過 lookback）。
    # Fix 5 之後 n 已被上一輪掃描 expunge，須重新取得 attached 實例。
    n = Note.query.get(n.id)
    n.updated_at = datetime.utcnow() + timedelta(seconds=WATERMARK_LOOKBACK_SECONDS + 1)
    db.session.commit()

    original_init = TrackedKeywordHit.__init__
    attempts = []

    def counting_init(self, *args, **kwargs):
        attempts.append((args, kwargs))
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(TrackedKeywordHit, '__init__', counting_init)

    assert scan_tracked_keyword(t) == 0
    assert attempts == [], (
        '不應該嘗試建構任何 TrackedKeywordHit —— 復活必須被 seen 集合擋下，'
        '不能只靠唯一約束的 IntegrityError 事後補救'
    )
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


def test_checklist_edit_route_bumps_updated_at(client, db_session):
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


def test_note_update_route_bumps_updated_at(client, db_session):
    """守門測試：保護建檔追蹤增量掃描依賴的另一半假設——「筆記標題/內容被改
    ⇒ Note.updated_at 會被 bump」。

    上面的 `test_checklist_edit_route_bumps_updated_at` 只守住了項目那一半，
    `Note.updated_at` 對 `_target_ids` 的 note 增量查詢同樣是必要條件、卻一直
    沒人守：`update_note`（notes/routes.py）目前是無條件
    `note.updated_at = datetime.utcnow()`，如果日後路由被改動、漏掉這行，
    建檔追蹤會開始「靜默」漏看被編輯過的筆記，且沒有其他測試會抓到。
    """
    from models import User, Note

    uname = f'tracker_{uuid.uuid4().hex[:6]}'
    u = User(username=uname, store='B店')
    u.set_password('1234')
    db_session.add(u)
    db_session.commit()

    n = Note(store='B店', title='日報', content='', note_type='note')
    db_session.add(n)
    db_session.commit()
    note_id = n.id
    original_updated_at = n.updated_at

    with client.session_transaction() as sess:
        sess['_user_id'] = str(u.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())

    time.sleep(0.01)  # 確保 updated_at 若真的被 bump，時間戳會可觀測地往後
    resp = client.put(f'/notes/api/{note_id}', json={'title': '改過的標題'})
    assert resp.status_code == 200

    updated_note = Note.query.get(note_id)
    assert updated_note.title == '改過的標題'
    assert updated_note.updated_at > original_updated_at, (
        '編輯筆記沒有 bump updated_at —— 建檔追蹤的增量掃描會從此看不到這類編輯'
    )
