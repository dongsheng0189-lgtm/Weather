# Notes 類別分類 + 篩選器重構 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `Note.priority`（高/中/低）換成 `Note.category`（員工/客人/雜事/個人/未分類），並重構 `/notes` 列表頁的篩選器（新順序 + cascading reset）。

**Architecture:** 新增單一 `category` 欄位（非加密 enum），`priority` drop 掉；新增流程前加一個類別彈窗選擇器；列表頁篩選器改為 server-side `<a href>` tabs，種類/時間/狀態三層 cascade（選上游 reset 下游），關鍵字仍互斥。完全不動 `mynotes` 模組。

**Tech Stack:** Flask / SQLAlchemy / PostgreSQL (生產) / SQLite in-memory (test) / Jinja2 / vanilla JS / pytest

**Spec 參考:** `docs/superpowers/specs/2026-04-20-notes-category-filter-design.md`

**工作目錄假設：** 所有路徑以 repo 根 `/home/hirain0126/projects/webapp/` 為基準。主程式碼在 `app_unified/`。

---

## File Structure

### 新建
- `app_unified/migrations/versions/006_replace_priority_with_category.py` — PostgreSQL migration
- `app_unified/tests/test_category.py` — 新功能單元測試

### 修改
- `app_unified/models.py` — `Note.priority` → `Note.category`；`PRIORITY_CHOICES` → `CATEGORY_CHOICES` / `CATEGORY_LABELS` / `CATEGORY_CREATABLE`
- `app_unified/notes/routes.py` — 7 處 priority 清理 + filter 邏輯改為 AND 組合 + new_note 支援 category 參數
- `app_unified/notes/ws.py` — 5 處 priority 清理（跟 routes 平行）
- `app_unified/templates/notes/editor.html` — 刪 priority-selector，加 category-selector
- `app_unified/templates/notes/index.html` — 按鈕改 modal 觸發、filter tabs 新順序、badge 改 category、pagination href 同步、底部加 `<dialog>` modal

---

## Task 1: Model 加 category 欄位 + 常數

**Files:**
- Modify: `app_unified/models.py:8-9, 97`
- Test: `app_unified/tests/test_category.py` (new)

- [ ] **Step 1: 建立 test 檔並寫第一個測試（category 預設值）**

建立 `app_unified/tests/test_category.py`：

```python
"""Tests for Note.category — 類別分類 + 篩選器重構"""
import uuid
import pytest
from models import User, Note, CATEGORY_CHOICES, CATEGORY_LABELS, CATEGORY_CREATABLE
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


def _mk_note(db_session, user, category="unclassified", **kwargs):
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


def test_category_default_is_unclassified(app, db_session):
    u = _mk_user(db_session)
    n = Note(user_id=u.id, author_name=u.username, title="x", content="y", store=u.store)
    db_session.add(n)
    db_session.commit()
    assert n.category == "unclassified"


def test_category_constants_defined():
    assert CATEGORY_CHOICES == ["employee", "customer", "misc", "personal", "unclassified"]
    assert CATEGORY_LABELS["employee"] == "員工"
    assert CATEGORY_LABELS["customer"] == "客人"
    assert CATEGORY_LABELS["misc"] == "雜事"
    assert CATEGORY_LABELS["personal"] == "個人"
    assert CATEGORY_LABELS["unclassified"] == "未分類"
    assert CATEGORY_CREATABLE == ["employee", "customer", "misc", "personal"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && pytest tests/test_category.py -v`
Expected: FAIL — `ImportError: cannot import name 'CATEGORY_CHOICES'` 或 `AttributeError: ... 'category'`

- [ ] **Step 3: 修改 `app_unified/models.py`**

找到 line 8-9：
```python
STATUS_CHOICES = ["pending", "in_progress", "resolved"]
PRIORITY_CHOICES = ["high", "medium", "low"]
```

改為：
```python
STATUS_CHOICES = ["pending", "in_progress", "resolved"]
CATEGORY_CHOICES = ["employee", "customer", "misc", "personal", "unclassified"]
CATEGORY_LABELS = {
    "employee": "員工",
    "customer": "客人",
    "misc": "雜事",
    "personal": "個人",
    "unclassified": "未分類",
}
CATEGORY_CREATABLE = ["employee", "customer", "misc", "personal"]
```

找到 line 97：
```python
    priority = db.Column(db.Text, nullable=False, default="medium")
```

改為：
```python
    category = db.Column(db.Text, nullable=False, default="unclassified")
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd app_unified && pytest tests/test_category.py -v`
Expected: PASS — `test_category_default_is_unclassified PASSED`, `test_category_constants_defined PASSED`

- [ ] **Step 5: 跑完整 test suite 驗證不炸其他測試**

Run: `cd app_unified && pytest -x 2>&1 | tail -30`
Expected: 可能有 import error 從 routes.py / ws.py (因為還在 import `PRIORITY_CHOICES`)。這是預期的，下一個 task 修。

如果其他舊測試（`test_crypto.py` / `test_admin_rename.py` / `test_paragraph_diff.py` / `test_cleanup.py`）不 import `priority` 就不該壞。先確認這些可以跑。

Run: `cd app_unified && pytest tests/test_crypto.py tests/test_admin_rename.py tests/test_paragraph_diff.py tests/test_cleanup.py -v`
Expected: 全部 PASS（共 ~70 cases）。

- [ ] **Step 6: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/models.py app_unified/tests/test_category.py
git commit -m "feat(notes): add Note.category column, remove priority from model

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `new_note()` 支援 category 參數 + 自動標題

**Files:**
- Modify: `app_unified/notes/routes.py:6, 192-204`
- Test: `app_unified/tests/test_category.py` (add cases)

- [ ] **Step 1: 加測試 — GET /notes/new 帶 category 的各種情境**

把以下加到 `test_category.py` 末尾：

```python
def test_new_note_with_category_employee(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.get("/notes/new?type=note&category=employee")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # default_title 應為 "M/D 員工"（不帶 username）
    assert "員工" in html
    assert u.username not in html.split("title-input")[1][:200], "employee 不應帶 username"


def test_new_note_with_category_personal(app, client, db_session):
    u = _mk_user(db_session, username="alice")
    _login(client, u)
    r = client.get("/notes/new?type=note&category=personal")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # default_title 應為 "M/D alice筆記"
    assert "alice筆記" in html


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
    # fallback 到 unclassified，title 用 username + 筆記（沿用舊 default）
    html = r.get_data(as_text=True)
    assert u.username in html


def test_new_note_invalid_category(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.get("/notes/new?type=note&category=hacker")
    assert r.status_code == 200
    # 防禦：非法值 fallback 到 unclassified
    html = r.get_data(as_text=True)
    assert u.username in html  # unclassified 走 username 標題
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && pytest tests/test_category.py::test_new_note_with_category_employee -v`
Expected: FAIL — 要不然是 import error，要不然是標題不含「員工」/ 含 username

- [ ] **Step 3: 修改 `app_unified/notes/routes.py`**

找到 line 6：
```python
from models import Note, Store, NoteLog, ChecklistItem, STATUS_CHOICES, PRIORITY_CHOICES
```

改為：
```python
from models import Note, Store, NoteLog, ChecklistItem, STATUS_CHOICES, CATEGORY_CHOICES, CATEGORY_LABELS, CATEGORY_CREATABLE
```

找到 line 192-204 的 `new_note()`：
```python
@notes_bp.route("/new", methods=["GET"])
@login_required
def new_note():
    stores = _get_stores()
    note_type = request.args.get("type", "note")
    if note_type not in ("note", "checklist"):
        note_type = "note"
    today = datetime.now(_TW).strftime("%-m/%-d")
    type_label = "確認表單" if note_type == "checklist" else "筆記"
    default_title = f"{today} {current_user.username}{type_label}"
    return render_template("notes/editor.html", note=None, stores=stores,
                           status_choices=STATUS_CHOICES, priority_choices=PRIORITY_CHOICES,
                           default_title=default_title, note_type=note_type)
```

改為：
```python
@notes_bp.route("/new", methods=["GET"])
@login_required
def new_note():
    stores = _get_stores()
    note_type = request.args.get("type", "note")
    if note_type not in ("note", "checklist"):
        note_type = "note"
    category = request.args.get("category", "unclassified")
    if category not in CATEGORY_CREATABLE:
        category = "unclassified"

    today = datetime.now(_TW).strftime("%-m/%-d")
    type_label = "確認表單" if note_type == "checklist" else "筆記"
    if category in ("personal", "unclassified"):
        default_title = f"{today} {current_user.username}{type_label}"
    else:
        default_title = f"{today} {CATEGORY_LABELS[category]}"

    return render_template("notes/editor.html", note=None, stores=stores,
                           status_choices=STATUS_CHOICES,
                           category_choices=CATEGORY_CHOICES,
                           category_labels=CATEGORY_LABELS,
                           default_title=default_title,
                           note_type=note_type,
                           default_category=category)
```

> 注意：`unclassified` 情境保留 username 標題是為了「沒選 category 也不會壞」的保守行為。`test_new_note_without_category` 和 `test_new_note_invalid_category` 都驗這個。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd app_unified && pytest tests/test_category.py -v -k "new_note or new_checklist"`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/routes.py app_unified/tests/test_category.py
git commit -m "feat(notes): new_note() accepts category param with auto title

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `/notes/api` POST / GET / PUT 改 priority → category

**Files:**
- Modify: `app_unified/notes/routes.py:256-313, 316-380` (update_note 涉及 priority 處理)
- Test: `app_unified/tests/test_category.py` (add cases)

- [ ] **Step 1: 加測試 — POST/GET/PUT 的 category 行為**

```python
def test_post_api_accepts_valid_category(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.post("/notes/api", json={
        "title": "T", "content": "C",
        "category": "employee", "status": "pending", "note_type": "note",
    })
    assert r.status_code == 201
    nid = r.get_json()["id"]
    n = Note.query.get(nid)
    assert n.category == "employee"


def test_post_api_rejects_invalid_category_fallback(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    r = client.post("/notes/api", json={
        "title": "T", "content": "C",
        "category": "hacker", "status": "pending", "note_type": "note",
    })
    assert r.status_code == 201  # 不回 400；fallback unclassified
    nid = r.get_json()["id"]
    assert Note.query.get(nid).category == "unclassified"


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
    n = _mk_note(db_session, u, category="unclassified")
    r = client.put(f"/notes/api/{n.id}", json={"category": "misc"})
    assert r.status_code == 200
    db_session.expire_all()
    assert Note.query.get(n.id).category == "misc"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && pytest tests/test_category.py -v -k "post_api or get_api or put_api"`
Expected: FAIL — create_note 仍讀 priority；get_note 不回 category；update_note 不處理 category

- [ ] **Step 3: 修改 `create_note()` (line 256-287)**

找到：
```python
    status = data.get("status") if data.get("status") in STATUS_CHOICES else "pending"
    priority = data.get("priority") if data.get("priority") in PRIORITY_CHOICES else "medium"
    note_type = data.get("note_type") if data.get("note_type") in ("note", "checklist") else "note"
    note = Note(
        user_id=current_user.id,
        author_name=current_user.username,
        note_type=note_type,
        title=data.get("title", "未命名筆記"),
        content=data.get("content", ""),
        store=store,
        status=status,
        priority=priority,
        created_at=now,
        updated_at=now,
    )
```

改為：
```python
    status = data.get("status") if data.get("status") in STATUS_CHOICES else "pending"
    category = data.get("category") if data.get("category") in CATEGORY_CHOICES else "unclassified"
    note_type = data.get("note_type") if data.get("note_type") in ("note", "checklist") else "note"
    note = Note(
        user_id=current_user.id,
        author_name=current_user.username,
        note_type=note_type,
        title=data.get("title", "未命名筆記"),
        content=data.get("content", ""),
        store=store,
        status=status,
        category=category,
        created_at=now,
        updated_at=now,
    )
```

- [ ] **Step 4: 修改 `get_note()` (line 290-313)**

找到 return jsonify block 裡：
```python
        "store": note.store, "status": note.status or "pending",
        "priority": note.priority or "medium",
```

改為：
```python
        "store": note.store, "status": note.status or "pending",
        "category": note.category or "unclassified",
```

- [ ] **Step 5: 修改 `update_note()` (line 351-354)**

找到：
```python
    if "priority" in data and data["priority"] in PRIORITY_CHOICES:
        if data["priority"] != note.priority:
            diff_parts.append(f"優先度: {note.priority} → {data['priority']}")
        note.priority = data["priority"]
```

替換為：
```python
    if "category" in data and data["category"] in CATEGORY_CHOICES:
        if data["category"] != note.category:
            diff_parts.append(f"類別: {note.category} → {data['category']}")
        note.category = data["category"]
```

- [ ] **Step 6: 跑測試確認通過**

Run: `cd app_unified && pytest tests/test_category.py -v -k "post_api or get_api or put_api"`
Expected: 4 PASS

- [ ] **Step 7: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/routes.py app_unified/tests/test_category.py
git commit -m "feat(notes): POST/GET/PUT /api swap priority for category

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 列表頁 `index()` 篩選邏輯改 AND 組合 + cascade

**Files:**
- Modify: `app_unified/notes/routes.py:124-189` (整個 index function + list_notes)
- Test: `app_unified/tests/test_category.py`

- [ ] **Step 1: 加測試 — 篩選邏輯**

```python
def test_filter_by_category(app, client, db_session):
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    _mk_note(db_session, u, category="employee", title="e1")
    _mk_note(db_session, u, category="customer", title="c1")
    _mk_note(db_session, u, category="misc", title="m1")
    r = client.get("/notes/?category=employee&range=30d")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "e1" in html
    assert "c1" not in html
    assert "m1" not in html


def test_filter_unclassified_visible(app, client, db_session):
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    _mk_note(db_session, u, category="unclassified", title="old_note")
    r = client.get("/notes/?category=unclassified&range=30d")
    assert r.status_code == 200
    assert "old_note" in r.get_data(as_text=True)


def test_filter_keyword_exclusive(app, client, db_session):
    """有 keyword 時，category/status/range 全部忽略"""
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    _mk_note(db_session, u, category="employee", title="hello_world", content="x")
    _mk_note(db_session, u, category="customer", title="hello_universe", content="y")
    # 傳 category=employee 但 q 存在 → category 被忽略，兩筆都回
    r = client.get("/notes/?q=hello&category=employee")
    html = r.get_data(as_text=True)
    assert "hello_world" in html
    assert "hello_universe" in html


def test_filter_category_and_status_combined(app, client, db_session):
    """無 keyword 時，category + status AND"""
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    _mk_note(db_session, u, category="employee", status="pending", title="A")
    _mk_note(db_session, u, category="employee", status="resolved", title="B")
    _mk_note(db_session, u, category="customer", status="pending", title="C")
    r = client.get("/notes/?category=employee&status=pending&range=30d")
    html = r.get_data(as_text=True)
    assert "A" in html
    assert "B" not in html  # status 不符
    assert "C" not in html  # category 不符


def test_filter_cascade_category_link_resets_time(app, client, db_session):
    """category tab 的 href 應該 reset range 成空（沒指定時 default today）"""
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    r = client.get("/notes/?category=employee&range=7d&status=pending")
    html = r.get_data(as_text=True)
    # 找「客人」tab 的 href —— 它應該只帶 category=customer，不帶 range 也不帶 status
    import re
    m = re.search(r'href="([^"]*category=customer[^"]*)"', html)
    assert m, f"找不到 category=customer tab"
    href = m.group(1)
    assert "range=" not in href or "range=&" in href or href.endswith("range=")
    assert "status=" not in href or "status=&" in href or href.endswith("status=")


def test_filter_cascade_time_link_resets_status(app, client, db_session):
    """range tab 的 href 應該維持 category 但 reset status"""
    u = _mk_user(db_session, role="super_admin", store="B")
    _login(client, u)
    r = client.get("/notes/?category=employee&range=today&status=pending")
    html = r.get_data(as_text=True)
    import re
    m = re.search(r'href="([^"]*range=3d[^"]*)"', html)
    assert m, "找不到 range=3d tab"
    href = m.group(1)
    assert "category=employee" in href
    assert "status=" not in href or "status=&" in href or href.endswith("status=")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && pytest tests/test_category.py -v -k "filter_"`
Expected: FAIL — 現行 index() 是互斥邏輯，不會同時套用 category+status+range

- [ ] **Step 3: 改寫 `index()` — line 124-189**

替換整段：
```python
@notes_bp.route("/")
@login_required
def index():
    try:
        from notes.cleanup import cleanup_resolved_notes
        cleanup_resolved_notes()
    except Exception:
        current_app.logger.exception("cleanup_resolved_notes failed")
    store_filter = request.args.get("store", "")
    status_filter = request.args.get("status", "")
    range_param = request.args.get("range", "")
    category_filter = request.args.get("category", "")
    keyword = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 20
    stores = _get_stores()

    query = Note.query

    # 店別範圍（不變）
    if current_user.is_super_admin():
        if store_filter in stores:
            query = query.filter_by(store=store_filter)
    elif current_user.is_admin():
        query = query.filter_by(store=current_user.store)
    else:
        query = query.filter_by(store=current_user.store)

    # keyword 互斥：有 keyword 時忽略其他篩選
    keyword_mode = False
    active_filter = None
    if keyword:
        active_filter = 'keyword'
        keyword_mode = True
    else:
        # AND 組合：category + status + range
        active_filter = 'combined'
        if category_filter in CATEGORY_CHOICES:
            query = query.filter_by(category=category_filter)
        if status_filter in STATUS_CHOICES:
            query = query.filter_by(status=status_filter)
        # range default today
        if not range_param:
            range_param = 'today'
        query = _date_filter(query, range_param)

    if keyword_mode:
        all_rows = query.order_by(Note.updated_at.desc()).all()
        kl = keyword.lower()
        filtered = [n for n in all_rows if _note_matches(n, kl)]
        pagination = _PyPagination(filtered, page=page, per_page=per_page)
    else:
        pagination = query.order_by(Note.updated_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

    return render_template("notes/index.html", notes=pagination.items, pagination=pagination,
                           stores=stores,
                           current_store=store_filter,
                           current_status=status_filter,
                           current_range=range_param,
                           current_category=category_filter,
                           current_keyword=keyword,
                           active_filter=active_filter,
                           status_choices=STATUS_CHOICES,
                           category_choices=CATEGORY_CHOICES,
                           category_labels=CATEGORY_LABELS)
```

- [ ] **Step 4: 同步清理 `list_notes()` (line 207-253) — 把 priority 換成 category**

找到：
```python
    priority_filter = request.args.get("priority", "")
```
改為：
```python
    category_filter = request.args.get("category", "")
```

找到：
```python
    elif priority_filter and priority_filter in PRIORITY_CHOICES:
        query = query.filter_by(priority=priority_filter)
```
改為：
```python
    elif category_filter and category_filter in CATEGORY_CHOICES:
        query = query.filter_by(category=category_filter)
```

找到 return jsonify 裡：
```python
        "priority": n.priority or "medium",
```
改為：
```python
        "category": n.category or "unclassified",
```

> 這個 API 的互斥邏輯要不要改成 AND？為保守起見維持現行（API 較少人用，UI 才是主戰場）。若後續要改再做。

- [ ] **Step 5: 跑測試確認通過**

Run: `cd app_unified && pytest tests/test_category.py -v -k "filter_"`
Expected: 6 PASS

- [ ] **Step 6: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/routes.py app_unified/tests/test_category.py
git commit -m "feat(notes): index() filter now AND-combines category+status+range

keyword 仍互斥；三層 cascade 交給 template href 處理

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 改 `editor.html` — category selector 取代 priority selector

**Files:**
- Modify: `app_unified/templates/notes/editor.html`

這 task 純 UI，沒有 unit test。改完跑 full test suite 不該 regress。

- [ ] **Step 1: 刪除 priority CSS (line 36-41)**

找到：
```css
.priority-selector { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; margin-bottom: .75rem; }
.priority-selector label { font-size: .85rem; color: #666; margin-right: .25rem; }
.priority-btn { padding: .35rem .7rem; border-radius: 16px; font-size: .82rem; font-weight: 600; cursor: pointer; border: 2px solid #ddd; background: #f5f5f5; color: #555; transition: all .15s; min-height: 36px; }
.priority-btn.selected[data-priority="high"]   { background: #e74c3c; color: #fff; border-color: transparent; }
.priority-btn.selected[data-priority="medium"] { background: #e67e22; color: #fff; border-color: transparent; }
.priority-btn.selected[data-priority="low"]    { background: #95a5a6; color: #fff; border-color: transparent; }
```

替換為：
```css
.category-selector { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; margin-bottom: .75rem; }
.category-selector label { font-size: .85rem; color: #666; margin-right: .25rem; }
.category-btn { padding: .35rem .7rem; border-radius: 16px; font-size: .82rem; font-weight: 600; cursor: pointer; border: 2px solid #ddd; background: #f5f5f5; color: #555; transition: all .15s; min-height: 36px; }
.category-btn.selected[data-category="employee"]     { background: #3498db; color: #fff; border-color: transparent; }
.category-btn.selected[data-category="customer"]     { background: #e67e22; color: #fff; border-color: transparent; }
.category-btn.selected[data-category="misc"]         { background: #95a5a6; color: #fff; border-color: transparent; }
.category-btn.selected[data-category="personal"]     { background: #9b59b6; color: #fff; border-color: transparent; }
.category-btn.selected[data-category="unclassified"] { background: #bdc3c7; color: #fff; border-color: transparent; }
```

- [ ] **Step 2: 刪除 priority HTML (line 116-122)**

找到：
```html
  {% set cur_priority = note.priority if note and note.priority else 'medium' %}
  <div class="priority-selector">
    <label>優先度：</label>
    <button class="priority-btn {% if cur_priority == 'high' %}selected{% endif %}" data-priority="high" type="button" onclick="selectPriority(this)">高</button>
    <button class="priority-btn {% if cur_priority == 'medium' %}selected{% endif %}" data-priority="medium" type="button" onclick="selectPriority(this)">中</button>
    <button class="priority-btn {% if cur_priority == 'low' %}selected{% endif %}" data-priority="low" type="button" onclick="selectPriority(this)">低</button>
  </div>
```

替換為：
```html
  {% set cur_category = note.category if note and note.category else (default_category or 'unclassified') %}
  <div class="category-selector">
    <label>類別：</label>
    <button class="category-btn {% if cur_category == 'employee' %}selected{% endif %}" data-category="employee" type="button" onclick="selectCategory(this)">員工</button>
    <button class="category-btn {% if cur_category == 'customer' %}selected{% endif %}" data-category="customer" type="button" onclick="selectCategory(this)">客人</button>
    <button class="category-btn {% if cur_category == 'misc' %}selected{% endif %}" data-category="misc" type="button" onclick="selectCategory(this)">雜事</button>
    <button class="category-btn {% if cur_category == 'personal' %}selected{% endif %}" data-category="personal" type="button" onclick="selectCategory(this)">個人</button>
    {% if cur_category == 'unclassified' %}
    <button class="category-btn selected" data-category="unclassified" type="button" onclick="selectCategory(this)">未分類</button>
    {% endif %}
  </div>
```

> 「未分類」只給有舊資料時顯示，使用者切到其他類別後就不會再回來（新選擇有 4 個）。

- [ ] **Step 3: 改 JS 變數宣告 (line 195)**

找到：
```javascript
var currentPriority = {{ (note.priority if note else 'medium')|tojson }};
```

改為：
```javascript
var currentCategory = {{ (note.category if note else (default_category or 'unclassified'))|tojson }};
```

- [ ] **Step 4: 改 selectPriority() → selectCategory() (line 314-319)**

找到：
```javascript
function selectPriority(btn) {
  var all = document.querySelectorAll('.priority-btn');
  for (var i = 0; i < all.length; i++) all[i].classList.remove('selected');
  btn.classList.add('selected');
  currentPriority = btn.getAttribute('data-priority');
}
```

改為：
```javascript
function selectCategory(btn) {
  var all = document.querySelectorAll('.category-btn');
  for (var i = 0; i < all.length; i++) all[i].classList.remove('selected');
  btn.classList.add('selected');
  currentCategory = btn.getAttribute('data-category');
}
```

- [ ] **Step 5: 改 save payload (line 338, 376)**

line 338 找到：
```javascript
  var body = { title: title, content: content, store: store, status: currentStatus, priority: currentPriority };
```
改為：
```javascript
  var body = { title: title, content: content, store: store, status: currentStatus, category: currentCategory };
```

line 376 找到：
```javascript
      status: currentStatus, priority: currentPriority, note_type: EDITOR_TYPE
```
改為：
```javascript
      status: currentStatus, category: currentCategory, note_type: EDITOR_TYPE
```

- [ ] **Step 6: 跑完整 test suite 驗證沒有 regression**

Run: `cd app_unified && pytest -x 2>&1 | tail -30`
Expected: 所有 test_category.py + 既有能跑的測試都 PASS。`test_auth.py` / `test_notes.py` 的 22 個 error 是先前已知（memory 裡說過的 /auth/login stub 問題）— 這些不該因本次改動變動。

- [ ] **Step 7: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/notes/editor.html
git commit -m "feat(notes): editor swaps priority selector for category selector

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 改 `index.html` — 新按鈕 modal + 新篩選 tabs + badge + pagination

**Files:**
- Modify: `app_unified/templates/notes/index.html`

- [ ] **Step 1: 改新增按鈕為 modal 觸發 (line 60-62)**

找到：
```html
  <div style="display:flex;gap:.5rem;margin-left:auto;flex-wrap:wrap;justify-content:flex-end;">
    <a href="{{ url_for('notes.new_note') }}" class="btn btn-primary">+ 新增筆記</a>
    <a href="{{ url_for('notes.new_note', type='checklist') }}" class="btn btn-primary" style="background:#27ae60;">☑ 新增確認表單</a>
  </div>
```

改為：
```html
  <div style="display:flex;gap:.5rem;margin-left:auto;flex-wrap:wrap;justify-content:flex-end;">
    <button type="button" class="btn btn-primary" onclick="openCategoryModal('note')">+ 新增筆記</button>
    <button type="button" class="btn btn-primary" style="background:#27ae60;" onclick="openCategoryModal('checklist')">☑ 新增確認表單</button>
  </div>
```

- [ ] **Step 2: 把舊 priority tabs 換成新 category tabs (line 112-120)**

找到：
```html
{% set priority_labels = {'':'全部優先權','high':'高','medium':'中','low':'低'} %}
<div class="range-tabs">
  <span style="font-size:.85rem;color:#666;align-self:center;margin-right:.25rem;">優先權：</span>
  {% for val, label in priority_labels.items() %}
  <a href="{{ url_for('notes.index', store=current_store, priority=val) }}"
     class="store-tab {% if active_filter == 'priority' and current_priority == val %}active{% endif %}"
     style="{% if active_filter == 'priority' and current_priority == val %}background:#e74c3c;color:#fff;{% endif %}">{{ label }}</a>
  {% endfor %}
</div>
```

替換為：
```html
{% set cat_tab_labels = [('','全部'),('employee','員工'),('customer','客人'),('misc','雜事'),('personal','個人'),('unclassified','未分類')] %}
<div class="range-tabs">
  <span style="font-size:.85rem;color:#666;align-self:center;margin-right:.25rem;">種類：</span>
  {% for val, label in cat_tab_labels %}
  <a href="{{ url_for('notes.index', store=current_store, category=val) }}"
     class="store-tab {% if current_category == val %}active{% endif %}"
     style="{% if current_category == val and val %}background:#3498db;color:#fff;{% endif %}">{{ label }}</a>
  {% endfor %}
</div>
```

> 點 category tab 的 href **不帶** `range`、`status`、`q` → cascade reset：時間/狀態/關鍵字會回到預設（range 預設 today、status 空 = 全部）

- [ ] **Step 3: 改 range tabs (line 121-129) — 保留 category 但 reset status**

找到：
```html
<div class="range-tabs">
  <span style="font-size:.85rem;color:#666;align-self:center;margin-right:.25rem;">日期範圍：</span>
  {% set range_labels = [('today','今天'),('3d','近3天'),('7d','近7天'),('30d','近月')] %}
  {% for val, label in range_labels %}
  <a href="{{ url_for('notes.index', store=current_store, range=val) }}"
     class="store-tab {% if active_filter == 'range' and current_range == val %}active{% endif %}"
     style="{% if active_filter == 'range' and current_range == val %}background:#2c3e50;color:#fff;{% endif %}">{{ label }}</a>
  {% endfor %}
</div>
```

替換為：
```html
<div class="range-tabs">
  <span style="font-size:.85rem;color:#666;align-self:center;margin-right:.25rem;">時間：</span>
  {% set range_labels = [('today','今天'),('3d','近3天'),('7d','近7天'),('30d','近月')] %}
  {% for val, label in range_labels %}
  <a href="{{ url_for('notes.index', store=current_store, category=current_category, range=val) }}"
     class="store-tab {% if current_range == val %}active{% endif %}"
     style="{% if current_range == val %}background:#2c3e50;color:#fff;{% endif %}">{{ label }}</a>
  {% endfor %}
</div>
```

> href 帶 category=current_category 維持；不帶 status → status reset。

- [ ] **Step 4: 改 status tabs (line 130-141) — 維持 category+range**

找到：
```html
{% set status_labels = {'':'全部狀態','pending':'待處理','in_progress':'待討論','resolved':'已解決'} %}
<div class="status-tabs">
  {% for val, label in status_labels.items() %}
  <a href="{{ url_for('notes.index', store=current_store, status=val) }}"
     class="store-tab {% if active_filter == 'status' and current_status == val %}active{% endif %}"
     style="{% if val == '' and active_filter == 'status' and current_status == val %}background:#2c3e50;color:#fff;
            {% elif val == 'pending' and active_filter == 'status' and current_status == val %}background:#95a5a6;color:#fff;
            {% elif val == 'in_progress' and active_filter == 'status' and current_status == val %}background:#3498db;color:#fff;
            {% elif val == 'resolved' and active_filter == 'status' and current_status == val %}background:#27ae60;color:#fff;
            {% endif %}">{{ label }}</a>
  {% endfor %}
</div>
```

替換為：
```html
{% set status_labels = [('','全部'),('pending','待處理'),('in_progress','待討論'),('resolved','已解決')] %}
<div class="status-tabs">
  <span style="font-size:.85rem;color:#666;align-self:center;margin-right:.25rem;">狀態：</span>
  {% for val, label in status_labels %}
  <a href="{{ url_for('notes.index', store=current_store, category=current_category, range=current_range, status=val) }}"
     class="store-tab {% if current_status == val %}active{% endif %}"
     style="{% if current_status == val %}
              {% if val == '' %}background:#2c3e50;color:#fff;
              {% elif val == 'pending' %}background:#95a5a6;color:#fff;
              {% elif val == 'in_progress' %}background:#3498db;color:#fff;
              {% elif val == 'resolved' %}background:#27ae60;color:#fff;
              {% endif %}
            {% endif %}">{{ label }}</a>
  {% endfor %}
</div>
```

- [ ] **Step 5: 改 badge (line 160-161)**

找到：
```html
      {% set plabels = {'high':'高','medium':'中','low':'低'} %}
      <span class="priority-badge priority-{{ note.priority or 'medium' }}">{{ plabels.get(note.priority or 'medium', note.priority) }}</span>
```

替換為（使用 template 傳進來的 CATEGORY_LABELS，變數名是 `category_labels`）：
```html
      <span class="category-badge category-{{ note.category or 'unclassified' }}">{{ category_labels.get(note.category or 'unclassified', note.category) }}</span>
```

> 對應 CSS 可以加到 `base.html` 或 `index.html` 的 style block。簡單起見加 inline style fallback：

在 index.html 頂部（或 extends block 裡）加 style（如果沒 style block 就 inline 在 badge 上）：

```html
<style>
.category-badge { display:inline-block; padding:.1rem .5rem; border-radius:10px; font-size:.78rem; font-weight:600; color:#fff; }
.category-badge.category-employee    { background:#3498db; }
.category-badge.category-customer    { background:#e67e22; }
.category-badge.category-misc        { background:#95a5a6; }
.category-badge.category-personal    { background:#9b59b6; }
.category-badge.category-unclassified{ background:#bdc3c7; color:#555; }
</style>
```

> 實務上這 style block 要放在 index.html 合適位置，若 template 用 `extends`，請放到 `{% block extra_head %}` 裡（若該 block 存在）。

- [ ] **Step 6: 改 pagination href (line 172-180 和後續 pagination links)**

找到所有出現 `priority=current_priority` 的地方並替換成 `category=current_category`。具體片段：

```html
  <a href="{{ url_for('notes.index', store=current_store, status=current_status, range=current_range, priority=current_priority, q=current_keyword, page=pagination.prev_num) }}"
```
改為：
```html
  <a href="{{ url_for('notes.index', store=current_store, status=current_status, range=current_range, category=current_category, q=current_keyword, page=pagination.prev_num) }}"
```

至少有 2 處類似（上一頁 + 下一頁 + 頁數連結），全部替換。

- [ ] **Step 7: 加 `<dialog>` modal 和 JS 到 index.html 底部**

在 `{% endblock %}` 前加：

```html
<dialog id="category-modal" style="padding:1.5rem; border-radius:12px; border:none; box-shadow:0 10px 40px rgba(0,0,0,0.15); max-width:360px;">
  <h3 style="margin:0 0 1rem 0;">選擇類別</h3>
  <div style="display:grid; grid-template-columns:1fr 1fr; gap:.6rem;">
    <button type="button" class="btn btn-primary" onclick="chooseCategoryAndGo('employee')" style="background:#3498db;">員工</button>
    <button type="button" class="btn btn-primary" onclick="chooseCategoryAndGo('customer')" style="background:#e67e22;">客人</button>
    <button type="button" class="btn btn-primary" onclick="chooseCategoryAndGo('misc')" style="background:#95a5a6;">雜事</button>
    <button type="button" class="btn btn-primary" onclick="chooseCategoryAndGo('personal')" style="background:#9b59b6;">個人</button>
  </div>
  <div style="text-align:right; margin-top:1rem;">
    <button type="button" class="btn btn-secondary" onclick="closeCategoryModal()" style="font-size:.85rem;">取消</button>
  </div>
</dialog>

<script>
var _pendingType = 'note';
function openCategoryModal(type) {
  _pendingType = type;
  var dlg = document.getElementById('category-modal');
  if (dlg && dlg.showModal) dlg.showModal();
}
function closeCategoryModal() {
  var dlg = document.getElementById('category-modal');
  if (dlg && dlg.close) dlg.close();
}
function chooseCategoryAndGo(cat) {
  var qs = '?type=' + encodeURIComponent(_pendingType) + '&category=' + encodeURIComponent(cat);
  window.location.href = '/notes/new' + qs;
}
// 點外面關閉
document.addEventListener('DOMContentLoaded', function() {
  var dlg = document.getElementById('category-modal');
  if (!dlg) return;
  dlg.addEventListener('click', function(e) {
    if (e.target === dlg) closeCategoryModal();
  });
});
</script>
```

- [ ] **Step 8: 跑完整 test suite（確保 filter test 仍 pass）**

Run: `cd app_unified && pytest tests/test_category.py -v`
Expected: 所有 test_category.py PASS

Run: `cd app_unified && pytest tests/test_crypto.py tests/test_admin_rename.py tests/test_paragraph_diff.py tests/test_cleanup.py -v`
Expected: 既有測試不 regress

- [ ] **Step 9: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/notes/index.html
git commit -m "feat(notes): index.html new category modal + filter tabs + cascade hrefs

- 新增按鈕觸發類別選擇彈窗 (<dialog>)
- 篩選 tabs 順序改為：關鍵字 → 種類 → 時間 → 狀態
- cascade 透過 href 帶/不帶參數實作（種類 href 不帶 range/status；時間帶 category 不帶 status；狀態帶 category+range）
- badge 改 category

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 同步改 `notes/ws.py`

**Files:**
- Modify: `app_unified/notes/ws.py` (line 6, 44, 56-57, 61-62, 87, 103, 111, 148-151)

WebSocket handler 平行改。保留現有「互斥篩選」邏輯（ws 的 _list_notes 不是主 UI 用，不做 AND 組合重構，降低 scope）。

- [ ] **Step 1: 改 import (line 6)**

找到：
```python
from models import Note, Store, NoteLog, User, STATUS_CHOICES, PRIORITY_CHOICES
```
改為：
```python
from models import Note, Store, NoteLog, User, STATUS_CHOICES, CATEGORY_CHOICES
```

- [ ] **Step 2: 改 `_list_notes()` (line 44, 56-57, 61-62, 87)**

line 44 找到：
```python
        priority_filter = data.get('priority', '')
```
改為：
```python
        category_filter = data.get('category', '')
```

line 56 的註解更新：
```python
        # 互斥篩選：關鍵字 > 優先權 > 狀態 > 日期範圍
```
改為：
```python
        # 互斥篩選：關鍵字 > 種類 > 狀態 > 日期範圍
```

line 57 找到：
```python
        from models import PRIORITY_CHOICES
```
改為：
```python
        from models import CATEGORY_CHOICES
```

line 61-62 找到：
```python
        elif priority_filter and priority_filter in PRIORITY_CHOICES:
            query = query.filter_by(priority=priority_filter)
```
改為：
```python
        elif category_filter and category_filter in CATEGORY_CHOICES:
            query = query.filter_by(category=category_filter)
```

line 87 找到：
```python
            'priority': n.priority or 'medium',
```
改為：
```python
            'category': n.category or 'unclassified',
```

- [ ] **Step 3: 改 `_create_note()` (line 103, 111)**

line 103 找到：
```python
        priority = data.get('priority') if data.get('priority') in PRIORITY_CHOICES else 'medium'
```
改為：
```python
        category = data.get('category') if data.get('category') in CATEGORY_CHOICES else 'unclassified'
```

line 111 找到：
```python
            store=store, status=status, priority=priority,
```
改為：
```python
            store=store, status=status, category=category,
```

- [ ] **Step 4: 改 `_update_note()` (line 148-151)**

找到：
```python
        if 'priority' in data and data['priority'] in PRIORITY_CHOICES:
            if data['priority'] != note.priority:
                diff_parts.append(f"優先度: {note.priority} → {data['priority']}")
            note.priority = data['priority']
```
改為：
```python
        if 'category' in data and data['category'] in CATEGORY_CHOICES:
            if data['category'] != note.category:
                diff_parts.append(f"類別: {note.category} → {data['category']}")
            note.category = data['category']
```

- [ ] **Step 5: 用 grep 確認 ws.py 已無 priority 殘留**

Run: `cd app_unified && grep -n "priority\|PRIORITY_CHOICES" notes/ws.py`
Expected: 無輸出

- [ ] **Step 6: 啟動 app 快速 smoke test**

Run: `cd app_unified && python -c "from app import create_app; app = create_app(); print('app imports OK')"`
Expected: `app imports OK`，無 ImportError

- [ ] **Step 7: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/ws.py
git commit -m "feat(notes): ws.py swap priority for category (parallel to routes.py)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 寫 Migration 006

**Files:**
- Create: `app_unified/migrations/versions/006_replace_priority_with_category.py`

沿用 005 的 psycopg2 模式。

- [ ] **Step 1: 建立 migration 檔**

```python
"""Replace Note.priority with Note.category

Revision ID: 006
Revises: 005
Create Date: 2026-04-20
"""
revision = '006'
down_revision = '005'

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def upgrade():
    url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(
        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'unclassified'"
    )
    cur.execute("ALTER TABLE notes DROP COLUMN IF EXISTS priority")

    cur.close()
    conn.close()
    print("Migration 006 done.")


if __name__ == "__main__":
    upgrade()
```

- [ ] **Step 2: 本地驗證（可選，需要本機 PG container）**

若有 docker：

```bash
# 起一個 PG container
docker run --rm -d --name pg-test -e POSTGRES_PASSWORD=test -p 5432:5432 postgres:15
sleep 3
# 造一個有 priority column 的假 table
docker exec -i pg-test psql -U postgres <<EOF
CREATE TABLE notes (id SERIAL PRIMARY KEY, priority TEXT DEFAULT 'medium');
INSERT INTO notes DEFAULT VALUES;
EOF

# 跑 migration
cd app_unified
DATABASE_URL='postgresql://postgres:test@localhost:5432/postgres' \
  python migrations/versions/006_replace_priority_with_category.py

# 驗證
docker exec -i pg-test psql -U postgres -c "\d notes"
# 應該看到 category 存在，priority 不存在

# 再跑一次驗證 idempotent
DATABASE_URL='postgresql://postgres:test@localhost:5432/postgres' \
  python migrations/versions/006_replace_priority_with_category.py
# 應該正常跑完不報錯

docker stop pg-test
```

如果沒 docker 就跳過此步，相信 `IF NOT EXISTS` / `IF EXISTS` 的保證。

- [ ] **Step 3: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/migrations/versions/006_replace_priority_with_category.py
git commit -m "feat(migration): 006 replace priority with category

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: 全測試驗證 + 清理 `PRIORITY_CHOICES` 殘留

**Files:**
- 可能 Modify: 任何 grep 仍出現 `PRIORITY_CHOICES` 或殘留 `priority` 的地方

- [ ] **Step 1: 全倉 grep 檢查 priority 殘留**

Run: `cd app_unified && grep -rn "priority\|PRIORITY_CHOICES" --include="*.py" --include="*.html" --include="*.js"`
Expected: 只剩：
- 完全無關的字串（例如跟優先順序毫無關係的變數名）
- 歷史 diff / note log 相關字串（不可動）
- test_category.py 裡的註解

如果發現遺漏就回頭補。

- [ ] **Step 2: 跑新測試 + 既有穩定測試**

Run:
```bash
cd app_unified && pytest tests/test_category.py tests/test_crypto.py tests/test_admin_rename.py tests/test_paragraph_diff.py tests/test_cleanup.py -v
```

Expected: 全綠，test_category.py 應 14+ case 全 pass

- [ ] **Step 3: 啟動 app 確認能 serve**

Run: `cd app_unified && python -c "from app import create_app; app = create_app(); print('OK'); print([str(r) for r in app.url_map.iter_rules() if 'notes' in str(r)][:10])"`
Expected: `OK` + 看到 /notes/ 相關 routes

- [ ] **Step 4: 手動 smoke test（本機 dev）**

啟動 dev server（若有的話）：
```bash
cd app_unified && FLASK_APP=app.py flask run --port 5000
```

打開 `http://localhost:5000/notes/`：
- [ ] 看到新篩選器（種類/時間/狀態 tab）
- [ ] 點「+ 新增筆記」→ 跳出彈窗 4 選項
- [ ] 選「員工」→ editor 標題顯示「M/D 員工」
- [ ] 選「個人」→ editor 標題顯示「M/D {username}筆記」
- [ ] editor 裡類別 pill 切換正常
- [ ] 儲存後回列表顯示 category badge

- [ ] **Step 5: 最終 commit（若有殘留清理）**

```bash
cd /home/hirain0126/projects/webapp
git add -u
git commit -m "chore(notes): final cleanup of priority remnants

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

若無殘留，跳過此 commit。

---

## 部署檢查清單（實作完成後）

- [ ] 所有 task 測試通過
- [ ] `git log` 看到 ~9 個 commit，每個有清楚訊息
- [ ] 未 push 到 remote（等 user 指示）
- [ ] 部署步驟（user 確認後）：
  1. push 到 main
  2. Zeabur 自動部署完
  3. 進 Zeabur container：`cd /app && python migrations/versions/006_replace_priority_with_category.py`
  4. 手動 smoke test 走完 9 個項目
  5. 更新 memory project_webapp.md

---

## 回滾計畫

若部署後發現問題：

1. **程式層 rollback**：`git revert` 本次所有 commit，Zeabur 自動部署回舊版
2. **DB rollback**（若需要）：
   ```sql
   ALTER TABLE notes ADD COLUMN IF NOT EXISTS priority TEXT NOT NULL DEFAULT 'medium';
   ALTER TABLE notes DROP COLUMN IF EXISTS category;
   ```
3. **資料損失**：priority 原值已捨棄（使用者選 A），rollback 後所有 note 的 priority 回到 `'medium'`

---

**Plan Status**: ready for execution
