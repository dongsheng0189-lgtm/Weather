"""人員查詢頁：授權者顯示白名單管理面板；非授權者完全看不到管理 UI；
D 段 JS 含 is_whitelisted 判斷與白名單內提示文字。"""
import uuid
from models import User
from extensions import db


def _mk(db_session, root_admin=False, features=None):
    name = "admin" if root_admin else f"u_{uuid.uuid4().hex[:6]}"
    existing = User.query.filter_by(username=name).first()
    if existing:
        if features is not None:
            existing.set_features(features); db_session.commit()
        return existing
    u = User(username=name, role="user", store="B", is_active=True)
    u.set_password("1234")
    if features is not None:
        u.set_features(features)
    db_session.add(u); db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_management_panel_shown_to_granted(app, client, db_session):
    """有 vetting_whitelist_add 的使用者看到完整管理面板（清單 + 改 + 刪 + 新增），
    且面板 JS 含清單端點與新增端點（URL 縮短中介層把 /vetting → /v）。"""
    u = _mk(db_session, features=["vetting", "vetting_whitelist_add"])
    _login(client, u)
    body = client.get("/vetting/").get_data(as_text=True)
    assert "白名單管理" in body               # 按鈕與面板標題
    assert "/v/whitelist" in body             # 清單端點 (short form)
    assert "/v/whitelist/add" in body         # 新增端點 (short form)


def test_management_panel_hidden_from_non_granted(app, client, db_session):
    """只有 vetting（無 vetting_whitelist_add）的使用者完全看不到管理 UI：
    無「白名單管理」按鈕/面板，也無任何管理 JS。"""
    u = _mk(db_session, features=["vetting"])
    _login(client, u)
    body = client.get("/vetting/").get_data(as_text=True)
    assert "白名單管理" not in body           # 管理面板文字
    assert "/v/whitelist" not in body         # 管理 JS（清單 API call）


def test_d_section_renders_whitelist_badge_marker(app, client, db_session):
    """D 段 renderResult JS 含 is_whitelisted 旗標處理與命中提示文字「白名單內」。
    此邏輯不依 vetting_whitelist_add，用 root_admin 代表有 vetting 的使用者。"""
    u = _mk(db_session, root_admin=True)
    _login(client, u)
    body = client.get("/vetting/").get_data(as_text=True)
    assert "is_whitelisted" in body           # D 段 JS 有白名單判斷
    assert "白名單內" in body                 # 命中提示文字（C2 最終版）
