# F11 ChecklistItem Authorship Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每個 ChecklistItem 顯示**最多 2 行** meta — 建立 (作者) / 修改 (最後修改者) — 條件式 render。「打勾完成」被視為一種 modification、併入「修改」行、若最後動作是 check 顯示為 `修改(完成): name · time`。

**Architecture:** 在既有 `ChecklistItem` model 加 4 個 column (`created_by`、`created_by_name`、`updated_by`、`updated_by_name`)。**任何** modification — POST create / PATCH text / POST attachment / DELETE attachment / PATCH is_checked (check or uncheck) — 都 set `updated_by` + bump `updated_at`；check 動作把 `updated_at` 與 `checked_at` 設為同一個 `now`、UI 用 `checked_at === updated_at && is_checked` 判斷加 `(完成)` suffix。

**Tech Stack:** Flask + SQLAlchemy + Flask-Login + WebSocket (Flask-SocketIO + Redis pub/sub) + Jinja2 + 原生 JS。

**Spec:** `app_unified/docs/superpowers/specs/2026-05-28-checklist-item-authorship-design.md`

**Branch:** `feat/checklist-item-authorship` (已開、HEAD = `c05e4ac` = 舊版 plan; 本 plan 為 user revision 後的更新版)

---

## Design Note (user revision)

第一版 plan 加 5 個 column (含 `modified_at`) 來區分「真的改了內容」vs「只是 check 一下」。User 後續說「最後打勾就是完成、其實等同修改、應該可以跟修改合併」— 也就是 check 也算 modification、不另設一行。

簡化結論：
- **4 個 column** (不要 `modified_at`、不要第 5 個)
- PATCH `is_checked` 也 bump `updated_by` + `updated_at`、跟 text edit 一樣
- check 動作同時 set `checked_at` = same `now` value、UI 比對 `checked_at === updated_at` 判斷顯示 `(完成)` suffix
- UI 最多 2 行 meta（不再 3 行）、刪除既有 `✓ 由 ... 完成` 那行

對 prod 行為的影響：
- `updated_at` bumping 規則**完全沒變**（既有 PATCH 已對所有 change 都 bump、含 check）→ item sort 行為 100% 保留
- 只多了 `updated_by` + name 在 check 路徑 set、純加法

---

## Prod Safety Analysis (Loading + Crash)

User 第一次 plan 確認時明問「會不會造成 prod 任何 loading 或 crash」。下方逐項列：

### Schema migration (prod 第一次跑 schema repair 時)

- 4 個 column 全 `nullable=True` + `ondelete="SET NULL"` (for FK) — PG `ALTER TABLE ADD COLUMN nullable` 是 **metadata-only** 操作、O(1)、不需 rewrite table、即使表有百萬 row 也是毫秒級
- Wrapped in `IF NOT EXISTS` (透過 `_safe_add_column` 既有 helper)、idempotent、重跑安全
- 失敗模式：若 column name 撞到既存 → IF NOT EXISTS skip；FK 加不上 (users 不存在) → 不可能 (users 是核心 table)
- **不會 crash**：app.py schema repair 在 worker boot 前跑、try/except 包住、單一 ALTER 失敗 log warning 不 abort startup
- **無 lock contention**：PG ADD COLUMN 只需 ACCESS EXCLUSIVE 短暫拿一下 metadata、< 100ms
- **無資料 backfill** — 全部留 NULL、沒有 UPDATE

### Runtime loading impact

- `_checklist_item_json` 多 expose 4 個 fields → response payload 每個 item +~200 bytes (2 timestamps 既有、僅 2 username + 1 timestamp 新增實際算 ~150 bytes)
  - 典型 checklist 10-30 items → response +1.5-4.5 KB
  - 跟 attachment URL 比起來不顯著
  - 不影響 worker capacity (2 CPU / 8 GB) — 純 in-memory dict serialization、無 extra query
- POST create / PATCH update：多寫 2-4 columns 進 DB、純 SQL UPDATE no extra query — 對既有 < 50ms endpoint 不影響
- WebSocket `'iu'` broadcast：payload 多 ~150 bytes → cross-worker via Redis pub/sub 不影響
- 前端 render：2-row meta 用 innerHTML join `<br>`、純 string ops、無 DOM thrash
- SW v37 不需要 bump：editor.html 是 navigation request (network-first per sw.js)、reload 拿到新版

### Crash vectors (排除過)

| 場景 | 風險 | 解 |
|---|---|---|
| 舊 client 連 WebSocket 收到含新 fields payload | JS 忽略 unknown keys、無 crash | OK |
| 前端讀 `item.created_by_name` for null 值 | `if (item.created_by_name)` 過濾、不顯示行 | OK |
| 同 transaction 內 SQL fail | 既有 db.session 處理、單 endpoint atomic | OK |
| EncryptedText column 寫入 null | EncryptedText 既有支援 null (Note.author_name 已驗) | OK |
| Datetime ISO format 給 UI | 用既有 `tw_iso()` helper、TW timezone (per [[feedback_timezone_taiwan]]) | OK |
| Multi-worker 一個 worker 看到 new schema 另一個沒看到 | 所有 worker 啟動時都跑 schema repair、deploy 是 rolling restart — 短暫 window <30s | OK |
| `checked_at === updated_at` UI 字串比對精準度 | Backend 同 transaction 用同一個 `now = datetime.utcnow()` 變數、兩個 column set 同 value、`tw_iso()` 輸出相同字串、精準命中 | OK |

### Capacity check (per [[feedback_capacity_check]])

- prod 2 CPU / 8 GB / 2 workers
- 每 ChecklistItem object 額外記憶體：< 1 KB (4 fields ~200 bytes)
- 典型 store 一天 100 checklists × 20 items = 2000 items → 額外 400 KB heap — 可忽略

### Rollback safety

- Revert commit + rollback image、ALTER 留著 (multi-deploy 都 IF NOT EXISTS)
- Data loss 風險：0 (純加法、無 backfill、無 DROP)

**結論**：prod 安全、無 loading 顯著增加、無 crash 風險。

---

## File Structure

| 檔案 | 變動 | 責任 |
|---|---|---|
| `app_unified/models.py` | Modify (ChecklistItem class ~line 186-217) | 加 4 column |
| `app_unified/app.py` | Modify (schema repair block) | 加 4 條 inline ALTER (IF NOT EXISTS) |
| `app_unified/migrations/015_checklist_item_authorship.py` | Create | Alembic upgrade/downgrade — dev SQLite 重建用 |
| `app_unified/notes/routes.py` | Modify (4 處：`_checklist_item_json` line 1469、`create_checklist_item` line 1551、`update_checklist_item` line 1586、`upload_attachment_api` line 1680、`delete_attachment_api` line 1800) | Serializer expose、route mutations set author fields |
| `app_unified/notes/ws.py` | Read (check if `'iu'` broadcast uses `_checklist_item_json`) | 若 hand-built 加 4 fields；若 shared 不動 |
| `app_unified/templates/notes/editor.html` | Modify (renderChecklistItem `~line 1445-1451` + ws re-render path `~line 1608`) | 改寫 meta block 為 2 行條件式 + `(完成)` suffix |
| `app_unified/tests/test_checklist_item_authorship.py` | Create | 7 case — create / text / check=true / check=false / text+check / attachment add / attachment delete |
| `app_unified/tests/test_checklist_item_serializer.py` | Create | `_checklist_item_json` 回 4 個新 fields |
| `app_unified/tests/test_notes_editor_meta_smoke.py` | Create | editor.html JS source 含 2 行 meta 條件 + `(完成)` suffix 邏輯 |
| `app_unified/tests/test_checklist_item_schema_alter.py` | Create | app.py 含 4 條 ALTER guard test |

---

## Task 1: Schema (model + migrations + app.py inline ALTER + guard test)

**Files:**
- Modify: `app_unified/models.py` (ChecklistItem class)
- Modify: `app_unified/app.py` (schema repair block)
- Create: `app_unified/migrations/015_checklist_item_authorship.py`
- Create: `app_unified/tests/test_checklist_item_schema_alter.py`

4 column 一次加完。Schema repair guard test + alembic migration 雙保。

- [ ] **Step 1: Read existing schema repair pattern**

Run:
```bash
grep -n "ALTER TABLE checklist_items\|_safe_add_column\|ADD COLUMN" app_unified/app.py | head -20
```

Expected: find existing inline ALTER block (F1+F2 added columns there). Note the helper / pattern used so new ALTERs follow it.

- [ ] **Step 2: Write the failing schema guard test (app_unified/tests/test_checklist_item_schema_alter.py)**

```python
"""Guard: app.py schema repair block has 4 new ALTERs for checklist_items authorship.

If anyone removes or renames these, prod first-deploy schema repair would
silently miss the columns and all writes to those fields would 500 ISE.
"""
from pathlib import Path

APP_PY = Path(__file__).parent.parent / 'app.py'


def test_checklist_items_authorship_alters_present():
    src = APP_PY.read_text(encoding='utf-8')
    assert 'checklist_items' in src
    # 4 new columns (no modified_at — user revised design)
    for col in ['created_by', 'created_by_name', 'updated_by', 'updated_by_name']:
        found = (
            f'ADD COLUMN {col}' in src
            or f'ADD COLUMN IF NOT EXISTS {col}' in src
            or (f"'{col}'" in src and 'checklist_items' in src)
            or f'"{col}"' in src
        )
        assert found, f'missing ALTER for checklist_items.{col}'
```

- [ ] **Step 3: Run guard test — verify it fails**

Run:
```bash
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_schema_alter.py -v
```

Expected: FAIL (no ALTERs yet)

- [ ] **Step 4: Add 4 columns to ChecklistItem in models.py**

In `app_unified/models.py`, find `class ChecklistItem(db.Model)` around line 186. Add 4 columns AFTER `deleted_by` (around line 200, before `created_at`):

```python
    # F11 authorship tracking
    created_by      = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_name = db.Column(EncryptedText, nullable=True)
    updated_by      = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_name = db.Column(EncryptedText, nullable=True)
```

Also add 2 relationships after the existing `checker` / `locker` / `deleter` relationships (around line 207-209):

```python
    creator = db.relationship("User", foreign_keys=[created_by])
    modifier = db.relationship("User", foreign_keys=[updated_by])
```

- [ ] **Step 5: Add 4 inline ALTERs to app.py schema repair block**

Find the schema repair section in `app.py` (block containing existing `checklist_items` ALTERs from F1+F2). Add immediately after the existing `checklist_items.deleted_by` ALTER, following the **same `_safe_add_column` helper pattern** observed in Step 1:

(use the exact helper signature found in Step 1; example assuming `_safe_add_column(table, col, sql_type)`)

```python
# F11: ChecklistItem authorship — creator/modifier tracking
_safe_add_column('checklist_items', 'created_by', 'INTEGER REFERENCES users(id) ON DELETE SET NULL')
_safe_add_column('checklist_items', 'created_by_name', 'TEXT')
_safe_add_column('checklist_items', 'updated_by', 'INTEGER REFERENCES users(id) ON DELETE SET NULL')
_safe_add_column('checklist_items', 'updated_by_name', 'TEXT')
```

If helper has different signature (e.g. takes dict, or is plain SQL with try/except), match the existing pattern exactly.

- [ ] **Step 6: Create alembic migration `app_unified/migrations/015_checklist_item_authorship.py`**

Use existing migration as template (`ls app_unified/migrations/ | sort | tail -3` to find latest). Copy structure:

```python
"""F11 ChecklistItem authorship: created_by/created_by_name/updated_by/updated_by_name

Revision ID: 015
Revises: 014
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa


revision = '015'
down_revision = '014'  # confirm via `cat app_unified/migrations/014*.py | head -10`
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('checklist_items', sa.Column('created_by', sa.Integer(), nullable=True))
    op.add_column('checklist_items', sa.Column('created_by_name', sa.Text(), nullable=True))
    op.add_column('checklist_items', sa.Column('updated_by', sa.Integer(), nullable=True))
    op.add_column('checklist_items', sa.Column('updated_by_name', sa.Text(), nullable=True))
    op.create_foreign_key('fk_ci_created_by', 'checklist_items', 'users', ['created_by'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_ci_updated_by', 'checklist_items', 'users', ['updated_by'], ['id'], ondelete='SET NULL')


def downgrade():
    op.drop_constraint('fk_ci_updated_by', 'checklist_items', type_='foreignkey')
    op.drop_constraint('fk_ci_created_by', 'checklist_items', type_='foreignkey')
    op.drop_column('checklist_items', 'updated_by_name')
    op.drop_column('checklist_items', 'updated_by')
    op.drop_column('checklist_items', 'created_by_name')
    op.drop_column('checklist_items', 'created_by')
```

Adjust `down_revision` based on actual previous migration ID (`014_tag_system` per memory note).

- [ ] **Step 7: Run schema guard test — verify it passes**

Run:
```bash
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_schema_alter.py -v
```

Expected: PASS (4 ALTERs present)

- [ ] **Step 8: Run full test suite — confirm no regression**

Run:
```bash
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/ -q --tb=line
```

Expected: 535 + 1 new = 536 passed (existing tests should not break — schema purely additive).

- [ ] **Step 9: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/models.py app_unified/app.py app_unified/migrations/015_checklist_item_authorship.py app_unified/tests/test_checklist_item_schema_alter.py
git commit -m "schema(checklist_items): add 4 authorship columns (created_by/by_name, updated_by/by_name) for F11"
```

---

## Task 2: Route mutations — POST create / PATCH update (text + is_checked)

**Files:**
- Modify: `app_unified/notes/routes.py` `create_checklist_item` (line 1551-1584)
- Modify: `app_unified/notes/routes.py` `update_checklist_item` (line 1586-1638)
- Create: `app_unified/tests/test_checklist_item_authorship.py`

Set author/modifier fields per spec mutation table. Critical detail: **check 動作把 `updated_at` 與 `checked_at` 設成同一個 `now` value** so UI can detect "last action was check".

- [ ] **Step 1: Write failing tests (app_unified/tests/test_checklist_item_authorship.py)**

```python
"""F11: ChecklistItem authorship fields populated correctly per mutation type."""
import pytest
import time
from app import create_app
from models import db, User, Note, ChecklistItem


@pytest.fixture
def app():
    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _login_user(client, app, username='alice'):
    """Returns the User id; client cookie set to logged-in."""
    with app.app_context():
        u = User(username=username, store='ST-test', is_active=True)
        u.set_password('x'); db.session.add(u); db.session.commit()
        uid = u.id
    with client.session_transaction() as sess:
        sess['_user_id'] = str(uid)
    return uid


def _make_note(app, uid):
    with app.app_context():
        note = Note(user_id=uid, store='ST-test', category='employee', note_type='checklist')
        db.session.add(note); db.session.commit()
        return note.id


def _make_creator_and_item(app, creator_name='bob'):
    """Create another user + a note + an item owned by that user. Returns (creator_id, note_id, item_id)."""
    with app.app_context():
        creator = User(username=creator_name, store='ST-test', is_active=True)
        creator.set_password('x'); db.session.add(creator); db.session.commit()
        cid = creator.id
        note = Note(user_id=cid, store='ST-test', category='employee', note_type='checklist')
        db.session.add(note); db.session.commit()
        item = ChecklistItem(
            note_id=note.id, text='task',
            created_by=cid, created_by_name=creator_name,
            updated_by=cid, updated_by_name=creator_name,
        )
        db.session.add(item); db.session.commit()
        return cid, note.id, item.id


def test_post_create_item_sets_all_four_fields(app):
    client = app.test_client()
    uid = _login_user(client, app)
    nid = _make_note(app, uid)
    r = client.post(f'/notes/api/{nid}/items', json={'text': 'item1'})
    assert r.status_code == 201, r.get_data(as_text=True)
    item_id = r.get_json()['item']['id']
    with app.app_context():
        item = ChecklistItem.query.get(item_id)
        assert item.created_by == uid
        assert item.created_by_name == 'alice'
        assert item.updated_by == uid
        assert item.updated_by_name == 'alice'
        # At creation, updated_at must equal created_at (same now value)
        assert item.updated_at == item.created_at


def test_patch_text_sets_updated_by_keeps_created_by(app):
    client = app.test_client()
    uid = _login_user(client, app)
    cid, nid, iid = _make_creator_and_item(app, 'bob')
    # alice (uid) edits bob's item text
    r = client.patch(f'/notes/api/items/{iid}', json={'text': 'edited'})
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        item = ChecklistItem.query.get(iid)
        assert item.text == 'edited'
        assert item.updated_by == uid
        assert item.updated_by_name == 'alice'
        assert item.created_by == cid  # unchanged
        assert item.created_by_name == 'bob'
        # checked_at unchanged (was None)
        assert item.checked_at is None


def test_patch_is_checked_true_bumps_updated_by_and_sets_checked_at_same_as_updated_at(app):
    """KEY: check 動作 updated_at === checked_at (same `now` value) — UI relies on this for `(完成)` suffix."""
    client = app.test_client()
    uid = _login_user(client, app)
    cid, nid, iid = _make_creator_and_item(app, 'bob')
    r = client.patch(f'/notes/api/items/{iid}', json={'is_checked': True})
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        item = ChecklistItem.query.get(iid)
        assert item.is_checked is True
        # updated_by ALSO bumps (check counts as modification per user revision)
        assert item.updated_by == uid
        assert item.updated_by_name == 'alice'
        # checked_by populated
        assert item.checked_by == uid
        assert item.checked_by_name == 'alice'
        # CRITICAL: same now value
        assert item.checked_at == item.updated_at, \
            f'check action must set checked_at == updated_at (got {item.checked_at} vs {item.updated_at})'


def test_patch_is_checked_false_bumps_updated_by_clears_checked_at(app):
    client = app.test_client()
    uid = _login_user(client, app)
    cid, nid, iid = _make_creator_and_item(app, 'bob')
    # First check it as bob (simulate prior state)
    with app.app_context():
        item = ChecklistItem.query.get(iid)
        item.is_checked = True
        from datetime import datetime
        item.checked_at = datetime.utcnow()
        item.checked_by = cid
        item.checked_by_name = 'bob'
        db.session.commit()
    # alice unchecks
    r = client.patch(f'/notes/api/items/{iid}', json={'is_checked': False})
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        item = ChecklistItem.query.get(iid)
        assert item.is_checked is False
        assert item.checked_at is None
        # updated_by still bumps (uncheck also counts as modification)
        assert item.updated_by == uid
        assert item.updated_by_name == 'alice'


def test_patch_text_and_check_in_same_request(app):
    """Text + check in one PATCH: both apply, updated_at == checked_at (same now)."""
    client = app.test_client()
    uid = _login_user(client, app)
    cid, nid, iid = _make_creator_and_item(app, 'bob')
    r = client.patch(f'/notes/api/items/{iid}', json={'text': 'edited', 'is_checked': True})
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        item = ChecklistItem.query.get(iid)
        assert item.text == 'edited'
        assert item.is_checked is True
        assert item.updated_by == uid
        assert item.checked_by == uid
        # MUST be same now
        assert item.checked_at == item.updated_at


def test_text_edit_after_check_does_not_preserve_completion_suffix_invariant(app):
    """If item was checked, then someone edits text later:
    updated_at bumps (new), checked_at stays the old checked time → not equal → UI shows '修改' (no '(完成)')."""
    client = app.test_client()
    uid = _login_user(client, app)
    cid, nid, iid = _make_creator_and_item(app, 'bob')
    # bob checks
    with app.app_context():
        item = ChecklistItem.query.get(iid)
        item.is_checked = True
        from datetime import datetime
        check_time = datetime.utcnow()
        item.checked_at = check_time
        item.checked_by = cid; item.checked_by_name = 'bob'
        item.updated_at = check_time
        item.updated_by = cid; item.updated_by_name = 'bob'
        db.session.commit()
    time.sleep(0.05)  # ensure datetime.utcnow() will return strictly later
    # alice edits text
    r = client.patch(f'/notes/api/items/{iid}', json={'text': 'edited later'})
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        item = ChecklistItem.query.get(iid)
        assert item.text == 'edited later'
        # updated_at strictly newer than checked_at — UI won't show '(完成)' suffix
        assert item.updated_at > item.checked_at
        assert item.updated_by == uid
        # checked_at preserved (item still checked, just edited)
        assert item.checked_at == check_time
        assert item.is_checked is True
```

- [ ] **Step 2: Run tests — verify they fail**

Run:
```bash
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_authorship.py -v
```

Expected: 6 failed (route logic doesn't populate new fields yet)

- [ ] **Step 3: Update `create_checklist_item` in notes/routes.py (line 1551)**

Find `item = ChecklistItem(...)` (around line 1563). Add 4 author fields:

```python
    item = ChecklistItem(
        note_id=note_id,
        order_index=max_order + 1,
        text=text,
        is_checked=False,
        updated_at=now,
        # F11 authorship — creator + modifier same on create
        created_by=current_user.id,
        created_by_name=current_user.username,
        updated_by=current_user.id,
        updated_by_name=current_user.username,
    )
```

Also ensure `created_at` is set to the same `now` (SQLAlchemy default uses `datetime.utcnow()` evaluated at insert, which may differ by microseconds from `now`; better to set explicitly):

```python
    item = ChecklistItem(
        note_id=note_id,
        order_index=max_order + 1,
        text=text,
        is_checked=False,
        created_at=now,    # explicit to match updated_at
        updated_at=now,
        # ... author fields above
    )
```

- [ ] **Step 4: Update `update_checklist_item` in notes/routes.py (line 1586)**

Rewrite the mutation block. Key change: **single `now`** variable shared across text + check branches, and ALL change types bump `updated_by`. Find around line 1607-1639:

Current pattern:
```python
    if "text" in data:
        new_text = (data.get("text") or "").strip()
        if new_text != item.text:
            item.text = new_text
            changed = True
    if "is_checked" in data:
        is_checked = bool(data.get("is_checked"))
        if is_checked and not item.is_checked:
            item.is_checked = True
            item.checked_at = datetime.utcnow()
            item.checked_by = current_user.id
            item.checked_by_name = current_user.username
            changed = True
        elif not is_checked and item.is_checked:
            item.is_checked = False
            item.checked_at = None
            item.checked_by = None
            item.checked_by_name = None
            changed = True
    if changed:
        item.updated_at = datetime.utcnow()
```

Change to:
```python
    now = datetime.utcnow()   # F11: single now shared across text/check so checked_at === updated_at on check
    if "text" in data:
        new_text = (data.get("text") or "").strip()
        if new_text != item.text:
            item.text = new_text
            changed = True
    if "is_checked" in data:
        is_checked = bool(data.get("is_checked"))
        if is_checked and not item.is_checked:
            item.is_checked = True
            item.checked_at = now   # F11: same `now` as updated_at — UI detects `(完成)` via equality
            item.checked_by = current_user.id
            item.checked_by_name = current_user.username
            changed = True
        elif not is_checked and item.is_checked:
            item.is_checked = False
            item.checked_at = None
            item.checked_by = None
            item.checked_by_name = None
            changed = True
    if changed:
        item.updated_at = now
        # F11: any modification bumps updated_by (text, check, uncheck — all count)
        item.updated_by = current_user.id
        item.updated_by_name = current_user.username
```

- [ ] **Step 5: Run tests — verify they pass**

Run:
```bash
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_authorship.py -v
```

Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/routes.py app_unified/tests/test_checklist_item_authorship.py
git commit -m "feat(notes): POST/PATCH item set author fields, check counts as modification (F11)"
```

---

## Task 3: Attachment mutations — POST upload + DELETE for items

**Files:**
- Modify: `app_unified/notes/routes.py` `upload_attachment_api` (line 1680-) and `delete_attachment_api` (line 1800-)
- Extend: `app_unified/tests/test_checklist_item_authorship.py` (add 2 tests)

When attachment uploaded/deleted for a checklist_item_id, bump parent item's `updated_by` + `updated_at`.

- [ ] **Step 1: Add 2 new failing tests to existing test_checklist_item_authorship.py**

Append:
```python
import io


def test_post_attachment_for_item_bumps_item_updated_by(app):
    client = app.test_client()
    uid = _login_user(client, app)
    cid, nid, iid = _make_creator_and_item(app, 'bob')
    data = {
        'note_id': str(nid),
        'checklist_item_id': str(iid),
        'file': (io.BytesIO(b'fake image bytes'), 'photo.png', 'image/png'),
    }
    r = client.post('/notes/api/attachments/upload', data=data, content_type='multipart/form-data')
    if r.status_code == 503:
        pytest.skip('storage not configured in test env')
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    with app.app_context():
        item = ChecklistItem.query.get(iid)
        assert item.updated_by == uid
        assert item.updated_by_name == 'alice'


def test_delete_attachment_for_item_bumps_item_updated_by(app):
    client = app.test_client()
    uid = _login_user(client, app)
    cid, nid, iid = _make_creator_and_item(app, 'bob')
    with app.app_context():
        from models import NoteAttachment
        att = NoteAttachment(note_id=nid, checklist_item_id=iid, user_id=cid,
                             object_key='dev/fake.png', filename='photo.png',
                             content_type='image/png', file_size=12345)
        db.session.add(att); db.session.commit()
        aid = att.id
    r = client.delete(f'/notes/api/attachments/{aid}')
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        item = ChecklistItem.query.get(iid)
        assert item.updated_by == uid
        assert item.updated_by_name == 'alice'
```

- [ ] **Step 2: Run new tests — verify they fail**

Run:
```bash
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_authorship.py::test_post_attachment_for_item_bumps_item_updated_by tests/test_checklist_item_authorship.py::test_delete_attachment_for_item_bumps_item_updated_by -v
```

Expected: FAIL or skip

- [ ] **Step 3: Modify upload_attachment_api in notes/routes.py (line 1680)**

Read the handler:
```bash
sed -n '1680,1763p' app_unified/notes/routes.py
```

Find where the `NoteAttachment` row is added (around line 1740-1760). If form includes `checklist_item_id` (item-level attachment), AFTER `db.session.add(att)` and BEFORE `db.session.commit()`:

```python
        # F11: attachment upload bumps parent item updated_by + updated_at
        if checklist_item_id:
            ci = ChecklistItem.query.get(checklist_item_id)
            if ci:
                now_attach = datetime.utcnow()
                ci.updated_at = now_attach
                ci.updated_by = current_user.id
                ci.updated_by_name = current_user.username
```

(Variable name `checklist_item_id` matches existing parameter — confirm via Step 1's `sed`. If named differently, adapt.)

- [ ] **Step 4: Modify delete_attachment_api in notes/routes.py (line 1800)**

Read:
```bash
sed -n '1800,1840p' app_unified/notes/routes.py
```

Find delete logic. If `attachment.checklist_item_id` is set, bump that item BEFORE `db.session.commit()`:

```python
    # F11: attachment delete bumps parent item updated_by + updated_at
    if attachment.checklist_item_id:
        ci = ChecklistItem.query.get(attachment.checklist_item_id)
        if ci:
            now_del = datetime.utcnow()
            ci.updated_at = now_del
            ci.updated_by = current_user.id
            ci.updated_by_name = current_user.username
```

- [ ] **Step 5: Run tests — verify they pass**

Run:
```bash
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_authorship.py -v
```

Expected: 8 passed (or 7 passed + 1 skipped if storage unavailable)

- [ ] **Step 6: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/routes.py app_unified/tests/test_checklist_item_authorship.py
git commit -m "feat(notes): attachment upload/delete bumps parent item updated_by (F11)"
```

---

## Task 4: Serializer + WebSocket payload

**Files:**
- Modify: `app_unified/notes/routes.py` `_checklist_item_json` (line 1469-1490)
- Read: `app_unified/notes/ws.py` (verify `'iu'` broadcast uses shared serializer)
- Create: `app_unified/tests/test_checklist_item_serializer.py`

API + WS both need 4 new fields in their item payload. Critical: `updated_at` must also be in serializer (UI reads it to compare against `checked_at` for `(完成)` suffix logic).

- [ ] **Step 1: Write the failing serializer test (app_unified/tests/test_checklist_item_serializer.py)**

```python
"""F11: _checklist_item_json exposes 4 new authorship fields + updated_at."""
import pytest
from datetime import datetime
from app import create_app
from models import db, User, Note, ChecklistItem
from notes.routes import _checklist_item_json


@pytest.fixture
def app():
    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_serializer_includes_authorship_fields(app):
    with app.app_context():
        u = User(username='alice', store='ST', is_active=True)
        u.set_password('x'); db.session.add(u); db.session.commit()
        note = Note(user_id=u.id, store='ST', category='employee', note_type='checklist')
        db.session.add(note); db.session.commit()
        item = ChecklistItem(
            note_id=note.id, text='t',
            created_by=u.id, created_by_name='alice',
            updated_by=u.id, updated_by_name='alice',
        )
        db.session.add(item); db.session.commit()
        d = _checklist_item_json(item)
        assert d['created_by_name'] == 'alice'
        assert d['updated_by_name'] == 'alice'
        assert 'created_at' in d
        assert 'updated_at' in d
        assert isinstance(d['created_at'], str)  # tw_iso → ISO string
        assert isinstance(d['updated_at'], str)


def test_serializer_check_action_yields_equal_updated_at_and_checked_at(app):
    """After a check action, updated_at and checked_at must be equal in serialized JSON
    so frontend `(完成)` suffix detection works."""
    with app.app_context():
        u = User(username='alice', store='ST', is_active=True)
        u.set_password('x'); db.session.add(u); db.session.commit()
        note = Note(user_id=u.id, store='ST', category='employee', note_type='checklist')
        db.session.add(note); db.session.commit()
        now = datetime.utcnow()
        item = ChecklistItem(
            note_id=note.id, text='t',
            created_by=u.id, created_by_name='alice',
            updated_by=u.id, updated_by_name='alice',
            is_checked=True,
            checked_at=now,
            checked_by=u.id, checked_by_name='alice',
            updated_at=now,
        )
        db.session.add(item); db.session.commit()
        d = _checklist_item_json(item)
        assert d['updated_at'] == d['checked_at'], \
            f"check action serializer must yield equal updated_at + checked_at, got {d['updated_at']} vs {d['checked_at']}"
```

- [ ] **Step 2: Run test — verify it fails**

Run:
```bash
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_serializer.py -v
```

Expected: FAIL on `updated_by_name` / `updated_at` missing

- [ ] **Step 3: Add 4 new fields + `updated_at` to `_checklist_item_json` (notes/routes.py:1469)**

```python
def _checklist_item_json(item):
    from storage import get_signed_url
    is_other_locked, locker = _item_lock_status(item)
    return {
        "id": item.id,
        "note_id": item.note_id,
        "order_index": item.order_index,
        "text": item.text,
        "is_checked": item.is_checked,
        "checked_at": tw_iso(item.checked_at, fallback=None),
        "checked_by_name": item.checked_by_name or (item.checker.username if item.checker else None),
        "created_at": tw_iso(item.created_at),
        # F11 authorship + updated_at exposure
        "created_by_name": item.created_by_name or (item.creator.username if item.creator else None),
        "updated_by_name": item.updated_by_name or (item.modifier.username if item.modifier else None),
        "updated_at": tw_iso(item.updated_at, fallback=None),
        "locked_by_username": (locker.username if locker else None) if is_other_locked else None,
        "attachments": [{
            "id": a.id,
            "filename": a.filename,
            "content_type": a.content_type,
            "file_size": a.file_size,
            "url": get_signed_url(a.object_key),
        } for a in item.attachments],
    }
```

- [ ] **Step 4: Run test — verify it passes**

Run:
```bash
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_serializer.py -v
```

Expected: PASS (both)

- [ ] **Step 5: Verify WebSocket broadcast picks up new fields**

Run:
```bash
grep -n "_checklist_item_json\|emit.*item\|'iu'" app_unified/notes/ws.py
```

If `notes/ws.py` calls `_checklist_item_json(item)` to build `'iu'` payload → no changes needed.

If it hand-builds a dict → add 4 fields + updated_at there matching the serializer above.

Capture finding in your report. If hand-built, modify and re-run any related ws tests.

- [ ] **Step 6: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/routes.py app_unified/tests/test_checklist_item_serializer.py
# also stage notes/ws.py if changed in Step 5
git commit -m "feat(notes): _checklist_item_json + WS payload expose F11 authorship fields"
```

---

## Task 5: Frontend renderChecklistItem — 2-row meta with `(完成)` suffix

**Files:**
- Modify: `app_unified/templates/notes/editor.html` (renderChecklistItem meta block around line 1445-1451 AND the WS re-render path around line 1608)
- Create: `app_unified/tests/test_notes_editor_meta_smoke.py`

2-row max meta — 建立 + 修改{(完成)?}. Old single `✓ 由 ... 完成` row is **deleted**.

- [ ] **Step 1: Write failing smoke test (app_unified/tests/test_notes_editor_meta_smoke.py)**

```python
"""F11: editor.html renderChecklistItem meta block — 2 rows + (完成) suffix logic."""
from pathlib import Path

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def test_meta_has_two_conditional_rows():
    src = EDITOR.read_text(encoding='utf-8')
    # 建立 row
    assert "'建立: '" in src or '"建立: "' in src
    assert 'item.created_by_name' in src
    # 修改 row + (完成) suffix
    assert "'修改: '" in src or '"修改: "' in src
    assert '修改(完成)' in src
    assert 'item.updated_by_name' in src
    # gate for 修改 row — only show when updated_at differs from created_at
    assert "item.updated_at !== item.created_at" in src
    # gate for (完成) suffix — last action was check
    assert ("item.checked_at === item.updated_at" in src
            or "item.checked_at == item.updated_at" in src)
    assert 'item.is_checked' in src
    # joined via <br>
    assert "rows.join('<br>')" in src or 'rows.join("<br>")' in src


def test_escape_html_used_for_authorship_names():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'escapeHtml(item.created_by_name)' in src
    assert 'escapeHtml(item.updated_by_name)' in src


def test_old_three_arg_completion_line_removed():
    """The old `✓ 由 X 在 Y 完成` line should be gone — replaced by 修改(完成) suffix."""
    src = EDITOR.read_text(encoding='utf-8')
    # The old pattern had `'✓ 由 ' + item.checked_by_name + ' 在 '` — should not appear anymore
    assert "'✓ 由 ' + item.checked_by_name" not in src
    assert '"✓ 由 " + item.checked_by_name' not in src
```

- [ ] **Step 2: Run smoke tests — verify they fail**

Run:
```bash
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_notes_editor_meta_smoke.py -v
```

Expected: FAIL

- [ ] **Step 3: Verify `escapeHtml` helper exists in editor.html**

Run:
```bash
grep -n "function escapeHtml" app_unified/templates/notes/editor.html
```

If not present, add at top of main `<script>` block (around line 900, before functions that use it):

```js
function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, function(c) {
    return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
  });
}
```

If already present (likely from F10), skip.

- [ ] **Step 4: Rewrite renderChecklistItem meta block (editor.html ~line 1445-1451)**

Find:
```js
  // meta
  var meta = document.createElement('div');
  meta.style.cssText = 'font-size:.75rem;color:#999;margin-top:.4rem;';
  if (item.is_checked && item.checked_by_name) {
    meta.textContent = '✓ 由 ' + item.checked_by_name + ' 在 ' + fmtDateTime(item.checked_at) + ' 完成';
  }
  content.appendChild(meta);
```

Replace with:
```js
  // meta — 2 rows max, conditional. (F11) check is treated as modification, shown as '修改(完成)' suffix.
  var meta = document.createElement('div');
  meta.style.cssText = 'font-size:.75rem;color:#999;margin-top:.4rem;line-height:1.5;';
  var rows = [];
  if (item.created_by_name) {
    rows.push('建立: ' + escapeHtml(item.created_by_name) + ' · ' + fmtDateTime(item.created_at));
  }
  if (item.updated_by_name && item.updated_at !== item.created_at) {
    var isLastActionCheck = item.is_checked && item.checked_at && item.checked_at === item.updated_at;
    var label = isLastActionCheck ? '修改(完成): ' : '修改: ';
    rows.push(label + escapeHtml(item.updated_by_name) + ' · ' + fmtDateTime(item.updated_at));
  }
  meta.innerHTML = rows.join('<br>');
  content.appendChild(meta);
```

- [ ] **Step 5: Apply same rewrite to WS re-render path (around line 1608)**

Find the second meta render (in the WS broadcast handler `'iu'` re-render logic):
```bash
grep -n "checked_by_name.*完成" app_unified/templates/notes/editor.html
```

If a second occurrence exists, apply the same 2-row pattern (likely identical 3 lines around line 1608-1610). Replace with the same `rows.push` block above.

- [ ] **Step 6: Run smoke tests — verify they pass**

Run:
```bash
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_notes_editor_meta_smoke.py -v
```

Expected: 3 PASS

- [ ] **Step 7: Run full test suite — verify no regression**

Run:
```bash
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/ -q --tb=line
```

Expected: 535 + new tests = ~547 passed (no regression)

- [ ] **Step 8: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/notes/editor.html app_unified/tests/test_notes_editor_meta_smoke.py
git commit -m "feat(editor): renderChecklistItem 2-row meta with 修改(完成) suffix (F11)"
```

---

## Task 6: Manual verify + push permission

**Files:** None (verify + push)

- [ ] **Step 1: Restart local dev server**

```bash
fuser -k 5000/tcp 2>/dev/null
bash /home/hirain0126/projects/webapp/app_unified/scripts/run_local.sh &
```

Wait for `* Running on http://...:5000`.

- [ ] **Step 2: Manual verify checklist (alice on 北店 員工 `/n/1` or similar checklist note)**

1. **新增 item** as alice → shows `建立: alice · time` only (no 修改 row, updated_at == created_at)
2. **編輯 text** of that item → shows 2 rows: `建立: alice · t1` + `修改: alice · t2`
3. **不同人勾選** (`/dev` switch to other user) → 修改 row updates to `修改(完成): otheruser · t3` (suffix appears because last action was check)
4. **回頭再 edit text** → 修改 row becomes `修改: editor · t4` (no 完成 suffix — last action is text edit, not check)
5. **取消勾選** → 修改 row becomes `修改: uncheker · t5` (no 完成 suffix — item now unchecked)
6. **加附件** → 修改 row updates to attachment-adder + time
7. **舊 item** (created before F11) → no 建立 row; first edit creates 修改 row (no 完成 even if pre-existing-checked, because checked_at would differ from new updated_at)

- [ ] **Step 3: Get user OK**

User confirms working OK → proceed. If broken, report and stop.

- [ ] **Step 4: Ask user push permission (per [[feedback_push_policy]] + [[feedback_push_peak_hours]])**

Check current time:
```bash
date "+%H:%M 台灣"
```

Ask: "F11 本機驗 OK。現在 [HH:MM]、不在 peak (19:30-21:00)。可以 ff-merge main + push origin/main 嗎？"

- [ ] **Step 5: After user approves, push**

```bash
cd /home/hirain0126/projects/webapp
git checkout main
git merge --ff-only feat/checklist-item-authorship
git push origin main
git branch -d feat/checklist-item-authorship
```

- [ ] **Step 6: Monitor GHA + tell user Zeabur Redeploy**

```bash
gh run list --workflow=docker.yml --limit 1
gh run list --workflow=test.yml --limit 1
```

Wait both green. Inform user: "Build 綠了、去 Zeabur 點 Redeploy 升 :latest"。

Prod schema repair runs on first worker startup → 4 ALTERs IF NOT EXISTS → no downtime.

---

## Self-Review Summary

**Spec coverage:**
- §2 Scope (4 cols + mutations 5 types + serializer + UI 2 rows) → Task 1-5 ✓
- §3 UX decisions (check counts as modification, 修改(完成) suffix) → Task 2 Step 4 + Task 5 Step 4 ✓
- §4.1 Schema 4 cols → Task 1 ✓
- §4.2 Schema repair 3-file alignment → Task 1 Step 4-6 ✓
- §4.3 Mutation table (check bumps updated_by, updated_at == checked_at same now) → Task 2 + tests ✓
- §4.4 Serializer 4 + updated_at fields → Task 4 ✓
- §4.5 WebSocket → Task 4 Step 5 ✓
- §5 UI (2-row + (完成) suffix logic) → Task 5 ✓
- §5.5 Delete old `✓ 由 ... 完成` row → Task 5 Step 4 + test_old_three_arg_completion_line_removed ✓
- §6 Tests (4 files) → Task 1 / 2 / 3 / 4 / 5 ✓
- §7 Rollout → Task 6 ✓

**Placeholder scan:** No TBD / TODO / vague. Each step has full code.

**Type consistency:**
- 4 column names consistent throughout
- `now` variable shared in PATCH update_item (Task 2 Step 4) so `updated_at === checked_at` on check
- `tw_iso()` used for all timestamps in serializer (Task 4 Step 3)
- `escapeHtml` used for all author names in UI (Task 5 Step 4)
- 2 places in editor.html require rewrite (initial render + WS re-render) — Task 5 Step 4-5

**Prod safety:** Top-of-plan section covers schema (PG metadata-only), loading (~150 bytes/item), crash vectors (all null-safe + backward compat), capacity (well under 2 CPU / 8 GB), rollback (purely additive).
