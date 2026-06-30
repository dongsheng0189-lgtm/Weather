"""後台 dashboard：白名單 card 已移除（改到 vetting 頁）；optgroup 分組仍存在。"""
import uuid
from models import User
from extensions import db


def _mk(db_session, root_admin=False):
    name = "admin" if root_admin else f"u_{uuid.uuid4().hex[:6]}"
    existing = User.query.filter_by(username=name).first()
    if existing:
        if root_admin and existing.role not in ("admin", "super_admin"):
            existing.role = "super_admin"
            db_session.commit()
        return existing
    u = User(username=name, role=("user" if not root_admin else "super_admin"),
             store="B", is_active=True)
    u.set_password("1234"); db_session.add(u); db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_whitelist_card_removed(app, client, db_session):
    """C3(b): dashboard 的「📞 電話白名單」card 已移除，連同 wlAdmin* JS 函數。"""
    u = _mk(db_session, root_admin=True); _login(client, u)
    body = client.get("/admin/dashboard").get_data(as_text=True)
    assert "電話白名單" not in body           # card 標題已移除
    assert "wlAdmin" not in body              # wlAdmin* JS 函數已移除


def test_optgroup_grouping_present(app, client, db_session):
    """模組權限管理下拉仍用 optgroup 依店別分組（獨立改善，不受白名單移除影響）。"""
    u = _mk(db_session, root_admin=True); _login(client, u)
    body = client.get("/admin/dashboard").get_data(as_text=True)
    assert "optgroup" in body                 # 使用者下拉依店別分組
