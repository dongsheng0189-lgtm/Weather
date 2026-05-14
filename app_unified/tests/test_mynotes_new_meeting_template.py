"""新增 meeting 筆記時 textarea 預填分類骨架；其他類型不帶。"""
import uuid

import pytest

from extensions import db
from models import User


@pytest.fixture
def _user(app, db_session):
    suffix = uuid.uuid4().hex[:8]
    u = User(username=f"u-{suffix}")
    u.set_password("x")
    u.set_features(["mynotes"])
    db_session.add(u)
    db_session.commit()
    return u


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def test_new_meeting_prefills_category_skeleton(app, client, _user):
    _login_as(client, _user)
    r = client.get("/mynotes/new?type=meeting")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    for header in ("代理商:", "追蹤部份:", "店務:", "人員:",
                   "    離職:", "    資遣:", "    調整:", "    其他:"):
        assert header in html, f"missing header: {header}"


def test_new_todo_note_has_no_template(app, client, _user):
    _login_as(client, _user)
    r = client.get("/mynotes/new?type=todo_note")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    # 純文字筆記 textarea 應該是空的；確認沒帶入 meeting 分類骨架
    assert "代理商:" not in html
    assert "    離職:" not in html


def test_new_todo_checklist_has_no_template(app, client, _user):
    _login_as(client, _user)
    r = client.get("/mynotes/new?type=todo_checklist")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "代理商:" not in html


def test_template_constant_matches_display_order(app):
    """模板分類順序必須跟 ai_local.DISPLAY_ORDER 一致、parser 才能依序組摘要。"""
    from mynotes.routes import MEETING_TEMPLATE
    from mynotes.ai_local import DISPLAY_ORDER
    idx = -1
    for section in DISPLAY_ORDER:
        new_idx = MEETING_TEMPLATE.find(section + ":")
        assert new_idx > idx, f"{section} 不在預期順序"
        idx = new_idx
