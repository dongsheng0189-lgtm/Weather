"""Tag filter on /notes/ test."""
import json as _json
import uuid
import pytest


def _uniq(prefix='T'):
    return f'{prefix}_{uuid.uuid4().hex[:6]}'


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_user(db, store='B店', username=None):
    from models import User
    u = User(username=username or _uniq('u_filt'),
             role='user', store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def test_notes_index_filter_by_tag(client, db):
    from models import Tag, Note, NoteTag
    u = _mk_user(db)
    has_title = _uniq('HAS_TAG')
    no_title = _uniq('NO_TAG')
    n1 = Note(user_id=u.id, store='B店', title=has_title,
              content='', note_type='regular')
    n2 = Note(user_id=u.id, store='B店', title=no_title,
              content='', note_type='regular')
    t = Tag(store='B店', name=_uniq('重要'), color='#E74C3C')
    db.session.add_all([n1, n2, t])
    db.session.commit()
    db.session.add(NoteTag(note_id=n1.id, tag_id=t.id))
    db.session.commit()
    _login(client, u)
    r = client.get(f'/notes/?tag={t.id}&range=all')
    html = r.get_data(as_text=True)
    assert has_title in html
    assert no_title not in html


def test_notes_index_filter_by_item_level_tag(client, db):
    """item-level tag 應該也會讓 note 出現在 filter 結果。"""
    from models import Tag, Note, ChecklistItem, ChecklistItemTag
    u = _mk_user(db)
    title_match = _uniq('CL_HAS_ITEM')
    title_other = _uniq('CL_NO_ITEM')
    n1 = Note(user_id=u.id, store='B店', title=title_match,
              content='', note_type='checklist')
    n2 = Note(user_id=u.id, store='B店', title=title_other,
              content='', note_type='checklist')
    db.session.add_all([n1, n2])
    db.session.commit()
    it1 = ChecklistItem(note_id=n1.id, text='item-w-tag', is_checked=False)
    it2 = ChecklistItem(note_id=n2.id, text='item-no-tag', is_checked=False)
    t = Tag(store='B店', name=_uniq('IF'), color='#E74C3C')
    db.session.add_all([it1, it2, t])
    db.session.commit()
    db.session.add(ChecklistItemTag(item_id=it1.id, tag_id=t.id))
    db.session.commit()
    _login(client, u)
    r = client.get(f'/notes/?tag={t.id}&range=all')
    html = r.get_data(as_text=True)
    assert title_match in html
    assert title_other not in html


def test_notes_index_no_tag_returns_all(client, db):
    from models import Note
    u = _mk_user(db)
    title = _uniq('ANY')
    db.session.add(Note(user_id=u.id, store='B店', title=title,
                        content='', note_type='regular'))
    db.session.commit()
    _login(client, u)
    r = client.get('/notes/?range=all')
    assert title in r.get_data(as_text=True)


def test_tag_filter_ignores_time_window(client, db):
    """標籤模式不吃時間：10 天前 updated 的 note 選標籤時仍應出現。"""
    from datetime import datetime, timedelta
    from models import Tag, Note, NoteTag
    u = _mk_user(db)
    old_title = _uniq('OLD_TAGGED')
    n = Note(user_id=u.id, store='B店', title=old_title,
             content='', note_type='regular')
    t = Tag(store='B店', name=_uniq('久遠'), color='#E74C3C')
    db.session.add_all([n, t])
    db.session.commit()
    db.session.add(NoteTag(note_id=n.id, tag_id=t.id))
    # updated_at 無 onupdate，手動塞 10 天前不會被 commit 覆蓋
    n.updated_at = datetime.utcnow() - timedelta(days=10)
    db.session.commit()
    _login(client, u)
    # 故意不帶 range=all：證明 tag 模式不再套 today 預設時間窗
    r = client.get(f'/notes/?tag={t.id}')
    assert old_title in r.get_data(as_text=True)


def test_tag_filter_ignores_category(client, db):
    """標籤模式不吃種類：帶 category 也不該縮小標籤結果。"""
    from models import Tag, Note, NoteTag
    u = _mk_user(db)
    title = _uniq('TAG_CAT')
    n = Note(user_id=u.id, store='B店', title=title, content='',
             note_type='regular', category='employee')
    t = Tag(store='B店', name=_uniq('CC'), color='#3498DB')
    db.session.add_all([n, t])
    db.session.commit()
    db.session.add(NoteTag(note_id=n.id, tag_id=t.id))
    db.session.commit()
    _login(client, u)
    # 帶一個不符的 category，tag 模式應忽略它、note 照樣出現
    r = client.get(f'/notes/?tag={t.id}&category=machine')
    assert title in r.get_data(as_text=True)


def test_keyword_takes_precedence_over_tag(client, db):
    """q + tag 同時有值 → keyword 模式，tag 被忽略。"""
    from models import Tag, Note, NoteTag
    u = _mk_user(db)
    kw = _uniq('KWHIT')
    n = Note(user_id=u.id, store='B店', title=kw, content='',
             note_type='regular')
    other = Note(user_id=u.id, store='B店', title=_uniq('TAGONLY'),
                 content='', note_type='regular')
    t = Tag(store='B店', name=_uniq('PX'), color='#E74C3C')
    db.session.add_all([n, other, t])
    db.session.commit()
    # tag 掛在 other（不含關鍵字的那筆）
    db.session.add(NoteTag(note_id=other.id, tag_id=t.id))
    db.session.commit()
    _login(client, u)
    r = client.get(f'/notes/?q={kw}&tag={t.id}')
    html = r.get_data(as_text=True)
    assert kw in html               # 關鍵字命中的出現
    assert other.title not in html  # 只掛 tag、無關鍵字的不出現（tag 被忽略）
