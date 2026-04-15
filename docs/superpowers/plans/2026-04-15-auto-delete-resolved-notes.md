# Auto-Delete Resolved Notes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 公用 `Note` 改 `status="resolved"` 起 30 天後自動刪除，同步刪 Cloudflare R2 上所有 NoteAttachment；沿用既有 on-request cleanup pattern。

**Architecture:**
- `Note` 加 `resolved_at` 欄位 + migration 005
- `notes/routes.py` create/update 筆記時維護 `resolved_at`（進 resolved 記時、離開 resolved 清零）
- 新檔 `notes/cleanup.py` 提供 `cleanup_resolved_notes(force=False)`，節流每 process 每小時 1 次
- `notes/routes.py::index()` + `admin/routes.py::dashboard()` try/except 呼叫 cleanup
- `templates/notes/editor.html` 加 resolved 狀態的小字警語

**Tech Stack:** Flask, SQLAlchemy, psycopg2（migration）, boto3（R2）, pytest。

**Spec:** `docs/superpowers/specs/2026-04-15-auto-delete-resolved-notes-design.md`

**Working directory:** `/home/hirain0126/projects/webapp/app_unified`（repo root: `/home/hirain0126/projects/webapp`；venv: `../venv/bin/python`）

---

## 檔案概覽

- Create: `app_unified/migrations/versions/005_add_note_resolved_at.py`
- Modify: `app_unified/models.py`（Note 加 resolved_at）
- Create: `app_unified/notes/cleanup.py`
- Modify: `app_unified/notes/routes.py`（維護 resolved_at + 觸發 cleanup）
- Modify: `app_unified/admin/routes.py`（dashboard 觸發 cleanup）
- Modify: `app_unified/templates/notes/editor.html`（警語）
- Create: `app_unified/tests/test_cleanup.py`

---

## Task 1：Schema — Note 加 resolved_at + migration

**Files:**
- Modify: `app_unified/models.py`
- Create: `app_unified/migrations/versions/005_add_note_resolved_at.py`

- [ ] **Step 1: 加欄位到 models.py**

找到 `Note` class（models.py line 84 起）。在 `updated_at` 欄位後方加 `resolved_at`：

```python
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
```

- [ ] **Step 2: 新增 migration 005**

建 `app_unified/migrations/versions/005_add_note_resolved_at.py`，內容：

```python
"""Add Note.resolved_at for auto-delete after 30 days

Revision ID: 005
Revises: 004
Create Date: 2026-04-15
"""
revision = '005'
down_revision = '004'

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def upgrade():
    url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ NULL")

    cur.close()
    conn.close()
    print("Migration 005 done.")


if __name__ == "__main__":
    upgrade()
```

- [ ] **Step 3: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/models.py app_unified/migrations/versions/005_add_note_resolved_at.py
git commit -m "feat(db): add Note.resolved_at + migration 005

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2：cleanup 模組 — 寫失敗測試

**Files:**
- Create: `app_unified/tests/test_cleanup.py`

- [ ] **Step 1: 建測試檔**

建 `app_unified/tests/test_cleanup.py`：

```python
"""Tests for notes.cleanup.cleanup_resolved_notes and resolved_at lifecycle."""
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest
from extensions import db
from models import Note, NoteAttachment, User


def _mk_note(db_session, status="resolved", resolved_at=None, user_id=None):
    n = Note(
        user_id=user_id,
        author_name="system",
        note_type="note",
        title="t",
        content="c",
        store=None,
        status=status,
        priority="medium",
    )
    n.resolved_at = resolved_at
    db_session.add(n)
    db_session.commit()
    return n


def _mk_attachment(db_session, note, object_key):
    a = NoteAttachment(
        note_id=note.id,
        object_key=object_key,
        filename="f.jpg",
        content_type="image/jpeg",
        file_size=10,
    )
    db_session.add(a)
    db_session.commit()
    return a


def _reset_throttle():
    from notes import cleanup
    cleanup._last_run = 0.0


def test_cleanup_deletes_expired_resolved(app, db_session):
    _reset_throttle()
    expired = _mk_note(db_session, resolved_at=datetime.utcnow() - timedelta(days=35))
    recent = _mk_note(db_session, resolved_at=datetime.utcnow() - timedelta(days=10))
    no_ts = _mk_note(db_session, resolved_at=None)

    from notes.cleanup import cleanup_resolved_notes
    with patch("notes.cleanup.delete_attachment"):
        n = cleanup_resolved_notes(force=True)

    assert n == 1
    db_session.expire_all()
    assert Note.query.get(expired.id) is None
    assert Note.query.get(recent.id) is not None
    assert Note.query.get(no_ts.id) is not None


def test_cleanup_removes_r2_attachments(app, db_session):
    _reset_throttle()
    note = _mk_note(db_session, resolved_at=datetime.utcnow() - timedelta(days=35))
    _mk_attachment(db_session, note, "attachments/X/a.jpg")
    _mk_attachment(db_session, note, "attachments/X/b.jpg")

    from notes.cleanup import cleanup_resolved_notes
    with patch("notes.cleanup.delete_attachment") as m:
        cleanup_resolved_notes(force=True)

    keys = {call.args[0] for call in m.call_args_list}
    assert keys == {"attachments/X/a.jpg", "attachments/X/b.jpg"}
    db_session.expire_all()
    assert NoteAttachment.query.filter_by(note_id=note.id).count() == 0


def test_cleanup_ignores_non_resolved(app, db_session):
    _reset_throttle()
    n = _mk_note(db_session, status="pending", resolved_at=datetime.utcnow() - timedelta(days=35))
    from notes.cleanup import cleanup_resolved_notes
    with patch("notes.cleanup.delete_attachment"):
        assert cleanup_resolved_notes(force=True) == 0
    db_session.expire_all()
    assert Note.query.get(n.id) is not None


def test_cleanup_tolerates_r2_failure(app, db_session):
    _reset_throttle()
    note = _mk_note(db_session, resolved_at=datetime.utcnow() - timedelta(days=35))
    _mk_attachment(db_session, note, "attachments/Y/a.jpg")

    from notes.cleanup import cleanup_resolved_notes
    with patch("notes.cleanup.delete_attachment", side_effect=RuntimeError("boom")):
        n = cleanup_resolved_notes(force=True)

    assert n == 1
    db_session.expire_all()
    assert Note.query.get(note.id) is None


def test_cleanup_throttle(app, db_session):
    _reset_throttle()
    _mk_note(db_session, resolved_at=datetime.utcnow() - timedelta(days=35))
    from notes.cleanup import cleanup_resolved_notes
    with patch("notes.cleanup.delete_attachment"):
        first = cleanup_resolved_notes()
        second = cleanup_resolved_notes()
    assert first >= 0
    assert second == 0


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_resolved_at_set_on_transition(app, client, db_session):
    u = User(username=f"u_{uuid.uuid4().hex[:6]}", role="super_admin", store=None)
    u.set_password("0000")
    db_session.add(u); db_session.commit()
    _login(client, u)

    r = client.post("/notes/api", json={"title": "t", "content": "c", "status": "pending"})
    assert r.status_code == 201
    note_id = r.get_json()["id"]

    db_session.expire_all()
    assert Note.query.get(note_id).resolved_at is None

    r = client.put(f"/notes/api/{note_id}", json={"status": "resolved"})
    assert r.status_code == 200
    db_session.expire_all()
    ra = Note.query.get(note_id).resolved_at
    assert ra is not None
    assert abs((datetime.utcnow() - ra).total_seconds()) < 10


def test_resolved_at_cleared_on_unresolve(app, client, db_session):
    u = User(username=f"u_{uuid.uuid4().hex[:6]}", role="super_admin", store=None)
    u.set_password("0000")
    db_session.add(u); db_session.commit()
    _login(client, u)

    r = client.post("/notes/api", json={"title": "t", "content": "c", "status": "resolved"})
    note_id = r.get_json()["id"]
    db_session.expire_all()
    assert Note.query.get(note_id).resolved_at is not None

    r = client.put(f"/notes/api/{note_id}", json={"status": "pending"})
    assert r.status_code == 200
    db_session.expire_all()
    assert Note.query.get(note_id).resolved_at is None
```

- [ ] **Step 2: 跑測試，確認全部 FAIL**

```
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/python -m pytest tests/test_cleanup.py -v 2>&1 | tail -20
```

Expected: 全部失敗（`ModuleNotFoundError: notes.cleanup` 或相關 import/attribute 錯誤；另外 2 個 integration tests 會因 resolved_at 還沒設邏輯而失敗）。

- [ ] **Step 3: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/tests/test_cleanup.py
git commit -m "test: auto-delete resolved notes + resolved_at lifecycle (failing)

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3：實作 notes/cleanup.py

**Files:**
- Create: `app_unified/notes/cleanup.py`

- [ ] **Step 1: 建 cleanup.py**

建 `app_unified/notes/cleanup.py`：

```python
"""Resolved 筆記 30 天後自動刪除（含 R2 附件清理）。

呼叫：外部呼叫應以 try/except 包住，避免例外影響頁面顯示。
節流：每個 process 每 _THROTTLE_SECONDS 最多跑一次（除非 force=True）。
"""
import time
from datetime import datetime, timedelta

from flask import current_app

from extensions import db
from models import Note, NoteAttachment
from storage import delete_attachment

_RETENTION_DAYS = 30
_THROTTLE_SECONDS = 3600
_last_run: float = 0.0


def cleanup_resolved_notes(force: bool = False) -> int:
    """回傳刪除筆數。"""
    global _last_run
    now = time.time()
    if not force and now - _last_run < _THROTTLE_SECONDS:
        return 0
    _last_run = now

    cutoff = datetime.utcnow() - timedelta(days=_RETENTION_DAYS)
    notes = Note.query.filter(
        Note.status == "resolved",
        Note.resolved_at.isnot(None),
        Note.resolved_at <= cutoff,
    ).all()

    deleted = 0
    for n in notes:
        for att in NoteAttachment.query.filter_by(note_id=n.id).all():
            try:
                delete_attachment(att.object_key)
            except Exception:
                current_app.logger.exception(
                    "R2 delete failed (orphan tolerated): note_id=%s key=%s",
                    n.id, att.object_key,
                )
        db.session.delete(n)
        deleted += 1

    if deleted:
        db.session.commit()
    return deleted
```

- [ ] **Step 2: 跑 cleanup 相關測試（不含 integration）**

```
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/python -m pytest tests/test_cleanup.py -v -k "not transition and not unresolve" 2>&1 | tail -15
```

Expected: 5 passed（`test_cleanup_deletes_expired_resolved`, `test_cleanup_removes_r2_attachments`, `test_cleanup_ignores_non_resolved`, `test_cleanup_tolerates_r2_failure`, `test_cleanup_throttle`）。

- [ ] **Step 3: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/cleanup.py
git commit -m "feat(notes): cleanup_resolved_notes — auto-delete resolved notes + R2 attachments

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4：notes/routes.py — 維護 resolved_at + 觸發 cleanup

**Files:**
- Modify: `app_unified/notes/routes.py`

- [ ] **Step 1: 在 create_note 中設定 resolved_at**

找到 `create_note` 中建立 `Note(...)` 的區塊（line 256-267）。在 `db.session.add(note)` 前（line 268 之前）加入：

```python
    if status == "resolved":
        note.resolved_at = datetime.utcnow()
```

（`status` 已於 line 253 計算出。）

- [ ] **Step 2: 在 update_note 中更新 resolved_at**

找到 line 322-325：

```python
    if "status" in data and data["status"] in STATUS_CHOICES:
        if data["status"] != note.status:
            diff_parts.append(f"狀態: {note.status} → {data['status']}")
        note.status = data["status"]
```

改成：

```python
    if "status" in data and data["status"] in STATUS_CHOICES:
        new_status = data["status"]
        if new_status != note.status:
            diff_parts.append(f"狀態: {note.status} → {new_status}")
            if new_status == "resolved" and note.status != "resolved":
                note.resolved_at = datetime.utcnow()
            elif note.status == "resolved" and new_status != "resolved":
                note.resolved_at = None
        note.status = new_status
```

- [ ] **Step 3: index() 最前方觸發 cleanup**

找到 `notes/routes.py` 的 `index()` 函式（`@notes_bp.route("/")`，或相似路徑 — grep `def index`）。在函式進入後第一行加入：

```python
    try:
        from notes.cleanup import cleanup_resolved_notes
        cleanup_resolved_notes()
    except Exception:
        current_app.logger.exception("cleanup_resolved_notes failed")
```

（先用 Read 確認 index 函式位置；若 index 第一行有 `@login_required` 外的既有邏輯（如 require_admin），把這塊放在那之後。）

- [ ] **Step 4: 跑全部 test_cleanup**

```
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/python -m pytest tests/test_cleanup.py -v 2>&1 | tail -15
```

Expected: 7 passed。

- [ ] **Step 5: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/routes.py
git commit -m "feat(notes): maintain resolved_at on status transitions + trigger cleanup

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5：admin dashboard 觸發 cleanup

**Files:**
- Modify: `app_unified/admin/routes.py`

- [ ] **Step 1: 在 dashboard() 加 cleanup 呼叫**

找到 `app_unified/admin/routes.py` 的 `@admin_bp.route("/dashboard")` 函式（約 line 124）。在 `require_admin()` 之後加入：

```python
    try:
        from notes.cleanup import cleanup_resolved_notes
        cleanup_resolved_notes()
    except Exception:
        current_app.logger.exception("cleanup_resolved_notes failed")
```

- [ ] **Step 2: 跑相關測試**

```
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/python -m pytest tests/test_cleanup.py tests/test_admin_rename.py -v 2>&1 | tail -15
```

Expected: 全綠（cleanup 7 + rename 8 = 15 passed）。

- [ ] **Step 3: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/admin/routes.py
git commit -m "feat(admin): trigger cleanup_resolved_notes on dashboard load

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6：UI 警語

**Files:**
- Modify: `app_unified/templates/notes/editor.html`

- [ ] **Step 1: 找到 status 選單位置**

```
grep -n "status" app_unified/templates/notes/editor.html | head -15
```

找到使用者看得到的 status selector（`<select name="status">` 或類似結構）。

- [ ] **Step 2: 在該 selector 的 container 底下加警語**

在 selector container 的關閉 tag 之前（或之後，取決於排版）加入：

```html
<div id="resolved-warning" style="font-size:.8rem;color:#888;margin-top:.3rem;display:none;">
  ⚠️ 此筆記標為已解決，30 天後將自動刪除（含附件）
</div>
```

然後找到 selector 對應的 JS（grep `status` 在 script 區段）或載入時初始化邏輯，加入：

```javascript
function updateResolvedWarning() {
  const sel = document.querySelector('[name="status"], #status, .status-select');
  const warn = document.getElementById('resolved-warning');
  if (!sel || !warn) return;
  warn.style.display = (sel.value === 'resolved') ? 'block' : 'none';
}
document.addEventListener('DOMContentLoaded', updateResolvedWarning);
document.addEventListener('change', (e) => {
  if (e.target && (e.target.name === 'status' || e.target.classList.contains('status-select'))) {
    updateResolvedWarning();
  }
});
```

（selector 定位可能需依實際 DOM 結構微調。若找不到乾淨的 hook，改用 `onchange` inline 綁定即可。）

- [ ] **Step 3: 驗證模板 parse**

```
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/python -c "from jinja2 import Environment, FileSystemLoader; Environment(loader=FileSystemLoader('templates')).get_template('notes/editor.html')" 2>&1 | head -5
```

Expected: 無輸出（模板 parse OK；若有 `fmt_date` 之類既有 filter 錯誤，忽略）。

- [ ] **Step 4: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/notes/editor.html
git commit -m "feat(ui): warn user that resolved notes auto-delete after 30 days

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7：Migration 執行 + 最終驗證

- [ ] **Step 1: 本地 / Zeabur 執行 migration 005**

**本地**（若有本地 DB）：

```
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/python -m migrations.versions.005_add_note_resolved_at
```

**Zeabur**：push 後在 Zeabur console 手動跑（或靠既有 release 流程自動跑；本 app 目前沒自動 migration runner，建議 push 後在使用者確認時手動執行）。

> 這一步交給使用者決定執行時機（未 push 前先不動 production DB）。

- [ ] **Step 2: 跑全測試**

```
cd /home/hirain0126/projects/webapp/app_unified && ../venv/bin/python -m pytest tests/ -q 2>&1 | tail -15
```

Expected: 新增測試全綠；既有測試數量與本 PR 之前相同（若 rename test / cleanup test 以外有其他 failure 是既有問題，不在本 plan 範圍）。

- [ ] **Step 3: 人工 smoke review**

本機（若可跑 server）或純閱讀 diff 檢查：
- `notes/editor.html` 選 resolved → 看到警語
- 管理後台 dashboard 開啟不炸

回報清單：migration 執行情況 + 測試結果 + 有無破版。

---

## Self-Review

- [x] Spec coverage：
  - Schema (`resolved_at` + migration) → Task 1
  - cleanup 模組（含 R2 失敗容忍、節流） → Task 3 + Task 2 測試
  - status 切換維護 resolved_at → Task 4
  - on-request trigger（notes index + admin dashboard） → Task 4 + Task 5
  - UI 警語 → Task 6
  - 測試 1-7（spec 列表） → Task 2 對應
- [x] Placeholder scan：各 step 都有具體 code / 指令。Task 6 的 DOM selector 定位需實作者依實際模板微調（在 step 中已明示），不是 placeholder 而是合理彈性。
- [x] Type consistency：`cleanup_resolved_notes(force=False)`、`_last_run`、`_THROTTLE_SECONDS`、`_RETENTION_DAYS` 全檔一致；`resolved_at` 名稱全篇一致。

已修正：Task 6 commit 指令的 typo cd path 已在 step 下加註說明。
