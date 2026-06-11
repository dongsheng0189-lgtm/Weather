"""三互斥模式的進階篩選 UI 顯示/隱藏。"""
import json as _json
import uuid
import pytest


def _uniq(prefix='T'):
    return f'{prefix}_{uuid.uuid4().hex[:6]}'


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_user(db, store='B店'):
    from models import User
    u = User(username=_uniq('u_ui'), role='user', store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def _mk_tag(db, store='B店'):
    from models import Tag
    t = Tag(store=store, name=_uniq('UI'), color='#E74C3C')
    db.session.add(t)
    db.session.commit()
    return t


def test_default_mode_shows_everything(client, db):
    u = _mk_user(db)
    _login(client, u)
    html = client.get('/notes/').get_data(as_text=True)
    assert '🔍 關鍵字：' in html          # 關鍵字框
    assert 'data-role="tag-row"' in html   # 標籤列
    assert '種類：' in html                # 6 排 tab 之一


def test_keyword_mode_hides_tag_row_and_tabs(client, db):
    u = _mk_user(db)
    _login(client, u)
    html = client.get('/notes/?q=anything').get_data(as_text=True)
    assert '🔍 關鍵字：' in html           # 關鍵字框留著
    assert 'data-role="tag-row"' not in html  # 標籤列隱藏
    assert '種類：' not in html            # tab 隱藏


def test_tag_mode_hides_keyword_and_tabs(client, db):
    u = _mk_user(db)
    t = _mk_tag(db)
    _login(client, u)
    html = client.get(f'/notes/?tag={t.id}').get_data(as_text=True)
    assert '🔍 關鍵字：' not in html       # 關鍵字框隱藏
    assert 'data-role="tag-row"' in html   # 標籤列留著
    assert '種類：' not in html            # tab 隱藏
    assert '清除過濾器' in html            # 清除過濾器仍在
