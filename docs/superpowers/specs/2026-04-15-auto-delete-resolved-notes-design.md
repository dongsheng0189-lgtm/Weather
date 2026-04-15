# Design: Resolved 筆記 30 天後自動刪除（含 R2 附件）

Date: 2026-04-15
Project: `app_unified/`

## 背景與範圍

公用 `Note`（含 `note_type='note'` 和 `note_type='checklist'`）在 `status` 改為 `"resolved"` 後 30 天自動刪除，同步刪除 Cloudflare R2 上所有關聯附件。

**不處理**：私人筆記（`PrivateNote` / `PrivateChecklistItem`）— 使用者自行管理。

## Schema 變動

`Note` 新增欄位：

```
resolved_at DATETIME NULL
```

Migration 檔案：`app_unified/migrations/versions/NNN_add_note_resolved_at.py`
- Upgrade：`ALTER TABLE notes ADD COLUMN resolved_at DATETIME NULL`
- Downgrade：`ALTER TABLE notes DROP COLUMN resolved_at`
- 既有 `status="resolved"` 的 row 不 backfill（假設數量少；也避免把很舊的筆記立刻刪掉造成驚嚇）。需要 backfill 時使用者明確要求再做。

## 狀態切換邏輯

在所有會改 `Note.status` 的路徑加入以下規則（無副作用可提取為 `_touch_resolved_at(note, new_status)` helper）：

- `new == "resolved"` and `old != "resolved"` → `note.resolved_at = datetime.utcnow()`
- `old == "resolved"` and `new != "resolved"` → `note.resolved_at = None`（**重置**）
- 其他組合：不動

需要改動的路徑（`notes/routes.py`）：
- 一般筆記更新 status 的 route（grep `Note.status` 找到所有賦值處）
- Checklist 類型若有另外切 status 的 endpoint

## 清理模組

新檔 `app_unified/notes/cleanup.py`：

```python
"""Resolved 筆記自動刪除邏輯（含 R2 附件清理）"""
import time
from datetime import datetime, timedelta
from flask import current_app
from extensions import db
from models import Note, NoteAttachment
from storage import delete_attachment

_RETENTION_DAYS = 30
_THROTTLE_SECONDS = 3600  # 每 process 每小時最多跑一次
_last_run: float = 0.0


def cleanup_resolved_notes(force: bool = False) -> int:
    """回傳刪除筆數。外部呼叫應以 try/except 包住，避免例外影響頁面。"""
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

**R2 孤兒政策**：R2 刪失敗 → log + 繼續刪 DB。可能產生孤兒物件，使用者接受此風險。

**Transaction 外部 side effect 風險**：R2 刪在 DB commit 前。若 commit 失敗，R2 已刪、DB 還在 → 下次清理會再嘗試刪 R2（已不存在）、再 commit；`delete_attachment` 對不存在的 key 的行為依賴 boto3 預設（通常 idempotent 回 204）。可接受。

## 觸發點

沿用既有 on-request 清理模式（`_cleanup_pending_devices` 先例）：

| 檔案 | Route | 位置 |
|---|---|---|
| `notes/routes.py` | `index()` 筆記列表 | 函式最前面 try/except |
| `admin/routes.py` | `dashboard()` | 函式最前面 try/except |

呼叫形式：

```python
from notes.cleanup import cleanup_resolved_notes
try:
    cleanup_resolved_notes()
except Exception:
    current_app.logger.exception("cleanup_resolved_notes failed")
```

節流由 `cleanup.py` 內的 `_last_run` 處理，呼叫端不需額外判斷。

## UI 警語

`templates/notes/editor.html`（公用筆記編輯頁）在 status 選單下方：

```html
{% if note.status == 'resolved' %}
  <div style="font-size:.8rem;color:#888;margin-top:.3rem;">
    ⚠️ 此筆記標為已解決，30 天後將自動刪除（含附件）
  </div>
{% endif %}
```

`templates/mynotes/editor.html` 若有公用筆記模式共用該模板則同步；mynotes 私人模式不加。

## 不做的事

- **不記 log**：NoteLog 本身會隨 Note 一起 cascade 刪除，記 UserLog 只是噪音。使用者同意不記。
- **不通知使用者即將被刪**：UI 警語即為提示。
- **不提供「凍結」此筆記的 opt-out**：若需保留，使用者改回 `pending` / `in_progress` 即可（resolved_at 自動清零）。

## 測試

`app_unified/tests/test_cleanup.py`：

1. `test_cleanup_deletes_expired_resolved`：建立 3 張筆記（resolved_at=35 天前 / 10 天前 / None），跑 `cleanup_resolved_notes(force=True)` → 只有第一張被刪，回傳 1。
2. `test_cleanup_removes_r2_attachments`：mock `storage.delete_attachment`，建立過期 resolved note + 2 attachments → cleanup 後 `delete_attachment` 被呼叫 2 次、NoteAttachment 行也消失（FK cascade）。
3. `test_cleanup_ignores_non_resolved`：status=pending 但 resolved_at=35 天前（理論上不該發生，但保險）→ 不刪。
4. `test_cleanup_tolerates_r2_failure`：`delete_attachment` raise → note 仍被刪、不 propagate。
5. `test_cleanup_throttle`：連兩次呼叫 `cleanup_resolved_notes()`（非 force），第二次回 0 且沒動 DB。
6. `test_resolved_at_set_on_transition`：note status pending → resolved，`resolved_at` 被設。
7. `test_resolved_at_cleared_on_unresolve`：note status resolved → pending，`resolved_at` 變 None。

## 交付範圍

- `app_unified/models.py`：`Note` 加 `resolved_at`
- `app_unified/migrations/versions/NNN_add_note_resolved_at.py`（新檔）
- `app_unified/notes/cleanup.py`（新檔）
- `app_unified/notes/routes.py`：
  - status 切換 hook（helper + 呼叫點）
  - `index()` 觸發 cleanup
- `app_unified/admin/routes.py`：`dashboard()` 觸發 cleanup
- `app_unified/templates/notes/editor.html`：UI 警語
- `app_unified/templates/mynotes/editor.html`：UI 警語（若共用模板邏輯）
- `app_unified/tests/test_cleanup.py`（新檔）
