"""Notes index：公告 + AI 摘要可摺疊 markup smoke tests."""
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _make_user(db, role, store=None, username=None):
    from models import User
    import json as _json
    u = User(
        username=username or f"{role}_{store or 'na'}",
        role=role,
        store=store,
        features=_json.dumps(["notes"]),
    )
    u.set_password("0000")
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_ai_summary_section_has_collapsible_wrapper(client, db):
    u = _make_user(db, "user", store="B店", username="u_collap_ai")
    _login(client, u)
    r = client.get("/notes/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'class="collapsible' in html
    assert 'data-section="ai-summary"' in html


def test_announcement_section_has_collapsible_wrapper_and_max_id(client, db):
    sa = _make_user(db, "super_admin", username="sa_collap_seed")
    u = _make_user(db, "user", store="B店", username="u_collap_ann")
    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "ANN_FOR_COLLAP", "content": "body", "store": None})
    _login(client, u)
    r = client.get("/notes/")
    html = r.get_data(as_text=True)
    assert 'data-section="announcements"' in html
    assert 'data-max-ann-id="' in html
    import re
    m = re.search(r'data-max-ann-id="(\d+)"', html)
    assert m and int(m.group(1)) > 0


def test_each_announcement_has_bullet_and_card(client, db):
    sa = _make_user(db, "super_admin", username="sa_collap_each_seed")
    u = _make_user(db, "user", store="B店", username="u_collap_each")
    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "FIRST", "content": "x", "store": None})
    client.post("/notes/announcement/api",
                json={"title": "SECOND", "content": "y", "store": None})
    _login(client, u)
    r = client.get("/notes/")
    html = r.get_data(as_text=True)
    # bullet 在 preview 區、一個 ann 一個
    assert html.count('class="ann-bullet"') >= 2
    # 每個 ann 都有 .announcement-card
    assert html.count('class="announcement-card') >= 2
    assert 'FIRST' in html
    assert 'SECOND' in html


def test_collapsible_css_and_js_linked(client, db):
    u = _make_user(db, "user", store="B店", username="u_collap_assets")
    _login(client, u)
    r = client.get("/notes/")
    html = r.get_data(as_text=True)
    assert 'collapsible_sections.css' in html
    assert 'collapsible_sections.js' in html


def test_reminders_section_present():
    from pathlib import Path
    idx = Path(__file__).parent.parent / 'templates' / 'notes' / 'index.html'
    js = Path(__file__).parent.parent / 'static' / 'js' / 'collapsible_sections.js'
    js_src = js.read_text(encoding='utf-8')
    assert 'data-section="reminders"' in idx.read_text(encoding='utf-8')
    assert 'initRemindersSection' in js_src
    # 跨店合併視圖才顯示店名 chip（super_admin 選「全部」）
    assert 'rem-store' in js_src
    assert 'showStore' in js_src
