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
