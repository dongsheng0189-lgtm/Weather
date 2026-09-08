"""標籤關聯要記錄「誰在什麼時候掛的」（2026-09-08 快速標籤鍵）。

三欄皆 nullable —— 既有資料沒有這些值，UI 遇到 null 就不顯示 hover。
"""
import uuid
from datetime import datetime

import pytest

from models import ChecklistItem, ChecklistItemTag, Note, NoteTag, Store, Tag, User


@pytest.fixture
def fixture_data(db_session):
    suffix = uuid.uuid4().hex[:6]
    store = Store(name=f'T{suffix}'.upper(), login_enabled=True)
    user = User(username=f'u-{suffix}', role='user')
    user.set_password('0000')
    db_session.add_all([store, user])
    db_session.commit()
    user.store = store.name
    note = Note(user_id=user.id, title='t', content='', note_type='checklist',
                store=store.name)
    db_session.add(note)
    db_session.commit()
    item = ChecklistItem(note_id=note.id, text='x', order_index=0)
    tag = Tag(store=store.name, name='陳店長知道', color='#3498db', pinned=True)
    db_session.add_all([item, tag])
    db_session.commit()
    yield store, user, note, item, tag
    ChecklistItemTag.query.filter_by(item_id=item.id).delete()
    NoteTag.query.filter_by(note_id=note.id).delete()
    ChecklistItem.query.filter_by(note_id=note.id).delete()
    db_session.delete(tag)
    db_session.delete(note)
    db_session.delete(user)
    db_session.delete(store)
    db_session.commit()


def test_item_tag_records_authorship(fixture_data, db_session):
    _, user, _, item, tag = fixture_data
    now = datetime.utcnow()
    db_session.add(ChecklistItemTag(item_id=item.id, tag_id=tag.id,
                                    tagged_by=user.id, tagged_by_name=user.username,
                                    tagged_at=now))
    db_session.commit()
    row = ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).first()
    assert row.tagged_by == user.id
    assert row.tagged_by_name == user.username
    assert row.tagged_at is not None


def test_note_tag_records_authorship(fixture_data, db_session):
    _, user, note, _, tag = fixture_data
    db_session.add(NoteTag(note_id=note.id, tag_id=tag.id,
                           tagged_by=user.id, tagged_by_name=user.username,
                           tagged_at=datetime.utcnow()))
    db_session.commit()
    row = NoteTag.query.filter_by(note_id=note.id, tag_id=tag.id).first()
    assert row.tagged_by_name == user.username


def test_authorship_columns_are_nullable(fixture_data, db_session):
    """既有資料沒有這些值 —— 不帶三欄也要能寫入。"""
    _, _, _, item, tag = fixture_data
    db_session.add(ChecklistItemTag(item_id=item.id, tag_id=tag.id))
    db_session.commit()
    row = ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).first()
    assert row.tagged_by is None
    assert row.tagged_by_name is None
    assert row.tagged_at is None


# ── 差集改寫：從選擇器改動其他標籤時，既有的作者資訊不可被洗掉 ──
def test_picker_preserves_existing_authorship(fixture_data, db_session, client):
    """這是最容易回歸的一條：全刪重插會讓 tagged_by/at 靜默消失。"""
    import time
    store, user, note, item, tag = fixture_data
    other = Tag(store=store.name, name='其他標籤', color='#27ae60', pinned=False)
    db_session.add(other)
    db_session.commit()

    # 先用「快速鍵那條路」的方式蓋上作者資訊
    stamped_at = datetime.utcnow()
    db_session.add(ChecklistItemTag(item_id=item.id, tag_id=tag.id,
                                    tagged_by=user.id, tagged_by_name=user.username,
                                    tagged_at=stamped_at))
    db_session.commit()

    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())

    # 從選擇器送出「原本那個 + 一個新的」
    res = client.put(f'/notes/api/items/{item.id}/tags',
                     json={'tag_ids': [tag.id, other.id]})
    assert res.status_code == 200, res.get_data(as_text=True)

    kept = ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).first()
    assert kept is not None
    assert kept.tagged_by_name == user.username, \
        '既有標籤的作者資訊被洗掉了 —— set_item_tags 仍在全刪重插'
    added = ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=other.id).first()
    assert added.tagged_by_name == user.username, '新加的標籤也要記錄作者'

    db_session.delete(other)
    db_session.commit()


def test_picker_removes_deselected_tag(fixture_data, db_session, client):
    """回歸防護：差集改寫不能把「移除」這件事弄丟。"""
    import time
    store, user, note, item, tag = fixture_data
    db_session.add(ChecklistItemTag(item_id=item.id, tag_id=tag.id))
    db_session.commit()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())
    res = client.put(f'/notes/api/items/{item.id}/tags', json={'tag_ids': []})
    assert res.status_code == 200
    assert ChecklistItemTag.query.filter_by(item_id=item.id).count() == 0
