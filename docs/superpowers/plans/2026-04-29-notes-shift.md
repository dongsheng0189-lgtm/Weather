# Notes 班別 (shift) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在店務筆記新增流程加入「班別」選擇 (A/B/N/不需要)，並在列表頁加入班別篩選列。

**Architecture:** 在 `Note` 加 `shift VARCHAR(1) NULL` 欄位 (NULL = 不需要)，由 `new_note()` 依 (category, shift) 組裝標題；列表頁加一條 cascade-aware filter 列；新增 modal 改成單一頁兩列 + 確認按鈕。

**Tech Stack:** Flask + SQLAlchemy + PostgreSQL (生產) / SQLite (測試) + 既有 pytest fixtures。

**Spec:** `docs/superpowers/specs/2026-04-29-notes-shift-design.md`

---

## File Structure

**Create:**
- `app_unified/migrations/versions/007_add_note_shift.py` — Postgres ALTER TABLE
- `app_unified/tests/test_shift.py` — title generation + filter SQL + cascade behavior

**Modify:**
- `app_unified/models.py` — `Note.shift` column + `SHIFT_CHOICES`
- `app_unified/notes/routes.py` — title generation, POST create, GET index filter, list_notes API
- `app_unified/notes/ws.py` — list/create/get serialization 帶 shift；ws filter 帶 shift
- `app_unified/templates/notes/index.html` — modal、filter row、cascade URL
- `app_unified/templates/notes/editor.html` — 暫時不用改（標題已由後端組好）

---

## Task 1: DB schema + model 常數

**Files:**
- Modify: `app_unified/models.py:8-17` (constants), `:92-117` (Note class)
- Create: `app_unified/migrations/versions/007_add_note_shift.py`
- Test: `app_unified/tests/test_shift.py`

- [ ] **Step 1: 寫第一個 failing test (model 預設 shift = None)**

Create `app_unified/tests/test_shift.py`:

```python
"""Tests for Note.shift — 班別 A/B/N + 標題組裝 + filter"""
import uuid
import pytest
from models import User, Note, SHIFT_CHOICES
from extensions import db


def _mk_user(db_session, username=None, store="B"):
    u = User(
        username=username or f"u_{uuid.uuid4().hex[:6]}",
        role="user",
        store=store,
        is_active=True,
    )
    u.set_password("1234")
    db_session.add(u)
    db_session.commit()
    return u


def _mk_note(db_session, user, **kwargs):
    n = Note(
        user_id=user.id,
        author_name=user.username,
        title=kwargs.get("title", "t"),
        content=kwargs.get("content", "c"),
        store=kwargs.get("store", user.store),
        status=kwargs.get("status", "pending"),
        category=kwargs.get("category", "unclassified"),
        note_type=kwargs.get("note_type", "note"),
        shift=kwargs.get("shift"),
    )
    db_session.add(n)
    db_session.commit()
    return n


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_shift_default_is_none(app, db_session):
    u = _mk_user(db_session)
    n = Note(user_id=u.id, author_name=u.username, title="x", content="y", store=u.store)
    db_session.add(n)
    db_session.commit()
    assert n.shift is None


def test_shift_choices_constant():
    assert SHIFT_CHOICES == ("A", "B", "N")
```

- [ ] **Step 2: Run tests，確認 fail**

```bash
cd app_unified && source ../venv/bin/activate
python -m pytest tests/test_shift.py -v
```
Expected: ImportError `cannot import name 'SHIFT_CHOICES' from 'models'`

- [ ] **Step 3: 改 `models.py` 加常數 + 欄位**

`app_unified/models.py:17` 後面加：

```python
SHIFT_CHOICES = ("A", "B", "N")  # NULL 表「不需要」
SHIFT_LABELS = {"A": "A", "B": "B", "N": "N"}
```

`app_unified/models.py:105` (status, category 之後) 加：

```python
    shift = db.Column(db.String(1), nullable=True)
```

- [ ] **Step 4: Run tests，確認 pass**

```bash
python -m pytest tests/test_shift.py -v
```
Expected: 2 passed

- [ ] **Step 5: 寫 migration 007**

Create `app_unified/migrations/versions/007_add_note_shift.py`:

```python
"""Add Note.shift column

Revision ID: 007
Revises: 006
Create Date: 2026-04-29
"""
revision = '007'
down_revision = '006'

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def upgrade():
    url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS shift VARCHAR(1) NULL")

    cur.close()
    conn.close()
    print("Migration 007 done.")


if __name__ == "__main__":
    upgrade()
```

- [ ] **Step 6: 確認既有測試沒退步**

```bash
python -m pytest tests/ --ignore=tests/test_auth.py --ignore=tests/test_notes.py --ignore=tests/test_face.py -q
```
Expected: 73 passed (71 既有 + 2 新增)

- [ ] **Step 7: Commit**

```bash
git add app_unified/models.py app_unified/migrations/versions/007_add_note_shift.py app_unified/tests/test_shift.py
git commit -m "feat(notes): add Note.shift column + migration 007"
```

---

## Task 2: 標題組裝 (`new_note` 加 shift 邏輯)

**Files:**
- Modify: `app_unified/notes/routes.py:6` (import), `:200-224` (`new_note`)
- Test: `app_unified/tests/test_shift.py`

- [ ] **Step 1: 加 failing tests for title generation**

Append to `app_unified/tests/test_shift.py`:

```python
def test_title_employee_shift_a(app, client, db_session):
    u = _mk_user(db_session, username="hirain")
    _login(client, u)
    res = client.get("/notes/new?type=note&category=employee&shift=A")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    # default_title 經模板渲染後出現在 input value
    assert "A員工" in body


def test_title_employee_no_shift(app, client, db_session):
    u = _mk_user(db_session, username="hirain")
    _login(client, u)
    res = client.get("/notes/new?type=note&category=employee&shift=none")
    body = res.get_data(as_text=True)
    assert "員工" in body
    # 不應有 A員工 / B員工 / N員工
    assert "A員工" not in body and "B員工" not in body and "N員工" not in body


def test_title_personal_shift_b(app, client, db_session):
    u = _mk_user(db_session, username="hirain")
    _login(client, u)
    res = client.get("/notes/new?type=note&category=personal&shift=B")
    body = res.get_data(as_text=True)
    assert "B hirain筆記" in body


def test_title_personal_no_shift(app, client, db_session):
    u = _mk_user(db_session, username="hirain")
    _login(client, u)
    res = client.get("/notes/new?type=note&category=personal&shift=none")
    body = res.get_data(as_text=True)
    assert "hirain筆記" in body
    assert "A hirain筆記" not in body


def test_title_checklist_employee_shift_n(app, client, db_session):
    u = _mk_user(db_session, username="hirain")
    _login(client, u)
    res = client.get("/notes/new?type=checklist&category=employee&shift=N")
    body = res.get_data(as_text=True)
    assert "N員工" in body


def test_title_invalid_shift_falls_back_to_none(app, client, db_session):
    u = _mk_user(db_session, username="hirain")
    _login(client, u)
    res = client.get("/notes/new?type=note&category=employee&shift=XYZ")
    body = res.get_data(as_text=True)
    # 不合法值應回退成「不需要」
    assert "A員工" not in body
    assert "員工" in body
```

- [ ] **Step 2: Run tests，確認 fail**

```bash
python -m pytest tests/test_shift.py -v
```
Expected: 6 new tests fail (current title doesn't include shift letter)

- [ ] **Step 3: 改 `notes/routes.py:6` import**

```python
from models import Note, Store, NoteLog, ChecklistItem, STATUS_CHOICES, CATEGORY_CHOICES, CATEGORY_LABELS, CATEGORY_CREATABLE, SHIFT_CHOICES
```

- [ ] **Step 4: 改 `notes/routes.py:200-224` `new_note()`**

替換整個 function body：

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
    # shift: 'A'/'B'/'N' or 'none' (= NULL)；其他值一律回退成 None
    shift_param = request.args.get("shift", "none")
    shift = shift_param if shift_param in SHIFT_CHOICES else None

    today = datetime.now(_TW).strftime("%-m/%-d")
    type_label = "確認表單" if note_type == "checklist" else "筆記"
    shift_prefix = f"{shift} " if shift else ""
    if category in ("personal", "unclassified"):
        # 個人/未分類：班別放 username 前
        default_title = f"{today} {shift_prefix}{current_user.username}{type_label}"
    else:
        # 員工/客人/雜事：班別緊貼類別 label，不加空格 (例如 "4/29 A員工")
        shift_glue = f"{shift}" if shift else ""
        default_title = f"{today} {shift_glue}{CATEGORY_LABELS[category]}"

    return render_template("notes/editor.html", note=None, stores=stores,
                           status_choices=STATUS_CHOICES,
                           category_choices=CATEGORY_CHOICES,
                           category_labels=CATEGORY_LABELS,
                           default_title=default_title,
                           note_type=note_type,
                           default_category=category,
                           default_shift=shift)
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_shift.py -v
```
Expected: all 8 pass

- [ ] **Step 6: Commit**

```bash
git add app_unified/notes/routes.py app_unified/tests/test_shift.py
git commit -m "feat(notes): generate title with shift prefix in new_note()"
```

---

## Task 3: 建立筆記時寫入 shift (POST API + WS)

**Files:**
- Modify: `app_unified/notes/routes.py:276-307` (`create_note` POST)
- Modify: `app_unified/notes/ws.py:93-116` (`_create_note`)
- Test: `app_unified/tests/test_shift.py`

- [ ] **Step 1: 加 failing test for POST create with shift**

Append to `tests/test_shift.py`:

```python
def test_create_note_persists_shift(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.post("/notes/api", json={
        "title": "test",
        "content": "x",
        "category": "employee",
        "shift": "A",
    })
    assert res.status_code == 201
    note_id = res.get_json()["id"]
    n = Note.query.get(note_id)
    assert n.shift == "A"


def test_create_note_no_shift_keeps_null(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.post("/notes/api", json={
        "title": "test",
        "content": "x",
        "category": "employee",
    })
    note_id = res.get_json()["id"]
    n = Note.query.get(note_id)
    assert n.shift is None


def test_create_note_invalid_shift_keeps_null(app, client, db_session):
    u = _mk_user(db_session)
    _login(client, u)
    res = client.post("/notes/api", json={
        "title": "test",
        "content": "x",
        "category": "employee",
        "shift": "XYZ",
    })
    note_id = res.get_json()["id"]
    n = Note.query.get(note_id)
    assert n.shift is None
```

- [ ] **Step 2: Run tests，確認 fail**

```bash
python -m pytest tests/test_shift.py::test_create_note_persists_shift -v
```
Expected: AssertionError (shift not saved)

- [ ] **Step 3: 改 `notes/routes.py:276-307` `create_note()`**

在 `category = data.get("category") ...` 那行下面加：

```python
        shift = data.get("shift") if data.get("shift") in SHIFT_CHOICES else None
```

並在 `Note(...)` 建構子內加 `shift=shift,`：

```python
    note = Note(
        user_id=current_user.id,
        author_name=current_user.username,
        note_type=note_type,
        title=data.get("title", "未命名筆記"),
        content=data.get("content", ""),
        store=store,
        status=status,
        category=category,
        shift=shift,
        created_at=now,
        updated_at=now,
    )
```

- [ ] **Step 4: 改 `notes/ws.py:93-116` `_create_note()`**

在 `category = data.get('category') ...` 那行下面加：

```python
        from models import SHIFT_CHOICES
        shift = data.get('shift') if data.get('shift') in SHIFT_CHOICES else None
```

並在 `Note(...)` 建構子加 `shift=shift,`：

```python
        note = Note(
            user_id=current_user.id,
            author_name=current_user.username,
            note_type=note_type,
            title=data.get('title', '未命名筆記'),
            content=data.get('content', ''),
            store=store, status=status, category=category, shift=shift,
            created_at=now, updated_at=now,
        )
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_shift.py -v
```
Expected: 11 passed

- [ ] **Step 6: Commit**

```bash
git add app_unified/notes/routes.py app_unified/notes/ws.py app_unified/tests/test_shift.py
git commit -m "feat(notes): persist shift on note creation (POST + WS)"
```

---

## Task 4: 列表頁 shift filter (`index()`)

**Files:**
- Modify: `app_unified/notes/routes.py:124-197` (`index`)
- Test: `app_unified/tests/test_shift.py`

- [ ] **Step 1: 加 failing tests for shift filter**

Append to `tests/test_shift.py`:

```python
def test_index_shift_filter_a(app, client, db_session):
    u = _mk_user(db_session, store="B")
    _login(client, u)
    n_a = _mk_note(db_session, u, title="alpha-A", category="employee", shift="A")
    n_b = _mk_note(db_session, u, title="alpha-B", category="employee", shift="B")
    n_none = _mk_note(db_session, u, title="alpha-NONE", category="employee", shift=None)
    res = client.get("/notes/?store=B&category=employee&shift=A&range=30d")
    body = res.get_data(as_text=True)
    assert "alpha-A" in body
    assert "alpha-B" not in body
    assert "alpha-NONE" not in body


def test_index_shift_filter_none_default(app, client, db_session):
    u = _mk_user(db_session, store="B")
    _login(client, u)
    n_a = _mk_note(db_session, u, title="bravo-A", category="employee", shift="A")
    n_none = _mk_note(db_session, u, title="bravo-NONE", category="employee", shift=None)
    # 不帶 shift 參數 → 預設 'none'
    res = client.get("/notes/?store=B&category=employee&range=30d")
    body = res.get_data(as_text=True)
    assert "bravo-NONE" in body
    assert "bravo-A" not in body


def test_index_shift_filter_explicit_none(app, client, db_session):
    u = _mk_user(db_session, store="B")
    _login(client, u)
    n_a = _mk_note(db_session, u, title="charlie-A", category="employee", shift="A")
    n_none = _mk_note(db_session, u, title="charlie-NONE", category="employee", shift=None)
    res = client.get("/notes/?store=B&category=employee&shift=none&range=30d")
    body = res.get_data(as_text=True)
    assert "charlie-NONE" in body
    assert "charlie-A" not in body
```

- [ ] **Step 2: Run tests，確認 fail**

```bash
python -m pytest tests/test_shift.py::test_index_shift_filter_a -v
```
Expected: AssertionError (filter not applied)

- [ ] **Step 3: 改 `notes/routes.py:124-197` `index()`**

在 `category_filter = request.args.get("category", "")` 之後加一行：

```python
    shift_filter = request.args.get("shift", "none")  # default 'none'
```

在 `if category_filter in CATEGORY_CHOICES:` block 之後（同層級，AND 組合內）加：

```python
        # shift 篩選 (AND)：'none' = NULL；'A'/'B'/'N' = 對應值；其他 = 不過濾(防呆)
        if shift_filter == "none":
            query = query.filter(Note.shift.is_(None))
        elif shift_filter in SHIFT_CHOICES:
            query = query.filter_by(shift=shift_filter)
```

在 `render_template(...)` 的 context 字典加：

```python
                           current_shift=shift_filter,
                           shift_choices=SHIFT_CHOICES,
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_shift.py -v
```
Expected: 14 passed

- [ ] **Step 5: 全部 test 確認沒退步**

```bash
python -m pytest tests/ --ignore=tests/test_auth.py --ignore=tests/test_notes.py --ignore=tests/test_face.py -q
```
Expected: 85 passed (71 既有 + 14 新增)

- [ ] **Step 6: Commit**

```bash
git add app_unified/notes/routes.py app_unified/tests/test_shift.py
git commit -m "feat(notes): add shift filter in list page (default 'none')"
```

---

## Task 5: List API + WebSocket serialization 帶 shift

**Files:**
- Modify: `app_unified/notes/routes.py:227-273` (`list_notes`)
- Modify: `app_unified/notes/ws.py:40-91` (`_list_notes`), `:193-214` (`_get_note`)

- [ ] **Step 1: 改 `notes/routes.py:227-273` `list_notes` API**

在 `category_filter = request.args.get("category", "")` 之後加：

```python
    shift_filter = request.args.get("shift", "")  # API 不強制 default，未帶則不過濾
```

在 `elif category_filter and ...` 同層加：

```python
    elif shift_filter:
        if shift_filter == "none":
            query = query.filter(Note.shift.is_(None))
        elif shift_filter in SHIFT_CHOICES:
            query = query.filter_by(shift=shift_filter)
```

回傳 jsonify 的 dict 加 `"shift": n.shift,`：

```python
    return jsonify([{
        "id": n.id, "title": n.title, "content": n.content,
        "note_type": n.note_type or "note",
        "store": n.store, "status": n.status or "pending",
        "category": n.category or "unclassified",
        "shift": n.shift,
        "author": n.display_author,
        "created_at": n.created_at.isoformat() if n.created_at else "",
        "updated_at": n.updated_at.isoformat() if n.updated_at else "",
    } for n in notes])
```

- [ ] **Step 2: 改 `notes/ws.py:40-91` `_list_notes`**

在 import 區加 (函式內 from import 維持風格)，於 `_list_notes` 函式內：

```python
        category_filter = data.get('category', '')
        shift_filter = data.get('shift', '')  # 新增
```

在 `elif category_filter and category_filter in CATEGORY_CHOICES:` 後同層加：

```python
        elif shift_filter:
            from models import SHIFT_CHOICES
            if shift_filter == 'none':
                query = query.filter(Note.shift.is_(None))
            elif shift_filter in SHIFT_CHOICES:
                query = query.filter_by(shift=shift_filter)
```

emit 的 dict 加 `'shift': n.shift,`：

```python
        emit('r', {'op': 'ln', 'notes': [{
            'id': n.id, 'title': n.title, 'content': n.content,
            'note_type': n.note_type or 'note',
            'store': n.store, 'status': n.status or 'pending',
            'category': n.category or 'unclassified',
            'shift': n.shift,
            'author': n.display_author,
            'created_at': n.created_at.isoformat() if n.created_at else '',
            'updated_at': n.updated_at.isoformat() if n.updated_at else '',
        } for n in notes]})
```

- [ ] **Step 3: 改 `notes/ws.py:193-214` `_get_note`**

emit 的 note dict 加 `'shift': note.shift,`：

```python
        emit('r', {'op': 'gn', 'note': {
            'id': note.id, 'title': note.title, 'content': note.content,
            'store': note.store, 'status': note.status or 'pending',
            'category': note.category or 'unclassified',
            'shift': note.shift,
            'updated_by': updater,
            'created_at': note.created_at.isoformat() if note.created_at else '',
            'updated_at': note.updated_at.isoformat() if note.updated_at else '',
        }})
```

- [ ] **Step 4: 跑全部測試確認沒退步**

```bash
python -m pytest tests/ --ignore=tests/test_auth.py --ignore=tests/test_notes.py --ignore=tests/test_face.py -q
```
Expected: 85 passed

- [ ] **Step 5: Commit**

```bash
git add app_unified/notes/routes.py app_unified/notes/ws.py
git commit -m "feat(notes): serialize shift in list/get APIs (HTTP + WS)"
```

---

## Task 6: Modal UI 改成兩列 + 確認按鈕

**Files:**
- Modify: `app_unified/templates/notes/index.html:216-251` (modal + JS)

UI 改動，hand-verify。

- [ ] **Step 1: 替換 modal HTML (`templates/notes/index.html:216-227`)**

```html
<dialog id="category-modal" style="padding:1.5rem; border-radius:12px; border:none; box-shadow:0 10px 40px rgba(0,0,0,0.15); max-width:380px;">
  <h3 style="margin:0 0 .8rem 0;">選擇類別與班別</h3>
  <div style="margin-bottom:.4rem;font-size:.85rem;color:#666;">類別</div>
  <div style="display:grid; grid-template-columns:1fr 1fr; gap:.5rem; margin-bottom:.9rem;">
    <button type="button" class="cm-cat-btn" data-cat="employee" onclick="selectCategory('employee')" style="padding:.55rem;border:2px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;font-weight:600;">員工</button>
    <button type="button" class="cm-cat-btn" data-cat="customer" onclick="selectCategory('customer')" style="padding:.55rem;border:2px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;font-weight:600;">客人</button>
    <button type="button" class="cm-cat-btn" data-cat="misc" onclick="selectCategory('misc')" style="padding:.55rem;border:2px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;font-weight:600;">雜事</button>
    <button type="button" class="cm-cat-btn" data-cat="personal" onclick="selectCategory('personal')" style="padding:.55rem;border:2px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;font-weight:600;">個人</button>
  </div>
  <div style="margin-bottom:.4rem;font-size:.85rem;color:#666;">班別</div>
  <div style="display:grid; grid-template-columns:1fr 1fr 1fr 1fr; gap:.5rem; margin-bottom:1rem;">
    <button type="button" class="cm-shift-btn" data-shift="none" onclick="selectShift('none')" style="padding:.55rem;border:2px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;font-weight:600;font-size:.85rem;">不需要</button>
    <button type="button" class="cm-shift-btn" data-shift="A" onclick="selectShift('A')" style="padding:.55rem;border:2px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;font-weight:600;">A</button>
    <button type="button" class="cm-shift-btn" data-shift="B" onclick="selectShift('B')" style="padding:.55rem;border:2px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;font-weight:600;">B</button>
    <button type="button" class="cm-shift-btn" data-shift="N" onclick="selectShift('N')" style="padding:.55rem;border:2px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;font-weight:600;">N</button>
  </div>
  <div style="display:flex; justify-content:flex-end; gap:.5rem;">
    <button type="button" class="btn btn-secondary" onclick="closeCategoryModal()" style="font-size:.85rem;">取消</button>
    <button type="button" id="cm-confirm" class="btn btn-primary" disabled onclick="confirmCreate()" style="font-size:.85rem;opacity:.5;">確認</button>
  </div>
</dialog>
```

- [ ] **Step 2: 替換 modal JS (`templates/notes/index.html:229-251`)**

```html
<script>
var _pendingType = 'note';
var _selCategory = null;
var _selShift = null;

var CAT_COLORS = {
  employee: '#3498db',
  customer: '#e67e22',
  misc:     '#95a5a6',
  personal: '#9b59b6',
};

function _highlightCat() {
  document.querySelectorAll('.cm-cat-btn').forEach(function(b){
    var match = (b.dataset.cat === _selCategory);
    b.style.background = match ? (CAT_COLORS[b.dataset.cat] || '#3498db') : '#fff';
    b.style.color = match ? '#fff' : '#333';
    b.style.borderColor = match ? (CAT_COLORS[b.dataset.cat] || '#3498db') : '#ddd';
  });
}
function _highlightShift() {
  document.querySelectorAll('.cm-shift-btn').forEach(function(b){
    var match = (b.dataset.shift === _selShift);
    b.style.background = match ? '#2c3e50' : '#fff';
    b.style.color = match ? '#fff' : '#333';
    b.style.borderColor = match ? '#2c3e50' : '#ddd';
  });
}
function _refreshConfirm() {
  var btn = document.getElementById('cm-confirm');
  if (!btn) return;
  var ready = (_selCategory !== null && _selShift !== null);
  btn.disabled = !ready;
  btn.style.opacity = ready ? '1' : '.5';
}

function selectCategory(cat) {
  _selCategory = cat;
  _highlightCat();
  _refreshConfirm();
}
function selectShift(s) {
  _selShift = s;
  _highlightShift();
  _refreshConfirm();
}

function openCategoryModal(type) {
  _pendingType = type;
  // 還原上次選擇 (cat 無記憶 → null；shift 無記憶 → 'none')
  try {
    _selCategory = localStorage.getItem('notes_new_category');
    if (_selCategory && ['employee','customer','misc','personal'].indexOf(_selCategory) < 0) _selCategory = null;
  } catch (e) { _selCategory = null; }
  try {
    _selShift = localStorage.getItem('notes_new_shift') || 'none';
    if (['none','A','B','N'].indexOf(_selShift) < 0) _selShift = 'none';
  } catch (e) { _selShift = 'none'; }
  _highlightCat();
  _highlightShift();
  _refreshConfirm();
  var dlg = document.getElementById('category-modal');
  if (dlg && dlg.showModal) dlg.showModal();
}
function closeCategoryModal() {
  var dlg = document.getElementById('category-modal');
  if (dlg && dlg.close) dlg.close();
}
function confirmCreate() {
  if (!_selCategory || !_selShift) return;
  try {
    localStorage.setItem('notes_new_category', _selCategory);
    localStorage.setItem('notes_new_shift', _selShift);
  } catch (e) { /* private mode */ }
  var qs = '?type=' + encodeURIComponent(_pendingType) +
           '&category=' + encodeURIComponent(_selCategory) +
           '&shift=' + encodeURIComponent(_selShift);
  window.location.href = '/notes/new' + qs;
}

document.addEventListener('DOMContentLoaded', function() {
  var dlg = document.getElementById('category-modal');
  if (!dlg) return;
  dlg.addEventListener('click', function(e) {
    if (e.target === dlg) closeCategoryModal();
  });
});
</script>
```

- [ ] **Step 3: 啟動 dev server，hand-verify**

```bash
cd /home/hirain0126/projects/webapp && bash start_app1.sh &
```

開瀏覽器到 `http://localhost:5000/notes/`，登入後：

- 點「+ 新增筆記」→ modal 開啟，類別未選、班別預設「不需要」高亮
- 確認按鈕灰色不能按
- 點「員工」→ 員工高亮藍色
- 確認按鈕變藍可按
- 按「確認」→ 跳到 `/notes/new?type=note&category=employee&shift=none`，editor 標題 `4/29 員工`
- 回上頁，再點「+ 新增筆記」→ 上次選的「員工」+「不需要」高亮
- 點「A」→ A 變藍
- 按「確認」→ 跳到 editor，標題 `4/29 A員工`
- 同樣流程測「☑ 新增確認表單」→ checklist `?type=checklist`

- [ ] **Step 4: Commit**

```bash
git add app_unified/templates/notes/index.html
git commit -m "feat(notes): redesign new-note modal with category + shift + confirm"
```

---

## Task 7: 列表頁加 shift filter 列 + cascade URL 更新

**Files:**
- Modify: `app_unified/templates/notes/index.html:95-157` (filter rows)
- Modify: `app_unified/templates/notes/index.html:184-205` (pagination URL)

UI + cascade，hand-verify。

- [ ] **Step 1: store filter cascade 重置 shift (`:97-100`)**

替換 store-tabs 區塊：

```html
{% if current_user.is_super_admin() %}
<div class="store-tabs">
  <a href="{{ url_for('notes.index') }}" class="store-tab {% if not current_store %}active{% endif %}" data-store="">全部</a>
  {% for s in stores %}
  <a href="{{ url_for('notes.index', store=s) }}" class="store-tab {% if current_store == s %}active{% endif %}" data-store="{{ s }}">{{ s }}</a>
  {% endfor %}
</div>
```

(拿掉 `status=current_status, range=current_range` — 切店別會重置全部，符合 cascade。原本就有這個行為，現在更要明確不帶 shift。)

- [ ] **Step 2: 種類 cascade — 不帶 shift (`:117-124`)**

替換種類 tabs：

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

(不帶 shift —— cascade 規則：點種類 reset shift。)

- [ ] **Step 3: 加 shift filter 列 (插入種類後、時間前)**

在「種類」div 結束後、「時間」div 開始前插入：

```html
{% set shift_tab_labels = [('none','不需要'),('A','A'),('B','B'),('N','N')] %}
<div class="range-tabs">
  <span style="font-size:.85rem;color:#666;align-self:center;margin-right:.25rem;">班別：</span>
  {% for val, label in shift_tab_labels %}
  <a href="{{ url_for('notes.index', store=current_store, category=current_category, shift=val) }}"
     class="store-tab {% if current_shift == val %}active{% endif %}"
     style="{% if current_shift == val %}background:#16a085;color:#fff;{% endif %}">{{ label }}</a>
  {% endfor %}
</div>
```

- [ ] **Step 4: 時間 cascade 帶上 shift (`:125-133`)**

替換時間 tabs：

```html
<div class="range-tabs">
  <span style="font-size:.85rem;color:#666;align-self:center;margin-right:.25rem;">時間：</span>
  {% set range_labels = [('today','今天'),('3d','近3天'),('7d','近7天'),('30d','近月')] %}
  {% for val, label in range_labels %}
  <a href="{{ url_for('notes.index', store=current_store, category=current_category, shift=current_shift, range=val) }}"
     class="store-tab {% if current_range == val %}active{% endif %}"
     style="{% if current_range == val %}background:#2c3e50;color:#fff;{% endif %}">{{ label }}</a>
  {% endfor %}
</div>
```

- [ ] **Step 5: 狀態 cascade 帶上 shift (`:134-148`)**

替換狀態 tabs：

```html
{% set status_labels = [('','全部'),('pending','待處理'),('in_progress','待討論'),('resolved','已解決')] %}
<div class="status-tabs">
  <span style="font-size:.85rem;color:#666;align-self:center;margin-right:.25rem;">狀態：</span>
  {% for val, label in status_labels %}
  <a href="{{ url_for('notes.index', store=current_store, category=current_category, shift=current_shift, range=current_range, status=val) }}"
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

- [ ] **Step 6: 排列 cascade 帶上 shift (`:149-157`)**

替換排列 tabs：

```html
{% set sort_labels = [('updated','照更新'),('created','照日期')] %}
<div class="status-tabs">
  <span style="font-size:.85rem;color:#666;align-self:center;margin-right:.25rem;">排列：</span>
  {% for val, label in sort_labels %}
  <a href="{{ url_for('notes.index', store=current_store, category=current_category, shift=current_shift, range=current_range, status=current_status, sort=val) }}"
     class="store-tab {% if current_sort == val %}active{% endif %}"
     style="{% if current_sort == val %}background:#8e44ad;color:#fff;{% endif %}">{{ label }}</a>
  {% endfor %}
</div>
```

- [ ] **Step 7: 分頁連結帶上 shift (`:184-205`)**

把 `:187-188`、`:194-196`、`:202-204` 三處 pagination 連結都加上 `shift=current_shift`：

```html
  <a href="{{ url_for('notes.index', store=current_store, status=current_status, range=current_range, category=current_category, shift=current_shift, q=current_keyword, page=pagination.prev_num) }}"
```

(三處同樣加 `shift=current_shift`)

- [ ] **Step 8: hand-verify cascade**

啟動 dev server，到 `/notes/`：

- 預設 URL 不帶 shift → 班別列「不需要」高亮綠色
- 點「員工」（種類）→ URL 變 `?store=B&category=employee`，班別仍是「不需要」(default 套用)
- 點「A」（班別）→ URL `?store=B&category=employee&shift=A`，A 高亮綠色
- 點「今天」（時間）→ URL 帶完整 `?store=B&category=employee&shift=A&range=today`，A 仍高亮
- 點「全部」（狀態）→ URL 帶完整含 shift
- 點別的種類 → shift 重置（URL 不帶 shift → server 預設 none）
- 切店別（super_admin）→ 全部重置

- [ ] **Step 9: 跑測試確認沒退步**

```bash
cd app_unified && python -m pytest tests/ --ignore=tests/test_auth.py --ignore=tests/test_notes.py --ignore=tests/test_face.py -q
```
Expected: 85 passed

- [ ] **Step 10: Commit**

```bash
git add app_unified/templates/notes/index.html
git commit -m "feat(notes): add shift filter row + update cascade URLs"
```

---

## Task 8: 端對端 smoke verify + push

- [ ] **Step 1: 完整流程實機驗證**

啟動 dev server：
```bash
cd /home/hirain0126/projects/webapp && bash start_app1.sh &
```

驗證項目：

1. **新增筆記 員工 + A 班**
   - `/notes/` → 點「+ 新增筆記」
   - 選「員工」+「A」，按「確認」
   - 標題顯示 `4/29 A員工`
   - 儲存
   - 回列表，看到該筆記，category badge=員工

2. **新增筆記 個人 + 不需要**
   - 點「+ 新增筆記」
   - 上次選擇還在（員工 + A）→ 改選「個人」+「不需要」
   - 確認 → 標題 `4/29 hirain筆記`（無 A）

3. **新增確認表單 客人 + N**
   - 點「☑ 新增確認表單」→「客人」+「N」→ 標題 `4/29 N客人`

4. **班別 filter cascade**
   - 列表頁預設只看到「不需要」班別的筆記（不看到剛建的 A/N 班）
   - 點「A」→ 只看到 `4/29 A員工`
   - 點「N」→ 只看到 `4/29 N客人`
   - 點種類「個人」→ 班別重置成「不需要」→ 只看到 `4/29 hirain筆記`

5. **資料保留**
   - 重整頁面，filter 狀態跟 URL 一致
   - localStorage 記憶：再開新增 modal，記得上次的選擇

- [ ] **Step 2: Push**

```bash
cd /home/hirain0126/projects/webapp
git push
```

注意：使用者已在這個 session 明確同意 push。Migration 007 在 Zeabur container 部署完成後需手動執行：

```bash
# Zeabur container shell:
cd /app && python migrations/versions/007_add_note_shift.py
```

- [ ] **Step 3: 提醒使用者跑 migration**

部署完成後告知使用者：「Zeabur 已部署，請進 container 跑 `cd /app && python migrations/versions/007_add_note_shift.py`」。
