"""可視店家（super_admin per-user 店家過濾）測試。"""
import json as _json
import uuid
import pytest


def _uniq(p='x'):
    return f'{p}_{uuid.uuid4().hex[:6]}'


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk(db, role='super_admin', store=None):
    from models import User
    u = User(username=_uniq('u'), role=role, store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    import time
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())


def test_hidden_stores_roundtrip(db):
    u = _mk(db)
    assert u.get_hidden_stores() == []
    u.set_hidden_stores(['B', 'C'])
    db.session.commit()
    assert sorted(u.get_hidden_stores()) == ['B', 'C']


def test_visible_super_admin_subtracts_hidden(db):
    u = _mk(db, role='super_admin')
    u.set_hidden_stores(['C'])
    db.session.commit()
    assert u.visible_store_names(['B', 'C', 'E']) == ['B', 'E']


def test_visible_new_store_auto_visible(db):
    u = _mk(db, role='super_admin')
    u.set_hidden_stores(['C'])
    db.session.commit()
    assert 'NEW' in u.visible_store_names(['B', 'C', 'NEW'])


def test_visible_admin_only_own_store(db):
    u = _mk(db, role='admin', store='B')
    u.set_hidden_stores(['B', 'E'])
    db.session.commit()
    assert u.visible_store_names(['B', 'C', 'E']) == ['B']


def test_visible_user_no_store_empty(db):
    u = _mk(db, role='user', store=None)
    assert u.visible_store_names(['B', 'C']) == []


def test_list_stores_includes_visible_flag(client, db):
    from models import Store
    s1, s2 = _uniq('S'), _uniq('S')
    db.session.add_all([Store(name=s1), Store(name=s2)])
    db.session.commit()
    su = _mk(db, role='super_admin')
    su.set_hidden_stores([s2])
    db.session.commit()
    _login(client, su)
    rows = client.get('/admin/stores').get_json()
    by = {r['name']: r for r in rows}
    assert by[s1]['visible'] is True
    assert by[s2]['visible'] is False


def test_toggle_visible_super_admin(client, db):
    from models import Store
    s1, s2 = _uniq('S'), _uniq('S')
    db.session.add_all([Store(name=s1), Store(name=s2)])
    db.session.commit()
    su = _mk(db, role='super_admin')
    _login(client, su)
    r = client.post(f'/admin/stores/{s2}/toggle-visible')
    assert r.get_json()['visible'] is False
    su2 = client.get('/admin/stores').get_json()
    assert {x['name']: x['visible'] for x in su2}[s2] is False
    r2 = client.post(f'/admin/stores/{s2}/toggle-visible')
    assert r2.get_json()['visible'] is True


def test_toggle_visible_admin_forbidden(client, db):
    from models import Store
    sname = _uniq('S')
    db.session.add(Store(name=sname))
    db.session.commit()
    a = _mk(db, role='admin', store=sname)
    _login(client, a)
    assert client.post(f'/admin/stores/{sname}/toggle-visible').status_code == 403


def test_toggle_visible_cannot_hide_last(client, db):
    from models import Store
    sname = _uniq('S')
    db.session.add(Store(name=sname))
    db.session.commit()
    su = _mk(db, role='super_admin')
    _login(client, su)
    # Hide all other stores first so sname is the only visible one
    all_others = [s.name for s in Store.query.all() if s.name != sname]
    su.set_hidden_stores(all_others)
    db.session.commit()
    r = client.post(f'/admin/stores/{sname}/toggle-visible')
    assert r.status_code == 400
    assert '至少' in r.get_json().get('message', '')


def test_dashboard_has_visible_column(client, db):
    su = _mk(db, role='super_admin')
    _login(client, su)
    html = client.get('/admin/dashboard').get_data(as_text=True)
    assert '可視</th>' in html
    assert 'toggleStoreVisible' in html
    assert 'IS_SUPER_ADMIN' in html


def test_notes_super_admin_hidden_store_excluded(client, db):
    from models import Store, Note
    sb, sc = _uniq('SB'), _uniq('SC')
    db.session.add_all([Store(name=sb), Store(name=sc)])
    db.session.commit()
    su = _mk(db, role='super_admin')
    nb = Note(user_id=su.id, store=sb, title=_uniq('NOTE_B'),
              content='', note_type='regular')
    nc = Note(user_id=su.id, store=sc, title=_uniq('NOTE_C'),
              content='', note_type='regular')
    db.session.add_all([nb, nc])
    db.session.commit()
    su.set_hidden_stores([sc])
    db.session.commit()
    _login(client, su)
    # 無 ?store：只看可視店（不含已隱藏的 sc）
    html = client.get('/notes/?range=all').get_data(as_text=True)
    assert nb.title in html
    assert nc.title not in html


def test_notes_super_admin_hidden_store_param_ignored(client, db):
    from models import Store, Note
    sc = _uniq('SC')
    db.session.add(Store(name=sc))
    db.session.commit()
    su = _mk(db, role='super_admin')
    nc = Note(user_id=su.id, store=sc, title=_uniq('ONLY_C'),
              content='', note_type='regular')
    db.session.add(nc)
    db.session.commit()
    su.set_hidden_stores([sc])
    db.session.commit()
    _login(client, su)
    # 顯式 ?store=<已隱藏> → 忽略、回退可視全集，仍看不到 sc
    html = client.get(f'/notes/?store={sc}&range=all').get_data(as_text=True)
    assert nc.title not in html


def test_notes_super_admin_null_store_note_visible_after_hiding(client, db):
    """super_admin 隱藏某店後，store=NULL 的筆記仍應看得到（不被 NOT IN 濾掉）。"""
    from models import Store, Note
    sc = _uniq('SC')
    db.session.add(Store(name=sc))
    db.session.commit()
    su = _mk(db, role='super_admin')
    nn = Note(user_id=su.id, store=None, title=_uniq('NULL_NOTE'),
              content='', note_type='regular')
    db.session.add(nn)
    db.session.commit()
    su.set_hidden_stores([sc])   # 隱藏一間 → 觸發 NOT IN
    db.session.commit()
    _login(client, su)
    html = client.get('/notes/?range=all').get_data(as_text=True)
    assert nn.title in html      # NULL-store 筆記仍可見


def test_notes_api_super_admin_respects_visible(client, db):
    from models import Store, Note
    sb, sc = _uniq('SB'), _uniq('SC')
    db.session.add_all([Store(name=sb), Store(name=sc)])
    db.session.commit()
    su = _mk(db, role='super_admin')
    nb = Note(user_id=su.id, store=sb, title=_uniq('APIB'),
              content='', note_type='regular')
    nc = Note(user_id=su.id, store=sc, title=_uniq('APIC'),
              content='', note_type='regular')
    db.session.add_all([nb, nc])
    db.session.commit()
    su.set_hidden_stores([sc])
    db.session.commit()
    _login(client, su)
    resp = client.get('/notes/api?range=all').get_data(as_text=True)
    assert nb.title in resp
    assert nc.title not in resp


def test_update_note_preserves_hidden_store(client, db):
    """super_admin 編輯隱藏店的 note、送出含該 store → 不可被清成 NULL（authoring 不受可視限制）。"""
    from models import Store, Note
    sc = _uniq('SC')
    db.session.add(Store(name=sc))
    db.session.commit()
    su = _mk(db, role='super_admin')
    n = Note(user_id=su.id, store=sc, title=_uniq('EDITME'),
             content='', note_type='regular')
    db.session.add(n)
    db.session.commit()
    su.set_hidden_stores([sc])
    db.session.commit()
    _login(client, su)
    nid = n.id
    client.put(f'/notes/api/{nid}', json={'store': sc, 'title': 'updated'})
    db.session.expire_all()
    refreshed = Note.query.get(nid)
    assert refreshed.store == sc


def test_announcements_super_admin_respects_visible(client, db):
    from models import Store, Announcement
    sb, sc = _uniq('SB'), _uniq('SC')
    db.session.add_all([Store(name=sb), Store(name=sc)])
    db.session.commit()
    su = _mk(db, role='super_admin')
    tb, tc, tg = _uniq('ANNB'), _uniq('ANNC'), _uniq('ANNG')
    ab = Announcement(store=sb, title=tb, content='x', user_id=su.id, author_name=su.username)
    ac = Announcement(store=sc, title=tc, content='x', user_id=su.id, author_name=su.username)
    ag = Announcement(store=None, title=tg, content='x', user_id=su.id, author_name=su.username)
    db.session.add_all([ab, ac, ag])
    db.session.commit()
    su.set_hidden_stores([sc])
    db.session.commit()
    _login(client, su)
    data = client.get('/notes/announcement/api?per_page=100').get_json()
    titles = {a['title'] for a in data['items']}
    assert tb in titles
    assert tg in titles      # 全站公告永遠顯示
    assert tc not in titles  # 隱藏店公告不出現


def test_announcement_compose_dropdown_excludes_hidden(client, db):
    from models import Store
    sb, sc = _uniq('SB'), _uniq('SC')
    db.session.add_all([Store(name=sb), Store(name=sc)])
    db.session.commit()
    su = _mk(db, role='super_admin')
    su.set_hidden_stores([sc])
    db.session.commit()
    _login(client, su)
    html = client.get('/notes/announcement/new').get_data(as_text=True)
    assert f'value="{sb}"' in html        # 可視店在 dropdown
    assert f'value="{sc}"' not in html     # 隱藏店不在 dropdown


def test_dashboard_user_list_respects_visible(client, db):
    from models import Store
    sb, sc = _uniq('SB'), _uniq('SC')
    db.session.add_all([Store(name=sb), Store(name=sc)])
    db.session.commit()
    su = _mk(db, role='super_admin')
    ub = _mk(db, role='user', store=sb)
    uc = _mk(db, role='user', store=sc)
    su.set_hidden_stores([sc])
    db.session.commit()
    _login(client, su)
    html = client.get('/admin/dashboard').get_data(as_text=True)
    assert ub.username in html       # 可視店的 user 在
    assert uc.username not in html   # 隱藏店的 user 不在
    assert su.username in html       # super_admin 自己（store=None）永遠在


def test_notes_index_announcements_respect_visible(client, db):
    """Fix 1: /notes/ 頁內嵌公告也要吃可視店。"""
    from models import Store, Announcement
    sb, sc = _uniq('SB'), _uniq('SC')
    db.session.add_all([Store(name=sb), Store(name=sc)])
    db.session.commit()
    su = _mk(db, role='super_admin')
    tb, tc, tg = _uniq('IDXANNB'), _uniq('IDXANNC'), _uniq('IDXANNG')
    db.session.add_all([
        Announcement(store=sb, title=tb, content='x', user_id=su.id, author_name=su.username),
        Announcement(store=sc, title=tc, content='x', user_id=su.id, author_name=su.username),
        Announcement(store=None, title=tg, content='x', user_id=su.id, author_name=su.username),
    ])
    db.session.commit()
    su.set_hidden_stores([sc])
    db.session.commit()
    _login(client, su)
    html = client.get('/notes/?range=all').get_data(as_text=True)
    assert tb in html
    assert tg in html       # 全站公告永遠在
    assert tc not in html   # 隱藏店公告不出現在 /notes/ 頁


def test_ai_summary_rejects_hidden_store(client, db):
    """Fix 2: super_admin 不能對隱藏店產生 AI 摘要。"""
    from models import Store
    sc = _uniq('SC')
    db.session.add(Store(name=sc))
    db.session.commit()
    su = _mk(db, role='super_admin')
    su.set_hidden_stores([sc])
    db.session.commit()
    _login(client, su)
    r = client.post('/notes/ai/summary', json={'store': sc})
    assert r.status_code == 400
    assert '店別' in r.get_json().get('message', '')


def test_announcement_edit_dropdown_excludes_hidden_but_keeps_current(client, db):
    """Fix 3: 編輯頁 dropdown 濾隱藏店，但保留該公告現有店。"""
    from models import Store, Announcement
    sb, sc = _uniq('SB'), _uniq('SC')
    db.session.add_all([Store(name=sb), Store(name=sc)])
    db.session.commit()
    su = _mk(db, role='super_admin')
    a = Announcement(store=sb, title=_uniq('EDITANN'), content='x',
                     user_id=su.id, author_name=su.username)
    db.session.add(a)
    db.session.commit()
    su.set_hidden_stores([sc])
    db.session.commit()
    _login(client, su)
    html = client.get(f'/notes/announcement/{a.id}/edit').get_data(as_text=True)
    assert f'value="{sb}"' in html       # 可視店（也是現有店）在
    assert f'value="{sc}"' not in html    # 隱藏店不在


def test_dashboard_set_store_dropdown_all_stores_for_admin(client, db):
    """回歸(F16)：admin 後台要能把 user 改成任一店別 — set-store 下拉=全部店家，
    不可被可視設定縮成只剩自己店。"""
    from models import Store
    sb, sc = _uniq('SB'), _uniq('SC')
    db.session.add_all([Store(name=sb), Store(name=sc)])
    db.session.commit()
    a = _mk(db, role='admin', store=sb)
    _mk(db, role='user', store=sb)   # admin 看得到本店 user → 該列有 set-store 下拉
    _login(client, a)
    html = client.get('/admin/dashboard').get_data(as_text=True)
    # 非自己店的 sc 也要出現在下拉（admin 能跨店指派）
    assert f'value="{sc}"' in html
    assert f'value="{sb}"' in html


def test_dashboard_set_store_dropdown_all_stores_for_super_admin(client, db):
    """super_admin 也要能把 user 改成任一店別 — 即使隱藏了某些店，set-store
    authoring 下拉仍含全部店家（指派不受可視設定限制）。"""
    from models import Store
    sb, sc = _uniq('SB'), _uniq('SC')
    db.session.add_all([Store(name=sb), Store(name=sc)])
    db.session.commit()
    su = _mk(db, role='super_admin')
    su.set_hidden_stores([sc])            # 隱藏 sc
    db.session.commit()
    _mk(db, role='user', store=sb)        # 一個 user 讓 set-store 下拉渲染
    _login(client, su)
    html = client.get('/admin/dashboard').get_data(as_text=True)
    # 隱藏的 sc 仍要出現在 set-store authoring 下拉（指派不受可視限制）
    assert f'value="{sc}"' in html
    assert f'value="{sb}"' in html
