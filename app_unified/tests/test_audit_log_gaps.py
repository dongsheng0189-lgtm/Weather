"""補齊「改了資料卻不寫 log」的路徑（2026-09-08）。

盤點來源：todo-store-edit-fix-and-log-audit / notes-save-return-silent-dataloss-risks。
2026-07-25 checklist 消失事故的教訓是「查不到痕跡比出錯更可怕」。

本次補兩類：

**A. 店家管理三個路由全無 log** —— 其中 `delete_store` 是破壞性操作（刪掉整間店）
卻完全不留痕跡，最該補。決策：沿用 `UserLog`（target_user=None、店名放
`target_name`），不新增資料表 —— 零 migration、馬上在現有後台看得到。

**B. checklist 項目的文字編輯與軟刪除不寫 NoteLog。**
決策：**勾選/取消勾選不補** —— 它是每天大量發生的例行操作，全記會把 NoteLog
洗版、反而讓真正重要的編輯/刪除變難找；而且項目本身已有
`checked_by` / `checked_at` / `checked_by_name` 三個快照欄位可查「誰勾的」。
"""
import time
import uuid

import pytest

from models import ChecklistItem, Note, NoteLog, Store, User


# ─────────────────────────────────────────────
# A. 店家管理
# ─────────────────────────────────────────────
@pytest.fixture
def admin_client(client, db_session):
    suffix = uuid.uuid4().hex[:6]
    admin = User(username=f'adm-{suffix}', role='admin')
    admin.set_password('0000')
    db_session.add(admin)
    db_session.commit()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(admin.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())
    yield client, admin, suffix
    from models import UserLog
    UserLog.query.filter_by(operator_id=admin.id).delete()
    db_session.delete(admin)
    db_session.commit()


def _logs_for(admin, action):
    from models import UserLog
    return UserLog.query.filter_by(operator_id=admin.id, action=action).all()


def test_create_store_writes_log(admin_client, db_session):
    c, admin, suffix = admin_client
    name = f'Z{suffix}'.upper()
    try:
        res = c.post('/admin/stores', json={'name': name})
        assert res.status_code == 201, res.get_data(as_text=True)
        logs = _logs_for(admin, 'create_store')
        assert logs, '新增店家沒有留下 log'
        assert logs[0].target_name == name
    finally:
        Store.query.filter_by(name=name).delete()
        db_session.commit()


def test_delete_store_writes_log(admin_client, db_session):
    """破壞性操作 —— 刪掉整間店卻不留痕跡是本次最該補的一條。"""
    c, admin, suffix = admin_client
    name = f'Y{suffix}'.upper()
    db_session.add(Store(name=name))
    db_session.commit()
    res = c.delete(f'/admin/stores/{name}')
    assert res.status_code == 200, res.get_data(as_text=True)
    logs = _logs_for(admin, 'delete_store')
    assert logs, '刪除店家沒有留下 log —— 破壞性操作無痕'
    assert logs[0].target_name == name


def test_toggle_store_login_writes_log(admin_client, db_session):
    c, admin, suffix = admin_client
    name = f'X{suffix}'.upper()
    db_session.add(Store(name=name, login_enabled=True))
    db_session.commit()
    try:
        res = c.post(f'/admin/stores/{name}/toggle-login')
        assert res.status_code == 200
        logs = _logs_for(admin, 'toggle_store_login')
        assert logs, '切換店家登入沒有留下 log'
        assert logs[0].target_name == name
        assert 'False' in (logs[0].detail or '') or '停用' in (logs[0].detail or ''), \
            'detail 應寫明切成什麼狀態'
    finally:
        Store.query.filter_by(name=name).delete()
        db_session.commit()


def test_store_action_labels_present_for_ui():
    """後台的操作紀錄要顯示中文，不能露出原始 action 代碼。"""
    from pathlib import Path
    src = (Path(__file__).parent.parent / 'admin' / 'routes.py').read_text(encoding='utf-8')
    for key in ('create_store', 'delete_store', 'toggle_store_login'):
        assert f'"{key}"' in src or f"'{key}'" in src
    assert '新增店家' in src and '刪除店家' in src


# ─────────────────────────────────────────────
# B. checklist 項目
# ─────────────────────────────────────────────
@pytest.fixture
def note_with_item(logged_in_client, db_session, test_user):
    n = Note(user_id=test_user.id, title='清單', content='',
             note_type='checklist', store=test_user.store)
    db_session.add(n)
    db_session.commit()
    it = ChecklistItem(note_id=n.id, text='原本的文字', order_index=0)
    db_session.add(it)
    db_session.commit()
    yield logged_in_client, n, it
    NoteLog.query.filter_by(note_id=n.id).delete()
    ChecklistItem.query.filter_by(note_id=n.id).delete()
    db_session.delete(n)
    db_session.commit()


def test_item_text_edit_writes_notelog(note_with_item):
    c, n, it = note_with_item
    res = c.patch(f'/notes/api/items/{it.id}', json={'text': '改過的文字'})
    assert res.status_code == 200, res.get_data(as_text=True)
    logs = NoteLog.query.filter_by(note_id=n.id, action='edit').all()
    assert logs, '項目文字編輯沒有寫 NoteLog'
    assert '原本的文字' in logs[0].diff or '改過的文字' in logs[0].diff


def test_item_soft_delete_writes_notelog(note_with_item):
    c, n, it = note_with_item
    res = c.delete(f'/notes/api/items/{it.id}')
    assert res.status_code == 200, res.get_data(as_text=True)
    logs = NoteLog.query.filter_by(note_id=n.id, action='delete').all()
    assert logs, '項目軟刪除沒有寫 NoteLog'
    assert '原本的文字' in (logs[0].diff or '')


def test_check_toggle_does_not_write_notelog(note_with_item):
    """刻意不記 —— 高頻例行操作，全記會洗版；誰勾的看 checked_by_name 即可。

    這條是**決策的守門測試**：日後有人想「順手補齊」勾選 log 時會先撞到它，
    看到這段說明再決定，而不是無意間改變行為。
    """
    c, n, it = note_with_item
    res = c.patch(f'/notes/api/items/{it.id}', json={'is_checked': True})
    assert res.status_code == 200
    assert not NoteLog.query.filter_by(note_id=n.id).all(), \
        '勾選不應寫 NoteLog（決策：高頻操作不記，已有 checked_by/at/name 快照）'
    refreshed = ChecklistItem.query.filter_by(id=it.id).first()
    assert refreshed.is_checked is True
    assert refreshed.checked_by_name, '誰勾的仍要查得到（快照欄位）'
