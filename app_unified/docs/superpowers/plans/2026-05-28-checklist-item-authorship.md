# F11 ChecklistItem Authorship Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每個 ChecklistItem 顯示 3 行 meta — 建立 (作者) / 修改 (最後修改者) / 完成 (勾選人) — 條件式 render。

**Architecture:** 在既有 `ChecklistItem` model 加 5 個 column (`created_by`、`created_by_name`、`updated_by`、`updated_by_name`、`modified_at`)。POST create item / PATCH text / POST attachment / DELETE attachment 都 set `updated_by` + bump `modified_at`；PATCH `is_checked` 不動 author 欄位（保留現有 `updated_at` sort bump 行為）。Serializer 多 expose 5 個 fields；前端 `renderChecklistItem` 條件式 render 3 行 meta。

**Tech Stack:** Flask + SQLAlchemy + Flask-Login + WebSocket (Flask-SocketIO + Redis pub/sub) + Jinja2 + 原生 JS。

**Spec:** `app_unified/docs/superpowers/specs/2026-05-28-checklist-item-authorship-design.md`

**Branch:** `feat/checklist-item-authorship` (已開、HEAD = `b4b343a` = spec commit)

---

## Spec Refinement (重要)

Spec 寫 4 個新 column。實作時發現現有 `PATCH /api/items/<id>` 對 `is_checked` toggle 也 bump `updated_at`（`notes/routes.py:1627-1629` 註解：「下次重進頁面時才會看到該項目排到最上面」— `updated_at` 是 sort key）。若按 spec 改成「is_checked 不 bump updated_at」會破 item sort 行為。

**解：加第 5 column `modified_at`**。`updated_at` 維持現狀（任何 change 都 bump、含 check、給 sort 用）；`modified_at` 只有 text edit / attachment add / attachment delete 才 bump，跟 `updated_by` 同進退。UI 條件 `if (modified_at && updated_by_name) show 修改 row` — 乾淨判斷 "user 真的改過內容"，不會被 check 觸發誤判。

**對 spec 的影響**：純加法、不改變 spec 描述的 UX 行為（user 看到 UI 結果相同）。Spec section 4.3 "is_checked 不動 updated_at" 那欄改為「不動 modified_at + updated_by；updated_at 維持既有 bump 行為」。

---

## Prod Safety Analysis (Loading + Crash)

User 明問「會不會造成 prod 任何 loading 或 crash」。逐項列：

### Schema migration (prod 第一次跑 schema repair 時)

- 5 個 column 全 `nullable=True` + `ondelete="SET NULL"` (for FK) — PG `ALTER TABLE ADD COLUMN` 是 **metadata-only** 操作、O(1)、不需要 rewrite table、即使表有百萬 row 也是毫秒級
- Wrapped in `IF NOT EXISTS` (透過 `_safe_add_column` 既有 helper)，idempotent、重跑安全
- 失敗模式：若 column name 撞到既存 → IF NOT EXISTS skip；若 FK constraint 加不上 (user table 不存在) → 不可能 (users 是核心 table)
- **不會 crash**：app.py schema repair 在 worker boot 前跑、整段 try/except 包住、單一 ALTER 失敗 log warning 但不 abort startup（per 既有 pattern）
- **無 lock contention**：PG ADD COLUMN 在 PG 11+ 只需要 ACCESS EXCLUSIVE 短暫拿一下 metadata、< 100ms
- **無資料 backfill** — 全部留 NULL，沒有 UPDATE 操作

### Runtime loading impact

- `_checklist_item_json` 多 expose 5 個 fields → response payload 每個 item +~250 bytes (3 timestamps + 2 username strings)
  - 典型 checklist 10-30 items → response +2.5-7.5 KB net
  - 跟 attachments 陣列 + tag 比起來不顯著（attachment URL 一個 ~400 bytes）
  - 不會影響 worker capacity (2 CPU / 8 GB) — 純 in-memory dict serialization、無 DB JOIN、無 R2 round-trip
- POST create item / PATCH text / POST DELETE attachment：多寫 4 columns 進 DB、純 SQL UPDATE no extra query — 對既有 < 50ms 的 endpoint 不影響
- WebSocket `'iu'` broadcast：payload 多 5 fields = ~250 bytes → cross-worker via Redis pub/sub 也不影響 (Redis 已部署)
- 前端 render：3-row meta 用 innerHTML join `<br>`，純 string ops，無 DOM thrash
- SW v37 不需要 bump：editor.html 是 navigation request (network-first per sw.js)、reload 拿到新版

### Crash vectors (排除過)

| 場景 | 風險 | 解 |
|---|---|---|
| 舊 client 連 WebSocket 收到含新 fields payload | JS 忽略 unknown keys、無 crash | OK |
| 前端讀 `item.created_by_name` for null 值 | `if (item.created_by_name)` 過濾、不顯示行 | OK |
| 同 transaction 內多次 SQL fail | 既有 db.session 處理、單 endpoint atomic | OK |
| EncryptedText column 寫入 null | EncryptedText 既有支援 null (Note.author_name 已驗) | OK |
| Datetime timezone — `modified_at` ISO format 給 UI | 用既有 `tw_iso()` helper、TW timezone (per [[feedback_timezone_taiwan]]) | OK |
| Multi-worker 一個 worker 看到 new schema 另一個沒看到 | 所有 worker 啟動時都跑 schema repair、deploy 是 rolling restart — 短暫 window <30s | OK |

### Capacity check (per [[feedback_capacity_check]])

- prod 2 CPU / 8 GB / 2 workers
- 每 worker 額外記憶體佔用：< 1 KB per ChecklistItem object (5 fields ~250 bytes)
- 典型 store 一天 100 checklists × 20 items = 2000 items → 額外 500 KB heap — 可忽略

### Rollback safety

- 若 deploy 後發現問題：revert commit + rollback image、ALTER 留著 (multi-deploy 都會檢查 IF NOT EXISTS)
- Data loss 風險：0 (純加法、沒 backfill、沒 DROP)

**結論**：prod 安全、無 loading 顯著增加、無 crash 風險。

---

## File Structure

| 檔案 | 變動 | 責任 |
|---|---|---|
| `app_unified/models.py` | Modify (ChecklistItem class ~line 186-217) | 加 5 column |
| `app_unified/app.py` | Modify (schema repair block) | 加 5 條 inline `ALTER TABLE checklist_items ADD COLUMN ...` (IF NOT EXISTS 模式) |
| `app_unified/migrations/015_checklist_item_authorship.py` | Create | Alembic upgrade/downgrade — dev SQLite 重建用 |
| `app_unified/notes/routes.py` | Modify (4 處：`_checklist_item_json` line 1469、`create_checklist_item` line 1551、`update_checklist_item` line 1586、`upload_attachment_api` line 1680、`delete_attachment_api` line 1800) | Serializer expose、create/PATCH 改 set author fields |
| `app_unified/notes/ws.py` | Read (check if `'iu'` broadcast uses `_checklist_item_json` or hand-built dict) | 若 hand-built 加 5 fields；若 shared 不動 |
| `app_unified/templates/notes/editor.html` | Modify (renderChecklistItem `~line 1445-1451`) | 改寫 meta block 為 3 行條件式 |
| `app_unified/tests/test_checklist_item_authorship.py` | Create | 6 case 涵蓋 POST / PATCH text / attachment add+del / is_checked / combined |
| `app_unified/tests/test_checklist_item_serializer.py` | Create | `_checklist_item_json` 回 5 個新 fields |
| `app_unified/tests/test_notes_editor_meta_smoke.py` | Create | editor.html JS source 含 3 行 meta 條件 |
| `app_unified/tests/test_checklist_item_schema_alter.py` | Create | app.py 含 5 條 ALTER guard test |

---

## Task 1: Schema (model + migrations + app.py inline ALTER + guard tests)

**Files:**
- Modify: `app_unified/models.py` (ChecklistItem class)
- Modify: `app_unified/app.py` (schema repair block)
- Create: `app_unified/migrations/015_checklist_item_authorship.py`
- Create: `app_unified/tests/test_checklist_item_schema_alter.py`

5 column 一次加完。Schema repair tests + alembic migration 雙保。

- [ ] **Step 1: Read existing schema repair pattern**

Run: `grep -n "ALTER TABLE checklist_items\|_safe_add_column\|ADD COLUMN" app_unified/app.py | head -20`

Expected: find existing inline ALTER block (F1+F2 added columns there, per [[feedback_inline_schema_repair]]). Note the helper / pattern used so the new ALTERs follow it exactly.

- [ ] **Step 2: Write the failing schema guard test (app_unified/tests/test_checklist_item_schema_alter.py)**

```python
"""Guard: app.py schema repair block has 5 new ALTERs for checklist_items authorship.

If anyone removes or renames these, the prod first-deploy schema repair would
silently miss the columns and all writes to those fields would 500 ISE.
"""
from pathlib import Path

APP_PY = Path(__file__).parent.parent / 'app.py'


def test_checklist_items_authorship_alters_present():
    src = APP_PY.read_text(encoding='utf-8')
    assert 'checklist_items' in src
    # 5 new columns
    for col in ['created_by', 'created_by_name', 'updated_by', 'updated_by_name', 'modified_at']:
        # one of: "ADD COLUMN created_by", "ADD COLUMN IF NOT EXISTS created_by", or _safe_add_column(..., 'created_by', ...)
        found = (
            f'ADD COLUMN {col}' in src
            or f'ADD COLUMN IF NOT EXISTS {col}' in src
            or f"'{col}'" in src and 'checklist_items' in src
            or f'"{col}"' in src
        )
        assert found, f'missing ALTER for checklist_items.{col}'
```

- [ ] **Step 3: Run guard test — verify it fails**

Run: `cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_schema_alter.py -v`

Expected: FAIL (no ALTERs yet)

- [ ] **Step 4: Add 5 columns to ChecklistItem in models.py**

In `app_unified/models.py`, find `class ChecklistItem(db.Model)` around line 186. Add these 5 columns AFTER `deleted_by` (around line 200, before `created_at`):

```python
    # F11 authorship tracking
    created_by      = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_name = db.Column(EncryptedText, nullable=True)
    updated_by      = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_name = db.Column(EncryptedText, nullable=True)
    modified_at     = db.Column(db.DateTime, nullable=True)  # bumps on text/attachment edit only (NOT on check)
```

Also add `creator` relationship after the existing `checker` / `locker` / `deleter` relationships (around line 207-209):

```python
    creator = db.relationship("User", foreign_keys=[created_by])
    modifier = db.relationship("User", foreign_keys=[updated_by])
```

- [ ] **Step 5: Add 5 inline ALTERs to app.py schema repair block**

Find the schema repair section in `app.py` (the block that contains existing `checklist_items` ALTERs from F1+F2). Add immediately after the existing `checklist_items.deleted_by` ALTER, following the **same `_safe_add_column` helper pattern** observed in Step 1:

(use the exact helper signature found in Step 1; example:)

```python
# F11: ChecklistItem authorship — building/modifier tracking
_safe_add_column('checklist_items', 'created_by', 'INTEGER REFERENCES users(id) ON DELETE SET NULL')
_safe_add_column('checklist_items', 'created_by_name', 'TEXT')
_safe_add_column('checklist_items', 'updated_by', 'INTEGER REFERENCES users(id) ON DELETE SET NULL')
_safe_add_column('checklist_items', 'updated_by_name', 'TEXT')
_safe_add_column('checklist_items', 'modified_at', 'TIMESTAMP')
```

If the helper has different signature (e.g. takes a dict, or is plain SQL with try/except), match the existing pattern exactly.

- [ ] **Step 6: Create alembic migration `app_unified/migrations/015_checklist_item_authorship.py`**

Use existing migration as template (find latest: `ls app_unified/migrations/ | sort | tail -3`). Copy structure:

```python
"""F11 ChecklistItem authorship: created_by/created_by_name/updated_by/updated_by_name/modified_at

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
    op.add_column('checklist_items', sa.Column('modified_at', sa.DateTime(), nullable=True))
    op.create_foreign_key('fk_ci_created_by', 'checklist_items', 'users', ['created_by'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_ci_updated_by', 'checklist_items', 'users', ['updated_by'], ['id'], ondelete='SET NULL')


def downgrade():
    op.drop_constraint('fk_ci_updated_by', 'checklist_items', type_='foreignkey')
    op.drop_constraint('fk_ci_created_by', 'checklist_items', type_='foreignkey')
    op.drop_column('checklist_items', 'modified_at')
    op.drop_column('checklist_items', 'updated_by_name')
    op.drop_column('checklist_items', 'updated_by')
    op.drop_column('checklist_items', 'created_by_name')
    op.drop_column('checklist_items', 'created_by')
```

Adjust `down_revision` based on actual previous migration ID (`014_tag_system` per memory note).

- [ ] **Step 7: Run schema guard test — verify it passes**

Run: `cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_schema_alter.py -v`

Expected: PASS (5 ALTERs present)

- [ ] **Step 8: Run full test suite — confirm no regression**

Run: `cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/ -q --tb=line`

Expected: 535 + 1 new = 536 passed (existing tests should not break — schema is purely additive).

If existing tests fail (e.g. SQLite in-memory test DB doesn't have new columns), confirm `db.create_all()` in test fixtures picks up new columns automatically (it should, since they're in the model).

- [ ] **Step 9: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/models.py app_unified/app.py app_unified/migrations/015_checklist_item_authorship.py app_unified/tests/test_checklist_item_schema_alter.py
git commit -m "schema(checklist_items): add 5 authorship columns (created_by/by_name, updated_by/by_name, modified_at)"
```

---

## Task 2: Route mutations — POST create / PATCH update

**Files:**
- Modify: `app_unified/notes/routes.py` `create_checklist_item` (line 1551-1584)
- Modify: `app_unified/notes/routes.py` `update_checklist_item` (line 1586-1638)
- Create: `app_unified/tests/test_checklist_item_authorship.py`

Set author/modifier fields per spec table.

- [ ] **Step 1: Write failing tests (app_unified/tests/test_checklist_item_authorship.py)**

```python
"""F11: ChecklistItem authorship fields are populated correctly per mutation type."""
import pytest
from datetime import datetime
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
    """Returns a logged-in client + the User object."""
    with app.app_context():
        u = User(username=username, store='ST-test', is_active=True)
        u.set_password('x')
        db.session.add(u)
        db.session.commit()
        uid = u.id
    with client.session_transaction() as sess:
        sess['_user_id'] = str(uid)
    return uid


def test_post_create_item_sets_created_and_updated_by(app):
    client = app.test_client()
    uid = _login_user(client, app)
    with app.app_context():
        note = Note(user_id=uid, store='ST-test', category='employee', note_type='checklist')
        db.session.add(note)
        db.session.commit()
        nid = note.id
    r = client.post(f'/notes/api/{nid}/items', json={'text': 'item1'})
    assert r.status_code == 201, r.get_data(as_text=True)
    item_id = r.get_json()['item']['id']
    with app.app_context():
        item = ChecklistItem.query.get(item_id)
        assert item.created_by == uid
        assert item.created_by_name == 'alice'
        assert item.updated_by == uid
        assert item.updated_by_name == 'alice'
        # modified_at stays NULL on create (no real modification yet)
        assert item.modified_at is None


def test_patch_text_sets_updated_by_and_modified_at(app):
    client = app.test_client()
    uid = _login_user(client, app)
    with app.app_context():
        note = Note(user_id=uid, store='ST-test', category='employee', note_type='checklist')
        db.session.add(note); db.session.commit()
        item = ChecklistItem(note_id=note.id, text='orig', created_by=uid, created_by_name='alice',
                             updated_by=uid, updated_by_name='alice')
        db.session.add(item); db.session.commit()
        iid = item.id; orig_created_at = item.created_at
    r = client.patch(f'/notes/api/items/{iid}', json={'text': 'edited'})
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        item = ChecklistItem.query.get(iid)
        assert item.text == 'edited'
        assert item.updated_by == uid
        assert item.updated_by_name == 'alice'
        assert item.modified_at is not None
        assert item.modified_at > orig_created_at
        assert item.created_by == uid  # unchanged
        assert item.created_by_name == 'alice'  # unchanged


def test_patch_is_checked_does_NOT_touch_updated_by_or_modified_at(app):
    """The crucial behavior — checking should NOT count as 'modifying content'."""
    client = app.test_client()
    uid = _login_user(client, app)
    with app.app_context():
        # different user creates note
        creator = User(username='bob', store='ST-test', is_active=True)
        creator.set_password('x')
        db.session.add(creator); db.session.commit()
        creator_id = creator.id
        note = Note(user_id=creator_id, store='ST-test', category='employee', note_type='checklist')
        db.session.add(note); db.session.commit()
        item = ChecklistItem(note_id=note.id, text='task', created_by=creator_id, created_by_name='bob',
                             updated_by=creator_id, updated_by_name='bob')
        db.session.add(item); db.session.commit()
        iid = item.id
    # alice checks it
    r = client.patch(f'/notes/api/items/{iid}', json={'is_checked': True})
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        item = ChecklistItem.query.get(iid)
        assert item.is_checked is True
        assert item.checked_by == uid
        assert item.checked_by_name == 'alice'
        # CRITICAL: updated_by stays bob, modified_at still NULL
        assert item.updated_by == creator_id, 'check should NOT change updated_by'
        assert item.updated_by_name == 'bob', 'check should NOT change updated_by_name'
        assert item.modified_at is None, 'check should NOT set modified_at'


def test_patch_text_and_check_in_same_request_bumps_updated_by(app):
    client = app.test_client()
    uid = _login_user(client, app)
    with app.app_context():
        creator = User(username='bob', store='ST-test', is_active=True)
        creator.set_password('x')
        db.session.add(creator); db.session.commit()
        creator_id = creator.id
        note = Note(user_id=creator_id, store='ST-test', category='employee', note_type='checklist')
        db.session.add(note); db.session.commit()
        item = ChecklistItem(note_id=note.id, text='orig', created_by=creator_id, created_by_name='bob',
                             updated_by=creator_id, updated_by_name='bob')
        db.session.add(item); db.session.commit()
        iid = item.id
    r = client.patch(f'/notes/api/items/{iid}', json={'text': 'edited', 'is_checked': True})
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        item = ChecklistItem.query.get(iid)
        # text edit triggers updated_by + modified_at; check populates checked_by
        assert item.updated_by == uid
        assert item.updated_by_name == 'alice'
        assert item.modified_at is not None
        assert item.checked_by == uid
        assert item.checked_by_name == 'alice'
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_authorship.py -v`

Expected: 4 failed (route logic doesn't populate new fields yet)

- [ ] **Step 3: Update `create_checklist_item` in notes/routes.py (line 1551)**

Find `item = ChecklistItem(...)` (around line 1563) and add the 4 author fields:

```python
    item = ChecklistItem(
        note_id=note_id,
        order_index=max_order + 1,
        text=text,
        is_checked=False,
        updated_at=now,
        # F11 authorship
        created_by=current_user.id,
        created_by_name=current_user.username,
        updated_by=current_user.id,
        updated_by_name=current_user.username,
        # modified_at stays NULL on create — bumps only on first real edit
    )
```

- [ ] **Step 4: Update `update_checklist_item` in notes/routes.py (line 1586)**

Find the `if is_text_edit:` block (around line 1600). After `item.text = new_text` is committed (still inside `if "text" in data` block, only when `changed = True`), add updated_by + modified_at:

Find around line 1607-1610:
```python
    if "text" in data:
        new_text = (data.get("text") or "").strip()
        if new_text != item.text:
            item.text = new_text
            changed = True
```

Change to (set author fields on actual text change):
```python
    if "text" in data:
        new_text = (data.get("text") or "").strip()
        if new_text != item.text:
            item.text = new_text
            # F11: text edit bumps modified_at + updated_by
            item.modified_at = datetime.utcnow()
            item.updated_by = current_user.id
            item.updated_by_name = current_user.username
            changed = True
```

**Do NOT** modify the `is_checked` branches (line 1611-1625) — keep them touching only `checked_by` / `checked_at`. Keep the existing line "任何修改（含勾選/取消勾選）都 bump updated_at" — `updated_at` continues to bump for sort.

- [ ] **Step 5: Run tests — verify they pass**

Run: `cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_authorship.py -v`

Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/routes.py app_unified/tests/test_checklist_item_authorship.py
git commit -m "feat(notes): POST/PATCH item set created_by/updated_by + modified_at (F11)"
```

---

## Task 3: Attachment mutations — POST upload + DELETE for items

**Files:**
- Modify: `app_unified/notes/routes.py` `upload_attachment_api` (line 1680-) and `delete_attachment_api` (line 1800-)
- Extend: `app_unified/tests/test_checklist_item_authorship.py` (add 2 tests)

When attachment uploaded/deleted for a checklist_item_id, bump that item's `modified_at` + `updated_by`.

- [ ] **Step 1: Add 2 new failing tests to existing test_checklist_item_authorship.py**

Append to `app_unified/tests/test_checklist_item_authorship.py`:

```python
import io


def test_post_attachment_for_item_bumps_item_updated_by_and_modified_at(app):
    client = app.test_client()
    uid = _login_user(client, app)
    with app.app_context():
        creator = User(username='bob', store='ST-test', is_active=True)
        creator.set_password('x')
        db.session.add(creator); db.session.commit()
        creator_id = creator.id
        note = Note(user_id=creator_id, store='ST-test', category='employee', note_type='checklist')
        db.session.add(note); db.session.commit()
        item = ChecklistItem(note_id=note.id, text='task',
                             created_by=creator_id, created_by_name='bob',
                             updated_by=creator_id, updated_by_name='bob')
        db.session.add(item); db.session.commit()
        iid = item.id; nid = note.id
    # alice uploads an attachment targeting that item
    data = {
        'note_id': str(nid),
        'checklist_item_id': str(iid),
        'file': (io.BytesIO(b'fake image bytes'), 'photo.png', 'image/png'),
    }
    r = client.post('/notes/api/attachments/upload', data=data, content_type='multipart/form-data')
    # If R2 not configured in test, upload returns 503; still expect item.updated_by to NOT have been bumped on failure
    if r.status_code == 503:
        pytest.skip('storage not configured in test env; skip attachment upload integration')
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    with app.app_context():
        item = ChecklistItem.query.get(iid)
        assert item.updated_by == uid, 'attachment upload should bump updated_by'
        assert item.updated_by_name == 'alice'
        assert item.modified_at is not None


def test_delete_attachment_for_item_bumps_item_updated_by_and_modified_at(app):
    client = app.test_client()
    uid = _login_user(client, app)
    with app.app_context():
        from models import NoteAttachment
        creator = User(username='bob', store='ST-test', is_active=True)
        creator.set_password('x')
        db.session.add(creator); db.session.commit()
        creator_id = creator.id
        note = Note(user_id=creator_id, store='ST-test', category='employee', note_type='checklist')
        db.session.add(note); db.session.commit()
        item = ChecklistItem(note_id=note.id, text='task',
                             created_by=creator_id, created_by_name='bob',
                             updated_by=creator_id, updated_by_name='bob')
        db.session.add(item); db.session.commit()
        att = NoteAttachment(note_id=note.id, checklist_item_id=item.id, user_id=creator_id,
                             object_key='dev/fake.png', filename='photo.png',
                             content_type='image/png', file_size=12345)
        db.session.add(att); db.session.commit()
        iid = item.id; aid = att.id
    r = client.delete(f'/notes/api/attachments/{aid}')
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        item = ChecklistItem.query.get(iid)
        assert item.updated_by == uid, 'attachment delete should bump updated_by'
        assert item.updated_by_name == 'alice'
        assert item.modified_at is not None
```

- [ ] **Step 2: Run new tests — verify they fail**

Run: `cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_authorship.py::test_post_attachment_for_item_bumps_item_updated_by_and_modified_at tests/test_checklist_item_authorship.py::test_delete_attachment_for_item_bumps_item_updated_by_and_modified_at -v`

Expected: FAIL (or skip if storage unavailable for upload test; delete test should fail definitely)

- [ ] **Step 3: Modify upload_attachment_api in notes/routes.py (line 1680)**

Read the existing handler:
```bash
sed -n '1680,1763p' app_unified/notes/routes.py
```

Find where the `NoteAttachment` row is added (around line 1740-1760). If the form includes `checklist_item_id` (item-level attachment), AFTER `db.session.add(att)` and BEFORE `db.session.commit()`, add:

```python
        # F11: attachment upload bumps parent item modified_at + updated_by
        if checklist_item_id:
            ci = ChecklistItem.query.get(checklist_item_id)
            if ci:
                ci.modified_at = datetime.utcnow()
                ci.updated_by = current_user.id
                ci.updated_by_name = current_user.username
```

(Variable name `checklist_item_id` to match the existing parameter — confirm via Step 1's `sed`. If named differently, adapt.)

- [ ] **Step 4: Modify delete_attachment_api in notes/routes.py (line 1800)**

Read the existing handler:
```bash
sed -n '1800,1840p' app_unified/notes/routes.py
```

Find where attachment is deleted. If `attachment.checklist_item_id` is set, bump that item BEFORE the `db.session.commit()`:

```python
    # F11: attachment delete bumps parent item modified_at + updated_by
    if attachment.checklist_item_id:
        ci = ChecklistItem.query.get(attachment.checklist_item_id)
        if ci:
            ci.modified_at = datetime.utcnow()
            ci.updated_by = current_user.id
            ci.updated_by_name = current_user.username
```

- [ ] **Step 5: Run tests — verify they pass (upload may skip)**

Run: `cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_authorship.py -v`

Expected: 6 passed (or 5 passed + 1 skipped if storage unavailable)

- [ ] **Step 6: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/routes.py app_unified/tests/test_checklist_item_authorship.py
git commit -m "feat(notes): attachment upload/delete bumps parent item modified_at + updated_by (F11)"
```

---

## Task 4: Serializer + WebSocket payload

**Files:**
- Modify: `app_unified/notes/routes.py` `_checklist_item_json` (line 1469-1490)
- Read: `app_unified/notes/ws.py` (verify `'iu'` broadcast uses shared serializer)
- Create: `app_unified/tests/test_checklist_item_serializer.py`

API + WS both need 5 new fields in their item payload.

- [ ] **Step 1: Write the failing serializer test**

Create `app_unified/tests/test_checklist_item_serializer.py`:

```python
"""F11: _checklist_item_json exposes 5 new authorship fields."""
import pytest
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
        assert 'created_by_name' in d and d['created_by_name'] == 'alice'
        assert 'updated_by_name' in d and d['updated_by_name'] == 'alice'
        assert 'modified_at' in d  # NULL for unmodified item
        assert d['modified_at'] is None
        assert 'created_at' in d   # already exists, keep
        assert 'updated_at' in d   # newly exposed
        # confirm timestamps are ISO strings (TW timezone via tw_iso)
        assert isinstance(d['created_at'], str)
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_serializer.py -v`

Expected: FAIL on `updated_by_name` / `modified_at` / `updated_at` missing

- [ ] **Step 3: Add 5 new fields to `_checklist_item_json` (notes/routes.py:1469)**

Modify the dict returned by `_checklist_item_json`:

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
        # F11 authorship fields
        "created_by_name": item.created_by_name or (item.creator.username if item.creator else None),
        "updated_by_name": item.updated_by_name or (item.modifier.username if item.modifier else None),
        "modified_at": tw_iso(item.modified_at, fallback=None),
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

Run: `cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_checklist_item_serializer.py -v`

Expected: PASS

- [ ] **Step 5: Verify WebSocket broadcast picks up new fields**

Run: `grep -n "_checklist_item_json\|emit.*item\|'iu'" app_unified/notes/ws.py`

If `notes/ws.py` calls `_checklist_item_json(item)` to build the `'iu'` broadcast payload → no changes needed, fields flow through automatically.

If it hand-builds a dict → add 5 fields there matching the serializer above.

Capture finding in your report. If hand-built, modify and re-run any related ws tests.

- [ ] **Step 6: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/routes.py app_unified/tests/test_checklist_item_serializer.py
# also stage notes/ws.py if changed in Step 5
git commit -m "feat(notes): _checklist_item_json + WS payload expose 5 F11 authorship fields"
```

---

## Task 5: Frontend renderChecklistItem — 3-row meta

**Files:**
- Modify: `app_unified/templates/notes/editor.html` (renderChecklistItem meta block around line 1445-1451)
- Create: `app_unified/tests/test_notes_editor_meta_smoke.py`

3-row conditional meta — 建立 / 修改 / 完成. Markup smoke test guard.

- [ ] **Step 1: Write failing smoke test (app_unified/tests/test_notes_editor_meta_smoke.py)**

```python
"""F11: editor.html renderChecklistItem meta block has 3 conditional rows."""
from pathlib import Path

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def test_meta_has_three_conditional_rows():
    src = EDITOR.read_text(encoding='utf-8')
    # 建立 row
    assert "'建立: '" in src or '"建立: "' in src
    assert 'item.created_by_name' in src
    # 修改 row (gated by modified_at + updated_by_name)
    assert "'修改: '" in src or '"修改: "' in src
    assert 'item.modified_at' in src
    assert 'item.updated_by_name' in src
    # 完成 row (existing, keep)
    assert 'item.is_checked && item.checked_by_name' in src
    # joined via <br>
    assert "rows.join('<br>')" in src or 'rows.join("<br>")' in src


def test_escape_html_used_for_authorship_names():
    """Author names go through escapeHtml to prevent XSS via username."""
    src = EDITOR.read_text(encoding='utf-8')
    assert 'escapeHtml(item.created_by_name)' in src
    assert 'escapeHtml(item.updated_by_name)' in src
```

- [ ] **Step 2: Run smoke tests — verify they fail**

Run: `cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_notes_editor_meta_smoke.py -v`

Expected: FAIL

- [ ] **Step 3: Verify `escapeHtml` helper exists in editor.html**

Run: `grep -n "function escapeHtml\|window.escapeHtml" app_unified/templates/notes/editor.html`

If helper not present, add at top of `<script>` block (around line 900, before any function that uses it):

```js
function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, function(c) {
    return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
  });
}
```

If already present (likely — F10 / earlier work added it), skip.

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
  // meta — 3 rows, each only renders if its data exists (F11)
  var meta = document.createElement('div');
  meta.style.cssText = 'font-size:.75rem;color:#999;margin-top:.4rem;line-height:1.5;';
  var rows = [];
  if (item.created_by_name) {
    rows.push('建立: ' + escapeHtml(item.created_by_name) + ' · ' + fmtDateTime(item.created_at));
  }
  if (item.modified_at && item.updated_by_name) {
    rows.push('修改: ' + escapeHtml(item.updated_by_name) + ' · ' + fmtDateTime(item.modified_at));
  }
  if (item.is_checked && item.checked_by_name) {
    rows.push('✓ 由 ' + escapeHtml(item.checked_by_name) + ' 在 ' + fmtDateTime(item.checked_at) + ' 完成');
  }
  meta.innerHTML = rows.join('<br>');
  content.appendChild(meta);
```

There may be a second similar meta render in the WebSocket re-render path (around line 1608). Find and apply the same change:

Run: `grep -n "checked_by_name.*完成" app_unified/templates/notes/editor.html`

If second occurrence exists with same single-row pattern, apply same rewrite.

- [ ] **Step 5: Run smoke tests — verify they pass**

Run: `cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/test_notes_editor_meta_smoke.py -v`

Expected: PASS (both tests)

- [ ] **Step 6: Run full test suite — verify no regression**

Run: `cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/pytest tests/ -q --tb=line`

Expected: 535 + new tests = ~545 passed (no regression)

- [ ] **Step 7: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/notes/editor.html app_unified/tests/test_notes_editor_meta_smoke.py
git commit -m "feat(editor): renderChecklistItem 3-row meta — 建立/修改/完成 (F11)"
```

---

## Task 6: Manual verify + push permission

**Files:** None (just verify + push)

- [ ] **Step 1: Restart local dev server**

If running, kill and restart to pick up model changes (Flask debug reload should auto-detect, but explicit restart safer):

```bash
fuser -k 5000/tcp 2>/dev/null
bash /home/hirain0126/projects/webapp/app_unified/scripts/run_local.sh &
```

Wait for `* Running on http://...:5000`.

- [ ] **Step 2: Manual verify checklist**

Open browser, `/dev` switch to alice (北店 員工).

Scenarios to verify on `/n/1` (or any checklist note):

1. **新增 item**：用 alice 加新項目「測試 F11」→ 應該立刻顯示「建立: alice · [time]」一行 meta、沒有「修改」行（因為 modified_at 還 NULL）
2. **編輯 text**：點編輯改文字、儲存 → meta 變成 2 行：「建立: alice · [t1]」+「修改: alice · [t2]」
3. **不同人勾選**：（用第二個帳號 / `/dev` 切其他帳號）勾完成 → 應該顯示 3 行：建立 / 修改 / ✓ 完成（其中「完成」是新勾的人）
4. **不同人加附件**：用第三個帳號加附件 → 「修改」行更新成那人 + 新時間
5. **回頭再勾選 / 取消勾選**：「修改」行**不**變、「✓ 完成」行才變
6. **舊 item**（資料庫已有的 F10 前 item）：無「建立」行；勾了會顯示「✓ 完成」；改了文字會出「修改」行（首次有資料）

順手驗：mynotes 不受影響（mynotes 用不同 model）；公告/筆記不顯示 checklist meta（不是 checklist）。

- [ ] **Step 3: Ask user for verify result**

User confirms working OK → proceed to push. If any case broken, report which and stop.

- [ ] **Step 4: Ask user push permission**

Per [[feedback_push_policy]] — user must explicitly approve push. Check current time vs [[feedback_push_peak_hours]] (19:30-21:00 forbidden).

Ask: "F11 本機驗 OK。現在 [HH:MM]、不在 peak (19:30-21:00)。可以 ff-merge main + push origin/main 嗎？"

- [ ] **Step 5: After user approves, push**

```bash
cd /home/hirain0126/projects/webapp
git checkout main
git merge --ff-only feat/checklist-item-authorship
git push origin main
git branch -d feat/checklist-item-authorship
```

- [ ] **Step 6: Monitor GHA build + tell user Zeabur Redeploy**

```bash
gh run list --workflow=docker.yml --limit 1
gh run list --workflow=test.yml --limit 1
```

Wait for both green. Inform user: "Build 綠了、去 Zeabur 點 Redeploy 把 app service 升到 :latest"。

Prod schema repair will run on first worker startup → 5 ALTERs idempotent IF NOT EXISTS → no downtime.

---

## Self-Review Summary

**Spec coverage:**
- §2 Scope (5 task: schema / route mutations / attachment / serializer / UI) → Task 1-5 ✓
- §3 UX decisions (3-row meta, 全文字, 舊 row 不 backfill) → Task 5 ✓
- §4.1 Schema 4 columns → Task 1 (refined to 5 with modified_at, justified in plan header) ✓
- §4.2 Schema repair 3-file alignment → Task 1 Step 4-6 ✓
- §4.3 Mutation behavior table → Task 2-3 + tests ✓
- §4.4 Serializer 4+2 fields → Task 4 ✓
- §4.5 WebSocket → Task 4 Step 5 ✓
- §5 UI (escapeHtml + 3-row + updated_at vs created_at) → Task 5 (spec UI condition `updated_at !== created_at` superseded by cleaner `modified_at && updated_by_name`) ✓
- §6 Tests (4 test files) → Task 1 / 2 / 3 / 4 / 5 ✓
- §7 Rollout (local verify + push permission) → Task 6 ✓

**Placeholder scan:** No TBD / TODO / vague handling. Each step has full code or commands.

**Type consistency:**
- Column names `created_by` / `created_by_name` / `updated_by` / `updated_by_name` / `modified_at` consistent across Task 1 (model + migration + ALTER) + Task 2-3 (route logic) + Task 4 (serializer) + Task 5 (frontend item.X reads)
- `tw_iso()` used for all timestamp serialization
- `current_user.username` used everywhere for snapshot name
- `escapeHtml` used in all 3 frontend rows

**Prod safety (per user explicit ask):** Top-of-plan "Prod Safety Analysis" section covers schema migration safety (PG metadata-only ALTER), runtime loading (~250 bytes/item payload increase = negligible), crash vectors (all NULL-safe, backward compat with old clients), capacity (well under 2 CPU / 8 GB), rollback (purely additive, no DROP/backfill).
