"""Tests for Note.category — 類別分類 + 篩選器重構"""
import uuid
import pytest
from models import User, Note, ChecklistItem, CATEGORY_CHOICES, CATEGORY_LABELS, CATEGORY_CREATABLE
from notes.routes import _classify_by_title, _build_categorized_checklist, _build_all_content
from extensions import db


def _mk_user(db_session, username=None, role="user", store="B", active=True):
    u = User(
        username=username or f"u_{uuid.uuid4().hex[:6]}",
        role=role,
        store=store,
        is_active=active,
    )
    u.set_password("1234")
    db_session.add(u)
    db_session.commit()
    return u


def _mk_note(db_session, user, category="misc", **kwargs):
    n = Note(
        user_id=user.id,
        author_name=user.username,
        title=kwargs.get("title", "t"),
        content=kwargs.get("content", "c"),
        store=kwargs.get("store", user.store),
        status=kwargs.get("status", "pending"),
        category=category,
        note_type=kwargs.get("note_type", "note"),
    )
    db_session.add(n)
    db_session.commit()
    return n


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_category_default_is_misc(app, db_session):
    u = _mk_user(db_session)
    n = Note(user_id=u.id, author_name=u.username, title="x", content="y", store=u.store)
    db_session.add(n)
    db_session.commit()
    assert n.category == "misc"


def test_category_constants_defined():
    assert CATEGORY_CHOICES == ["employee", "customer", "machine", "misc"]
    assert CATEGORY_LABELS["employee"] == "員工"
    assert CATEGORY_LABELS["customer"] == "客人"
    assert CATEGORY_LABELS["machine"] == "機台"
    assert CATEGORY_LABELS["misc"] == "雜項"
    assert "personal" not in CATEGORY_LABELS
    assert "unclassified" not in CATEGORY_LABELS
    assert CATEGORY_CREATABLE == ["employee", "customer", "machine", "misc"]


def test_new_note_with_category_employee(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.get("/notes/new?type=note&category=employee")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # default_title 應為 "M/D 員工"（不帶 username）
    assert "員工" in html
    # 找 title-input 的 value，驗證不含 username
    import re
    m = re.search(r'id="title-input"[^>]*value="([^"]*)"', html)
    assert m, "找不到 title-input"
    title_val = m.group(1)
    assert "員工" in title_val
    assert u.username not in title_val, f"employee 類別不該帶 username，但 title={title_val!r}"


def test_new_note_with_category_machine(app, client, db_session):
    u = _mk_user(db_session, username="alice")
    _login(client, u)
    r = client.get("/notes/new?type=note&category=machine")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "機台" in html


def test_new_checklist_with_category_customer(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.get("/notes/new?type=checklist&category=customer")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "客人" in html


def test_new_note_without_category(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.get("/notes/new?type=note")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # 沒帶 category → fallback misc → 標題含「雜項」
    assert "雜項" in html


def test_new_note_invalid_category(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.get("/notes/new?type=note&category=hacker")
    assert r.status_code == 200
    # 防禦：無效 category → fallback misc → 標題含「雜項」
    html = r.get_data(as_text=True)
    assert "雜項" in html


def test_post_api_accepts_valid_category(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.post("/notes/api", json={
        "title": "T", "content": "C",
        "category": "employee", "status": "pending", "note_type": "note",
    })
    assert r.status_code == 201
    nid = r.get_json()["id"]
    db_session.expire_all()
    n = Note.query.get(nid)
    assert n.category == "employee"


def test_post_api_rejects_invalid_category_fallback(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.post("/notes/api", json={
        "title": "T", "content": "C",
        "category": "hacker", "status": "pending", "note_type": "note",
    })
    assert r.status_code == 201  # fallback misc, not 400
    nid = r.get_json()["id"]
    db_session.expire_all()
    assert Note.query.get(nid).category == "misc"


def test_get_api_returns_category(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    n = _mk_note(db_session, u, category="customer")
    r = client.get(f"/notes/api/{n.id}")
    assert r.status_code == 200
    assert r.get_json()["category"] == "customer"


def test_put_api_updates_category(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    n = _mk_note(db_session, u, category="misc")
    r = client.put(f"/notes/api/{n.id}", json={"category": "employee"})
    assert r.status_code == 200
    db_session.expire_all()
    assert Note.query.get(n.id).category == "employee"


def test_filter_by_category(app, client, db_session):
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    _mk_note(db_session, u, category="employee", title="emp_note_uniqXYZ")
    _mk_note(db_session, u, category="customer", title="cust_note_uniqXYZ")
    _mk_note(db_session, u, category="misc", title="misc_note_uniqXYZ")
    r = client.get("/notes/?category=employee&range=30d")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "emp_note_uniqXYZ" in html
    assert "cust_note_uniqXYZ" not in html
    assert "misc_note_uniqXYZ" not in html


def test_filter_misc_visible(app, client, db_session):
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    _mk_note(db_session, u, category="misc", title="old_note_abc")
    r = client.get("/notes/?category=misc&range=30d")
    assert r.status_code == 200
    assert "old_note_abc" in r.get_data(as_text=True)


def test_filter_keyword_exclusive(app, client, db_session):
    """有 keyword 時,category/status/range 全部忽略"""
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    _mk_note(db_session, u, category="employee", title="hello_world_A", content="x")
    _mk_note(db_session, u, category="customer", title="hello_universe_B", content="y")
    r = client.get("/notes/?q=hello&category=employee")
    html = r.get_data(as_text=True)
    assert "hello_world_A" in html
    assert "hello_universe_B" in html


def test_filter_category_and_status_combined(app, client, db_session):
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    _mk_note(db_session, u, category="employee", status="pending", title="noteA")
    _mk_note(db_session, u, category="employee", status="resolved", title="noteB")
    _mk_note(db_session, u, category="customer", status="pending", title="noteC")
    r = client.get("/notes/?category=employee&status=pending&range=30d")
    html = r.get_data(as_text=True)
    assert "noteA" in html
    assert "noteB" not in html
    assert "noteC" not in html


def test_filter_cascade_category_link_resets_time(app, client, db_session):
    """category tab href 應該 reset range (不帶 range 或 range=)"""
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    r = client.get("/notes/?category=employee&range=7d&status=pending")
    html = r.get_data(as_text=True)
    import re
    # 找 category=customer 的 tab href
    m = re.search(r'href="([^"]*category=customer[^"]*)"', html)
    assert m, "找不到 category=customer tab"
    href = m.group(1)
    # href 應該不含 range=7d 也不含 status=pending
    assert "range=7d" not in href
    assert "status=pending" not in href


def test_filter_cascade_time_link_preserves_category_resets_status(app, client, db_session):
    """range tab href 應維持 category 但 reset status"""
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    r = client.get("/notes/?category=employee&range=today&status=pending")
    html = r.get_data(as_text=True)
    import re
    m = re.search(r'href="([^"]*range=3d[^"]*)"', html)
    assert m, "找不到 range=3d tab"
    href = m.group(1)
    assert "category=employee" in href
    assert "status=pending" not in href


# ─── 新增：_classify_by_title 4 桶測試 ───

def test_classify_by_title_employee():
    assert _classify_by_title("員工排班") == "員工"
    assert _classify_by_title("4/30 員工") == "員工"
    assert _classify_by_title("請假紀錄") == "員工"

def test_classify_by_title_customer():
    assert _classify_by_title("客人意見") == "客人"
    assert _classify_by_title("客訴處理") == "客人"
    assert _classify_by_title("顧客需求") == "客人"

def test_classify_by_title_machine():
    assert _classify_by_title("機台保養") == "機台"
    assert _classify_by_title("故障排除") == "機台"
    assert _classify_by_title("設備維修") == "機台"

def test_classify_by_title_fallback_to_misc():
    assert _classify_by_title("隨便寫的") == "雜項"
    assert _classify_by_title("") == "雜項"
    assert _classify_by_title(None) == "雜項"


# ─── 新增：_build_categorized_checklist 走 note.category 測試 ───

def _mk_checklist_note(db_session, user, category, items):
    n = Note(
        user_id=user.id, author_name=user.username,
        title=f"標題-{category}", content="",
        store=user.store, status="pending", category=category,
        note_type="checklist",
    )
    db_session.add(n)
    db_session.flush()
    for text, checked in items:
        ci = ChecklistItem(note_id=n.id, text=text, is_checked=checked,
                           checked_by_name=user.username if checked else None)
        db_session.add(ci)
    db_session.commit()
    return n

def test_build_categorized_checklist_routes_by_category(app, db_session):
    u = _mk_user(db_session)
    _mk_checklist_note(db_session, u, "employee", [("員工A 任務", False)])
    _mk_checklist_note(db_session, u, "customer", [("客人B 任務", False)])
    _mk_checklist_note(db_session, u, "machine", [("機台C 任務", False)])
    _mk_checklist_note(db_session, u, "misc", [("雜事D 任務", False)])

    cls = Note.query.filter_by(note_type="checklist", user_id=u.id).all()
    assert len(cls) == 4
    block = _build_categorized_checklist(cls)

    assert "## 👥 員工" in block and "員工A 任務" in block
    assert "## 🧑‍🤝‍🧑 客人" in block and "客人B 任務" in block
    assert "## 🔧 機台" in block and "機台C 任務" in block
    assert "## 📋 雜項" in block and "雜事D 任務" in block


# ─── 新增：_build_all_content 過濾 is_ai_summary ───

def test_build_all_content_excludes_ai_summary(app, db_session):
    # 用 unique store 避免跨 test 漏（conftest db_session 不 rollback）
    store = f"S_{uuid.uuid4().hex[:6]}"
    u = _mk_user(db_session, store=store)
    _mk_note(db_session, u, category="misc", title="一般筆記", content="一般內容", store=store)
    s = _mk_note(db_session, u, category="misc", title="2026-04-30 摘要", content="AI 結果", store=store)
    s.is_ai_summary = True
    db_session.commit()

    block, count, has_cl = _build_all_content(store)
    assert "一般筆記" in block
    assert "2026-04-30 摘要" not in block
    assert count == 1
