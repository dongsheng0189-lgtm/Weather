# Notes Announcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「公告」(`Announcement`) 功能加進 webapp 的 notes 模組 — super_admin 可發跨店或單店公告、admin 只能發自己店、user 唯讀；公告永遠置頂在 `/notes/` index 最上面，跟一般 note 視覺區隔；公告附件支援圖片預覽放大、影片 inline 播放；R2 URL 走 presigned (1hr)。

**Architecture:** 新表 `announcements` + `announcement_attachments` 完全獨立於 `notes` / `note_attachments`；新檔 `notes/announcement_routes.py` 註冊到既有 `notes_bp`（不獨立 blueprint，URL prefix 共用 `/notes`）；新目錄 `templates/announcements/`；新 R2 storage prefix `announcements/{id}/`。`notes` 模組唯一改動處：`index()` route + `templates/notes/index.html`（其他 1500+ 行不動）。

**Tech Stack:** Python 3.12, Flask, SQLAlchemy (Flask-SQLAlchemy), Alembic, gevent, boto3 (R2 / S3-compatible), pytest, vanilla JS。

**Spec reference:** `docs/superpowers/specs/2026-05-08-notes-announcement-design.md`（commit `ee6d093`）

---

## File Structure

| 檔案 | 動作 | 責任 |
|------|------|------|
| `app_unified/migrations/versions/010_announcements.py` | **Create** | DDL：兩個新表 + 索引；無資料 backfill。 |
| `app_unified/models.py` | **Modify** | 加 `Announcement` + `AnnouncementAttachment` 兩個 class（檔尾 append）。 |
| `app_unified/notes/announcement_routes.py` | **Create** | 公告 CRUD endpoints + permission helpers，註冊到既有 `notes_bp`。 |
| `app_unified/notes/__init__.py` | **Modify** | import `announcement_routes` 觸發 route 註冊（如同 `from . import routes`）。 |
| `app_unified/notes/routes.py` | **Modify** | 唯一改動：`index()` 路由加 query 公告 + `editable_ann_ids` set，傳進 template。 |
| `app_unified/templates/notes/index.html` | **Modify** | `<head>` link `announcements.css`；button row 第三個按鈕「+ 新增公告」（admin/super_admin 才顯示）；`<h1>` 後 include partial。 |
| `app_unified/templates/announcements/new.html` | **Create** | 建立公告表單頁。 |
| `app_unified/templates/announcements/edit.html` | **Create** | 編輯公告表單頁（含刪除按鈕）。 |
| `app_unified/templates/announcements/_card_list.html` | **Create** | 公告卡片列表 partial。 |
| `app_unified/static/css/announcements.css` | **Create** | 公告卡片 / scope chip / 附件縮圖樣式。 |
| `app_unified/static/js/announcement_attachments.js` | **Create** | 附件 UI（複製 notes/editor.html 的 createAttItem / openFullscreen pattern）。 |
| `app_unified/tests/test_announcements_permission.py` | **Create** | Permission helper 純單元測試（不需 Flask app）。 |
| `app_unified/tests/test_announcements_api.py` | **Create** | API CRUD + visibility + attachment 整合測試。 |
| `app_unified/tests/test_announcements_index_integration.py` | **Create** | `/notes/` index 渲染整合測試（含按鈕 conditional / 公告區塊位置）。 |

---

## 重要約定

**Migration 編號**：當前 head 是 `009_v_aux_logs`。本次新檔 `010_announcements.py`，`revision = '010'`，`down_revision = '009'`。

**EncryptedText 在 migration 用 `sa.Text()`**：`EncryptedText.impl = Text`（見 `crypto.py:90`），底層是 PostgreSQL TEXT，不是 LargeBinary。

**pytest 執行**：所有 `pytest` 指令都用：
```bash
cd /home/hirain0126/projects/webapp/.worktrees/feat-notes-announcement/app_unified && \
/home/hirain0126/projects/webapp/venv/bin/python -m pytest <path> -v
```

**Commit message convention**：跟既有 webapp 一致 — `feat(announcements): ...` / `test(announcements): ...` / `feat(notes/announcement): ...`（混合 notes/announcement 子路徑時用後者）。

**EncryptedText import**：`from crypto import EncryptedText`（不是 `from .crypto`）。

---

## Task 0: Worktree setup

**Files:** N/A (環境準備)

- [ ] **Step 1: Open isolated worktree**

跟 `feedback_branch_workflow` 一致：

```bash
cd /home/hirain0126/projects/webapp
git worktree add /home/hirain0126/projects/webapp/.worktrees/feat-notes-announcement -b feat/notes-announcement main
cd /home/hirain0126/projects/webapp/.worktrees/feat-notes-announcement
pwd
git log --oneline -3
```

預期輸出：
- `pwd` → `/home/hirain0126/projects/webapp/.worktrees/feat-notes-announcement`
- `git log` 最頂 commit 為 `ee6d093 docs(spec): notes/announcement design ...`

之後所有檔案編輯、test 執行都在這個目錄裡。

---

## Task 1: Migration `010_announcements.py`

**Files:**
- Create: `app_unified/migrations/versions/010_announcements.py`

- [ ] **Step 1: Create migration file**

Create `app_unified/migrations/versions/010_announcements.py`:

```python
"""Add announcements + announcement_attachments tables

Revision ID: 010
Revises: 009
Create Date: 2026-05-08
"""
from alembic import op
import sqlalchemy as sa


revision = '010'
down_revision = '009'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_name", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("store", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_announcements_created_at_desc",
        "announcements",
        [sa.text("created_at DESC")],
    )
    op.create_index("ix_announcements_store", "announcements", ["store"])

    op.create_table(
        "announcement_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("announcement_id", sa.Integer(),
                  sa.ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_announcement_attachments_announcement_id",
        "announcement_attachments",
        ["announcement_id"],
    )


def downgrade():
    op.drop_index("ix_announcement_attachments_announcement_id", "announcement_attachments")
    op.drop_table("announcement_attachments")
    op.drop_index("ix_announcements_store", "announcements")
    op.drop_index("ix_announcements_created_at_desc", "announcements")
    op.drop_table("announcements")
```

- [ ] **Step 2: Run migration up against dev DB**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m flask db upgrade head
```

預期輸出：`Running upgrade 009 -> 010, Add announcements + announcement_attachments tables`

- [ ] **Step 3: Verify schema**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -c "
from app import create_app
from extensions import db
from sqlalchemy import inspect
app = create_app()
with app.app_context():
    insp = inspect(db.engine)
    print('announcements cols:', [c['name'] for c in insp.get_columns('announcements')])
    print('announcement_attachments cols:', [c['name'] for c in insp.get_columns('announcement_attachments')])
    print('announcements indexes:', [i['name'] for i in insp.get_indexes('announcements')])
    print('aa indexes:', [i['name'] for i in insp.get_indexes('announcement_attachments')])
"
```

預期 `announcements cols`：`['id', 'user_id', 'author_name', 'title', 'content', 'store', 'created_at', 'updated_at']`
預期 `announcement_attachments cols`：`['id', 'announcement_id', 'user_id', 'object_key', 'filename', 'content_type', 'file_size', 'created_at']`
預期 indexes：含 `ix_announcements_created_at_desc`、`ix_announcements_store`、`ix_announcement_attachments_announcement_id`。

- [ ] **Step 4: Verify downgrade works (then re-upgrade)**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m flask db downgrade -1 && \
/home/hirain0126/projects/webapp/venv/bin/python -m flask db upgrade head
```

預期：先看到 `Running downgrade 010 -> 009`，再看到 `Running upgrade 009 -> 010`。

- [ ] **Step 5: Commit**

```bash
git add app_unified/migrations/versions/010_announcements.py
git commit -m "feat(announcements): add migration 010 for announcements + announcement_attachments tables"
```

---

## Task 2: Models — `Announcement` + `AnnouncementAttachment`

**Files:**
- Modify: `app_unified/models.py` (append at file end)

- [ ] **Step 1: Append models to `models.py`**

在 `app_unified/models.py` 檔尾加入（`PRIVATE_NOTE_TYPES` 區塊後是檔末，把以下 block append 在最後）：

```python
# ════════════════════════════════════════════════════════
# 公告（announcements）— 與店務筆記表獨立，UI 整合進 notes 模組
# super_admin 可發「全部」或單店；admin 只能發自己店；user 唯讀
# ════════════════════════════════════════════════════════

class Announcement(db.Model):
    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_name = db.Column(EncryptedText, nullable=True)  # 建立當下作者帳號（刪除使用者後保留）
    title = db.Column(EncryptedText, nullable=False, default="")
    content = db.Column(EncryptedText, nullable=False, default="")
    store = db.Column(db.Text, nullable=True)  # NULL = 全部；否則 = 單店店名
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    author = db.relationship("User", foreign_keys=[user_id])

    @property
    def is_global(self) -> bool:
        return self.store is None

    @property
    def display_author(self):
        """同 Note.display_author pattern：作者刪除後 fallback 到 author_name"""
        if self.user_id and self.author:
            return self.author.username
        return f"{self.author_name or '?'}（帳號已刪除）"


class AnnouncementAttachment(db.Model):
    __tablename__ = "announcement_attachments"

    id = db.Column(db.Integer, primary_key=True)
    announcement_id = db.Column(
        db.Integer,
        db.ForeignKey("announcements.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    object_key = db.Column(db.Text, nullable=False)
    filename = db.Column(db.Text, nullable=False)
    content_type = db.Column(db.Text, nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    announcement = db.relationship(
        "Announcement",
        backref=db.backref("attachments", lazy=True, cascade="all, delete-orphan"),
    )
    uploader = db.relationship("User", foreign_keys=[user_id])
```

- [ ] **Step 2: Smoke test — model import + 建立刪除一筆**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -c "
from app import create_app
from extensions import db
from models import Announcement, AnnouncementAttachment, User

app = create_app()
with app.app_context():
    # 找一個 super_admin 來建假資料
    u = User.query.filter_by(role='super_admin').first()
    assert u, 'no super_admin user — create one before running this test'

    a = Announcement(user_id=u.id, author_name=u.username, title='hello', content='world', store=None)
    db.session.add(a)
    db.session.commit()

    fetched = Announcement.query.get(a.id)
    print('id:', fetched.id, 'is_global:', fetched.is_global, 'title:', fetched.title)
    assert fetched.is_global is True
    assert fetched.title == 'hello'
    assert fetched.display_author == u.username

    db.session.delete(fetched)
    db.session.commit()
    print('delete ok')
"
```

預期：印出 `id: <N> is_global: True title: hello`、`delete ok`。如果 ImportError 或 AttributeError 就修。

- [ ] **Step 3: Commit**

```bash
git add app_unified/models.py
git commit -m "feat(announcements): add Announcement + AnnouncementAttachment models"
```

---

## Task 3: Permission helpers (pure functions, TDD)

**Files:**
- Create: `app_unified/notes/announcement_routes.py` (僅 helper 函式，本 task 不加 routes)
- Create: `app_unified/tests/test_announcements_permission.py`

- [ ] **Step 1: Write failing tests**

Create `app_unified/tests/test_announcements_permission.py`:

```python
"""Pure permission helpers — no Flask app required."""
import pytest
from unittest.mock import MagicMock

from notes.announcement_routes import (
    _can_create,
    _can_target_store,
    _can_modify,
    _can_view,
)


# ── _can_create ──────────────────────────────────────────────

def test_can_create_super_admin(monkeypatch):
    user = MagicMock()
    user.is_admin = MagicMock(return_value=True)
    user.is_super_admin = MagicMock(return_value=True)
    user.is_authenticated = True
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_create() is True


def test_can_create_admin(monkeypatch):
    user = MagicMock()
    user.is_admin = MagicMock(return_value=True)
    user.is_super_admin = MagicMock(return_value=False)
    user.is_authenticated = True
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_create() is True


def test_can_create_user(monkeypatch):
    user = MagicMock()
    user.is_admin = MagicMock(return_value=False)
    user.is_super_admin = MagicMock(return_value=False)
    user.is_authenticated = True
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_create() is False


# ── _can_target_store ─────────────────────────────────────────

def test_can_target_store_super_admin_any(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=True)
    user.is_admin = MagicMock(return_value=True)
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_target_store(None) is True       # 全部
    assert _can_target_store("B店") is True      # 任一店
    assert _can_target_store("C店") is True


def test_can_target_store_admin_own_only(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=False)
    user.is_admin = MagicMock(return_value=True)
    user.store = "B店"
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_target_store("B店") is True
    assert _can_target_store("C店") is False    # 別店
    assert _can_target_store(None) is False     # 全部 (admin 不能)


def test_can_target_store_user_never(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=False)
    user.is_admin = MagicMock(return_value=False)
    user.store = "B店"
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_target_store("B店") is False
    assert _can_target_store(None) is False


# ── _can_modify / _can_view ──────────────────────────────────

def _make_announcement(store):
    a = MagicMock()
    a.store = store
    return a


def test_can_modify_super_admin_anything(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=True)
    user.is_admin = MagicMock(return_value=True)
    user.store = "B店"
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_modify(_make_announcement(None)) is True
    assert _can_modify(_make_announcement("C店")) is True


def test_can_modify_admin_own_store_only(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=False)
    user.is_admin = MagicMock(return_value=True)
    user.store = "B店"
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_modify(_make_announcement("B店")) is True
    assert _can_modify(_make_announcement("C店")) is False
    assert _can_modify(_make_announcement(None)) is False  # admin 不能改全部


def test_can_modify_user_never(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=False)
    user.is_admin = MagicMock(return_value=False)
    user.store = "B店"
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_modify(_make_announcement("B店")) is False


def test_can_view_super_admin_anything(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=True)
    user.store = "B店"
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_view(_make_announcement(None)) is True
    assert _can_view(_make_announcement("C店")) is True


def test_can_view_global_or_own(monkeypatch):
    user = MagicMock()
    user.is_super_admin = MagicMock(return_value=False)
    user.store = "B店"
    monkeypatch.setattr("notes.announcement_routes.current_user", user)
    assert _can_view(_make_announcement(None)) is True   # 全部
    assert _can_view(_make_announcement("B店")) is True  # 自己店
    assert _can_view(_make_announcement("C店")) is False # 別店
```

- [ ] **Step 2: Run tests to verify they FAIL with import error**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_announcements_permission.py -v
```

預期：所有 test FAIL with `ModuleNotFoundError: No module named 'notes.announcement_routes'`。

- [ ] **Step 3: Create `notes/announcement_routes.py` with helpers (no routes yet)**

Create `app_unified/notes/announcement_routes.py`:

```python
"""公告 (announcements) — UI 整合進 notes 模組，資料層獨立。

Routes 註冊到既有 notes_bp（共用 URL prefix `/notes`），檔案分離避免 notes/routes.py 繼續肥。
"""
from flask import request, jsonify, render_template, abort
from flask_login import login_required, current_user

from extensions import db
from notes.routes import notes_bp


# ── Permission helpers ───────────────────────────────────────

def _can_create() -> bool:
    """誰能建立公告：admin / super_admin"""
    return current_user.is_admin()


def _can_target_store(store) -> bool:
    """檢查當前 user 能否對這個 scope 發/改公告。

    super_admin: 任何 store 或 None（全部）皆可
    admin:       只能 store == user.store（不能 None、不能別店）
    user:        永遠 False
    """
    if current_user.is_super_admin():
        return True
    if current_user.is_admin():
        return store == current_user.store and store is not None
    return False


def _can_modify(announcement) -> bool:
    """編輯/刪除這筆公告"""
    if current_user.is_super_admin():
        return True
    if current_user.is_admin() and announcement.store == current_user.store:
        return True
    return False


def _can_view(announcement) -> bool:
    """看得到這筆公告（API GET 用；list 是 query filter，不走這函式）"""
    if current_user.is_super_admin():
        return True
    return announcement.store is None or announcement.store == current_user.store
```

- [ ] **Step 4: Run tests to verify they PASS**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_announcements_permission.py -v
```

預期：12 tests PASS。

- [ ] **Step 5: Commit**

```bash
git add app_unified/notes/announcement_routes.py app_unified/tests/test_announcements_permission.py
git commit -m "feat(announcements): permission helpers (super_admin/admin/user matrix)"
```

---

## Task 4: API CRUD Part 1 — POST + GET list

**Files:**
- Modify: `app_unified/notes/announcement_routes.py`
- Modify: `app_unified/notes/__init__.py` (確保 announcement_routes 被 import 觸發 route 註冊)
- Create: `app_unified/tests/test_announcements_api.py`

- [ ] **Step 1: Wire announcement_routes import**

檢查 `app_unified/notes/__init__.py` 內容。如果該檔案是空的（`__init__.py` 0 bytes），加入：

```python
from . import routes
from . import announcement_routes  # noqa: F401  (公告路由註冊到 notes_bp)
```

如果 `__init__.py` 已有 `from . import routes`，**append** 第二行 `from . import announcement_routes`。

驗證：
```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -c "from notes import announcement_routes; print('ok')"
```
預期：`ok`。

- [ ] **Step 2: Write failing API tests for POST + GET list**

Create `app_unified/tests/test_announcements_api.py`:

```python
"""Announcement API integration tests — uses Flask test client + DB fixtures."""
import json
import pytest

# 既有 conftest.py 已提供 app/client/db fixtures（沿用 mynotes/notes 的測試風格）
# 若 conftest 沒有 user-creation helper，本檔案內提供 inline helper。


def _make_user(db, role, store=None, username=None, features=None):
    from models import User
    import json as _json
    if features is None:
        features = ["notes"]
    u = User(
        username=username or f"{role}_{store or 'na'}_{id(role)}",
        role=role,
        store=store,
        features=_json.dumps(features),
    )
    u.set_password("0000")
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    """flask-login 直接 session 設定（避開人臉驗證）。"""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


# ── POST /notes/announcement/api ─────────────────────────────

def test_post_user_forbidden(client, db):
    u = _make_user(db, "user", store="B店", username="u_post")
    _login(client, u)
    r = client.post("/notes/announcement/api",
                    json={"title": "hi", "content": "x", "store": "B店"})
    assert r.status_code == 403


def test_post_admin_own_store_ok(client, db):
    a = _make_user(db, "admin", store="B店", username="adm_post_ok")
    _login(client, a)
    r = client.post("/notes/announcement/api",
                    json={"title": "B店清潔", "content": "x", "store": "B店"})
    assert r.status_code == 201
    body = r.get_json()
    assert body["store"] == "B店"
    assert body["is_global"] is False
    assert body["author_name"] == a.username


def test_post_admin_global_forbidden(client, db):
    a = _make_user(db, "admin", store="B店", username="adm_post_global")
    _login(client, a)
    r = client.post("/notes/announcement/api",
                    json={"title": "x", "content": "x", "store": None})
    assert r.status_code == 403


def test_post_admin_other_store_forbidden(client, db):
    a = _make_user(db, "admin", store="B店", username="adm_post_other")
    _login(client, a)
    r = client.post("/notes/announcement/api",
                    json={"title": "x", "content": "x", "store": "C店"})
    assert r.status_code == 403


def test_post_super_admin_global_ok(client, db):
    sa = _make_user(db, "super_admin", store=None, username="sa_post_global")
    _login(client, sa)
    r = client.post("/notes/announcement/api",
                    json={"title": "全店公告", "content": "x", "store": None})
    assert r.status_code == 201
    body = r.get_json()
    assert body["is_global"] is True
    assert body["store"] is None


def test_post_super_admin_any_store_ok(client, db):
    sa = _make_user(db, "super_admin", store=None, username="sa_post_any")
    _login(client, sa)
    r = client.post("/notes/announcement/api",
                    json={"title": "C店公告", "content": "x", "store": "C店"})
    assert r.status_code == 201
    assert r.get_json()["store"] == "C店"


def test_post_requires_title(client, db):
    sa = _make_user(db, "super_admin", username="sa_post_title")
    _login(client, sa)
    r = client.post("/notes/announcement/api",
                    json={"title": "", "content": "x", "store": None})
    assert r.status_code == 400


# ── GET /notes/announcement/api (list) ────────────────────────

def test_list_user_sees_global_and_own(client, db):
    sa = _make_user(db, "super_admin", username="sa_list_seed")
    u_b = _make_user(db, "user", store="B店", username="u_list_b")

    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "全店", "content": "x", "store": None})
    client.post("/notes/announcement/api",
                json={"title": "B店", "content": "x", "store": "B店"})
    client.post("/notes/announcement/api",
                json={"title": "C店", "content": "x", "store": "C店"})

    _login(client, u_b)
    r = client.get("/notes/announcement/api")
    assert r.status_code == 200
    items = r.get_json()["items"]
    titles = [it["title"] for it in items]
    assert "全店" in titles
    assert "B店" in titles
    assert "C店" not in titles


def test_list_super_admin_sees_all(client, db):
    sa = _make_user(db, "super_admin", username="sa_list_all")
    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "T1", "content": "x", "store": None})
    client.post("/notes/announcement/api",
                json={"title": "T2", "content": "x", "store": "B店"})
    client.post("/notes/announcement/api",
                json={"title": "T3", "content": "x", "store": "C店"})

    r = client.get("/notes/announcement/api")
    assert r.status_code == 200
    titles = [it["title"] for it in r.get_json()["items"]]
    assert {"T1", "T2", "T3"}.issubset(set(titles))


def test_list_orders_by_created_at_desc(client, db):
    import time
    sa = _make_user(db, "super_admin", username="sa_list_order")
    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "FIRST", "content": "x", "store": None})
    time.sleep(0.05)
    client.post("/notes/announcement/api",
                json={"title": "SECOND", "content": "x", "store": None})
    time.sleep(0.05)
    client.post("/notes/announcement/api",
                json={"title": "THIRD", "content": "x", "store": None})

    r = client.get("/notes/announcement/api")
    titles = [it["title"] for it in r.get_json()["items"]]
    # 最新建立的在最上面
    assert titles.index("THIRD") < titles.index("SECOND") < titles.index("FIRST")
```

- [ ] **Step 3: Run tests to verify they FAIL**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_announcements_api.py -v
```

預期：所有 test FAIL（404 — route 還沒建）。

- [ ] **Step 4: Add POST + GET list routes to `notes/announcement_routes.py`**

在 `app_unified/notes/announcement_routes.py` 檔尾 append：

```python
from datetime import datetime
from sqlalchemy import or_

from models import Announcement, AnnouncementAttachment, Store
from tw_time import tw_iso


# ── Helpers ──────────────────────────────────────────────────

def _ann_to_json(a: Announcement, include_attachments: bool = True):
    from storage import get_signed_url
    out = {
        "id": a.id,
        "title": a.title or "",
        "content": a.content or "",
        "store": a.store,
        "is_global": a.is_global,
        "author_name": a.author_name or "",
        "user_id": a.user_id,
        "created_at": tw_iso(a.created_at),
        "updated_at": tw_iso(a.updated_at),
    }
    if include_attachments:
        out["attachments"] = [
            {
                "id": att.id,
                "filename": att.filename,
                "content_type": att.content_type,
                "file_size": att.file_size,
                "url": get_signed_url(att.object_key, expires_in=3600),
            }
            for att in (a.attachments or [])
        ]
    return out


def _visibility_query():
    """回傳已套用 visibility filter 的 query（給 list / 詳情用）。"""
    if current_user.is_super_admin():
        return Announcement.query
    return Announcement.query.filter(
        or_(
            Announcement.store.is_(None),
            Announcement.store == current_user.store,
        )
    )


def _validate_store_payload(store):
    """store 必須是 None 或合法 Store.name。回傳 (ok, err_msg)。"""
    if store is None:
        return True, None
    if not isinstance(store, str) or not store.strip():
        return False, "store 必須是非空字串或 null"
    exists = Store.query.filter_by(name=store).first()
    if not exists:
        return False, f"store '{store}' 不存在"
    return True, None


# ── POST /notes/announcement/api ─────────────────────────────

@notes_bp.route("/announcement/api", methods=["POST"])
@login_required
def announcement_create():
    if not _can_create():
        return jsonify({"status": "error", "message": "無建立公告權限"}), 403

    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    content = payload.get("content") or ""
    store = payload.get("store")  # None or str

    if not title:
        return jsonify({"status": "error", "message": "title 不可為空"}), 400

    ok, err = _validate_store_payload(store)
    if not ok:
        return jsonify({"status": "error", "message": err}), 400

    if not _can_target_store(store):
        return jsonify({"status": "error", "message": "無此 scope 的權限"}), 403

    a = Announcement(
        user_id=current_user.id,
        author_name=current_user.username,  # snapshot
        title=title,
        content=content,
        store=store,
    )
    db.session.add(a)
    db.session.commit()
    return jsonify(_ann_to_json(a)), 201


# ── GET /notes/announcement/api (list) ────────────────────────

@notes_bp.route("/announcement/api", methods=["GET"])
@login_required
def announcement_list():
    page = request.args.get("page", default=1, type=int) or 1
    per_page = request.args.get("per_page", default=20, type=int) or 20
    per_page = max(1, min(100, per_page))

    q = _visibility_query().order_by(Announcement.created_at.desc())

    # super_admin 可以額外用 ?store= filter
    if current_user.is_super_admin():
        store_filter = request.args.get("store", default=None)
        if store_filter == "__global__":
            q = q.filter(Announcement.store.is_(None))
        elif store_filter:
            q = q.filter(Announcement.store == store_filter)

    pag = q.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "items": [_ann_to_json(a) for a in pag.items],
        "total": pag.total,
        "page": pag.page,
        "per_page": pag.per_page,
        "has_next": pag.has_next,
        "has_prev": pag.has_prev,
    })
```

- [ ] **Step 5: Run tests to verify they PASS**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_announcements_api.py -v
```

預期：10 個 test PASS。如果 conftest 沒有 `client` / `db` fixture，照下面 "如果 conftest fixture 不齊" 處理；如果 fail 是 query/邏輯問題，修 route 不修 test。

**如果 conftest fixture 不齊：** 看 `app_unified/tests/conftest.py` 是否已有 `client` / `db` fixture。如果沒有，沿用 `tests/test_notes.py` 已使用的 conftest pattern；若仍缺 user-creation helper，把上面 `_make_user` / `_login` 留在 test 檔內 inline 就好。

- [ ] **Step 6: Commit**

```bash
git add app_unified/notes/announcement_routes.py app_unified/notes/__init__.py app_unified/tests/test_announcements_api.py
git commit -m "feat(announcements): POST create + GET list endpoints with visibility filter"
```

---

## Task 5: API CRUD Part 2 — GET single + PUT + DELETE

**Files:**
- Modify: `app_unified/notes/announcement_routes.py`
- Modify: `app_unified/tests/test_announcements_api.py`

- [ ] **Step 1: Append failing tests for GET / PUT / DELETE**

Append 到 `app_unified/tests/test_announcements_api.py` 檔尾：

```python
# ── GET /notes/announcement/api/<id> ─────────────────────────

def test_get_single_visible(client, db):
    sa = _make_user(db, "super_admin", username="sa_get_seed")
    u_b = _make_user(db, "user", store="B店", username="u_get_b")

    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "B店公告", "content": "x", "store": "B店"})
    aid = rc.get_json()["id"]

    _login(client, u_b)
    r = client.get(f"/notes/announcement/api/{aid}")
    assert r.status_code == 200
    assert r.get_json()["title"] == "B店公告"


def test_get_single_invisible_returns_404(client, db):
    sa = _make_user(db, "super_admin", username="sa_get_inv_seed")
    u_b = _make_user(db, "user", store="B店", username="u_get_inv")

    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "C店公告", "content": "x", "store": "C店"})
    aid = rc.get_json()["id"]

    _login(client, u_b)
    r = client.get(f"/notes/announcement/api/{aid}")
    assert r.status_code == 404


def test_get_single_not_found(client, db):
    sa = _make_user(db, "super_admin", username="sa_get_404")
    _login(client, sa)
    r = client.get("/notes/announcement/api/999999")
    assert r.status_code == 404


# ── PUT /notes/announcement/api/<id> ─────────────────────────

def test_put_super_admin_can_edit_anything(client, db):
    sa = _make_user(db, "super_admin", username="sa_put_any")
    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "old", "content": "x", "store": "C店"})
    aid = rc.get_json()["id"]

    r = client.put(f"/notes/announcement/api/{aid}",
                   json={"title": "new", "content": "y", "store": "C店"})
    assert r.status_code == 200
    assert r.get_json()["title"] == "new"


def test_put_admin_cannot_edit_other_store(client, db):
    sa = _make_user(db, "super_admin", username="sa_put_seed")
    adm_b = _make_user(db, "admin", store="B店", username="adm_put_b")

    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "C店", "content": "x", "store": "C店"})
    aid = rc.get_json()["id"]

    _login(client, adm_b)
    r = client.put(f"/notes/announcement/api/{aid}",
                   json={"title": "hijack", "content": "x", "store": "C店"})
    # admin (B店) 不可修改 C店 公告 → 看不見 → 404
    assert r.status_code == 404


def test_put_admin_cannot_change_global(client, db):
    sa = _make_user(db, "super_admin", username="sa_put_global_seed")
    adm_b = _make_user(db, "admin", store="B店", username="adm_put_global")

    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "全店", "content": "x", "store": None})
    aid = rc.get_json()["id"]

    _login(client, adm_b)
    r = client.put(f"/notes/announcement/api/{aid}",
                   json={"title": "x", "content": "x", "store": None})
    assert r.status_code == 403  # 看得到（visibility 通過）但 modify 失敗


def test_put_admin_cannot_change_store_to_other(client, db):
    adm_b = _make_user(db, "admin", store="B店", username="adm_put_change")
    _login(client, adm_b)
    rc = client.post("/notes/announcement/api",
                     json={"title": "B店", "content": "x", "store": "B店"})
    aid = rc.get_json()["id"]

    r = client.put(f"/notes/announcement/api/{aid}",
                   json={"title": "B店", "content": "x", "store": "C店"})
    assert r.status_code == 403  # _can_target_store("C店") 失敗


# ── DELETE /notes/announcement/api/<id> ───────────────────────

def test_delete_super_admin_ok(client, db):
    sa = _make_user(db, "super_admin", username="sa_del")
    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "to delete", "content": "x", "store": None})
    aid = rc.get_json()["id"]

    r = client.delete(f"/notes/announcement/api/{aid}")
    assert r.status_code == 200

    r2 = client.get(f"/notes/announcement/api/{aid}")
    assert r2.status_code == 404


def test_delete_admin_other_store_forbidden(client, db):
    sa = _make_user(db, "super_admin", username="sa_del_seed")
    adm_b = _make_user(db, "admin", store="B店", username="adm_del_b")

    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "C店", "content": "x", "store": "C店"})
    aid = rc.get_json()["id"]

    _login(client, adm_b)
    r = client.delete(f"/notes/announcement/api/{aid}")
    assert r.status_code == 404  # 看不見


def test_delete_user_forbidden(client, db):
    sa = _make_user(db, "super_admin", username="sa_del_user_seed")
    u = _make_user(db, "user", store="B店", username="u_del")

    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "B店", "content": "x", "store": "B店"})
    aid = rc.get_json()["id"]

    _login(client, u)
    r = client.delete(f"/notes/announcement/api/{aid}")
    assert r.status_code == 403  # 看得到但無權限刪
```

- [ ] **Step 2: Run tests to verify they FAIL**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_announcements_api.py::test_get_single_visible tests/test_announcements_api.py::test_put_super_admin_can_edit_anything tests/test_announcements_api.py::test_delete_super_admin_ok -v
```

預期：FAIL (404 — route 還沒實作)。

- [ ] **Step 3: Append GET single / PUT / DELETE routes**

Append 到 `app_unified/notes/announcement_routes.py` 檔尾：

```python
from storage import delete_attachment as _r2_delete


# ── GET /notes/announcement/api/<id> ─────────────────────────

@notes_bp.route("/announcement/api/<int:announcement_id>", methods=["GET"])
@login_required
def announcement_get(announcement_id):
    a = Announcement.query.get(announcement_id)
    if not a or not _can_view(a):
        return jsonify({"status": "error", "message": "公告不存在"}), 404
    return jsonify(_ann_to_json(a))


# ── PUT /notes/announcement/api/<id> ─────────────────────────

@notes_bp.route("/announcement/api/<int:announcement_id>", methods=["PUT"])
@login_required
def announcement_update(announcement_id):
    a = Announcement.query.get(announcement_id)
    if not a or not _can_view(a):
        return jsonify({"status": "error", "message": "公告不存在"}), 404
    if not _can_modify(a):
        return jsonify({"status": "error", "message": "無權限編輯"}), 403

    payload = request.get_json(silent=True) or {}
    title = payload.get("title")
    if title is not None:
        title = title.strip()
        if not title:
            return jsonify({"status": "error", "message": "title 不可為空"}), 400
    content = payload.get("content")
    new_store = payload.get("store", a.store)  # 不傳就保留原 scope

    if "store" in payload:
        ok, err = _validate_store_payload(new_store)
        if not ok:
            return jsonify({"status": "error", "message": err}), 400
        if not _can_target_store(new_store):
            return jsonify({"status": "error", "message": "無此 scope 的權限"}), 403

    if title is not None:
        a.title = title
    if content is not None:
        a.content = content
    if "store" in payload:
        a.store = new_store
    a.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(_ann_to_json(a))


# ── DELETE /notes/announcement/api/<id> ───────────────────────

@notes_bp.route("/announcement/api/<int:announcement_id>", methods=["DELETE"])
@login_required
def announcement_delete(announcement_id):
    a = Announcement.query.get(announcement_id)
    if not a or not _can_view(a):
        return jsonify({"status": "error", "message": "公告不存在"}), 404
    if not _can_modify(a):
        return jsonify({"status": "error", "message": "無權限刪除"}), 403

    # 先 R2 清理（cascade=delete-orphan 會處理 DB rows）
    for att in list(a.attachments or []):
        _r2_delete(att.object_key)

    db.session.delete(a)
    db.session.commit()
    return jsonify({"status": "ok"})
```

- [ ] **Step 4: Run all api tests to verify they PASS**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_announcements_api.py -v
```

預期：所有 test (10 + 9 = 19 個) PASS。

- [ ] **Step 5: Commit**

```bash
git add app_unified/notes/announcement_routes.py app_unified/tests/test_announcements_api.py
git commit -m "feat(announcements): GET single + PUT + DELETE endpoints with permission checks"
```

---

## Task 6: Attachment endpoints

**Files:**
- Modify: `app_unified/notes/announcement_routes.py`
- Modify: `app_unified/tests/test_announcements_api.py`

- [ ] **Step 1: Append failing attachment tests**

Append 到 `app_unified/tests/test_announcements_api.py` 檔尾：

```python
# ── Attachments ──────────────────────────────────────────────

def test_attachment_upload_returns_signed_url(client, db, monkeypatch):
    """確認回傳 url 是 R2 presigned (含 X-Amz-Signature) 或 fallback 值。"""
    # mock storage so test doesn't need real R2
    fake_key = "announcements/1/abc.jpg"
    fake_signed = ("https://r2.example.com/announcements/1/abc.jpg"
                   "?X-Amz-Signature=fakesig&X-Amz-Expires=3600")
    monkeypatch.setattr("storage.upload_attachment",
                        lambda *a, **kw: fake_key)
    monkeypatch.setattr("storage.get_signed_url",
                        lambda key, expires_in=3600: fake_signed)

    sa = _make_user(db, "super_admin", username="sa_att_upload")
    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "x", "content": "y", "store": None})
    aid = rc.get_json()["id"]

    from io import BytesIO
    data = {
        "file": (BytesIO(b"\xff\xd8\xff\xe0fake_jpeg_content"), "menu.jpg",
                 "image/jpeg"),
    }
    r = client.post(f"/notes/announcement/api/{aid}/attachments",
                    data=data, content_type="multipart/form-data")
    assert r.status_code == 201
    body = r.get_json()
    assert "X-Amz-Signature" in body["attachment"]["url"]


def test_attachment_storage_prefix(client, db, monkeypatch):
    """確認上傳時呼叫 upload_attachment(prefix='announcements')."""
    captured = {}
    def _capture(file_bytes, ann_id, filename, content_type, prefix="attachments"):
        captured["prefix"] = prefix
        captured["ann_id"] = ann_id
        return f"{prefix}/{ann_id}/uuid.jpg"
    monkeypatch.setattr("storage.upload_attachment", _capture)
    monkeypatch.setattr("storage.get_signed_url", lambda *a, **kw: "https://x")

    sa = _make_user(db, "super_admin", username="sa_att_prefix")
    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "x", "content": "y", "store": None})
    aid = rc.get_json()["id"]

    from io import BytesIO
    data = {"file": (BytesIO(b"x"), "a.jpg", "image/jpeg")}
    client.post(f"/notes/announcement/api/{aid}/attachments",
                data=data, content_type="multipart/form-data")

    assert captured["prefix"] == "announcements"
    assert captured["ann_id"] == aid


def test_attachment_disallowed_content_type(client, db, monkeypatch):
    monkeypatch.setattr("storage.upload_attachment", lambda *a, **kw: "x")
    monkeypatch.setattr("storage.get_signed_url", lambda *a, **kw: "https://x")
    sa = _make_user(db, "super_admin", username="sa_att_disallow")
    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "x", "content": "y", "store": None})
    aid = rc.get_json()["id"]

    from io import BytesIO
    data = {"file": (BytesIO(b"x"), "evil.exe", "application/x-msdownload")}
    r = client.post(f"/notes/announcement/api/{aid}/attachments",
                    data=data, content_type="multipart/form-data")
    assert r.status_code == 400


def test_attachment_user_forbidden(client, db, monkeypatch):
    monkeypatch.setattr("storage.upload_attachment", lambda *a, **kw: "x")
    monkeypatch.setattr("storage.get_signed_url", lambda *a, **kw: "https://x")

    sa = _make_user(db, "super_admin", username="sa_att_user_seed")
    u = _make_user(db, "user", store="B店", username="u_att")
    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "x", "content": "y", "store": "B店"})
    aid = rc.get_json()["id"]

    _login(client, u)
    from io import BytesIO
    data = {"file": (BytesIO(b"x"), "a.jpg", "image/jpeg")}
    r = client.post(f"/notes/announcement/api/{aid}/attachments",
                    data=data, content_type="multipart/form-data")
    assert r.status_code == 403


def test_attachment_delete_cascades_when_announcement_deleted(client, db, monkeypatch):
    """刪公告後 attachment row 被 cascade 刪掉。"""
    monkeypatch.setattr("storage.upload_attachment",
                        lambda *a, **kw: "announcements/1/x.jpg")
    monkeypatch.setattr("storage.get_signed_url",
                        lambda *a, **kw: "https://x")
    monkeypatch.setattr("storage.delete_attachment",
                        lambda *a, **kw: True)

    sa = _make_user(db, "super_admin", username="sa_cascade")
    _login(client, sa)
    rc = client.post("/notes/announcement/api",
                     json={"title": "x", "content": "y", "store": None})
    aid = rc.get_json()["id"]

    from io import BytesIO
    data = {"file": (BytesIO(b"x"), "a.jpg", "image/jpeg")}
    client.post(f"/notes/announcement/api/{aid}/attachments",
                data=data, content_type="multipart/form-data")

    from models import AnnouncementAttachment
    assert AnnouncementAttachment.query.filter_by(announcement_id=aid).count() == 1

    client.delete(f"/notes/announcement/api/{aid}")

    assert AnnouncementAttachment.query.filter_by(announcement_id=aid).count() == 0
```

- [ ] **Step 2: Run new tests to verify they FAIL**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_announcements_api.py -v -k attachment
```

預期：5 test FAIL (404 — attachment route 還沒建)。

- [ ] **Step 3: Append attachment routes**

Append 到 `app_unified/notes/announcement_routes.py` 檔尾：

```python
# ── Attachments ──────────────────────────────────────────────

@notes_bp.route("/announcement/api/<int:announcement_id>/attachments",
                methods=["POST"])
@login_required
def announcement_attachment_upload(announcement_id):
    from storage import (upload_attachment, get_signed_url,
                         ALLOWED_CONTENT_TYPES, MAX_FILE_SIZE)

    a = Announcement.query.get(announcement_id)
    if not a or not _can_view(a):
        return jsonify({"status": "error", "message": "公告不存在"}), 404
    if not _can_modify(a):
        return jsonify({"status": "error", "message": "無權限"}), 403

    try:
        file = request.files.get("file")
    except OSError as e:
        if "unexpected end of file" in str(e):
            return jsonify({"status": "error", "message": "上傳中斷，請重試"}), 400
        raise
    if not file or not file.filename:
        return jsonify({"status": "error", "message": "未選擇檔案"}), 400

    content_type = file.content_type or ""
    if content_type not in ALLOWED_CONTENT_TYPES:
        return jsonify({"status": "error", "message": "不支援的檔案格式"}), 400

    file_bytes = file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        return jsonify({"status": "error", "message": "檔案超過 50MB 上限"}), 413

    object_key = upload_attachment(file_bytes, a.id, file.filename,
                                    content_type, prefix="announcements")
    if not object_key:
        return jsonify({"status": "error", "message": "儲存服務未設定"}), 503

    att = AnnouncementAttachment(
        announcement_id=a.id,
        user_id=current_user.id,
        object_key=object_key,
        filename=file.filename,
        content_type=content_type,
        file_size=len(file_bytes),
    )
    db.session.add(att)
    a.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "status": "ok",
        "attachment": {
            "id": att.id,
            "filename": att.filename,
            "content_type": att.content_type,
            "file_size": att.file_size,
            "url": get_signed_url(att.object_key, expires_in=3600),
        },
    }), 201


@notes_bp.route("/announcement/api/<int:announcement_id>/attachments",
                methods=["GET"])
@login_required
def announcement_attachment_list(announcement_id):
    from storage import get_signed_url
    a = Announcement.query.get(announcement_id)
    if not a or not _can_view(a):
        return jsonify({"status": "error", "message": "公告不存在"}), 404
    return jsonify({
        "status": "ok",
        "attachments": [
            {
                "id": att.id,
                "filename": att.filename,
                "content_type": att.content_type,
                "file_size": att.file_size,
                "url": get_signed_url(att.object_key, expires_in=3600),
                "created_at": tw_iso(att.created_at),
            }
            for att in (a.attachments or [])
        ],
    })


@notes_bp.route("/announcement/api/attachments/<int:attachment_id>",
                methods=["DELETE"])
@login_required
def announcement_attachment_delete(attachment_id):
    att = AnnouncementAttachment.query.get(attachment_id)
    if not att:
        return jsonify({"status": "error", "message": "附件不存在"}), 404
    a = Announcement.query.get(att.announcement_id)
    if not a or not _can_view(a):
        return jsonify({"status": "error", "message": "公告不存在"}), 404
    if not _can_modify(a):
        return jsonify({"status": "error", "message": "無權限"}), 403

    _r2_delete(att.object_key)
    db.session.delete(att)
    a.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"status": "ok"})
```

- [ ] **Step 4: Run all api tests**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_announcements_api.py -v
```

預期：全部 ~24 個 test PASS。

- [ ] **Step 5: Commit**

```bash
git add app_unified/notes/announcement_routes.py app_unified/tests/test_announcements_api.py
git commit -m "feat(announcements): attachment upload/list/delete with R2 presigned URLs"
```

---

## Task 7: HTML pages — `new` + `edit` + templates

**Files:**
- Modify: `app_unified/notes/announcement_routes.py` (新加 GET HTML routes)
- Create: `app_unified/templates/announcements/new.html`
- Create: `app_unified/templates/announcements/edit.html`

- [ ] **Step 1: Append HTML page routes**

Append 到 `app_unified/notes/announcement_routes.py` 檔尾：

```python
# ── HTML pages ───────────────────────────────────────────────

@notes_bp.route("/announcement/new", methods=["GET"])
@login_required
def announcement_new_page():
    if not _can_create():
        abort(403)
    stores = Store.query.order_by(Store.name).all()
    return render_template(
        "announcements/new.html",
        stores=stores,
        is_super_admin=current_user.is_super_admin(),
        user_store=current_user.store,
    )


@notes_bp.route("/announcement/<int:announcement_id>/edit", methods=["GET"])
@login_required
def announcement_edit_page(announcement_id):
    a = Announcement.query.get(announcement_id)
    if not a or not _can_view(a):
        abort(404)
    if not _can_modify(a):
        abort(403)
    stores = Store.query.order_by(Store.name).all()
    return render_template(
        "announcements/edit.html",
        announcement=a,
        stores=stores,
        is_super_admin=current_user.is_super_admin(),
        user_store=current_user.store,
    )
```

- [ ] **Step 2: Create `templates/announcements/new.html`**

Create `app_unified/templates/announcements/new.html`:

```html
{% extends "base.html" %}
{% block title %}新增公告{% endblock %}

{% block head_extra %}
<link rel="stylesheet" href="{{ url_for('static', filename='css/announcements.css') }}">
{% endblock %}

{% block content %}
<div class="ann-form-page">
  <h1>📢 新增公告</h1>

  <form id="ann-form" onsubmit="return submitAnnouncement(event)">
    <label for="ann-title">標題 <span class="required">*</span></label>
    <input type="text" id="ann-title" name="title" required maxlength="200"
           placeholder="輸入公告標題">

    <label>適用範圍 <span class="required">*</span></label>
    <div class="scope-radios">
      {% if is_super_admin %}
      <label class="scope-opt"><input type="radio" name="store" value="" checked> 全部（所有店看得到）</label>
      {% for s in stores %}
      <label class="scope-opt"><input type="radio" name="store" value="{{ s.name }}"> {{ s.name }}</label>
      {% endfor %}
      {% else %}
      <input type="hidden" name="store" value="{{ user_store }}">
      <span class="scope-locked">{{ user_store }}（admin 只能對自己店發公告）</span>
      {% endif %}
    </div>

    <label for="ann-content">內容</label>
    <textarea id="ann-content" name="content" rows="10"
              placeholder="輸入公告內容..."></textarea>

    <div class="attachment-section">
      <h4>附件</h4>
      <input type="file" id="ann-file-input"
             accept="image/jpeg,image/png,image/gif,image/webp,video/mp4,video/quicktime,video/webm"
             multiple onchange="handleAnnFileUpload(this)">
      <div class="attachment-list" id="ann-attachment-list"></div>
      <div id="ann-upload-status" class="upload-status"></div>
    </div>

    <div class="form-actions">
      <a href="{{ url_for('notes.index') }}" class="btn btn-secondary">取消</a>
      <button type="submit" class="btn btn-primary">建立公告</button>
    </div>
  </form>
</div>

<script>
window.ANN_ID = null;  // 新建頁，submit 後才有 id
window.ANN_PENDING_FILES = [];
</script>
<script src="{{ url_for('static', filename='js/announcement_attachments.js') }}"></script>
<script>
function submitAnnouncement(e) {
  e.preventDefault();
  var form = e.target;
  var fd = new FormData(form);
  var payload = {
    title: fd.get('title'),
    content: fd.get('content'),
    store: fd.get('store') || null,
  };
  if (payload.store === '') payload.store = null;
  fetch('/notes/announcement/api', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  }).then(function(r) { return r.json().then(function(j) { return [r, j]; }); })
    .then(function(pair) {
      var r = pair[0], j = pair[1];
      if (r.status !== 201) {
        alert('建立失敗：' + (j.message || r.statusText));
        return;
      }
      // 上傳 pending files 到剛建立的公告
      uploadAnnPendingFiles(j.id, function() {
        location.href = "{{ url_for('notes.index') }}";
      });
    });
  return false;
}
</script>
{% endblock %}
```

- [ ] **Step 3: Create `templates/announcements/edit.html`**

Create `app_unified/templates/announcements/edit.html`:

```html
{% extends "base.html" %}
{% block title %}編輯公告{% endblock %}

{% block head_extra %}
<link rel="stylesheet" href="{{ url_for('static', filename='css/announcements.css') }}">
{% endblock %}

{% block content %}
<div class="ann-form-page">
  <h1>📢 編輯公告</h1>

  <form id="ann-form" onsubmit="return updateAnnouncement(event)">
    <label for="ann-title">標題 <span class="required">*</span></label>
    <input type="text" id="ann-title" name="title" value="{{ announcement.title }}"
           required maxlength="200">

    <label>適用範圍 <span class="required">*</span></label>
    <div class="scope-radios">
      {% if is_super_admin %}
      <label class="scope-opt">
        <input type="radio" name="store" value=""
               {% if announcement.is_global %}checked{% endif %}>
        全部
      </label>
      {% for s in stores %}
      <label class="scope-opt">
        <input type="radio" name="store" value="{{ s.name }}"
               {% if announcement.store == s.name %}checked{% endif %}>
        {{ s.name }}
      </label>
      {% endfor %}
      {% else %}
      <input type="hidden" name="store" value="{{ user_store }}">
      <span class="scope-locked">{{ user_store }}</span>
      {% endif %}
    </div>

    <label for="ann-content">內容</label>
    <textarea id="ann-content" name="content" rows="10">{{ announcement.content }}</textarea>

    <div class="attachment-section">
      <h4>附件</h4>
      <input type="file" id="ann-file-input"
             accept="image/jpeg,image/png,image/gif,image/webp,video/mp4,video/quicktime,video/webm"
             multiple onchange="handleAnnFileUpload(this)">
      <div class="attachment-list" id="ann-attachment-list"></div>
      <div id="ann-upload-status" class="upload-status"></div>
    </div>

    <div class="form-actions">
      <a href="{{ url_for('notes.index') }}" class="btn btn-secondary">取消</a>
      <button type="submit" class="btn btn-primary">儲存</button>
      <button type="button" class="btn btn-danger" onclick="deleteAnnouncement()">刪除公告</button>
    </div>
  </form>
</div>

<script>
window.ANN_ID = {{ announcement.id }};
window.ANN_PENDING_FILES = [];
</script>
<script src="{{ url_for('static', filename='js/announcement_attachments.js') }}"></script>
<script>
// 載入既有附件
fetch('/notes/announcement/api/{{ announcement.id }}/attachments')
  .then(function(r) { return r.json(); })
  .then(function(d) {
    var list = document.getElementById('ann-attachment-list');
    list.innerHTML = '';
    (d.attachments || []).forEach(function(att) {
      list.appendChild(createAnnAttItem(att));
    });
  });

function updateAnnouncement(e) {
  e.preventDefault();
  var form = e.target;
  var fd = new FormData(form);
  var payload = {
    title: fd.get('title'),
    content: fd.get('content'),
    store: fd.get('store') || null,
  };
  if (payload.store === '') payload.store = null;
  fetch('/notes/announcement/api/{{ announcement.id }}', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  }).then(function(r) { return r.json().then(function(j) { return [r, j]; }); })
    .then(function(pair) {
      var r = pair[0], j = pair[1];
      if (r.status !== 200) {
        alert('儲存失敗：' + (j.message || r.statusText));
        return;
      }
      uploadAnnPendingFiles({{ announcement.id }}, function() {
        location.href = "{{ url_for('notes.index') }}";
      });
    });
  return false;
}

function deleteAnnouncement() {
  if (!confirm('確定刪除這則公告？')) return;
  fetch('/notes/announcement/api/{{ announcement.id }}', {method: 'DELETE'})
    .then(function(r) {
      if (r.status === 200) location.href = "{{ url_for('notes.index') }}";
      else alert('刪除失敗');
    });
}
</script>
{% endblock %}
```

- [ ] **Step 4: Smoke test page rendering**

啟動 app（如果還沒）：
```bash
cd /home/hirain0126/projects/webapp/.worktrees/feat-notes-announcement && \
bash app_unified/start.sh > /tmp/app_start.log 2>&1 &
sleep 3
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5000/notes/announcement/new
```

預期：`302`（未登入跳 login，這代表 route 註冊成功）。

如果是 `404` → route 沒註冊，回去檢查 `notes/__init__.py` 是否 import 了 `announcement_routes`。

- [ ] **Step 5: Commit**

```bash
git add app_unified/notes/announcement_routes.py app_unified/templates/announcements/new.html app_unified/templates/announcements/edit.html
git commit -m "feat(announcements): GET /new + /<id>/edit HTML pages with form templates"
```

---

## Task 8: Notes index 整合（route + index.html + partial + CSS + JS）

**Files:**
- Modify: `app_unified/notes/routes.py::index()`
- Modify: `app_unified/templates/notes/index.html`
- Create: `app_unified/templates/announcements/_card_list.html`
- Create: `app_unified/static/css/announcements.css`
- Create: `app_unified/static/js/announcement_attachments.js`
- Create: `app_unified/tests/test_announcements_index_integration.py`

- [ ] **Step 1: Write failing index integration tests**

Create `app_unified/tests/test_announcements_index_integration.py`:

```python
"""Notes index 渲染整合測試 — 公告區塊 / 第三按鈕 conditional 顯示。"""
import pytest


def _make_user(db, role, store=None, username=None, features=None):
    from models import User
    import json as _json
    if features is None:
        features = ["notes"]
    u = User(
        username=username or f"{role}_{store or 'na'}",
        role=role,
        store=store,
        features=_json.dumps(features),
    )
    u.set_password("0000")
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_admin_sees_create_announcement_button(client, db):
    a = _make_user(db, "admin", store="B店", username="adm_idx_btn")
    _login(client, a)
    r = client.get("/notes/")
    assert r.status_code == 200
    assert "新增公告" in r.get_data(as_text=True)


def test_user_does_not_see_create_announcement_button(client, db):
    u = _make_user(db, "user", store="B店", username="u_idx_btn")
    _login(client, u)
    r = client.get("/notes/")
    assert r.status_code == 200
    assert "新增公告" not in r.get_data(as_text=True)


def test_announcements_render_above_notes(client, db):
    sa = _make_user(db, "super_admin", username="sa_idx_seed")
    u = _make_user(db, "user", store="B店", username="u_idx_render")

    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "TEST_ANNOUNCEMENT", "content": "x", "store": None})

    _login(client, u)
    r = client.get("/notes/")
    html = r.get_data(as_text=True)
    assert "TEST_ANNOUNCEMENT" in html
    # announcement-section / announcement-card class 出現在 HTML 中
    assert "announcement-section" in html or "announcement-card" in html


def test_user_does_not_see_other_store_announcement_in_index(client, db):
    sa = _make_user(db, "super_admin", username="sa_idx_other_seed")
    u = _make_user(db, "user", store="B店", username="u_idx_other")

    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "C_STORE_SECRET", "content": "x", "store": "C店"})

    _login(client, u)
    r = client.get("/notes/")
    html = r.get_data(as_text=True)
    assert "C_STORE_SECRET" not in html


def test_admin_sees_edit_button_for_own_store(client, db):
    sa = _make_user(db, "super_admin", username="sa_idx_edit_seed")
    adm_b = _make_user(db, "admin", store="B店", username="adm_idx_edit")

    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "B_OWN", "content": "x", "store": "B店"})
    client.post("/notes/announcement/api",
                json={"title": "C_NOT_MINE", "content": "x", "store": "C店"})

    _login(client, adm_b)
    r = client.get("/notes/")
    html = r.get_data(as_text=True)
    # 看得到自己店公告 + 編輯連結
    assert "B_OWN" in html
    # 看不到 C 店
    assert "C_NOT_MINE" not in html
```

- [ ] **Step 2: Run tests to verify they FAIL**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_announcements_index_integration.py -v
```

預期：FAIL（template 還沒改 / route 沒傳公告資料）。

- [ ] **Step 3: Modify `notes/routes.py::index()` to query announcements**

`app_unified/notes/routes.py` 的 `index()` 函式（line 175 附近）：找到 `return render_template("notes/index.html", ...)` 那一行，**前面**插入：

```python
    # ─── Announcements query (visibility-filtered) ───
    from models import Announcement
    from sqlalchemy import or_ as _or
    if current_user.is_super_admin():
        _ann_q = Announcement.query
    else:
        _ann_q = Announcement.query.filter(
            _or(Announcement.store.is_(None),
                Announcement.store == current_user.store)
        )
    announcements = _ann_q.order_by(Announcement.created_at.desc()).all()
    if current_user.is_super_admin():
        editable_ann_ids = {a.id for a in announcements}
    elif current_user.is_admin():
        editable_ann_ids = {a.id for a in announcements
                            if a.store == current_user.store}
    else:
        editable_ann_ids = set()
```

然後修改 `return render_template(...)` 把這兩個變數加進 context kwargs：

```python
    return render_template(
        "notes/index.html",
        # ... 既有 kwargs 全部保留 ...
        announcements=announcements,
        editable_ann_ids=editable_ann_ids,
    )
```

⚠️ 注意：**不要動其他 1500+ 行**。只改 `index()` 函式內部。

- [ ] **Step 4: Modify `templates/notes/index.html`**

在 `app_unified/templates/notes/index.html` 找到下列三處小改動：

(a) `<head>` block（找已有的 `<link>` 區塊或 `{% block head_extra %}`），加：
```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/announcements.css') }}">
```

(b) 找 `<button type="button" class="btn btn-primary" onclick="openCategoryModal('checklist')"...>☑ 新增確認表單</button>`（line 88 附近），**緊接後面**加第三個按鈕：
```html
{% if current_user.is_admin() %}
<a href="{{ url_for('notes.announcement_new_page') }}" class="btn btn-primary"
   style="background:#f39c12;text-decoration:none;display:inline-block;">📢 新增公告</a>
{% endif %}
```

(c) 找一般筆記列表/搜尋區塊起始處（在 `<h1>` 後、第一個 `<form>` 或第一個 note 卡片前），插入 partial include：
```html
{% include 'announcements/_card_list.html' %}
```

- [ ] **Step 5: Create partial `templates/announcements/_card_list.html`**

Create `app_unified/templates/announcements/_card_list.html`:

```html
{% if announcements %}
<section class="announcement-section">
  <h2 class="announcement-section-title">📢 公告</h2>
  {% for a in announcements %}
  <article class="announcement-card {% if a.is_global %}is-global{% endif %}"
           data-ann-id="{{ a.id }}">
    <header class="ann-header">
      <span class="scope-chip">{{ '全部' if a.is_global else a.store }}</span>
      <h3 class="ann-title">{{ a.title }}</h3>
    </header>
    <div class="ann-content">{{ a.content }}</div>
    <div class="ann-attachments" data-ann-id="{{ a.id }}"></div>
    <footer class="ann-footer">
      <span class="ann-author">{{ a.display_author }}</span>
      <time class="ann-time">{{ a.created_at.strftime('%Y-%m-%d %H:%M') }}</time>
      {% if a.id in editable_ann_ids %}
      <a class="btn btn-secondary btn-sm"
         href="{{ url_for('notes.announcement_edit_page', announcement_id=a.id) }}">編輯</a>
      {% endif %}
    </footer>
  </article>
  {% endfor %}
</section>

<script src="{{ url_for('static', filename='js/announcement_attachments.js') }}"></script>
<script>
// 為每張公告卡片載入附件 list（縮圖 + 影片 inline + 點圖放大）
document.querySelectorAll('.ann-attachments[data-ann-id]').forEach(function(holder) {
  var id = holder.getAttribute('data-ann-id');
  fetch('/notes/announcement/api/' + id + '/attachments')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      (d.attachments || []).forEach(function(att) {
        holder.appendChild(renderAnnAttView(att));
      });
    });
});
</script>
{% endif %}
```

- [ ] **Step 6: Create `static/css/announcements.css`**

Create `app_unified/static/css/announcements.css`:

```css
/* ===== Announcement card (in /notes/ index 上方) ===== */
.announcement-section {
  margin: 1rem 0 1.5rem;
  border-left: 4px solid #f39c12;
  padding-left: .75rem;
}
.announcement-section-title {
  font-size: 1.1rem;
  margin: 0 0 .5rem;
  color: #b88a00;
}
.announcement-card {
  background: #fffbea;
  border: 1px solid #f9d77b;
  border-radius: 6px;
  padding: .75rem 1rem;
  margin-bottom: .5rem;
}
.announcement-card.is-global {
  background: #fff5f5;
  border-color: #f5a3a3;
}
.ann-header { display: flex; align-items: center; gap: .5rem; margin-bottom: .35rem; }
.ann-title { margin: 0; font-size: 1rem; color: #333; }
.scope-chip {
  display: inline-block;
  padding: 2px 10px;
  background: #f39c12;
  color: white;
  border-radius: 10px;
  font-size: .75rem;
}
.announcement-card.is-global .scope-chip { background: #e74c3c; }
.ann-content { white-space: pre-wrap; color: #555; margin-bottom: .5rem; }
.ann-attachments { display: flex; flex-wrap: wrap; gap: .5rem; margin-bottom: .5rem; }
.ann-attachments .att-view { max-width: 200px; }
.ann-attachments .att-view img,
.ann-attachments .att-view video { max-width: 200px; max-height: 150px; border-radius: 4px; cursor: pointer; }
.ann-footer { display: flex; gap: .75rem; align-items: center; font-size: .8rem; color: #777; }
.btn-sm { padding: .2rem .6rem; font-size: .75rem; }

/* ===== Form pages (/notes/announcement/new + /<id>/edit) ===== */
.ann-form-page { max-width: 720px; margin: 0 auto; padding: 1rem; }
.ann-form-page label { display: block; margin-top: .75rem; font-weight: 600; color: #555; }
.ann-form-page label .required { color: #e74c3c; }
.ann-form-page input[type="text"],
.ann-form-page textarea {
  width: 100%; padding: .5rem; border: 1px solid #ccc; border-radius: 4px;
  font-size: .95rem; box-sizing: border-box;
}
.ann-form-page textarea { resize: vertical; min-height: 200px; }
.scope-radios { display: flex; flex-wrap: wrap; gap: .75rem; margin-top: .35rem; }
.scope-opt { font-weight: normal; }
.scope-locked { color: #888; font-style: italic; }
.attachment-section { margin-top: 1.5rem; }
.attachment-section h4 { font-size: .9rem; color: #666; margin-bottom: .5rem; }
.attachment-list { display: flex; flex-wrap: wrap; gap: .75rem; margin-top: .5rem; }
.att-item { position: relative; }
.att-item img,
.att-item video { display: block; max-width: 280px; max-height: 200px; border-radius: 8px; }
.att-item .att-info { font-size: .75rem; color: #777; margin-top: .25rem; }
.att-delete {
  position: absolute; top: 4px; right: 4px;
  background: rgba(231, 76, 60, .9); color: white; border: none;
  border-radius: 50%; width: 22px; height: 22px; cursor: pointer;
  font-size: 14px; line-height: 18px; padding: 0;
}
.upload-status { margin-top: .5rem; font-size: .8rem; color: #888; }
.form-actions { margin-top: 1.5rem; display: flex; gap: .5rem; }
.btn-danger { background: #e74c3c; color: white; }
```

- [ ] **Step 7: Create `static/js/announcement_attachments.js`**

Create `app_unified/static/js/announcement_attachments.js`:

```javascript
/* 公告附件 UI — 複製 notes/editor.html 的 createAttItem / openFullscreen pattern。
 * 提供：
 *   - createAnnAttItem(att) : 編輯/建立頁的附件 item（含刪除 ×）
 *   - renderAnnAttView(att) : index 卡片唯讀展示（無刪除 ×）
 *   - handleAnnFileUpload(input) : 編輯/建立頁 file input change handler
 *   - uploadAnnPendingFiles(annId, cb) : submit 後逐檔上傳
 *   - openFullscreen(url)
 * 依賴 window.ANN_ID（編輯頁 = id；建立頁 = null）+ window.ANN_PENDING_FILES。
 */

function openFullscreen(url) {
  var overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,.92);z-index:9999;display:flex;align-items:center;justify-content:center;cursor:zoom-out;';
  var img = document.createElement('img');
  img.src = url;
  img.style.cssText = 'max-width:95vw;max-height:95vh;';
  overlay.appendChild(img);
  overlay.onclick = function() { document.body.removeChild(overlay); };
  document.body.appendChild(overlay);
}

function _formatSize(bytes) {
  if (bytes > 1048576) return (bytes / 1048576).toFixed(1) + ' MB';
  return (bytes / 1024).toFixed(0) + ' KB';
}

function _renderMedia(att, opts) {
  // opts.maxWidth / opts.maxHeight optional
  if (att.content_type && att.content_type.indexOf('image/') === 0) {
    var img = document.createElement('img');
    img.src = att.url;
    img.alt = att.filename;
    img.onclick = function(e) { e.stopPropagation(); openFullscreen(att.url); };
    return img;
  }
  if (att.content_type && att.content_type.indexOf('video/') === 0) {
    var video = document.createElement('video');
    video.src = att.url;
    video.controls = true;
    video.preload = 'metadata';
    video.playsInline = true;
    return video;
  }
  // fallback：純連結
  var a = document.createElement('a');
  a.href = att.url;
  a.textContent = '📎 ' + att.filename;
  a.target = '_blank';
  return a;
}

function createAnnAttItem(att) {
  var div = document.createElement('div');
  div.className = 'att-item';
  div.id = 'ann-att-' + att.id;
  div.appendChild(_renderMedia(att));

  var info = document.createElement('div');
  info.className = 'att-info';
  info.textContent = att.filename + ' (' + _formatSize(att.file_size) + ')';
  div.appendChild(info);

  var delBtn = document.createElement('button');
  delBtn.className = 'att-delete';
  delBtn.textContent = '×';
  delBtn.onclick = function(e) {
    e.stopPropagation();
    if (!confirm('刪除此附件？')) return;
    fetch('/notes/announcement/api/attachments/' + att.id, {method: 'DELETE'})
      .then(function(r) {
        if (r.status === 200) {
          var el = document.getElementById('ann-att-' + att.id);
          if (el) el.remove();
        } else {
          alert('刪除失敗');
        }
      });
  };
  div.appendChild(delBtn);
  return div;
}

function renderAnnAttView(att) {
  // index 卡片唯讀版：沒有刪除按鈕
  var div = document.createElement('div');
  div.className = 'att-view';
  div.appendChild(_renderMedia(att));
  return div;
}

function handleAnnFileUpload(input) {
  var files = input.files;
  if (!files.length) return;
  var statusEl = document.getElementById('ann-upload-status');
  var list = document.getElementById('ann-attachment-list');

  if (!window.ANN_ID) {
    // 新建頁：暫存到記憶體 + 顯示本地預覽
    for (var i = 0; i < files.length; i++) {
      window.ANN_PENDING_FILES.push(files[i]);
      var f = files[i];
      var url = URL.createObjectURL(f);
      var div = document.createElement('div');
      div.className = 'att-item';
      var fakeAtt = {url: url, content_type: f.type, filename: f.name, file_size: f.size};
      div.appendChild(_renderMedia(fakeAtt));
      var info = document.createElement('div');
      info.className = 'att-info';
      info.textContent = f.name + ' (本地預覽，建立後上傳)';
      div.appendChild(info);
      list.appendChild(div);
    }
    input.value = '';
    return;
  }
  // 編輯頁：直接上傳
  Array.prototype.forEach.call(files, function(f) {
    var fd = new FormData();
    fd.append('file', f);
    statusEl.textContent = '上傳中：' + f.name + ' …';
    fetch('/notes/announcement/api/' + window.ANN_ID + '/attachments',
          {method: 'POST', body: fd})
      .then(function(r) { return r.json().then(function(j) { return [r, j]; }); })
      .then(function(pair) {
        var r = pair[0], d = pair[1];
        if (r.status === 201) {
          list.appendChild(createAnnAttItem(d.attachment));
          statusEl.textContent = '';
        } else {
          alert('上傳失敗：' + (d.message || r.statusText));
          statusEl.textContent = '';
        }
      });
  });
  input.value = '';
}

function uploadAnnPendingFiles(annId, cb) {
  var pending = window.ANN_PENDING_FILES || [];
  if (!pending.length) { cb(); return; }
  var idx = 0;
  function next() {
    if (idx >= pending.length) { cb(); return; }
    var f = pending[idx++];
    var fd = new FormData();
    fd.append('file', f);
    fetch('/notes/announcement/api/' + annId + '/attachments',
          {method: 'POST', body: fd})
      .then(function(r) { next(); })
      .catch(function() { next(); });
  }
  next();
}
```

- [ ] **Step 8: Run integration tests to verify they PASS**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_announcements_index_integration.py -v
```

預期：5 test PASS。

- [ ] **Step 9: Run all announcements tests + notes regression**

```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest \
  tests/test_announcements_permission.py \
  tests/test_announcements_api.py \
  tests/test_announcements_index_integration.py \
  tests/test_notes.py \
  tests/test_notes_lock.py \
  tests/test_notes_misc.py \
  tests/test_category.py \
  tests/test_admin_set_role.py \
  tests/test_ai_task_race.py \
  -v
```

預期：announcements 全綠、既有 notes/admin/category/race tests 沒退步（可能仍有 9 個 pre-existing test_notes broken stubs，那是 baseline，不是本 task 造成）。

- [ ] **Step 10: Commit**

```bash
git add app_unified/notes/routes.py \
        app_unified/templates/notes/index.html \
        app_unified/templates/announcements/_card_list.html \
        app_unified/static/css/announcements.css \
        app_unified/static/js/announcement_attachments.js \
        app_unified/tests/test_announcements_index_integration.py
git commit -m "feat(notes/announcement): integrate announcements into /notes/ index (above pinned)"
```

---

## Task 9: Manual verification + final review

**Files:** N/A (手動驗證 + 整體 review)

- [ ] **Step 1: 啟動 dev server + 走完 spec 第 8.4 節 checklist**

```bash
cd /home/hirain0126/projects/webapp/.worktrees/feat-notes-announcement && \
bash app_unified/start.sh > /tmp/app_start.log 2>&1 &
sleep 3
echo "App PID: $!"
```

開瀏覽器 `http://localhost:5000/notes/`，逐項驗證：

- [ ] super_admin 登入，看到「📢 新增公告」按鈕
- [ ] super_admin 建立「全部」公告 → 各店 user 都看得到（淡紅卡片）
- [ ] super_admin 建立「B 店」公告 → 只有 B 店 user 看得到（淡黃卡片）
- [ ] admin (B 店) 登入 → 看到「📢 新增公告」按鈕
- [ ] admin (B 店) 進入 `/notes/announcement/new`，**沒有**「全部」radio 選項，store 鎖定 B 店
- [ ] admin (B 店) 嘗試手動 POST `{"store": "C店"}` → 403
- [ ] user 登入 → **看不到**「📢 新增公告」按鈕
- [ ] user 仍看得到公告卡片，但卡片上**沒有**「編輯」連結
- [ ] 上傳 image → 公告卡片顯示縮圖；點縮圖 → 全螢幕放大；ESC 或點背景關閉
- [ ] 上傳 video → 公告卡片 inline 播放（按 ▶︎ 可播）
- [ ] 上傳 disallowed (`.exe`) → 收到錯誤訊息
- [ ] DevTools → Network → 看公告附件 GET response → `url` 欄位含 `X-Amz-Signature`
- [ ] 進入 /notes/ → 公告區塊在最上面，順序最新建立優先
- [ ] super_admin 編輯公告 → 改 title 後刷新 → 看到新 title
- [ ] super_admin 刪除公告 → 卡片消失；`announcement_attachments` row 也 cascade 刪掉（DB 直接查）
- [ ] /notes/ AI 摘要產出（按「產生摘要」）→ 公告**不出現**在分類結果中

- [ ] **Step 2: Final code review (self-check)**

```bash
cd /home/hirain0126/projects/webapp/.worktrees/feat-notes-announcement
git diff main --stat
git log --oneline main..HEAD
```

確認：
- 所有 commit message 都遵循 `feat(announcements): ...` / `feat(notes/announcement): ...` 格式
- `notes/routes.py` 只動 `index()` 函式內部（其他 1500+ 行未動）— `git diff main -- app_unified/notes/routes.py` 應該只顯示 `index()` 內的新增行
- `notes/__init__.py` 多 1 行 import
- 沒有意外動到 `mynotes/`、`weather/`、`admin/`、`vetting/`、`auth/`、`face/`

- [ ] **Step 3: Stop dev server**

```bash
fuser -k 5000/tcp 2>/dev/null || true
```

- [ ] **Step 4: Merge 回 main（依 `feedback_branch_workflow`，使用者驗證後）**

⚠️ 這一步要等使用者明確確認所有手動驗證都 OK 再執行。先不要自動 merge。

```bash
# 使用者說 OK 後：
cd /home/hirain0126/projects/webapp
git checkout main
git merge --no-ff feat/notes-announcement -m "Merge feat/notes-announcement: super_admin/admin global + per-store announcements"
git log --oneline -5
```

不 push（依 `feedback_push_policy`，使用者明確說推才推）。

- [ ] **Step 5: Worktree 收尾**

依 `feedback_branch_workflow`：「Merge 回 main 之後才考慮 push」。worktree 可保留供萬一回退（mirror `feat-notes-ux-polish` / `feat-icinfo` 做法）。

至此 plan 完成。

---

## 實作完畢 Checklist

| 項目 | 來源 |
|------|------|
| ✅ 新表 announcements + announcement_attachments | Task 1 |
| ✅ Models with `is_global` / `display_author` | Task 2 |
| ✅ Permission helpers (super_admin/admin/user matrix) | Task 3 |
| ✅ POST + GET list (visibility filter) | Task 4 |
| ✅ GET single + PUT + DELETE (cascade R2 cleanup) | Task 5 |
| ✅ Attachment upload/list/delete (signed URL, R2 prefix) | Task 6 |
| ✅ HTML pages new + edit | Task 7 |
| ✅ /notes/ index 整合（route + button + partial + CSS + JS） | Task 8 |
| ✅ 手動驗證 + merge | Task 9 |
| ✅ Spec 第 8 節所有 test scenarios | Task 3, 4, 5, 6, 8 |
| ✅ R2 presigned URL 1hr | Task 4 (`_ann_to_json`), Task 6 (upload response) |
| ✅ 圖片放大、影片播放 | Task 7 (`new.html` / `edit.html`)、Task 8 (`announcement_attachments.js`) |
| ✅ 三按鈕並列「新建筆記 / 新建確認表單 / 新建公告」 | Task 8 step 4 |
| ✅ Notes 模組唯一改動 = `index()` + `index.html` | Task 8 step 3-4 |
