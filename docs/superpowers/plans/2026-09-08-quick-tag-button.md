# 快速標籤鍵 Implementation Plan（階段一）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 checklist 項目與筆記上，把「已釘選的標籤」渲染成一鍵按鈕，按下即掛上並記錄誰按的、什麼時候，且不改變項目排序。

**Architecture:** 沿用現有的 `Tag.pinned`（每店最多 3 個）決定哪些標籤有按鈕。關聯表 `note_tags` / `checklist_item_tags` 各加三欄記錄掛標籤的人與時間。新增專用的單標籤 toggle 端點（不重用會覆寫整份清單的 `PUT .../tags`），並刻意不碰 `updated_at`。

**Tech Stack:** Flask 3 + Flask-SQLAlchemy、Alembic migration、原生 JS（編輯器是 inline script，無前端框架）、pytest。

**規格：** `docs/superpowers/specs/2026-09-08-quick-tag-and-keyword-tracking-design.md`

## Global Constraints

- **不得觸碰 `updated_at`**：`ChecklistItem` 與 `Note` 的 `updated_at` 是項目/筆記列表的排序依據。掛標籤絕不可更新它，否則使用者交接時逐項按會把整張清單的順序打亂。這是使用者的硬性要求，需有測試守著。
- **`MAX_TAGS_PER_ITEM = 5`**（`app_unified/notes/tags.py:13`）。上限已滿時必須回明確錯誤訊息，不可靜默無反應。
- **`PIN_LIMIT = 3`**（同檔 :12）。釘選上限不變，本階段不動。
- **權限沿用既有標籤指派**：`current_user.role != 'super_admin' and X.store != current_user.store` → 403。不新增權限概念。
- **新欄位一律 nullable**：既有的標籤關聯沒有這些值，UI 遇到 null 就不顯示 hover 資訊。
- **prod 的欄位要靠開機修復 DDL 才會生效**：本專案 prod 除了 Alembic migration，`app_unified/app.py` 的開機 schema 修復區塊還會跑一輪 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`。**兩邊都要加**，只加 migration 在 prod 不會生效。
- 所有指令都在 `app_unified/` 目錄下執行，Python 用 `../venv/bin/python`。

---

### Task 1: 資料模型 + migration + 開機修復 DDL

**Files:**
- Modify: `app_unified/models.py`（`NoteTag` 約 :526、`ChecklistItemTag` 約 :533）
- Create: `app_unified/migrations/versions/020_tag_assignment_authorship.py`
- Modify: `app_unified/app.py`（開機 schema 修復區塊末端，`thumb_key` 那個迴圈之後）
- Test: `app_unified/tests/test_tag_assignment_authorship.py`

**Interfaces:**
- Consumes: 無（第一個任務）
- Produces: `NoteTag.tagged_by` / `.tagged_by_name` / `.tagged_at`、`ChecklistItemTag` 同名三欄。後續任務全部依賴這三個欄位名。

- [ ] **Step 1: 寫失敗的測試**

建立 `app_unified/tests/test_tag_assignment_authorship.py`：

```python
"""標籤關聯要記錄「誰在什麼時候掛的」（2026-09-08 快速標籤鍵）。

三欄皆 nullable —— 既有資料沒有這些值，UI 遇到 null 就不顯示 hover。
"""
import uuid
from datetime import datetime

import pytest

from models import ChecklistItem, ChecklistItemTag, Note, NoteTag, Store, Tag, User


@pytest.fixture
def fixture_data(db_session):
    suffix = uuid.uuid4().hex[:6]
    store = Store(name=f'T{suffix}'.upper(), login_enabled=True)
    user = User(username=f'u-{suffix}', role='user')
    user.set_password('0000')
    db_session.add_all([store, user])
    db_session.commit()
    user.store = store.name
    note = Note(user_id=user.id, title='t', content='', note_type='checklist',
                store=store.name)
    db_session.add(note)
    db_session.commit()
    item = ChecklistItem(note_id=note.id, text='x', order_index=0)
    tag = Tag(store=store.name, name='陳店長知道', color='#3498db', pinned=True)
    db_session.add_all([item, tag])
    db_session.commit()
    yield store, user, note, item, tag
    ChecklistItemTag.query.filter_by(item_id=item.id).delete()
    NoteTag.query.filter_by(note_id=note.id).delete()
    ChecklistItem.query.filter_by(note_id=note.id).delete()
    db_session.delete(tag)
    db_session.delete(note)
    db_session.delete(user)
    db_session.delete(store)
    db_session.commit()


def test_item_tag_records_authorship(fixture_data, db_session):
    _, user, _, item, tag = fixture_data
    now = datetime.utcnow()
    db_session.add(ChecklistItemTag(item_id=item.id, tag_id=tag.id,
                                    tagged_by=user.id, tagged_by_name=user.username,
                                    tagged_at=now))
    db_session.commit()
    row = ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).first()
    assert row.tagged_by == user.id
    assert row.tagged_by_name == user.username
    assert row.tagged_at is not None


def test_note_tag_records_authorship(fixture_data, db_session):
    _, user, note, _, tag = fixture_data
    db_session.add(NoteTag(note_id=note.id, tag_id=tag.id,
                           tagged_by=user.id, tagged_by_name=user.username,
                           tagged_at=datetime.utcnow()))
    db_session.commit()
    row = NoteTag.query.filter_by(note_id=note.id, tag_id=tag.id).first()
    assert row.tagged_by_name == user.username


def test_authorship_columns_are_nullable(fixture_data, db_session):
    """既有資料沒有這些值 —— 不帶三欄也要能寫入。"""
    _, _, _, item, tag = fixture_data
    db_session.add(ChecklistItemTag(item_id=item.id, tag_id=tag.id))
    db_session.commit()
    row = ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).first()
    assert row.tagged_by is None
    assert row.tagged_by_name is None
    assert row.tagged_at is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `../venv/bin/python -m pytest tests/test_tag_assignment_authorship.py -q`
Expected: FAIL，錯誤訊息類似 `TypeError: 'tagged_by' is an invalid keyword argument for ChecklistItemTag`

- [ ] **Step 3: 在 models.py 加欄位**

把 `app_unified/models.py` 的兩個類別改成：

```python
class NoteTag(db.Model):
    __tablename__ = "note_tags"

    note_id = db.Column(db.Integer, db.ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    # 誰在什麼時候掛上這個標籤（2026-09-08 快速標籤鍵）。三欄皆 nullable：
    # 既有關聯沒有這些值，UI 遇到 null 就不顯示 hover 資訊。
    tagged_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    tagged_by_name = db.Column(EncryptedText, nullable=True)  # 快照，比照 ChecklistItem.checked_by_name
    tagged_at = db.Column(db.DateTime, nullable=True)


class ChecklistItemTag(db.Model):
    __tablename__ = "checklist_item_tags"

    item_id = db.Column(db.Integer, db.ForeignKey("checklist_items.id", ondelete="CASCADE"), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    tagged_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    tagged_by_name = db.Column(EncryptedText, nullable=True)
    tagged_at = db.Column(db.DateTime, nullable=True)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `../venv/bin/python -m pytest tests/test_tag_assignment_authorship.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 寫 Alembic migration**

建立 `app_unified/migrations/versions/020_tag_assignment_authorship.py`：

```python
"""tag assignment authorship: 誰在什麼時候掛上標籤

Revision ID: 020
Revises: 019
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa

revision = '020'
down_revision = '019'
branch_labels = None
depends_on = None

_TABLES = ("note_tags", "checklist_item_tags")


def upgrade():
    for t in _TABLES:
        op.add_column(t, sa.Column('tagged_by', sa.Integer(), nullable=True))
        op.add_column(t, sa.Column('tagged_by_name', sa.Text(), nullable=True))
        op.add_column(t, sa.Column('tagged_at', sa.DateTime(), nullable=True))


def downgrade():
    for t in _TABLES:
        op.drop_column(t, 'tagged_at')
        op.drop_column(t, 'tagged_by_name')
        op.drop_column(t, 'tagged_by')
```

- [ ] **Step 6: 加開機修復 DDL**

在 `app_unified/app.py` 的 schema 修復區塊裡，找到 `thumb_key` 那個迴圈（約 :444-446）：

```python
                for _tbl in ("note_attachments", "private_attachments", "announcement_attachments"):
                    _safe_ddl(f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS thumb_key TEXT")
```

在它**後面**加上：

```python
                # 標籤指派的作者資訊（2026-09-08 快速標籤鍵）。
                # ⚠️ 只加 Alembic migration 在 prod 不會生效 —— prod 靠這段 DDL。
                for _tbl in ("note_tags", "checklist_item_tags"):
                    _safe_ddl(f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS tagged_by INTEGER")
                    _safe_ddl(f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS tagged_by_name TEXT")
                    _safe_ddl(f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS tagged_at TIMESTAMP")
```

- [ ] **Step 7: 跑全套件確認沒弄壞別的**

Run: `../venv/bin/python -m pytest tests/ -q`
Expected: 全部 PASS（本任務前的基準是 866 passed，本任務後應為 869 passed）

- [ ] **Step 8: Commit**

```bash
git add app_unified/models.py app_unified/app.py \
        app_unified/migrations/versions/020_tag_assignment_authorship.py \
        app_unified/tests/test_tag_assignment_authorship.py
git commit -m "feat(tags): 標籤關聯記錄掛標籤的人與時間

note_tags / checklist_item_tags 各加 tagged_by / tagged_by_name / tagged_at，
供快速標籤鍵顯示「王小明 9/08 08:15」。三欄皆 nullable，既有關聯不受影響。

prod 的欄位靠 app.py 的開機修復 DDL 生效，只加 migration 不夠 —— 兩邊都加了。"
```

---

### Task 2: 標籤指派改成差集（前置缺陷修正）

**Files:**
- Modify: `app_unified/notes/tags.py`（`set_note_tags` :166、`set_item_tags` :195）
- Test: `app_unified/tests/test_tag_assignment_authorship.py`（沿用 Task 1 的檔案）

**Interfaces:**
- Consumes: Task 1 的 `tagged_by` / `tagged_by_name` / `tagged_at`
- Produces: `notes/tags.py::_stamp(row)` —— 把 `current_user` 與現在時間蓋到關聯列上，Task 3 會重用

**背景：** 現行的 `set_note_tags` / `set_item_tags` 是「先全刪再重插」。加了作者欄位之後，使用者只要從標籤選擇器動過任何一個標籤，先前存下的 `tagged_by` / `tagged_at` 就會被整批洗掉。這在加欄位之前無害（純關聯表），加了之後變成靜默資料遺失，所以是本功能的前置條件。

- [ ] **Step 1: 寫失敗的測試**

在 `app_unified/tests/test_tag_assignment_authorship.py` 末端追加：

```python


# ── 差集改寫：從選擇器改動其他標籤時，既有的作者資訊不可被洗掉 ──
def test_picker_preserves_existing_authorship(fixture_data, db_session, client):
    """這是最容易回歸的一條：全刪重插會讓 tagged_by/at 靜默消失。"""
    import time
    store, user, note, item, tag = fixture_data
    other = Tag(store=store.name, name='其他標籤', color='#27ae60', pinned=False)
    db_session.add(other)
    db_session.commit()

    # 先用「快速鍵那條路」的方式蓋上作者資訊
    stamped_at = datetime.utcnow()
    db_session.add(ChecklistItemTag(item_id=item.id, tag_id=tag.id,
                                    tagged_by=user.id, tagged_by_name=user.username,
                                    tagged_at=stamped_at))
    db_session.commit()

    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())

    # 從選擇器送出「原本那個 + 一個新的」
    res = client.put(f'/notes/api/items/{item.id}/tags',
                     json={'tag_ids': [tag.id, other.id]})
    assert res.status_code == 200, res.get_data(as_text=True)

    kept = ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).first()
    assert kept is not None
    assert kept.tagged_by_name == user.username, \
        '既有標籤的作者資訊被洗掉了 —— set_item_tags 仍在全刪重插'
    added = ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=other.id).first()
    assert added.tagged_by_name == user.username, '新加的標籤也要記錄作者'

    db_session.delete(other)
    db_session.commit()


def test_picker_removes_deselected_tag(fixture_data, db_session, client):
    """回歸防護：差集改寫不能把「移除」這件事弄丟。"""
    import time
    store, user, note, item, tag = fixture_data
    db_session.add(ChecklistItemTag(item_id=item.id, tag_id=tag.id))
    db_session.commit()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())
    res = client.put(f'/notes/api/items/{item.id}/tags', json={'tag_ids': []})
    assert res.status_code == 200
    assert ChecklistItemTag.query.filter_by(item_id=item.id).count() == 0
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `../venv/bin/python -m pytest tests/test_tag_assignment_authorship.py -q`
Expected: `test_picker_preserves_existing_authorship` FAIL，訊息為「既有標籤的作者資訊被洗掉了」

- [ ] **Step 3: 在 notes/tags.py 加 `_stamp` helper**

在 `app_unified/notes/tags.py` 的 `_tag_to_dict` 定義**之前**插入：

```python
def _stamp(row):
    """把「誰在什麼時候掛的」蓋到標籤關聯列上。

    快速標籤鍵與標籤選擇器兩條路都會呼叫，確保兩邊記錄的內容一致。
    """
    row.tagged_by = current_user.id
    row.tagged_by_name = current_user.username
    row.tagged_at = datetime.utcnow()
    return row
```

- [ ] **Step 4: 改寫 `set_item_tags`**

把 `app_unified/notes/tags.py` 裡這兩行：

```python
    ChecklistItemTag.query.filter_by(item_id=item.id).delete()
    for tid in tag_ids:
        db.session.add(ChecklistItemTag(item_id=item.id, tag_id=tid))
```

換成：

```python
    # 差集改寫，不可退回「全刪重插」—— 那會把 tagged_by / tagged_at 靜默洗掉。
    existing = {r.tag_id: r for r in
                ChecklistItemTag.query.filter_by(item_id=item.id).all()}
    wanted = set(tag_ids)
    for tid, row in existing.items():
        if tid not in wanted:
            db.session.delete(row)
    for tid in wanted - set(existing):
        db.session.add(_stamp(ChecklistItemTag(item_id=item.id, tag_id=tid)))
```

- [ ] **Step 5: 改寫 `set_note_tags`**

把同檔裡這兩行：

```python
    NoteTag.query.filter_by(note_id=n.id).delete()
    for tid in tag_ids:
        db.session.add(NoteTag(note_id=n.id, tag_id=tid))
```

換成：

```python
    # 差集改寫，理由同 set_item_tags。
    existing = {r.tag_id: r for r in NoteTag.query.filter_by(note_id=n.id).all()}
    wanted = set(tag_ids)
    for tid, row in existing.items():
        if tid not in wanted:
            db.session.delete(row)
    for tid in wanted - set(existing):
        db.session.add(_stamp(NoteTag(note_id=n.id, tag_id=tid)))
```

- [ ] **Step 6: 跑測試確認通過**

Run: `../venv/bin/python -m pytest tests/test_tag_assignment_authorship.py -q`
Expected: PASS（5 passed）

- [ ] **Step 7: 跑全套件**

Run: `../venv/bin/python -m pytest tests/ -q`
Expected: 全部 PASS（871 passed）

- [ ] **Step 8: Commit**

```bash
git add app_unified/notes/tags.py app_unified/tests/test_tag_assignment_authorship.py
git commit -m "fix(tags): 標籤指派改成差集，不再全刪重插

set_note_tags / set_item_tags 原本先刪光再重插。加了 tagged_by/at 之後，
使用者只要從選擇器動過任何一個標籤，先前的作者資訊就會被整批洗掉。
改成只刪移除的、只插新增的、原有的不動。

順帶讓選擇器這條路也會記錄作者（共用 _stamp），兩條路的紀錄一致。"
```

---

### Task 3: 快速標籤 toggle API

**Files:**
- Modify: `app_unified/notes/tags.py`（檔案末端新增四個路由）
- Test: `app_unified/tests/test_quick_tag_api.py`

**Interfaces:**
- Consumes: Task 2 的 `_stamp(row)`；Task 1 的三個欄位
- Produces: 四個端點，Task 5/6 的前端會呼叫：
  - `POST   /notes/api/items/<int:item_id>/tags/<int:tag_id>` → `{tag_id, tagged_by_name, tagged_at}`
  - `DELETE /notes/api/items/<int:item_id>/tags/<int:tag_id>` → `{ok: true}`
  - `POST   /notes/api/<int:note_id>/tags/<int:tag_id>` → 同上
  - `DELETE /notes/api/<int:note_id>/tags/<int:tag_id>` → 同上

**為什麼不重用既有的 `PUT .../tags`：** 那個端點吃的是「整份標籤清單」，前端必須先知道目前有哪些標籤才能送出，兩個人同時操作會互相覆蓋。快速鍵是單一標籤的 toggle，用專屬端點才是原子操作。

- [ ] **Step 1: 寫失敗的測試**

建立 `app_unified/tests/test_quick_tag_api.py`：

```python
"""快速標籤鍵的 API（2026-09-08）。

使用者交接時逐項核對，要一鍵掛上「陳店長知道」這類標籤。
最硬的一條要求是**不可改變項目排序** —— 項目列表按 updated_at DESC 排，
掛標籤若碰了 updated_at，逐項按下去會把整張清單順序打亂。
"""
import time
import uuid

import pytest

from models import ChecklistItem, ChecklistItemTag, Note, NoteTag, Store, Tag, User


@pytest.fixture
def ctx(db_session, client):
    suffix = uuid.uuid4().hex[:6]
    store = Store(name=f'Q{suffix}'.upper(), login_enabled=True)
    db_session.add(store)
    db_session.commit()
    user = User(username=f'q-{suffix}', role='user', store=store.name)
    user.set_password('0000')
    db_session.add(user)
    db_session.commit()
    note = Note(user_id=user.id, title='交接清單', content='',
                note_type='checklist', store=store.name)
    db_session.add(note)
    db_session.commit()
    item = ChecklistItem(note_id=note.id, text='冰箱溫度異常', order_index=0)
    tag = Tag(store=store.name, name='陳店長知道', color='#3498db', pinned=True)
    db_session.add_all([item, tag])
    db_session.commit()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())
    yield client, user, note, item, tag, store
    ChecklistItemTag.query.filter_by(item_id=item.id).delete()
    NoteTag.query.filter_by(note_id=note.id).delete()
    Tag.query.filter_by(store=store.name).delete()
    ChecklistItem.query.filter_by(note_id=note.id).delete()
    db_session.delete(note)
    db_session.delete(user)
    db_session.delete(store)
    db_session.commit()


def test_quick_add_item_tag(ctx):
    c, user, _, item, tag, _ = ctx
    res = c.post(f'/notes/api/items/{item.id}/tags/{tag.id}')
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body['tag_id'] == tag.id
    assert body['tagged_by_name'] == user.username
    assert body['tagged_at']
    assert ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).count() == 1


def test_quick_add_does_not_touch_updated_at(ctx, db_session):
    """硬性要求：掛標籤不可讓項目跳到清單最上面。"""
    c, _, _, item, tag, _ = ctx
    before = ChecklistItem.query.filter_by(id=item.id).first().updated_at
    res = c.post(f'/notes/api/items/{item.id}/tags/{tag.id}')
    assert res.status_code == 200
    db_session.expire_all()
    after = ChecklistItem.query.filter_by(id=item.id).first().updated_at
    assert after == before, 'updated_at 被更新了 —— 項目會在下次載入時跳到最上面'


def test_quick_remove_item_tag(ctx):
    c, _, _, item, tag, _ = ctx
    c.post(f'/notes/api/items/{item.id}/tags/{tag.id}')
    res = c.delete(f'/notes/api/items/{item.id}/tags/{tag.id}')
    assert res.status_code == 200
    assert ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).count() == 0


def test_quick_add_is_idempotent(ctx):
    """連按兩次不可爆 500，也不可產生重複列（複合主鍵會衝突）。"""
    c, _, _, item, tag, _ = ctx
    assert c.post(f'/notes/api/items/{item.id}/tags/{tag.id}').status_code == 200
    assert c.post(f'/notes/api/items/{item.id}/tags/{tag.id}').status_code == 200
    assert ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).count() == 1


def test_quick_add_rejects_when_tag_limit_reached(ctx, db_session):
    """上限已滿要回明確錯誤，不可靜默沒反應。"""
    c, _, _, item, tag, store = ctx
    fillers = []
    for i in range(5):
        t = Tag(store=store.name, name=f'填充{i}', color='#888888')
        db_session.add(t)
        fillers.append(t)
    db_session.commit()
    for t in fillers:
        db_session.add(ChecklistItemTag(item_id=item.id, tag_id=t.id))
    db_session.commit()

    res = c.post(f'/notes/api/items/{item.id}/tags/{tag.id}')
    assert res.status_code == 400
    assert '5' in res.get_json()['error']


def test_quick_add_note_tag(ctx, db_session):
    c, user, note, _, tag, _ = ctx
    before = Note.query.filter_by(id=note.id).first().updated_at
    res = c.post(f'/notes/api/{note.id}/tags/{tag.id}')
    assert res.status_code == 200
    assert res.get_json()['tagged_by_name'] == user.username
    db_session.expire_all()
    assert Note.query.filter_by(id=note.id).first().updated_at == before, \
        '筆記的 updated_at 也不可被碰 —— 筆記列表同樣按它排序'


def test_cross_store_tag_rejected(ctx, db_session):
    c, _, _, item, _, _ = ctx
    other_store = Store(name=f'X{uuid.uuid4().hex[:5]}'.upper())
    db_session.add(other_store)
    db_session.commit()
    foreign = Tag(store=other_store.name, name='別店標籤', color='#111111')
    db_session.add(foreign)
    db_session.commit()
    res = c.post(f'/notes/api/items/{item.id}/tags/{foreign.id}')
    assert res.status_code == 400
    db_session.delete(foreign)
    db_session.delete(other_store)
    db_session.commit()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `../venv/bin/python -m pytest tests/test_quick_tag_api.py -q`
Expected: FAIL，全部 404（路由還不存在）

- [ ] **Step 3: 實作四個端點**

在 `app_unified/notes/tags.py` 檔案**末端**追加：

```python
# ─────────────────────────────────────────────────────────────
# 快速標籤鍵（2026-09-08）
#
# 專用的單標籤 toggle 端點。刻意**不**重用 `PUT .../tags` —— 那個吃的是整份
# 清單，前端得先知道目前有哪些標籤才能送，兩個人同時操作會互相覆蓋。
#
# ⚠️ 這些端點刻意**不觸碰** item.updated_at / note.updated_at。項目與筆記列表
# 都按 updated_at DESC 排序，碰了就會讓使用者交接時逐項按下去、把整張清單的
# 順序打亂。有測試守著（test_quick_tag_api.py）。
# ─────────────────────────────────────────────────────────────

def _assignment_to_dict(row):
    return {
        'tag_id': row.tag_id,
        'tagged_by_name': row.tagged_by_name,
        'tagged_at': row.tagged_at.isoformat() if row.tagged_at else None,
    }


def _resolve_item_and_tag(item_id, tag_id):
    """回傳 (item, note, tag)。權限或資料不符時直接 abort / 回傳錯誤 response。"""
    item = ChecklistItem.active().filter_by(id=item_id).first()
    if not item:
        abort(404)
    note = db.session.get(Note, item.note_id)
    if not note or note.deleted_at is not None:
        abort(404)
    if current_user.role != 'super_admin' and note.store != current_user.store:
        abort(403)
    tag = db.session.get(Tag, tag_id)
    if not tag:
        abort(404)
    return item, note, tag


@notes_bp.route('/api/items/<int:item_id>/tags/<int:tag_id>', methods=['POST'])
@login_required
def quick_add_item_tag(item_id, tag_id):
    item, note, tag = _resolve_item_and_tag(item_id, tag_id)
    if tag.store != note.store:
        return jsonify({'error': '跨店標籤'}), 400
    existing = ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).first()
    if existing:
        return jsonify(_assignment_to_dict(existing))   # 連按兩次不視為錯誤
    if ChecklistItemTag.query.filter_by(item_id=item.id).count() >= MAX_TAGS_PER_ITEM:
        return jsonify({'error': f'此項目已有 {MAX_TAGS_PER_ITEM} 個標籤，請先移除其他標籤'}), 400
    row = _stamp(ChecklistItemTag(item_id=item.id, tag_id=tag.id))
    db.session.add(row)
    db.session.commit()
    return jsonify(_assignment_to_dict(row))


@notes_bp.route('/api/items/<int:item_id>/tags/<int:tag_id>', methods=['DELETE'])
@login_required
def quick_remove_item_tag(item_id, tag_id):
    item, _, tag = _resolve_item_and_tag(item_id, tag_id)
    ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).delete()
    db.session.commit()
    return jsonify({'ok': True})


def _resolve_note_and_tag(note_id, tag_id):
    note = db.session.get(Note, note_id)
    if not note or note.deleted_at is not None:
        abort(404)
    if current_user.role != 'super_admin' and note.store != current_user.store:
        abort(403)
    tag = db.session.get(Tag, tag_id)
    if not tag:
        abort(404)
    return note, tag


@notes_bp.route('/api/<int:note_id>/tags/<int:tag_id>', methods=['POST'])
@login_required
def quick_add_note_tag(note_id, tag_id):
    note, tag = _resolve_note_and_tag(note_id, tag_id)
    if tag.store != note.store:
        return jsonify({'error': '跨店標籤'}), 400
    existing = NoteTag.query.filter_by(note_id=note.id, tag_id=tag.id).first()
    if existing:
        return jsonify(_assignment_to_dict(existing))
    if NoteTag.query.filter_by(note_id=note.id).count() >= MAX_TAGS_PER_ITEM:
        return jsonify({'error': f'此筆記已有 {MAX_TAGS_PER_ITEM} 個標籤，請先移除其他標籤'}), 400
    row = _stamp(NoteTag(note_id=note.id, tag_id=tag.id))
    db.session.add(row)
    db.session.commit()
    return jsonify(_assignment_to_dict(row))


@notes_bp.route('/api/<int:note_id>/tags/<int:tag_id>', methods=['DELETE'])
@login_required
def quick_remove_note_tag(note_id, tag_id):
    note, tag = _resolve_note_and_tag(note_id, tag_id)
    NoteTag.query.filter_by(note_id=note.id, tag_id=tag.id).delete()
    db.session.commit()
    return jsonify({'ok': True})
```

- [ ] **Step 4: 跑測試確認通過**

Run: `../venv/bin/python -m pytest tests/test_quick_tag_api.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: 跑全套件**

Run: `../venv/bin/python -m pytest tests/ -q`
Expected: 全部 PASS（878 passed）

- [ ] **Step 6: Commit**

```bash
git add app_unified/notes/tags.py app_unified/tests/test_quick_tag_api.py
git commit -m "feat(tags): 快速標籤鍵的單標籤 toggle API

四個端點（項目/筆記 × 加/移除）。刻意不重用 PUT .../tags —— 那個吃整份清單，
兩人同時操作會互相覆蓋；快速鍵是單一標籤的原子操作。

⚠️ 刻意不觸碰 updated_at：項目與筆記列表都按它排序，碰了會讓使用者交接時
逐項按下去把清單順序打亂。這是使用者的硬性要求，有測試守著。

連按兩次視為成功（回既有列），不是錯誤；上限已滿回明確訊息不靜默失敗。"
```

---

### Task 4: 把 `pinned` 與作者資訊送到前端

**Files:**
- Modify: `app_unified/notes/routes.py`（`edit_note` 約 :2135-2151、`new_note` 約 :568-580）
- Modify: `app_unified/templates/notes/editor.html`（約 :2184-2191 的 script 區塊）
- Test: `app_unified/tests/test_quick_tag_wiring.py`

**Interfaces:**
- Consumes: Task 1 的三個欄位
- Produces: 三個瀏覽器端全域變數，Task 5/6 會用：
  - `window.ALL_STORE_TAGS` —— 每個元素新增 `pinned: true|false`
  - `window.ITEM_TAG_META_MAP` —— `{ "<item_id>": { "<tag_id>": {by, at} } }`
  - `window.NOTE_TAG_META` —— `{ "<tag_id>": {by, at} }`

- [ ] **Step 1: 寫失敗的測試**

建立 `app_unified/tests/test_quick_tag_wiring.py`：

```python
"""快速標籤鍵的前端接線（2026-09-08）。

同專案 editor.html wiring test 慣例：原始碼層斷言。
"""
from pathlib import Path

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def test_all_store_tags_exposes_pinned():
    """前端要靠 pinned 決定哪些標籤渲染成按鈕 —— 沒傳就什麼都不會出現。"""
    src = EDITOR.read_text(encoding='utf-8')
    assert 'pinned:' in src, 'ALL_STORE_TAGS 沒有帶 pinned 欄位'


def test_tag_meta_maps_exposed():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'window.ITEM_TAG_META_MAP' in src, '缺少項目標籤的作者資訊 map'
    assert 'window.NOTE_TAG_META' in src, '缺少筆記標籤的作者資訊 map'
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `../venv/bin/python -m pytest tests/test_quick_tag_wiring.py -q`
Expected: FAIL（2 個測試都找不到對應字串）

- [ ] **Step 3: 後端建 meta map（`edit_note`）**

在 `app_unified/notes/routes.py` 的 `edit_note` 裡，把這一段：

```python
    current_tag_ids = [nt.tag_id for nt in _NT.query.filter_by(note_id=note.id).all()]
    item_tag_ids_map = {}
    if note.note_type == 'checklist':
        active_item_ids = [it.id for it in note.active_checklist_items]
        if active_item_ids:
            for r in _CIT.query.filter(_CIT.item_id.in_(active_item_ids)).all():
                item_tag_ids_map.setdefault(r.item_id, []).append(r.tag_id)
```

換成：

```python
    # 快速標籤鍵要顯示「誰在什麼時候掛的」，所以除了 id 清單，另外帶一份 meta。
    # key 一律轉成字串：|tojson 之後 JSON 物件的 key 本來就是字串，前端用
    # 數字取值時 JS 會自動轉，兩邊一致比較不會踩坑。
    note_tag_rows = _NT.query.filter_by(note_id=note.id).all()
    current_tag_ids = [nt.tag_id for nt in note_tag_rows]
    note_tag_meta = {
        str(r.tag_id): {'by': r.tagged_by_name,
                        'at': r.tagged_at.isoformat() if r.tagged_at else None}
        for r in note_tag_rows
    }
    item_tag_ids_map = {}
    item_tag_meta_map = {}
    if note.note_type == 'checklist':
        active_item_ids = [it.id for it in note.active_checklist_items]
        if active_item_ids:
            for r in _CIT.query.filter(_CIT.item_id.in_(active_item_ids)).all():
                item_tag_ids_map.setdefault(r.item_id, []).append(r.tag_id)
                item_tag_meta_map.setdefault(str(r.item_id), {})[str(r.tag_id)] = {
                    'by': r.tagged_by_name,
                    'at': r.tagged_at.isoformat() if r.tagged_at else None,
                }
```

再把同一個函式尾端的 `render_template(...)` 呼叫，從：

```python
                           current_tag_ids=current_tag_ids,
                           item_tag_ids_map=item_tag_ids_map,
                           all_store_tags=all_store_tags)
```

改成：

```python
                           current_tag_ids=current_tag_ids,
                           item_tag_ids_map=item_tag_ids_map,
                           item_tag_meta_map=item_tag_meta_map,
                           note_tag_meta=note_tag_meta,
                           all_store_tags=all_store_tags)
```

- [ ] **Step 4: 新建頁也要傳空的 map（`new_note`）**

在 `app_unified/notes/routes.py` 的 `new_note` 裡，把：

```python
                           item_tag_ids_map={},
                           all_store_tags=all_store_tags)
```

改成：

```python
                           item_tag_ids_map={},
                           item_tag_meta_map={},
                           note_tag_meta={},
                           all_store_tags=all_store_tags)
```

- [ ] **Step 5: 模板加上 pinned 與兩個 meta map**

在 `app_unified/templates/notes/editor.html` 把這段：

```html
  window.ITEM_TAG_IDS_MAP = {{ item_tag_ids_map|tojson|safe if item_tag_ids_map is defined else '{}' }};
  window.ALL_STORE_TAGS = [
    {% if all_store_tags is defined and all_store_tags %}
    {% for t in all_store_tags %}
    {id: {{ t.id }}, name: "{{ t.name|e }}", color: "{{ t.color }}"}{% if not loop.last %},{% endif %}
    {% endfor %}
    {% endif %}
  ];
```

換成：

```html
  window.ITEM_TAG_IDS_MAP = {{ item_tag_ids_map|tojson|safe if item_tag_ids_map is defined else '{}' }};
  // 快速標籤鍵用：誰在什麼時候掛的（hover 顯示）。key 都是字串。
  window.ITEM_TAG_META_MAP = {{ item_tag_meta_map|tojson|safe if item_tag_meta_map is defined else '{}' }};
  window.NOTE_TAG_META = {{ note_tag_meta|tojson|safe if note_tag_meta is defined else '{}' }};
  window.NOTE_TAG_IDS = {{ current_tag_ids|tojson|safe if current_tag_ids is defined else '[]' }};
  window.ALL_STORE_TAGS = [
    {% if all_store_tags is defined and all_store_tags %}
    {% for t in all_store_tags %}
    {id: {{ t.id }}, name: "{{ t.name|e }}", color: "{{ t.color }}", pinned: {{ t.pinned|tojson }}}{% if not loop.last %},{% endif %}
    {% endfor %}
    {% endif %}
  ];
```

- [ ] **Step 6: 跑測試確認通過**

Run: `../venv/bin/python -m pytest tests/test_quick_tag_wiring.py -q`
Expected: PASS（2 passed）

- [ ] **Step 7: 驗證模板真的渲染得出來**

Run: `../venv/bin/python -m pytest tests/ -q -k "editor or notes"`
Expected: 全部 PASS。**若 Jinja 有誤，渲染編輯器的測試會直接失敗。**

- [ ] **Step 8: Commit**

```bash
git add app_unified/notes/routes.py app_unified/templates/notes/editor.html \
        app_unified/tests/test_quick_tag_wiring.py
git commit -m "feat(tags): 把 pinned 與掛標籤者資訊送到編輯器前端

ALL_STORE_TAGS 補上 pinned（決定哪些標籤要渲染成快速鍵），
另加 ITEM_TAG_META_MAP / NOTE_TAG_META 供 hover 顯示「王小明 9/08 08:15」。"
```

---

### Task 5: checklist 項目上的快速鍵 UI

**Files:**
- Modify: `app_unified/templates/notes/editor.html`（`renderChecklistItem` 約 :1610-1630 的 tag chips 之後）
- Test: `app_unified/tests/test_quick_tag_wiring.py`（沿用 Task 4 的檔案）

**Interfaces:**
- Consumes: Task 3 的四個端點；Task 4 的 `ALL_STORE_TAGS.pinned` / `ITEM_TAG_META_MAP` / `NOTE_TAG_META` / `NOTE_TAG_IDS`
- Produces: `makeQuickTagBtn(targetType, targetId, tag)` —— Task 6 會重用同一個函式做筆記層的按鈕

- [ ] **Step 1: 寫失敗的測試**

在 `app_unified/tests/test_quick_tag_wiring.py` 末端追加：

```python


def test_quick_tag_button_factory_exists():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'function makeQuickTagBtn(' in src, '缺少快速標籤鍵的建構函式'


def test_quick_tag_uses_dedicated_endpoint_not_full_list():
    """必須打單標籤端點。用 PUT .../tags 會覆寫整份清單、兩人同時操作互相蓋掉。"""
    src = EDITOR.read_text(encoding='utf-8')
    assert "'/notes/api/items/' + targetId + '/tags/' + tag.id" in src or \
           '"/notes/api/items/" + targetId + "/tags/" + tag.id' in src, \
        '快速鍵沒有打單標籤端點'


def test_quick_tag_rendered_for_pinned_only():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'return t.pinned;' in src, '沒有依 pinned 過濾 —— 會把所有標籤都變成按鈕'


def test_quick_tag_shows_authorship_on_hover():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'ITEM_TAG_META_MAP' in src and 'title' in src, \
        '沒有把作者資訊放進 title（hover 提示）'
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `../venv/bin/python -m pytest tests/test_quick_tag_wiring.py -q`
Expected: 新增的 4 條 FAIL

- [ ] **Step 3: 加入 `makeQuickTagBtn`**

在 `app_unified/templates/notes/editor.html` 的 `function renderChecklistItem(item) {` **之前**插入：

```javascript
// ── 快速標籤鍵（2026-09-08）───────────────────────────────────
// 釘選的標籤各渲染一顆按鈕，按一下掛上、再按一下取消。
// 打的是單標籤端點（POST/DELETE .../tags/<tag_id>），不是會覆寫整份清單的
// PUT .../tags —— 後者兩個人同時操作會互相蓋掉。
// 後端刻意不碰 updated_at，所以按了不會讓項目跳到清單最上面。
function _quickTagState(targetType, targetId) {
  if (targetType === 'item') {
    return (window.ITEM_TAG_IDS_MAP && window.ITEM_TAG_IDS_MAP[targetId]) || [];
  }
  return window.NOTE_TAG_IDS || [];
}

function _quickTagMeta(targetType, targetId, tagId) {
  if (targetType === 'item') {
    var m = window.ITEM_TAG_META_MAP || {};
    return (m[String(targetId)] || {})[String(tagId)] || null;
  }
  return (window.NOTE_TAG_META || {})[String(tagId)] || null;
}

function _quickTagUrl(targetType, targetId, tagId) {
  if (targetType === 'item') {
    return '/notes/api/items/' + targetId + '/tags/' + tagId;
  }
  return '/notes/api/' + targetId + '/tags/' + tagId;
}

function makeQuickTagBtn(targetType, targetId, tag) {
  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'quick-tag-btn';
  btn.dataset.tagId = tag.id;

  function paint() {
    var on = _quickTagState(targetType, targetId).indexOf(tag.id) >= 0;
    btn.textContent = (on ? '✓ ' : '+ ') + tag.name;
    btn.style.cssText =
      'font-size:.75rem;padding:.2rem .5rem;border-radius:12px;cursor:pointer;' +
      'margin-right:.3rem;margin-top:.3rem;border:1px solid ' + tag.color + ';' +
      (on ? ('background:' + tag.color + ';color:#fff;')
          : 'background:#fff;color:' + tag.color + ';');
    var meta = on ? _quickTagMeta(targetType, targetId, tag.id) : null;
    btn.title = (meta && meta.by)
      ? (meta.by + (meta.at ? ' · ' + fmtDateTime(meta.at) : ''))
      : (on ? '已掛上' : '點一下掛上「' + tag.name + '」');
  }

  btn.onclick = function() {
    if (btn.disabled) return;
    var on = _quickTagState(targetType, targetId).indexOf(tag.id) >= 0;
    btn.disabled = true;
    fetch(_quickTagUrl(targetType, targetId, tag.id), { method: on ? 'DELETE' : 'POST' })
      .then(function(r) {
        return r.json().catch(function() { return {}; })
                .then(function(j) { return [r, j]; });
      })
      .then(function(pair) {
        var r = pair[0], d = pair[1];
        btn.disabled = false;
        if (!r.ok) {
          // 上限已滿等情形要看得見，不可靜默失敗
          alert(d.error || '操作失敗');
          return;
        }
        var ids = _quickTagState(targetType, targetId).slice();
        if (on) {
          ids = ids.filter(function(x) { return x !== tag.id; });
        } else if (ids.indexOf(tag.id) < 0) {
          ids.push(tag.id);
        }
        if (targetType === 'item') {
          window.ITEM_TAG_IDS_MAP = window.ITEM_TAG_IDS_MAP || {};
          window.ITEM_TAG_IDS_MAP[targetId] = ids;
          window.ITEM_TAG_META_MAP = window.ITEM_TAG_META_MAP || {};
          var im = window.ITEM_TAG_META_MAP[String(targetId)] || {};
          if (on) { delete im[String(tag.id)]; }
          else { im[String(tag.id)] = { by: d.tagged_by_name, at: d.tagged_at }; }
          window.ITEM_TAG_META_MAP[String(targetId)] = im;
        } else {
          window.NOTE_TAG_IDS = ids;
          window.NOTE_TAG_META = window.NOTE_TAG_META || {};
          if (on) { delete window.NOTE_TAG_META[String(tag.id)]; }
          else { window.NOTE_TAG_META[String(tag.id)] = { by: d.tagged_by_name, at: d.tagged_at }; }
        }
        paint();
      })
      .catch(function(err) {
        btn.disabled = false;
        alert('連線失敗：' + err.message);
      });
  };

  paint();
  return btn;
}
```

- [ ] **Step 4: 在項目上渲染按鈕**

在 `renderChecklistItem` 裡，找到 tag chips 區塊的結尾（`content.appendChild(tagWrap);` 之後、`// meta — 2 rows max` 之前），插入：

```javascript
  // 快速標籤鍵：釘選的標籤各一顆。唯讀時不顯示。
  if (window.ALL_STORE_TAGS && !IS_READONLY) {
    var pinnedTags = window.ALL_STORE_TAGS.filter(function(t) { return t.pinned; });
    if (pinnedTags.length > 0) {
      var qWrap = document.createElement('div');
      qWrap.className = 'quick-tag-row';
      pinnedTags.forEach(function(t) {
        qWrap.appendChild(makeQuickTagBtn('item', item.id, t));
      });
      content.appendChild(qWrap);
    }
  }
```

- [ ] **Step 5: 跑測試確認通過**

Run: `../venv/bin/python -m pytest tests/test_quick_tag_wiring.py -q`
Expected: PASS（6 passed）

- [ ] **Step 6: 驗證 JS 語法**

編輯器是 inline script，語法錯會讓整頁壞掉。用 node 檢查：

```bash
../venv/bin/python - <<'EOF'
import re, pathlib
src = pathlib.Path('templates/notes/editor.html').read_text(encoding='utf-8')
blocks = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', src, re.S)
out = []
for i, b in enumerate(blocks):
    b = re.sub(r'\{%.*?%\}', '', b, flags=re.S)
    b = re.sub(r'\{\{.*?\}\}', 'null', b, flags=re.S)
    out.append(f"// block {i}\n{b}\n")
pathlib.Path('/tmp/editor_check.js').write_text('\n'.join(out), encoding='utf-8')
print(f"抽出 {len(blocks)} 個區塊")
EOF
node --check /tmp/editor_check.js && echo "✅ JS 語法通過"
```

Expected: `✅ JS 語法通過`

- [ ] **Step 7: 跑全套件**

Run: `../venv/bin/python -m pytest tests/ -q`
Expected: 全部 PASS（884 passed）

- [ ] **Step 8: Commit**

```bash
git add app_unified/templates/notes/editor.html app_unified/tests/test_quick_tag_wiring.py
git commit -m "feat(tags): checklist 項目上的快速標籤鍵

釘選的標籤各渲染一顆按鈕，按一下掛上、再按一下取消，hover 顯示誰按的。
打單標籤端點（不是覆寫整份清單的 PUT），失敗會 alert 不靜默。
唯讀模式不顯示。JS 語法以 node --check 驗過。"
```

---

### Task 6: 筆記層的快速鍵 UI

**Files:**
- Modify: `app_unified/templates/notes/editor.html`（工具列 :263-275，以及頁面初始化處）
- Test: `app_unified/tests/test_quick_tag_wiring.py`（沿用）

**Interfaces:**
- Consumes: Task 5 的 `makeQuickTagBtn`；Task 4 的 `NOTE_TAG_IDS` / `NOTE_TAG_META`
- Produces: 無（本階段最後一個任務）

- [ ] **Step 1: 寫失敗的測試**

在 `app_unified/tests/test_quick_tag_wiring.py` 末端追加：

```python


def test_note_level_quick_tag_container_exists():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'id="note-quick-tags"' in src, '工具列缺少筆記層快速鍵的容器'


def test_note_level_quick_tags_are_rendered():
    src = EDITOR.read_text(encoding='utf-8')
    assert "makeQuickTagBtn('note'" in src, '筆記層沒有重用 makeQuickTagBtn'


def test_note_quick_tags_only_when_note_exists():
    """新建但還沒存的筆記沒有 id，不能渲染（會打到 /notes/api/undefined/tags/...）。"""
    src = EDITOR.read_text(encoding='utf-8')
    assert 'if (!NOTE_ID) return;' in src or 'if (NOTE_ID)' in src, \
        '沒有擋住「筆記尚未建立」的情形'
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `../venv/bin/python -m pytest tests/test_quick_tag_wiring.py -q`
Expected: 新增的 3 條 FAIL

- [ ] **Step 3: 工具列加容器**

在 `app_unified/templates/notes/editor.html` 的工具列，把：

```html
      🏷️ 標籤 <span id="note-tag-count">(0/5)</span>
    </button>
```

改成：

```html
      🏷️ 標籤 <span id="note-tag-count">(0/5)</span>
    </button>
    {# 筆記層的快速標籤鍵；內容由 renderNoteQuickTags() 填 #}
    <span id="note-quick-tags" style="display:inline-flex;flex-wrap:wrap;align-items:center;"></span>
```

- [ ] **Step 4: 加渲染函式並在載入時呼叫**

在 `app_unified/templates/notes/editor.html` 的 `makeQuickTagBtn` 定義**之後**插入：

```javascript
function renderNoteQuickTags() {
  var host = document.getElementById('note-quick-tags');
  if (!host) return;
  host.innerHTML = '';
  // 新建但尚未存檔的筆記沒有 id，這時渲染會打到 /notes/api/undefined/...
  if (!NOTE_ID) return;
  if (IS_READONLY || !window.ALL_STORE_TAGS) return;
  window.ALL_STORE_TAGS.filter(function(t) { return t.pinned; })
    .forEach(function(t) {
      host.appendChild(makeQuickTagBtn('note', NOTE_ID, t));
    });
}
```

再找到編輯器初始化那段（`if (EDITOR_TYPE === 'checklist' && NOTE_ID) loadChecklistItems();`），在它**後面**加一行：

```javascript
renderNoteQuickTags();
```

- [ ] **Step 5: 跑測試確認通過**

Run: `../venv/bin/python -m pytest tests/test_quick_tag_wiring.py -q`
Expected: PASS（9 passed）

- [ ] **Step 6: 驗證 JS 語法**

Run: 重跑 Task 5 Step 6 的 node --check 指令
Expected: `✅ JS 語法通過`

- [ ] **Step 7: 驗證兩種編輯器都渲染得出來**

建立暫時的檢查檔 `app_unified/tests/test_zz_render_check.py`：

```python
def test_editor_renders_new_and_existing(logged_in_client, db_session):
    from models import Note
    assert logged_in_client.get('/notes/new').status_code == 200
    n = Note(user_id=1, title='t', content='c', note_type='checklist')
    db_session.add(n); db_session.commit()
    assert logged_in_client.get(f'/notes/{n.id}').status_code == 200
    db_session.delete(n); db_session.commit()
```

Run: `../venv/bin/python -m pytest tests/test_zz_render_check.py -q`
Expected: PASS。之後刪除這個檔案：`rm tests/test_zz_render_check.py`

- [ ] **Step 8: 跑全套件**

Run: `../venv/bin/python -m pytest tests/ -q`
Expected: 全部 PASS（887 passed）

- [ ] **Step 9: Commit**

```bash
git add app_unified/templates/notes/editor.html app_unified/tests/test_quick_tag_wiring.py
git commit -m "feat(tags): 筆記層的快速標籤鍵

工具列的標籤按鈕旁加上釘選標籤的一鍵按鈕，重用 makeQuickTagBtn。
新建但尚未存檔的筆記沒有 id，此時不渲染（否則會打到 /notes/api/undefined）。"
```

---

## 完成後的人工驗收

自動測試涵蓋不到瀏覽器的實際互動，部署後請確認：

1. 開一張既有的 checklist，每個項目下方應出現釘選標籤的按鈕（例如「+ 陳店長知道」）。
2. 按一下 → 變成實心的「✓ 陳店長知道」。
3. **重新整理頁面，項目的順序不變** —— 這是本功能最重要的驗收點。
4. 滑鼠移到已掛上的按鈕上，顯示「你的帳號 · 日期時間」。
5. 再按一下 → 取消。
6. 把某個項目掛滿 5 個標籤，再按快速鍵 → 跳出「已有 5 個標籤」的提示。
7. 用標籤選擇器改動其他標籤後，先前快速鍵掛的那個標籤 hover 資訊**仍在**。
