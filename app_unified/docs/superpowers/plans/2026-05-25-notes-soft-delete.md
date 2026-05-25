# Notes / Item Soft Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Note + ChecklistItem 改 soft delete (`deleted_at` + `deleted_by` columns)、24h 內 admin 可從 `/admin/logs` 新 tab 復原、超過 24h cleanup_soft_deleted lazy job 真刪 + R2 一併。

**Architecture:** Schema 加 4 columns (Note + ChecklistItem 各 2)、新增 `active()` classmethod 提供「filter 未刪」query；71 個 `Note.query` / `ChecklistItem.query` audit 分類 (list view 走 `active()`、admin/restore 走 `query` bypass)、DELETE endpoints 改 set deleted_at；cleanup 沿用既有 `notes/cleanup.py` lazy throttle pattern；admin restore UI 在既有 `dashboard.html` 加新 tab。

**Tech Stack:** Flask + SQLAlchemy + Alembic + vanilla JS + pytest。

**Spec:** `docs/superpowers/specs/2026-05-25-notes-soft-delete.md`

**Branch:** `feat/notes-soft-delete`（已開、chain on `feat/checklist-item-lock`）

**Query audit baseline**: 71 個 `Note.query` / `ChecklistItem.query` 站點（grep 2026-05-25 結果），跨 4 個 file：
- `notes/routes.py`: ~50 處（最多、含 list view、edit、API、attachment）
- `notes/ws.py`: 12 處（socket handlers、跟 routes 邏輯類似）
- `admin/routes.py`: 5 處（含 user cleanup、要 bypass filter）
- `notes/announcement_routes.py`: 0 處（公告獨立、不涉及）

---

## Task 1: Schema + Migration

**Files:**
- Modify: `models.py:94-122` (Note class) + `models.py:166-185` (ChecklistItem class)
- Create: `migrations/versions/013_soft_delete_notes_items.py`

- [ ] **Step 1: Note 加 deleted_at + deleted_by + relationship**

在 `models.py` `class Note` 內、`resolved_at = db.Column(...)` 後加：

```python
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)
    deleted_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
```

在 `locker = db.relationship(...)` 後加：

```python
    deleter = db.relationship("User", foreign_keys=[deleted_by])
```

- [ ] **Step 2: ChecklistItem 加 deleted_at + deleted_by + relationship**

在 `class ChecklistItem` 內、`locked_at = db.Column(...)` 後加：

```python
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)
    deleted_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
```

在 `locker = db.relationship(...)` 後加：

```python
    deleter = db.relationship("User", foreign_keys=[deleted_by])
```

- [ ] **Step 3: 寫 migration 013**

```python
"""Add deleted_at + deleted_by for soft delete (F1)

Revision ID: 013
Revises: 012
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa


revision = '013'
down_revision = '012'
branch_labels = None
depends_on = None


def upgrade():
    # Note
    op.add_column('notes',
        sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.add_column('notes',
        sa.Column('deleted_by', sa.Integer(), nullable=True))
    op.create_index('ix_notes_deleted_at', 'notes', ['deleted_at'])
    op.create_foreign_key(
        'fk_notes_deleted_by',
        'notes', 'users',
        ['deleted_by'], ['id'],
        ondelete='SET NULL'
    )
    # ChecklistItem
    op.add_column('checklist_items',
        sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.add_column('checklist_items',
        sa.Column('deleted_by', sa.Integer(), nullable=True))
    op.create_index('ix_checklist_items_deleted_at', 'checklist_items', ['deleted_at'])
    op.create_foreign_key(
        'fk_checklist_items_deleted_by',
        'checklist_items', 'users',
        ['deleted_by'], ['id'],
        ondelete='SET NULL'
    )


def downgrade():
    op.drop_constraint('fk_checklist_items_deleted_by', 'checklist_items', type_='foreignkey')
    op.drop_index('ix_checklist_items_deleted_at', 'checklist_items')
    op.drop_column('checklist_items', 'deleted_by')
    op.drop_column('checklist_items', 'deleted_at')
    op.drop_constraint('fk_notes_deleted_by', 'notes', type_='foreignkey')
    op.drop_index('ix_notes_deleted_at', 'notes')
    op.drop_column('notes', 'deleted_by')
    op.drop_column('notes', 'deleted_at')
```

- [ ] **Step 4: 跑既有 test 確認 db.create_all() 能起來**

```bash
PYTHONPATH=. ../venv/bin/python -m pytest tests/test_notes_lock.py tests/test_checklist_item_lock.py -q 2>&1 | tail -3
```

Expected: 全綠（既有 lock test 不該打破、新欄位 NULL 預設）

- [ ] **Step 5: 刪 dev.db re-seed（dev SQLite 既有 table 不會自動 ALTER）**

```bash
rm -f instance/dev.db
```

下次 `bash scripts/run_local.sh` 會 `db.create_all()` 重建含新欄位 + re-seed。

- [ ] **Step 6: Commit**

```bash
git add models.py migrations/versions/013_soft_delete_notes_items.py
git -c commit.gpgsign=false commit -m "feat(model): Note + ChecklistItem add deleted_at + deleted_by (F1 task 1)

migration 013、ondelete SET NULL、deleted_at indexed for cleanup query。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Soft delete helpers in notes/routes.py

**Files:**
- Modify: `notes/routes.py`（加在既有 item lock helpers 之後，~line 175 附近）

- [ ] **Step 1: 加 4 個 soft delete helper**

加在 `_broadcast_item_lock` 之後：

```python
# ══════════ Soft delete helpers (F1 spec) ══════════

def soft_delete_note(note, user_id):
    """軟刪除 note。子 item 不個別 mark (Note.deleted_at IS NULL filter 自動隔離)。
    連帶清 lock 避免殘留。"""
    note.deleted_at = datetime.utcnow()
    note.deleted_by = user_id
    note.locked_by = None
    note.locked_at = None


def soft_delete_item(item, user_id):
    """軟刪除 item。清 lock 避免殘留。"""
    item.deleted_at = datetime.utcnow()
    item.deleted_by = user_id
    item.locked_by = None
    item.locked_at = None


def restore_note(note):
    """還原 note。子 item 自動跟著恢復 (沒個別 mark)。"""
    note.deleted_at = None
    note.deleted_by = None


def restore_item(item):
    item.deleted_at = None
    item.deleted_by = None
```

- [ ] **Step 2: Commit**

```bash
git add notes/routes.py
git -c commit.gpgsign=false commit -m "feat(notes): soft_delete + restore helpers (F1 task 2)

soft_delete_note / soft_delete_item / restore_note / restore_item。
連帶清 lock 避免 deleted note 殘留 lock 卡 cleanup。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Note + ChecklistItem 加 `active()` classmethod

**Files:**
- Modify: `models.py`（Note class + ChecklistItem class）

- [ ] **Step 1: Note 加 classmethod**

在 `Note` class 內、`@property def display_author` **之前**加：

```python
    @classmethod
    def active(cls):
        """Query builder filter 出未軟刪的 notes。用於 list / edit view。"""
        return cls.query.filter(cls.deleted_at.is_(None))
```

- [ ] **Step 2: ChecklistItem 加 classmethod**

在 `ChecklistItem` class 末（relationship 之後）加：

```python
    @classmethod
    def active(cls):
        """Query builder filter 出未軟刪的 items。"""
        return cls.query.filter(cls.deleted_at.is_(None))
```

- [ ] **Step 3: Commit**

```bash
git add models.py
git -c commit.gpgsign=false commit -m "feat(model): Note.active() + ChecklistItem.active() classmethod (F1 task 3)

提供「filter 未刪 row」query builder、後續 audit 將 list/edit 路徑改用。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: DELETE endpoints 改 soft delete

**Files:**
- Modify: `notes/routes.py:708-730` (delete_note) + `notes/routes.py:1544-1585` (delete_checklist_item)

- [ ] **Step 1: delete_note 改 soft delete**

找到 `def delete_note(note_id):`（grep 確認 line）、看內部既有 `for a in note.attachments: ... delete_attachment` + `db.session.delete(note)` block，替換成：

```python
    # F1: soft delete、R2 attachment 不在這刪（24h cleanup 才刪）
    soft_delete_note(note, current_user.id)
    log = NoteLog(
        note_id=note.id,
        note_title=note.title,
        user_id=current_user.id,
        action='delete',
        diff=None,
    )
    db.session.add(log)
    db.session.commit()
    return jsonify({"status": "ok"})
```

具體找原內容、用 Edit 替換。原內容大致：

```python
    for a in note.attachments:
        try:
            r2_delete(a.object_key)
        except Exception:
            pass
    log = NoteLog(...)
    db.session.add(log)
    db.session.delete(note)
    db.session.commit()
    return jsonify({"status": "ok"})
```

(實際 grep 看現況再 edit)

- [ ] **Step 2: delete_checklist_item 改 soft delete**

找到 `def delete_checklist_item(item_id):`、原本（F2 改過）有：

```python
    for a in list(item.attachments):
        try:
            delete_attachment(a.object_key)
        except Exception:
            pass
    item_id_for_broadcast = item.id
    note_id_for_broadcast = item.note_id
    db.session.delete(item)
    note.updated_at = datetime.utcnow()
    note.updated_by = current_user.id
    db.session.commit()
    # ... broadcast ...
```

替換成：

```python
    # F1: soft delete、R2 attachment 不在這刪（24h cleanup 才刪）
    item_id_for_broadcast = item.id
    note_id_for_broadcast = item.note_id
    soft_delete_item(item, current_user.id)
    note.updated_at = datetime.utcnow()
    note.updated_by = current_user.id
    db.session.commit()
    # F1: broadcast deleted=True 仍發、client DOM 移除（從 user 視角等同 hard delete）
    from extensions import socketio
    try:
        socketio.emit('r', {
            'op': 'il',
            'note_id': note_id_for_broadcast,
            'item_id': item_id_for_broadcast,
            'locked_by_id': None,
            'locked_by_username': None,
            'deleted': True,
        }, room=f'note-{note_id_for_broadcast}', namespace='/')
    except Exception:
        pass
    return jsonify({"status": "ok"})
```

- [ ] **Step 3: Commit**

```bash
git add notes/routes.py
git -c commit.gpgsign=false commit -m "refactor(notes): DELETE endpoints 改 soft delete (F1 task 4)

delete_note / delete_checklist_item 改 set deleted_at + deleted_by、
不再 db.session.delete 也不再 R2 delete (24h cleanup 才真刪)。
WebSocket 'il' deleted=True broadcast 不變、client UI 等同 hard delete。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5a: Audit notes/routes.py — list/view queries 改用 active()

**Files:**
- Modify: `notes/routes.py` (~25 處 Note.query / ChecklistItem.query)

分類 (從 grep 結果)：

| Line | 用途 | 處理 |
|---|---|---|
| 327 | `Note.query` list view | 改 `Note.active()` |
| 455 | `Note.query` list view | 改 `Note.active()` |
| 541-545 | `Note.query.get_or_404` edit page | 改 `Note.active().filter_by(id=note_id).first_or_404()`（不能用 active().get、classmethod 返 BaseQuery）|
| 567-571 | edit lock page 同上 | 改 `Note.active().filter_by(...)` |
| 640 | `Note.query.get` ai summary | 改 `Note.active().filter_by(id=note_id).first()` |
| 672 | `Note.query.get` ai task | 改 `Note.active().filter_by(id=note_id).first()` |
| 711-715 | `delete_note` self lookup | **不改**（要能拿到 deleted 的 note 進行 re-delete？實際應該擋；先改 active）|
| 836 | `Note.query.get` thread pool | 改 `Note.active().filter_by(id=note_id).first()` |
| 1221 | `Note.query.filter(...is_ai_summary)` | 改 `Note.active().filter(...)` |
| 1232 | edit | 改 `Note.active().filter_by(id=note_id).first_or_404()` |
| 1236 | `notes_for_count` 統計 | 改 `Note.active().filter_by(...)` |
| 1256 | edit | 改 `Note.active().filter_by(...)` |
| 1299 | list query | 改 `Note.active()` |
| 1342 | list query | 改 `Note.active()` |
| 1373-1375 | `_get_note_for_edit` | 改 `Note.active().filter_by(...)` |
| 1412/1416 | `ChecklistItem.query.filter_by(note_id=...)` list items | 改 `ChecklistItem.active().filter_by(note_id=...)` |
| 1425/1449/1489/1545 | item heartbeat/release/PATCH/DELETE lookup | 改 `ChecklistItem.active().filter_by(id=item_id).first()`（**例外**：DELETE 內也許要看 deleted... 先一律 active）|
| 1605-1609/1681-1685/1750-1754 | attachment endpoints note lookup | 改 `Note.active().filter_by(...)` |
| 1620 | `ChecklistItem.query.filter_by(id=item_id, note_id=note_id)` attachment | 改 `ChecklistItem.active().filter_by(...)` |
| 1652 | `ChecklistItem.query.get` attachment | 改 `ChecklistItem.active().filter_by(id=item_id).first()` |
| 1716 | `Note.query.get(attachment.note_id)` attachment delete | 改 `Note.active().filter_by(...)` |
| 1739 | `ChecklistItem.query.get(item_id)` | 改 `ChecklistItem.active().filter_by(...)` |

- [ ] **Step 1: 在 notes/routes.py 每處按上表改動**

用 Edit 工具逐 line 改。例子（line 327）：

```python
# Before
query = Note.query
# After
query = Note.active()
```

例子（line 541）：

```python
# Before
note = Note.query.get_or_404(note_id)
# After
note = Note.active().filter_by(id=note_id).first_or_404()
```

例子（line 1412）:

```python
# Before
items = ChecklistItem.query.filter_by(note_id=note_id).order_by(...)
# After
items = ChecklistItem.active().filter_by(note_id=note_id).order_by(...)
```

**遇到不確定**：對 admin/cross-store 邏輯、保守用 `active()`（不會看到 deleted note、避免誤操作）。Restore endpoint Task 8 會 explicit 用 `Note.query` bypass。

- [ ] **Step 2: 跑既有 test 確認沒打破**

```bash
PYTHONPATH=. ../venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: 全綠。如有 fail：通常是 test 用 hard delete 後 expect 找不到 note、但現在 list 也用 active() filter→ 應該還是找不到（OK）。如 fail 看訊息 debug。

- [ ] **Step 3: Commit**

```bash
git add notes/routes.py
git -c commit.gpgsign=false commit -m "refactor(notes): audit Note.query / ChecklistItem.query → active() (F1 task 5a)

~25 處 list/edit/view query 改用 active() classmethod filter 出未軟刪 row。
Restore endpoint 之後另寫、保持 query 不過 filter。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5b: Audit notes/ws.py — socket handler queries 改用 active()

**Files:**
- Modify: `notes/ws.py`（12 處）

從 grep：line 50, 134, 136, 138, 192, 194, 196, 221, 223, 225, 255, 257, 259。全部是 `Note.query` / `Note.query.get` / `Note.query.filter_by(id=note_id, store=current_user.store).first()`。

- [ ] **Step 1: 全部改用 Note.active()**

| Line | Before | After |
|---|---|---|
| 50 | `query = Note.query` | `query = Note.active()` |
| 134 | `note = Note.query.get(note_id)` | `note = Note.active().filter_by(id=note_id).first()` |
| 136, 138 | `note = Note.query.filter_by(id=note_id, store=...).first()` | `note = Note.active().filter_by(id=note_id, store=...).first()` |
| 192, 221, 255 | same as 134 pattern | same as 134 |
| 194, 196, 223, 225, 257, 259 | same as 136 pattern | same as 136 |

- [ ] **Step 2: 跑全 test**

```bash
PYTHONPATH=. ../venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: 全綠。

- [ ] **Step 3: Commit**

```bash
git add notes/ws.py
git -c commit.gpgsign=false commit -m "refactor(notes/ws): socket queries 改用 Note.active() (F1 task 5b)

12 處 Note.query 改 Note.active()、跟 routes.py audit 同 pattern。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5c: Audit admin/routes.py — 分類 cleanup vs view query

**Files:**
- Modify: `admin/routes.py`（5 處）

從 grep：line 301, 302, 303, 304, 308。**全部是 admin user cleanup 場景**：

```python
Note.query.filter_by(user_id=user.id, author_name=None).update({"author_name": user.username})
Note.query.filter_by(user_id=user.id).update({"user_id": None})
Note.query.filter_by(updated_by=user.id).update({"updated_by": None})
Note.query.filter_by(locked_by=user.id).update({"locked_by": None, "locked_at": None})
ChecklistItem.query.filter_by(checked_by=user.id).update({"checked_by": None})
```

這些是「user 被刪除時、清理該 user 跟 notes/items 的引用」。應該 cover 所有 row（包含已 soft-deleted、避免之後復原時還引用已刪 user）。

- [ ] **Step 1: 保持 admin/routes.py 不變**

5 處全部**不**改、保留 `Note.query` / `ChecklistItem.query`、留 comment 解釋：

在 admin/routes.py line 301 之前加 comment：

```python
# F1: 以下 5 處 user-cleanup update 故意 bypass soft-delete filter，
# 因為要 cover 所有 row (含 soft-deleted) — 否則 restore 時引用已刪 user 仍 dangling。
```

- [ ] **Step 2: Commit**

```bash
git add admin/routes.py
git -c commit.gpgsign=false commit -m "docs(admin/routes): comment soft-delete bypass for user-cleanup (F1 task 5c)

5 處 user-cleanup update 故意保留 Note.query (bypass active()) cover
所有 row 含 soft-deleted。加 comment 解釋給未來 reviewer。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: cleanup.py 加 cleanup_soft_deleted

**Files:**
- Modify: `notes/cleanup.py`

- [ ] **Step 1: 加 module-level state + new function**

在 cleanup.py `_last_run: float = 0.0` 之後加：

```python
_last_soft_delete_run: float = 0.0
```

在 `cleanup_resolved_notes` 之後加新 function：

```python
def cleanup_soft_deleted(force: bool = False) -> tuple:
    """24h 後 hard delete soft-deleted notes + items + R2。回傳 (notes_deleted, items_deleted)。"""
    global _last_soft_delete_run
    now = time.time()
    if not force and now - _last_soft_delete_run < _THROTTLE_SECONDS:
        return 0, 0
    _last_soft_delete_run = now

    from datetime import datetime, timedelta
    from models import Note, NoteAttachment, ChecklistItem
    cutoff = datetime.utcnow() - timedelta(hours=24)

    # 1. 過期 deleted notes → R2 delete 所有 attachments (含 item attachments，
    #    都在 NoteAttachment table、用 note_id 關聯) + db delete cascade
    notes = Note.query.filter(
        Note.deleted_at.isnot(None),
        Note.deleted_at <= cutoff,
    ).all()
    notes_deleted = 0
    for n in notes:
        for att in NoteAttachment.query.filter_by(note_id=n.id).all():
            try:
                delete_attachment(att.object_key)
            except Exception:
                pass
        db.session.delete(n)  # cascade items + attachments via FK
        notes_deleted += 1

    # 2. 過期 deleted items（note 還在、單獨 deleted item）
    items = ChecklistItem.query.filter(
        ChecklistItem.deleted_at.isnot(None),
        ChecklistItem.deleted_at <= cutoff,
    ).all()
    items_deleted = 0
    for item in items:
        # item attachment 用 NoteAttachment + checklist_item_id 關聯
        for att in NoteAttachment.query.filter_by(checklist_item_id=item.id).all():
            try:
                delete_attachment(att.object_key)
            except Exception:
                pass
        db.session.delete(item)
        items_deleted += 1

    db.session.commit()
    return notes_deleted, items_deleted
```

- [ ] **Step 2: 找到既有 cleanup hook 加上 cleanup_soft_deleted 呼叫**

Grep 現況：

```bash
grep -rn "cleanup_resolved_notes" --include="*.py" 2>/dev/null
```

找到 hook 位置（通常 app.py 或 routes.py before_request hook、用 try/except 包），在同位置加：

```python
try:
    cleanup_soft_deleted()
except Exception:
    pass
```

實際 edit 看現況。如果 hook 在 app.py：

```python
# app.py 找到
from notes.cleanup import cleanup_resolved_notes
@app.before_request
def _cleanup_hook():
    try:
        cleanup_resolved_notes()
    except Exception:
        pass
```

改成：

```python
from notes.cleanup import cleanup_resolved_notes, cleanup_soft_deleted
@app.before_request
def _cleanup_hook():
    try:
        cleanup_resolved_notes()
    except Exception:
        pass
    try:
        cleanup_soft_deleted()
    except Exception:
        pass
```

- [ ] **Step 3: Commit**

```bash
git add notes/cleanup.py app.py
git -c commit.gpgsign=false commit -m "feat(cleanup): cleanup_soft_deleted 24h hard delete + R2 (F1 task 6)

加新 function、cutoff = now - 24h、cascade delete + R2 cleanup。
Hook to before_request 跟既有 cleanup_resolved_notes 平行跑、share _THROTTLE_SECONDS=3600。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Admin restore API endpoints

**Files:**
- Modify: `admin/routes.py`（加 3 個新 endpoint）

- [ ] **Step 1: 加 GET /admin/api/deleted endpoint**

在 admin/routes.py 末尾加：

```python
@admin_bp.route("/api/deleted", methods=["GET"])
@login_required
def list_deleted():
    """列 24h 內 soft-deleted notes + items。admin 看本店、super_admin 跨店。"""
    if not current_user.is_admin():
        abort(403)
    from datetime import datetime, timedelta
    from models import Note, ChecklistItem, User
    cutoff = datetime.utcnow() - timedelta(hours=24)
    now = datetime.utcnow()

    # 1. Deleted notes
    note_q = Note.query.filter(
        Note.deleted_at.isnot(None),
        Note.deleted_at > cutoff,  # 還沒過期、仍可救
    )
    if not current_user.is_super_admin():
        note_q = note_q.filter_by(store=current_user.store)
    deleted_notes = note_q.order_by(Note.deleted_at.desc()).all()

    # 2. Deleted items (parent note 還在的)
    item_q = ChecklistItem.query.filter(
        ChecklistItem.deleted_at.isnot(None),
        ChecklistItem.deleted_at > cutoff,
    ).join(Note, ChecklistItem.note_id == Note.id).filter(
        Note.deleted_at.is_(None),  # parent note 不能是 deleted 的
    )
    if not current_user.is_super_admin():
        item_q = item_q.filter(Note.store == current_user.store)
    deleted_items = item_q.order_by(ChecklistItem.deleted_at.desc()).all()

    def _note_dict(n):
        d = n.deleted_by_user if hasattr(n, 'deleter') else None
        deleter = User.query.get(n.deleted_by) if n.deleted_by else None
        hours_remaining = round(24 - (now - n.deleted_at).total_seconds() / 3600, 1)
        return {
            "id": n.id, "title": n.title or "(無標題)", "store": n.store,
            "note_type": n.note_type,
            "deleted_at": n.deleted_at.strftime("%Y-%m-%d %H:%M"),
            "deleted_by_name": deleter.username if deleter else "(unknown)",
            "hours_remaining": hours_remaining,
        }

    def _item_dict(item):
        deleter = User.query.get(item.deleted_by) if item.deleted_by else None
        hours_remaining = round(24 - (now - item.deleted_at).total_seconds() / 3600, 1)
        return {
            "id": item.id, "text": (item.text or "")[:60], "note_id": item.note_id,
            "note_title": (item.note.title if item.note else "(unknown)"),
            "deleted_at": item.deleted_at.strftime("%Y-%m-%d %H:%M"),
            "deleted_by_name": deleter.username if deleter else "(unknown)",
            "hours_remaining": hours_remaining,
        }

    return jsonify({
        "notes": [_note_dict(n) for n in deleted_notes],
        "items": [_item_dict(i) for i in deleted_items],
    })
```

- [ ] **Step 2: 加 POST /admin/api/restore/note/<id>**

```python
@admin_bp.route("/api/restore/note/<int:note_id>", methods=["POST"])
@login_required
def restore_note_endpoint(note_id):
    if not current_user.is_admin():
        abort(403)
    from models import Note
    note = Note.query.filter_by(id=note_id).first()
    if not note or note.deleted_at is None:
        return jsonify({"status": "error", "message": "找不到或已過期"}), 404
    if not current_user.is_super_admin() and note.store != current_user.store:
        abort(403)
    from notes.routes import restore_note
    restore_note(note)
    _log_user_action('restore_note', None,
                     detail=f'note_id={note.id}, title={note.title}')
    db.session.commit()
    return jsonify({"status": "ok"})
```

- [ ] **Step 3: 加 POST /admin/api/restore/item/<id>**

```python
@admin_bp.route("/api/restore/item/<int:item_id>", methods=["POST"])
@login_required
def restore_item_endpoint(item_id):
    if not current_user.is_admin():
        abort(403)
    from models import ChecklistItem
    item = ChecklistItem.query.filter_by(id=item_id).first()
    if not item or item.deleted_at is None:
        return jsonify({"status": "error", "message": "找不到或已過期"}), 404
    if not current_user.is_super_admin():
        if not item.note or item.note.store != current_user.store:
            abort(403)
    from notes.routes import restore_item
    restore_item(item)
    _log_user_action('restore_item', None,
                     detail=f'item_id={item.id}, note_id={item.note_id}')
    db.session.commit()
    return jsonify({"status": "ok"})
```

- [ ] **Step 4: Commit**

```bash
git add admin/routes.py
git -c commit.gpgsign=false commit -m "feat(admin): restore API endpoints (F1 task 7)

GET /admin/api/deleted 列 24h 內 deleted notes + items (admin 本店、
super_admin 跨店)。POST /admin/api/restore/{note,item}/<id> 還原、
寫 UserLog action='restore_note'/'restore_item'。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Admin dashboard.html 加「已刪除」tab

**Files:**
- Modify: `templates/admin/dashboard.html`（加 tab UI + fetch + JS）

- [ ] **Step 1: 找到 logs section 加新 tab**

Grep dashboard.html：

```bash
grep -n "/admin/logs\|<button.*tab\|class=\"tab" templates/admin/dashboard.html | head -10
```

定位 logs section（line ~459 附近、`const res = await fetch('/admin/logs');`）。

在該 section 加新 tab 按鈕 + tab panel + fetch JS。具體 markup 依現有 tab pattern。如果現況 logs section 沒 tab 機制（單一 list）、新增 tab 結構。

範本（在 logs section markup 內加）：

```html
<div class="tabs" style="display:flex;gap:.5rem;margin-bottom:1rem;border-bottom:1px solid #ddd;">
  <button class="tab-btn active" data-tab="note-logs"
          onclick="switchLogTab('note-logs')">操作紀錄</button>
  <button class="tab-btn" data-tab="user-logs"
          onclick="switchLogTab('user-logs')">用戶紀錄</button>
  <button class="tab-btn" data-tab="deleted"
          onclick="switchLogTab('deleted')">
    已刪除 <span id="deleted-badge" style="background:#e74c3c;color:#fff;border-radius:10px;padding:0 .4rem;font-size:.7rem;display:none;"></span>
  </button>
</div>

<div id="log-panel-note-logs" class="log-panel"><!-- 既有 note logs --></div>
<div id="log-panel-user-logs" class="log-panel" style="display:none;"><!-- 既有 user logs --></div>
<div id="log-panel-deleted" class="log-panel" style="display:none;">
  <div id="deleted-list"></div>
</div>
```

```javascript
function switchLogTab(name) {
  document.querySelectorAll('.tab-btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.tab === name);
  });
  document.querySelectorAll('.log-panel').forEach(function(p) {
    p.style.display = (p.id === 'log-panel-' + name) ? '' : 'none';
  });
  if (name === 'deleted') loadDeleted();
}

async function loadDeleted() {
  const res = await fetch('/admin/api/deleted');
  if (!res.ok) return;
  const data = await res.json();
  const list = document.getElementById('deleted-list');
  list.innerHTML = '';
  const total = data.notes.length + data.items.length;
  const badge = document.getElementById('deleted-badge');
  if (total > 0) { badge.textContent = total; badge.style.display = 'inline-block'; }
  else { badge.style.display = 'none'; }

  data.notes.forEach(function(n) {
    const row = document.createElement('div');
    row.style.cssText = 'padding:.7rem;border:1px solid #eee;border-radius:6px;margin-bottom:.5rem;display:flex;gap:.8rem;align-items:center;';
    row.innerHTML = '<span style="background:#3498db;color:#fff;padding:.2rem .5rem;border-radius:4px;font-size:.75rem;">' +
                    (n.note_type === 'checklist' ? '確認表單' : '筆記') + '</span>' +
                    '<div style="flex:1;"><strong>' + escapeHtml(n.title) + '</strong>' +
                    '<div style="font-size:.8rem;color:#888;">' + (n.store || '') +
                    ' · ' + n.deleted_at + ' 由 ' + escapeHtml(n.deleted_by_name) +
                    ' 刪除 · 剩 ' + n.hours_remaining + ' 小時</div></div>' +
                    '<button class="btn btn-primary" onclick="restoreNote(' + n.id + ', this)">復原</button>';
    list.appendChild(row);
  });

  data.items.forEach(function(item) {
    const row = document.createElement('div');
    row.style.cssText = 'padding:.7rem;border:1px solid #eee;border-radius:6px;margin-bottom:.5rem;display:flex;gap:.8rem;align-items:center;';
    row.innerHTML = '<span style="background:#27ae60;color:#fff;padding:.2rem .5rem;border-radius:4px;font-size:.75rem;">項目</span>' +
                    '<div style="flex:1;">「' + escapeHtml(item.text) + '」<small>來自 ' +
                    escapeHtml(item.note_title) + '</small>' +
                    '<div style="font-size:.8rem;color:#888;">' + item.deleted_at +
                    ' 由 ' + escapeHtml(item.deleted_by_name) +
                    ' 刪除 · 剩 ' + item.hours_remaining + ' 小時</div></div>' +
                    '<button class="btn btn-primary" onclick="restoreItem(' + item.id + ', this)">復原</button>';
    list.appendChild(row);
  });

  if (total === 0) list.innerHTML = '<div style="text-align:center;color:#888;padding:2rem;">最近 24 小時內無刪除記錄</div>';
}

async function restoreNote(noteId, btn) {
  if (!confirm('確定復原這個筆記？')) return;
  btn.disabled = true;
  const res = await fetch('/admin/api/restore/note/' + noteId, { method: 'POST' });
  if (res.ok) {
    btn.closest('div').style.background = '#d4edda';
    setTimeout(loadDeleted, 800);
  } else {
    alert('復原失敗');
    btn.disabled = false;
  }
}

async function restoreItem(itemId, btn) {
  if (!confirm('確定復原這個項目？')) return;
  btn.disabled = true;
  const res = await fetch('/admin/api/restore/item/' + itemId, { method: 'POST' });
  if (res.ok) {
    btn.closest('div').style.background = '#d4edda';
    setTimeout(loadDeleted, 800);
  } else {
    alert('復原失敗');
    btn.disabled = false;
  }
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, function(c) {
    return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
  });
}
```

如 escapeHtml 已存在 dashboard.html、不重複定義。

- [ ] **Step 2: Commit**

```bash
git add templates/admin/dashboard.html
git -c commit.gpgsign=false commit -m "feat(admin/dashboard): 已刪除 tab + restore UI (F1 task 8)

新 tab + badge 顯示 24h 內可救 row 數、列 deleted notes + items、
每行復原 button + confirm dialog → POST API + 0.8s 後 reload list。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Pytest backend tests

**Files:**
- Create: `tests/test_soft_delete.py`

- [ ] **Step 1: 寫 ~15 條 test**

```python
"""Soft delete tests for F1 spec: docs/superpowers/specs/2026-05-25-notes-soft-delete.md"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from extensions import db
from models import User, Note, ChecklistItem, NoteAttachment


def _mk_users(db_session):
    suffix = uuid.uuid4().hex[:6]
    alice = User(username=f"alice_{suffix}", store="A", role="admin")
    alice.set_password("x")
    bob = User(username=f"bob_{suffix}", store="A", role="user")
    bob.set_password("x")
    db_session.add_all([alice, bob])
    db_session.commit()
    return alice, bob


def _mk_note(db_session, store="A", note_type="note"):
    n = Note(title="t", content="c", store=store, category="misc", note_type=note_type)
    db_session.add(n)
    db_session.commit()
    return n


def _mk_item(db_session, note, text="x"):
    item = ChecklistItem(note_id=note.id, text=text, order_index=0)
    db_session.add(item)
    db_session.commit()
    return item


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        import time
        sess["_last_request_at"] = int(time.time())


def test_soft_delete_note_sets_deleted_at(app, db_session):
    from notes.routes import soft_delete_note
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    soft_delete_note(n, alice.id)
    db_session.commit()
    db_session.refresh(n)
    assert n.deleted_at is not None
    assert n.deleted_by == alice.id


def test_soft_delete_item_sets_deleted_at(app, db_session):
    from notes.routes import soft_delete_item
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session, note_type="checklist")
    item = _mk_item(db_session, n)
    soft_delete_item(item, alice.id)
    db_session.commit()
    db_session.refresh(item)
    assert item.deleted_at is not None
    assert item.deleted_by == alice.id


def test_delete_note_endpoint_soft_deletes(app, client, db_session):
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    nid = n.id
    _login_as(client, alice)
    res = client.delete(f'/notes/api/{nid}')
    assert res.status_code == 200
    n2 = Note.query.filter_by(id=nid).first()
    assert n2 is not None
    assert n2.deleted_at is not None


def test_delete_item_endpoint_soft_deletes(app, client, db_session):
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session, note_type="checklist")
    item = _mk_item(db_session, n)
    iid = item.id
    _login_as(client, alice)
    res = client.delete(f'/notes/api/items/{iid}')
    assert res.status_code == 200
    item2 = ChecklistItem.query.filter_by(id=iid).first()
    assert item2 is not None
    assert item2.deleted_at is not None


def test_soft_deleted_note_not_in_list(app, db_session):
    alice, _ = _mk_users(db_session)
    n1 = _mk_note(db_session)
    n2 = _mk_note(db_session)
    n2.deleted_at = datetime.utcnow()
    n2.deleted_by = alice.id
    db_session.commit()
    active = Note.active().all()
    assert n1 in active
    assert n2 not in active


def test_soft_deleted_item_not_in_active(app, db_session):
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session, note_type="checklist")
    i1 = _mk_item(db_session, n)
    i2 = _mk_item(db_session, n)
    i2.deleted_at = datetime.utcnow()
    db_session.commit()
    active = ChecklistItem.active().filter_by(note_id=n.id).all()
    assert i1 in active
    assert i2 not in active


def test_delete_note_does_not_delete_r2_attachment(app, client, db_session):
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    att = NoteAttachment(note_id=n.id, filename="x.jpg",
                          object_key="key1", file_size=100, content_type="image/jpeg")
    db_session.add(att)
    db_session.commit()
    _login_as(client, alice)
    with patch('notes.routes.r2_delete') as mock_r2:
        res = client.delete(f'/notes/api/{n.id}')
        assert res.status_code == 200
        mock_r2.assert_not_called()


def test_cleanup_soft_deleted_within_24h_no_action(app, db_session):
    from notes.cleanup import cleanup_soft_deleted
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    n.deleted_at = datetime.utcnow() - timedelta(hours=10)  # < 24h
    n.deleted_by = alice.id
    db_session.commit()
    notes_deleted, items_deleted = cleanup_soft_deleted(force=True)
    assert notes_deleted == 0
    assert Note.query.filter_by(id=n.id).first() is not None


def test_cleanup_soft_deleted_after_24h_hard_deletes(app, db_session):
    from notes.cleanup import cleanup_soft_deleted
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    n.deleted_at = datetime.utcnow() - timedelta(hours=25)  # > 24h
    n.deleted_by = alice.id
    db_session.commit()
    notes_deleted, _ = cleanup_soft_deleted(force=True)
    assert notes_deleted == 1
    assert Note.query.filter_by(id=n.id).first() is None


def test_cleanup_soft_deleted_calls_r2_delete(app, db_session):
    from notes.cleanup import cleanup_soft_deleted
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    att = NoteAttachment(note_id=n.id, filename="x.jpg",
                          object_key="ck1", file_size=100, content_type="image/jpeg")
    db_session.add(att)
    n.deleted_at = datetime.utcnow() - timedelta(hours=25)
    n.deleted_by = alice.id
    db_session.commit()
    with patch('notes.cleanup.delete_attachment') as mock_r2:
        cleanup_soft_deleted(force=True)
        mock_r2.assert_called_once_with("ck1")


def test_restore_note_clears_deleted_at(app, db_session):
    from notes.routes import restore_note
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    n.deleted_at = datetime.utcnow()
    n.deleted_by = alice.id
    db_session.commit()
    restore_note(n)
    db_session.commit()
    assert n.deleted_at is None
    assert n.deleted_by is None


def test_admin_list_deleted_endpoint(app, client, db_session):
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session, store="A")
    n.deleted_at = datetime.utcnow() - timedelta(hours=1)
    n.deleted_by = alice.id
    db_session.commit()
    _login_as(client, alice)
    res = client.get('/admin/api/deleted')
    assert res.status_code == 200
    data = res.get_json()
    assert len(data['notes']) >= 1
    assert any(x['id'] == n.id for x in data['notes'])


def test_admin_restore_note_endpoint(app, client, db_session):
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session, store="A")
    n.deleted_at = datetime.utcnow() - timedelta(hours=1)
    n.deleted_by = alice.id
    db_session.commit()
    _login_as(client, alice)
    res = client.post(f'/admin/api/restore/note/{n.id}')
    assert res.status_code == 200
    db_session.refresh(n)
    assert n.deleted_at is None


def test_admin_restore_cross_store_rejected_for_admin(app, client, db_session):
    alice, _ = _mk_users(db_session)
    alice.store = "A"
    n_other_store = _mk_note(db_session, store="B")
    n_other_store.deleted_at = datetime.utcnow() - timedelta(hours=1)
    n_other_store.deleted_by = alice.id
    db_session.commit()
    _login_as(client, alice)
    res = client.post(f'/admin/api/restore/note/{n_other_store.id}')
    assert res.status_code == 403


def test_admin_restore_after_24h_returns_404(app, client, db_session):
    alice, _ = _mk_users(db_session)
    n = _mk_note(db_session)
    # 模擬 cleanup 已跑、hard delete 完了 — 直接 db.session.delete
    db_session.delete(n)
    db_session.commit()
    _login_as(client, alice)
    res = client.post('/admin/api/restore/note/999999')
    assert res.status_code == 404
```

- [ ] **Step 2: 跑新 test**

```bash
PYTHONPATH=. ../venv/bin/python -m pytest tests/test_soft_delete.py -v 2>&1 | tail -25
```

Expected: 15 passed。如有 fail：
- Import error → 確認 helpers Task 2 已 commit
- Endpoint 404 → 確認 Task 4 / Task 7 已 commit
- Permission test fail → 確認 admin role + store 設對

- [ ] **Step 3: 跑全 suite 確認沒打破其他 test**

```bash
PYTHONPATH=. ../venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: 0 failed（既有 419 + 新 15 = 434 passed）。

- [ ] **Step 4: Commit**

```bash
git add tests/test_soft_delete.py
git -c commit.gpgsign=false commit -m "test(notes): soft delete + restore + cleanup tests (F1 task 9)

15 條 test：helpers / endpoints soft delete / R2 not called on delete /
cleanup 24h boundary / restore / admin permission cross-store / expired 404。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Local manual verification（user 跑）

**Files:** 無新檔、user 手動驗證

按 [[feedback_local_test_before_push]] 流程。

- [ ] **Step 1: 確認 dev server 跑 + 重新 seed**

```bash
fuser -k 5000/tcp 2>&1
rm -f instance/dev.db
bash scripts/run_local.sh
```

開 Chrome `http://localhost:5000/dev` 切 admin。

- [ ] **Step 2: 建 + 刪 sample data**

- 建 1 個一般 note `/notes/new?type=note` → 存
- 建 1 個 checklist note → 存 + 加 3 個 item
- 刪一般 note（刪除按鈕）
- 進 checklist note edit、刪 2 個 item

- [ ] **Step 3: 驗 deleted 不出現在 list**

- `/notes/` → 確認剛刪的 note 不在 list（list view 自動 active() filter）
- 進 checklist note → 確認剛刪的 2 個 item 不在 checklist

- [ ] **Step 4: 進 /admin/dashboard 驗 「已刪除」tab**

- Logs section 看到新 tab「已刪除 N」
- 點開 tab → 看到 3 行（1 note + 2 item）
- 每行顯示 標題 / 店別 / 「剛剛由 admin 刪除、剩 ~24 小時」/ 復原 button

- [ ] **Step 5: 復原 1 個 item**

- 點 1 個 item 的「復原」→ confirm dialog → 確認 → 0.8s 後 tab list 重新整理、該 item 消失
- 回去 checklist edit → 該 item 重新出現
- /admin/dashboard 「已刪除」tab 看 N badge 從 3 → 2

- [ ] **Step 6: 復原 deleted note**

- 點 deleted note 復原 → /notes/ 看 note 回到 list
- /admin/dashboard 「已刪除」tab 看 N badge 從 2 → 1

- [ ] **Step 7: 驗 cleanup 24h boundary（模擬）**

- 開 SQLite CLI 改 row：
  ```bash
  sqlite3 instance/dev.db
  UPDATE notes SET deleted_at = datetime('now', '-25 hours') WHERE id = ?;
  .exit
  ```
  （把剩下那 deleted item 改成 25 小時前）
- reload 任何 page → trigger before_request hook → cleanup_soft_deleted 跑（force=False、_THROTTLE_SECONDS=3600 第一次後不再跑）
- 第一次跑時 deleted row 應被 hard delete
- DB 直接看 `SELECT * FROM checklist_items WHERE deleted_at IS NOT NULL` → 應該 0 row
- /admin/dashboard 「已刪除」tab N badge → 0

(注意：cleanup throttle 1 小時、所以 dev test 可能要 `cleanup_soft_deleted(force=True)` 從 Python REPL 直接呼叫)

- [ ] **Step 8: User 確認 OK 才算 done**

---

## Self-Review 對照 spec

| Spec 段落 | 對應 Task |
|---|---|
| §1 Schema (2 tables × 2 columns + indexes) | Task 1 |
| §2 Helpers (soft_delete / restore × 2) | Task 2 |
| §3 DELETE endpoints 改 soft delete | Task 4 |
| §4 Query filter audit (~30 處) — 用 active() classmethod 策略 | Task 3 (helper) + Task 5a/5b/5c (audit 71 處) |
| §5 ChecklistItem 連帶（Note deleted 隔離 item） | Task 5a (`_get_note_for_edit` 走 active()) + Task 4 (soft_delete_note 不個別 mark item) |
| §6 Cleanup (lazy on request + 24h boundary) | Task 6 |
| §7 Admin UI (deleted tab + restore button) + API | Task 7 (API) + Task 8 (UI) |
| §8 UserLog 紀錄 restore action | Task 7 (_log_user_action call) |
| 邊界情境表 17 條 | Task 9 涵蓋 11+ 條 + Task 10 manual 7 步 |
| Capacity check | spec 內、無 prod 壓力 |

**Type consistency check**:
- `soft_delete_note` / `soft_delete_item` / `restore_note` / `restore_item` 在 Task 2-7 名稱對齊
- `Note.active()` / `ChecklistItem.active()` 在 Task 3-5 用法對齊
- `cleanup_soft_deleted` 在 Task 6-9 一致
- API endpoint paths `/admin/api/deleted` + `/admin/api/restore/{note,item}/<id>` 在 Task 7-8 對齊

**Placeholder scan**: 無 TODO / TBD、所有 code step 含完整 code、commit message 含 task 編號。

---

## 不在這個 plan 內

- 不 ff-merge to main（按 chain 策略、F1 是最後一條、user 確認後一起 push 6 條）
- 不 push origin
- 不還原 .env.local OWM key（gitignored、無 push 影響）
- 不還原 dev/outbound_guard.py（F4 task 已 reset、現在不該再動）
- 不更新 project memory（push 後一次性寫紀錄整 6 條 feature）
