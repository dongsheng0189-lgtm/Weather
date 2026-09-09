"""詳細頁的資料組裝：即時取原文、過濾軟刪與已剔除、排序。"""
import json as _json
import uuid
from datetime import datetime, timedelta
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_note(db, store='B店', title='日報', content=''):
    from models import Note
    n = Note(store=store, title=title, content=content, note_type='note')
    db.session.add(n)
    db.session.commit()
    return n


def _mk_item(db, note, text):
    from models import ChecklistItem
    i = ChecklistItem(note_id=note.id, text=text)
    db.session.add(i)
    db.session.commit()
    return i


def _mk_tracked_and_scan(db, keyword, store='B店'):
    from models import TrackedKeyword
    from notes.tracking_scan import scan_tracked_keyword
    t = TrackedKeyword(store=store, keyword=keyword, created_by_name='tester')
    db.session.add(t)
    db.session.commit()
    scan_tracked_keyword(t)
    return t


def test_rows_read_live_text_not_a_copy(db):
    """原始項目被修正後，追蹤表必須顯示最新文字（參照，不是複本）。"""
    from notes.tracking import _detail_rows
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title='日報')
    item = _mk_item(db, n, f'{kw} 遲到 10 分鐘')
    t = _mk_tracked_and_scan(db, kw)

    item.text = f'{kw} 遲到 5 分鐘'
    db.session.commit()

    rows = _detail_rows(t)
    assert len(rows) == 1
    assert '5 分鐘' in rows[0]['text']


def test_rows_hide_soft_deleted_targets(db):
    """一筆資料可能是先被比對到、之後才被刪 —— 顯示時要再過濾一次。"""
    from notes.tracking import _detail_rows
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title='日報')
    item = _mk_item(db, n, f'{kw} 遲到')
    t = _mk_tracked_and_scan(db, kw)
    assert len(_detail_rows(t)) == 1

    item.deleted_at = datetime.utcnow()
    db.session.commit()

    assert _detail_rows(t) == []


def test_rows_hide_soft_deleted_note_target(db):
    """note 型命中：原始 Note 本身被軟刪後，這一列要消失。

    護的是 _detail_rows 裡 Note 查詢的 `Note.deleted_at.is_(None)` 這道過濾——
    與 test_rows_hide_soft_deleted_targets（護的是 ChecklistItem 查詢那道）
    是兩個不同的守門，刪掉任何一個都不該只讓另一個頂上。"""
    from notes.tracking import _detail_rows
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title=f'{kw} 的日報')
    t = _mk_tracked_and_scan(db, kw)
    assert len(_detail_rows(t)) == 1

    n.deleted_at = datetime.utcnow()
    db.session.commit()

    assert _detail_rows(t) == []


def test_rows_hide_item_whose_parent_note_soft_deleted(db):
    """item 型命中：項目本身仍是活的，但它的母筆記被軟刪 —— 這一列也要消失。

    護的是 item 分支裡「母筆記軟刪」的檢查，與上面兩個測試（Note 查詢的過濾、
    ChecklistItem 查詢的過濾）加起來，_detail_rows 的三道軟刪守門才算全部
    有測試守著。"""
    from notes.tracking import _detail_rows
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title='日報')
    item = _mk_item(db, n, f'{kw} 遲到')
    t = _mk_tracked_and_scan(db, kw)
    assert len(_detail_rows(t)) == 1

    n.deleted_at = datetime.utcnow()
    db.session.commit()

    assert _detail_rows(t) == []
    assert item.deleted_at is None   # 項目本身沒被動，只有母筆記被刪


def test_rows_hide_excluded(db):
    from notes.tracking import _detail_rows
    from models import TrackedKeywordHit
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    _mk_note(db, title=f'{kw} 的交接')
    t = _mk_tracked_and_scan(db, kw)

    h = TrackedKeywordHit.query.filter_by(keyword_id=t.id).first()
    h.excluded_at = datetime.utcnow()
    db.session.commit()

    assert _detail_rows(t) == []


def test_rows_carry_memo_and_link_target(db):
    from notes.tracking import _detail_rows
    from models import TrackedKeywordHit
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title='日報')
    item = _mk_item(db, n, f'{kw} 遲到')
    t = _mk_tracked_and_scan(db, kw)
    h = TrackedKeywordHit.query.filter_by(keyword_id=t.id).first()
    h.note_text = '已口頭告誡'
    db.session.commit()

    row = _detail_rows(t)[0]
    assert row['memo'] == '已口頭告誡'
    assert row['note_id'] == n.id       # [→ 原文] 連回母筆記
    assert row['target_type'] == 'item'
    assert row['hit_id'] == h.id
    assert row['title'] == '日報'
    assert item.id


def test_rows_sorted_newest_first(db):
    from notes.tracking import _detail_rows
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    old = _mk_note(db, title=f'{kw} 舊的')
    new = _mk_note(db, title=f'{kw} 新的')
    old.updated_at = datetime.utcnow() - timedelta(days=3)
    new.updated_at = datetime.utcnow()
    db.session.commit()
    t = _mk_tracked_and_scan(db, kw)

    rows = _detail_rows(t)
    assert [r['note_id'] for r in rows] == [new.id, old.id]


def _mk_user(db, role='user', store='B店'):
    from models import User
    u = User(username=f'u_{uuid.uuid4().hex[:6]}', role=role, store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def test_detail_page_shows_last_updated_from_scanned_through(client, db, monkeypatch):
    """增量掃描是無聲的：使用者看不出系統剛剛有沒有檢查過新命中，所以詳細頁
    要顯示「最後更新」，且必須來自掃描完成後的 scanned_through（能證明這次
    開頁確實跑過掃描），不是隨便一個 render 當下的 utcnow()。

    把掃描的「現在」固定在很久以前、跟真實現在錯開一個分鐘以上的整分鐘 ——
    否則若實作偷懶改成顯示 utcnow()，格式化到分鐘後很可能剛好跟真正的
    scanned_through 同一分鐘、測試會誤判通過。
    """
    import notes.tracking_scan as scan_mod
    from datetime import datetime as real_datetime, timedelta
    from tw_time import tw_str

    kw = f'王小明{uuid.uuid4().hex[:6]}'
    _mk_note(db, title=f'{kw} 的交接')
    u = _mk_user(db)
    _login(client, u)

    r = client.post('/notes/api/tracking', json={'keyword': kw})
    kid = r.get_json()['id']

    fake_started = real_datetime(2020, 1, 1, 3, 0, 0)

    class _FakeDateTime(real_datetime):
        @classmethod
        def utcnow(cls):
            return fake_started

    monkeypatch.setattr(scan_mod, 'datetime', _FakeDateTime)

    r = client.get(f'/notes/tracking/{kid}')
    html = r.get_data(as_text=True)
    assert '最後更新' in html

    expected = tw_str(
        fake_started - timedelta(seconds=scan_mod.WATERMARK_LOOKBACK_SECONDS),
        fmt='%m/%d %H:%M')
    assert expected in html
    # 反向斷言：確保不是巧合顯示了「現在」——真正的現在絕不會等於 2020 年。
    assert tw_str(real_datetime.utcnow(), fmt='%m/%d %H:%M') not in html
