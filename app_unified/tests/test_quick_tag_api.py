"""快速標籤鍵的 API（2026-09-08）。

使用者交接時逐項核對，要一鍵掛上「陳店長知道」這類標籤。
最硬的一條要求是**不可改變項目排序** —— 項目列表按 updated_at DESC 排，
掛標籤若碰了 updated_at，逐項按下去會把整張清單順序打亂。
"""
import time
import uuid

import pytest

from models import ChecklistItem, ChecklistItemTag, Note, NoteTag, Store, Tag, User


@pytest.fixture
def ctx(db_session, client):
    suffix = uuid.uuid4().hex[:6]
    store = Store(name=f'Q{suffix}'.upper(), login_enabled=True)
    db_session.add(store)
    db_session.commit()
    user = User(username=f'q-{suffix}', role='user', store=store.name)
    user.set_password('0000')
    db_session.add(user)
    db_session.commit()
    note = Note(user_id=user.id, title='交接清單', content='',
                note_type='checklist', store=store.name)
    db_session.add(note)
    db_session.commit()
    item = ChecklistItem(note_id=note.id, text='冰箱溫度異常', order_index=0)
    tag = Tag(store=store.name, name='陳店長知道', color='#3498db', pinned=True)
    db_session.add_all([item, tag])
    db_session.commit()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())
    yield client, user, note, item, tag, store
    ChecklistItemTag.query.filter_by(item_id=item.id).delete()
    NoteTag.query.filter_by(note_id=note.id).delete()
    Tag.query.filter_by(store=store.name).delete()
    ChecklistItem.query.filter_by(note_id=note.id).delete()
    db_session.delete(note)
    db_session.delete(user)
    db_session.delete(store)
    db_session.commit()


def test_quick_add_item_tag(ctx):
    c, user, _, item, tag, _ = ctx
    res = c.post(f'/notes/api/items/{item.id}/tags/{tag.id}')
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body['tag_id'] == tag.id
    assert body['tagged_by_name'] == user.username
    assert body['tagged_at']
    assert ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).count() == 1


def test_quick_add_does_not_touch_updated_at(ctx, db_session):
    """硬性要求：掛標籤不可讓項目跳到清單最上面。"""
    c, _, _, item, tag, _ = ctx
    before = ChecklistItem.query.filter_by(id=item.id).first().updated_at
    res = c.post(f'/notes/api/items/{item.id}/tags/{tag.id}')
    assert res.status_code == 200
    db_session.expire_all()
    after = ChecklistItem.query.filter_by(id=item.id).first().updated_at
    assert after == before, 'updated_at 被更新了 —— 項目會在下次載入時跳到最上面'


def test_quick_remove_item_tag(ctx):
    c, _, _, item, tag, _ = ctx
    c.post(f'/notes/api/items/{item.id}/tags/{tag.id}')
    res = c.delete(f'/notes/api/items/{item.id}/tags/{tag.id}')
    assert res.status_code == 200
    assert ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).count() == 0


def test_quick_add_is_idempotent(ctx):
    """連按兩次不可爆 500，也不可產生重複列（複合主鍵會衝突）。"""
    c, _, _, item, tag, _ = ctx
    assert c.post(f'/notes/api/items/{item.id}/tags/{tag.id}').status_code == 200
    assert c.post(f'/notes/api/items/{item.id}/tags/{tag.id}').status_code == 200
    assert ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).count() == 1


def test_quick_add_rejects_when_tag_limit_reached(ctx, db_session):
    """上限已滿要回明確錯誤，不可靜默沒反應。"""
    c, _, _, item, tag, store = ctx
    fillers = []
    for i in range(5):
        t = Tag(store=store.name, name=f'填充{i}', color='#888888')
        db_session.add(t)
        fillers.append(t)
    db_session.commit()
    for t in fillers:
        db_session.add(ChecklistItemTag(item_id=item.id, tag_id=t.id))
    db_session.commit()

    res = c.post(f'/notes/api/items/{item.id}/tags/{tag.id}')
    assert res.status_code == 400
    assert '5' in res.get_json()['error']


def test_quick_add_note_tag(ctx, db_session):
    c, user, note, _, tag, _ = ctx
    before = Note.query.filter_by(id=note.id).first().updated_at
    res = c.post(f'/notes/api/{note.id}/tags/{tag.id}')
    assert res.status_code == 200
    assert res.get_json()['tagged_by_name'] == user.username
    db_session.expire_all()
    assert Note.query.filter_by(id=note.id).first().updated_at == before, \
        '筆記的 updated_at 也不可被碰 —— 筆記列表同樣按它排序'


def test_cross_store_tag_rejected(ctx, db_session):
    c, _, _, item, _, _ = ctx
    other_store = Store(name=f'X{uuid.uuid4().hex[:5]}'.upper())
    db_session.add(other_store)
    db_session.commit()
    foreign = Tag(store=other_store.name, name='別店標籤', color='#111111')
    db_session.add(foreign)
    db_session.commit()
    res = c.post(f'/notes/api/items/{item.id}/tags/{foreign.id}')
    assert res.status_code == 400
    db_session.delete(foreign)
    db_session.delete(other_store)
    db_session.commit()


def test_cross_store_tag_rejected_on_delete(ctx, db_session):
    """DELETE 要與 POST 對稱 —— 否則 200/404 的差別會洩漏別店存在哪些 tag_id。"""
    c, _, _, item, _, _ = ctx
    other_store = Store(name=f'Y{uuid.uuid4().hex[:5]}'.upper())
    db_session.add(other_store)
    db_session.commit()
    foreign = Tag(store=other_store.name, name='別店標籤', color='#111111')
    db_session.add(foreign)
    db_session.commit()
    assert c.delete(f'/notes/api/items/{item.id}/tags/{foreign.id}').status_code == 400
    db_session.delete(foreign)
    db_session.delete(other_store)
    db_session.commit()


def test_cross_store_tag_rejected_on_delete_note(ctx, db_session):
    """對稱測試：note 端點的 DELETE 也要擋跨店標籤。"""
    c, _, note, _, _, _ = ctx
    other_store = Store(name=f'W{uuid.uuid4().hex[:5]}'.upper())
    db_session.add(other_store)
    db_session.commit()
    foreign = Tag(store=other_store.name, name='別店標籤N', color='#222222')
    db_session.add(foreign)
    db_session.commit()
    assert c.delete(f'/notes/api/{note.id}/tags/{foreign.id}').status_code == 400
    db_session.delete(foreign)
    db_session.delete(other_store)
    db_session.commit()
