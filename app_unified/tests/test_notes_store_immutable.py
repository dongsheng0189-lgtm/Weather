"""編輯既有筆記時不得變更 store（新建時仍可選）。

2026-07-25 事故：super_admin 在編輯畫面手滑點到別的店別按鈕，筆記被靜默搬到
別店 —— 而 `store` 變更**不寫 NoteLog**，所以事後查不到任何痕跡，只能靠
SQL 比對才找回來。見 incident-checklist-vanish-20260725。

店別選單身兼兩用：新建時是正當功能（選要建在哪店），編輯時是地雷。
本次只封編輯路徑，新建保留。

取捨（已與使用者確認）：封掉後，要搬移建錯店的既有筆記只能靠 SQL，
或日後另做一個明確的「搬移」功能（會寫 log 的那種）。
"""
import time
import uuid
from pathlib import Path

import pytest

from models import Note, NoteLog, Store, User

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


@pytest.fixture
def super_admin_client(client, db_session):
    """以 super_admin 登入，並備妥兩間真實店家（路由會檢查 store in stores）。"""
    suffix = uuid.uuid4().hex[:6]
    sb = Store(name=f'B-{suffix}', login_enabled=True)
    sc = Store(name=f'C-{suffix}', login_enabled=True)
    sa = User(username=f'sa-{suffix}', role='super_admin')
    sa.set_password('0000')
    db_session.add_all([sb, sc, sa])
    db_session.commit()

    with client.session_transaction() as sess:
        sess['_user_id'] = str(sa.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())

    yield client, sa, sb, sc

    # ⚠️ NoteLog 也要清。SQLite 預設不強制外鍵，`ondelete="SET NULL"` 不會觸發，
    # 而刪掉 row 後主鍵會被重用 —— 殘留的 log 會掛到別的測試新建的 note id 上，
    # 造成遠端的、看起來毫不相干的測試失敗（實際踩過：test_paragraph_diff）。
    note_ids = [n.id for n in Note.query.filter_by(user_id=sa.id).all()]
    if note_ids:
        NoteLog.query.filter(NoteLog.note_id.in_(note_ids)).delete(
            synchronize_session=False)
    Note.query.filter_by(user_id=sa.id).delete()
    db_session.delete(sa)
    db_session.delete(sb)
    db_session.delete(sc)
    db_session.commit()


def test_create_note_can_still_choose_store(super_admin_client):
    """回歸防護：新建時仍然選得了店別，這條路不能被一起封掉。"""
    c, sa, sb, sc = super_admin_client
    res = c.post('/notes/api', json={'title': 'x', 'content': '', 'store': sc.name})
    assert res.status_code in (200, 201)
    note = Note.query.filter_by(user_id=sa.id).order_by(Note.id.desc()).first()
    assert note.store == sc.name, '新建時應該仍可指定店別'


def test_update_note_cannot_change_store(super_admin_client):
    """核心：PUT 帶 store 也不准搬店。"""
    c, sa, sb, sc = super_admin_client
    c.post('/notes/api', json={'title': 'x', 'content': '', 'store': sb.name})
    note = Note.query.filter_by(user_id=sa.id).order_by(Note.id.desc()).first()
    assert note.store == sb.name
    note_id = note.id

    res = c.put(f'/notes/api/{note_id}',
                json={'title': 'x2', 'store': sc.name})
    assert res.status_code == 200, res.get_data(as_text=True)

    refreshed = Note.query.filter_by(id=note_id).first()
    assert refreshed.title == 'x2', '其他欄位仍應正常更新'
    assert refreshed.store == sb.name, \
        f'store 被改動了（{sb.name} → {refreshed.store}）—— 這正是 7/25 事故的路徑'


def test_ws_update_handler_does_not_mutate_store():
    """WebSocket 的 update 走另一條程式路徑，同樣不可改 store。

    專案沒有 socketio 測試工具，比照 editor 模板測試的慣例做原始碼層比對。
    """
    ws_src = (Path(__file__).parent.parent / 'notes' / 'ws.py').read_text(encoding='utf-8')
    assert "note.store = data['store']" not in ws_src, \
        'notes/ws.py 仍會改 store —— HTTP 那條封了但 WS 這條沒封，等於沒封'
    assert 'note.store = data["store"]' not in ws_src


def test_http_update_handler_does_not_mutate_store():
    """同上，守住 HTTP 那條（與行為測試互為雙保險）。"""
    src = (Path(__file__).parent.parent / 'notes' / 'routes.py').read_text(encoding='utf-8')
    assert 'note.store = data["store"]' not in src
    assert "note.store = data['store']" not in src


def test_editor_shows_store_buttons_only_when_creating():
    """編輯既有筆記時，店別要變成唯讀徽章，不能是可點按鈕。"""
    src = EDITOR.read_text(encoding='utf-8')
    assert 'is_super_admin() and not note' in src, \
        '店別按鈕沒有加上「僅新建」的條件'
    assert 'store-badge-readonly' in src, \
        '編輯時應顯示唯讀店別徽章，讓 super_admin 看得到但改不了'
