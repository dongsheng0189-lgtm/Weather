# Notes / Checklist Item Soft Delete + 24h Restore Window — Spec

**Branch**: `feat/notes-soft-delete`（chain on `feat/checklist-item-lock`）
**Date**: 2026-05-25
**Scope**: Note / ChecklistItem 刪除改成 soft delete、24h 內 admin 可從 `/admin/logs` 新 tab 救回、超過 24h cleanup job 真刪 + R2 一併。

## 問題

既有 DELETE flow（`notes/routes.py:708` `delete_note` + `notes/routes.py:1544` `delete_checklist_item`）直接 `db.session.delete` + R2 delete。誤刪後**永遠找不回來**。100+ item checklist 操作頻繁、誤刪 item 機率高。

## Design

### 1. Schema 變動

`models.py`:

**Note**（加在 `resolved_at` 後）：
```python
deleted_at = db.Column(db.DateTime, nullable=True, index=True)
deleted_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
deleter = db.relationship("User", foreign_keys=[deleted_by])
```

**ChecklistItem**（加在 `locked_at` 後）：
```python
deleted_at = db.Column(db.DateTime, nullable=True, index=True)
deleted_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
deleter = db.relationship("User", foreign_keys=[deleted_by])
```

**Migration 013**：`migrations/versions/013_soft_delete_notes_items.py`
- ALTER TABLE notes ADD deleted_at + deleted_by + FK
- ALTER TABLE checklist_items ADD deleted_at + deleted_by + FK
- CREATE INDEX on deleted_at（兩個 table）

### 2. Helpers (`notes/routes.py` 加在既有 lock helpers 後)

```python
def soft_delete_note(note, user_id):
    """軟刪除 note。子 item 不個別 mark（query 過濾 Note.deleted_at IS NULL 已隔離）。"""
    note.deleted_at = datetime.utcnow()
    note.deleted_by = user_id
    # 連帶清 lock 避免殘留
    note.locked_by = None
    note.locked_at = None


def soft_delete_item(item, user_id):
    item.deleted_at = datetime.utcnow()
    item.deleted_by = user_id
    item.locked_by = None
    item.locked_at = None


def restore_note(note):
    note.deleted_at = None
    note.deleted_by = None


def restore_item(item):
    item.deleted_at = None
    item.deleted_by = None
```

### 3. DELETE endpoints 改 soft delete

**`notes/routes.py:708` `delete_note`**：

把原本的 `db.session.delete(note)` 段（含 R2 cleanup）替換成：
```python
soft_delete_note(note, current_user.id)
log = NoteLog(...)  # 既有 log 行為保留
db.session.add(log)
db.session.commit()
# R2 不在這刪、24h cleanup 才刪
```

**`notes/routes.py:1544` `delete_checklist_item`**（F2 已改成 item-lock check、現在再加 soft delete）：

把原本的 `db.session.delete(item)` + R2 cleanup 替換成：
```python
soft_delete_item(item, current_user.id)
# F2 broadcast 改成 'soft-deleted' 仍 broadcast 'il' deleted=True、client DOM 移除
_broadcast_item_lock(item, locked_by_user=None, deleted=True)
db.session.commit()
# R2 不在這刪
```

### 4. Query filter audit — 所有 Note.query / ChecklistItem.query 加 deleted_at IS NULL

策略：每處 `.query` audit、加 `.filter(<Model>.deleted_at.is_(None))`。**Sentinel 模式 vs SQLAlchemy default scope**：

選 **explicit filter** 而非 default scope，理由：
- SQLAlchemy 無 native default scope
- Explicit 更容易 audit（grep 確認每處都有加）
- 復原查詢時想 bypass 也明確（`Note.query.filter(Note.id == x)` 不加 filter）

**~25 處 Note.query + ~5 處 ChecklistItem.query** 全部要加：
- `notes/routes.py:327` `Note.query`（list view）
- `notes/routes.py:455` 同
- `notes/routes.py:541-545` `Note.query.get_or_404` / `filter_by`（edit page、需思考）
- ...（全 audit、見 plan）

**特殊情境**：
- `_get_note_for_edit` 內查詢：加 deleted_at filter（已刪 note 不應能 edit）
- 統計類查詢（如 `notes_for_count`）：加 filter
- `_checklist_item_json` 內 `item.attachments`：不需加 filter（attachment 沒 soft delete）
- Admin restore API 內查詢：**bypass** filter（用 `Note.query.get` 直接拿 deleted note）

### 5. ChecklistItem 連帶處理

**設計**：Note soft deleted 不個別 mark item、查詢時靠 Note.deleted_at IS NULL 隔離。

實作：
- `_get_note_for_edit(note_id)`：加 deleted_at filter → 返回 None for deleted note → list_checklist_items 自動 403
- `list_checklist_items` 直接 query `ChecklistItem.query.filter_by(note_id=note_id, ChecklistItem.deleted_at.is_(None))` 但前面 `_get_note_for_edit` 已擋
- Note restore → 子 item 自動可見（因為個別 item deleted_at 沒設、Note 也已恢復）

### 6. Cleanup (擴 `notes/cleanup.py`)

**Module-level state**（加在 cleanup.py 頂）：
```python
_last_soft_delete_run: float = 0.0  # 跟既有 _last_run 平行
```

加新 function `cleanup_soft_deleted(force=False)`：

```python
def cleanup_soft_deleted(force: bool = False) -> tuple:
    """24h 後 hard delete soft-deleted notes + items + R2。回傳 (notes_deleted, items_deleted)。"""
    global _last_soft_delete_run
    now = time.time()
    if not force and now - _last_soft_delete_run < _THROTTLE_SECONDS:
        return 0, 0
    _last_soft_delete_run = now

    cutoff = datetime.utcnow() - timedelta(hours=24)

    # 1. 過期 deleted notes → R2 delete 所有 attachments (含 item attachments，
    #    都在 NoteAttachment table、用 checklist_item_id 區分) + db delete cascade
    notes = Note.query.filter(
        Note.deleted_at.isnot(None),
        Note.deleted_at <= cutoff,
    ).all()
    notes_deleted = 0
    for n in notes:
        # 所有 attachment（note-level + 子 item 的 checklist_item_id 都關 n.id）
        for att in NoteAttachment.query.filter_by(note_id=n.id).all():
            try:
                delete_attachment(att.object_key)
            except Exception:
                pass
        db.session.delete(n)  # FK ondelete CASCADE delete items + attachments
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

**Attachment model 注意**：webapp 只有 `NoteAttachment` 一個 table、用 `note_id` (必填) + `checklist_item_id` (nullable、屬於某 item 時設) 區分 note-level vs item-level 附件。沒有獨立 `ChecklistItemAttachment` model。

Page request hook 同時呼叫兩個 cleanup（既有 `cleanup_resolved_notes` + 新 `cleanup_soft_deleted`）。

### 7. Admin UI: `/admin/logs` 加「已刪除 (24h 內)」tab

**Server-side**:
- 既有 admin UI 集中在 `templates/admin/dashboard.html`（無 logs.html 獨立檔）、logs section 是 dashboard 內既有區塊（grep `/admin/logs` fetch in dashboard.html line 459）
- 在 dashboard.html logs section 加新 tab 切換
- 新 API endpoint `GET /admin/api/deleted` 回 JSON:
  ```json
  {
    "notes": [
      {"id": 17, "title": "...", "store": "B", "deleted_at": "2026-05-25 16:30",
       "deleted_by_name": "alice", "hours_remaining": 23.4}
    ],
    "items": [
      {"id": 42, "text": "...", "note_id": 17, "note_title": "...",
       "deleted_at": "...", "deleted_by_name": "bob", "hours_remaining": 18.2}
    ]
  }
  ```
- 新 API: `POST /admin/api/restore/note/<id>` + `POST /admin/api/restore/item/<id>`
  - 200 OK → `restore_note(note)` / `restore_item(item)` + commit + log
  - 404 if not found (deleted_at IS NULL → 已被 cleanup 或本來就不在)
  - 403 if not admin / not store-scoped

**權限**：
- admin: 看本店 deleted、可復原本店 deleted
- super_admin: 看全店 deleted、可復原任何 deleted
- user: 完全看不到 tab、API 403

**Frontend**:
- 新 tab button 旁加 badge 顯示「N 筆可救」
- 每行：標題 + 類型 (note/item) + 店別 + 「N 小時前由 XXX 刪除」+「復原」button
- Click 復原 → confirm dialog「確定復原這筆？」→ POST API → 成功 → 從 list 移除 + toast「已復原」

### 8. UserLog 紀錄

restore action 寫 UserLog（既有 admin 動作 audit 模式）：

```python
log_user_action('restore_note', target=None, detail=f'note_id={note.id}, title={note.title}')
log_user_action('restore_item', target=None, detail=f'item_id={item.id}, note_id={item.note_id}')
```

soft delete 動作沿用既有 NoteLog `action='delete'`（不重複加 UserLog）。

## 邊界情境

| 情境 | 處理 |
|---|---|
| User 刪自己的 note | soft delete、24h 內可救 |
| Admin 刪別人的 note | soft delete、24h 內可救（不限 user 的） |
| Note soft deleted + 5min 後再 list | `/notes/?store=X` 不再列出（query 加 deleted_at IS NULL）|
| Note soft deleted + 子 item 查詢 | `_get_note_for_edit` 返回 None → 404 |
| Note soft deleted + 24h 後 cleanup | R2 attachment + item attachment + cascade DB delete |
| Item soft deleted、note 沒 deleted | list 不顯示該 item、別人新增/編其他 item 不受影響 |
| Item soft deleted + 復原 | item 重新出現在 list、order_index 不變、is_checked 狀態保留 |
| Restore 已過 24h 的 note | 404（已被 cleanup） |
| Soft deleted note 內 item attachment | R2 物件保留 24h、cleanup 才真 delete |
| Note soft deleted 又 restore + 再 soft delete | `deleted_at` 重設、24h 重新計時 |
| Concurrent restore + cleanup | restore 先拿到 deleted_at IS NOT NULL row、cleanup 後拿不到、cleanup 不誤刪（race 安全）|
| Admin restore 跨店嘗試 | 403、admin 只能本店 |
| super_admin restore 跨店 | 200、跨店允許 |
| 100 item checklist、user 刪 5 item | 5 個獨立 soft delete row、admin 復原 UI 看到 5 條（按 item 列）|
| 整 note 刪除後、admin 復原 → 子 item 全部回來嗎？| 是（item 個別 deleted_at 沒設、Note restore 後 query 自動可見） |

## Capacity

- Schema: 4 new columns × 2 tables = 每 row +24 bytes（INT + TIMESTAMP）
- Prod estimate: < 10K notes + < 100K items = 加 ~2.4 MB total、無 PG 壓力
- Index on deleted_at: 加快 cleanup query、單 index 開銷可忽略
- Cleanup throttle 1hr、每 request 跑一次 O(1) check、不影響 latency
- 對 prod 2 CPU / 8GB RAM: 0 顯著影響

## 測試策略

### Backend pytest (`tests/test_soft_delete.py`)

預估 15 條：

1. `soft_delete_note` set deleted_at + deleted_by
2. `soft_delete_item` set deleted_at + deleted_by
3. DELETE /api/<note_id> → note 仍在 DB、deleted_at IS NOT NULL
4. DELETE /api/items/<id> → 同
5. Soft deleted note 不出現在 /notes/ list
6. Soft deleted item 不出現在 /api/<note_id>/items
7. `_get_note_for_edit(deleted_note_id)` 返回 None
8. R2 attachment 在 soft delete 時**不**被 delete (mock R2 client 驗 0 calls)
9. `cleanup_soft_deleted(force=True)` 過 24h boundary 的 note: R2 delete + DB delete
10. `cleanup_soft_deleted` 未過 24h 不 delete
11. Restore note → deleted_at None、list 再出現
12. Restore item → 同
13. Admin permission: admin 看本店 deleted、跨店 403
14. super_admin 跨店 OK
15. Restore 過 24h note 已被 cleanup → 404
16. （bonus）UserLog action='restore_note' 寫入

### Markup smoke test (`tests/test_admin_logs_deleted_tab.py`)

- `/admin/logs` page 含「已刪除」tab markup
- 含 restore button class

### Manual verification（user）

按 [[feedback_local_test_before_push]]：
1. 建 2 個 note + 1 個 checklist with 5 item
2. 刪 1 個 note + 2 個 item
3. /notes/ 確認 deleted note 不顯示、checklist 內 deleted item 不顯示
4. 進 /admin/logs → 「已刪除」tab → 列出 3 條（1 note + 2 item）+ 復原 button + 顯示「剛剛由 X 刪除、剩 24 小時」
5. 復原 1 個 item → 該 item 重新出現在 checklist、tab 內消失
6. 復原 deleted note → note 回到 list
7. 模擬 25 小時前刪除（DevTools console 改 cache or DB 直接改）→ cleanup 跑 → DB row 真刪 + R2 attachment 也刪

## YAGNI 排除

- 不做「永久刪除」按鈕（超 24h 自動 cleanup）
- 不做 deleted note 復原時 reorder（保留原 order_index）
- 不做 trash bin global view（log tab 足夠）
- 不顯示「永久刪除倒數」秒級即時更新（每次 refresh 重新計算 hours_remaining）
- 不做 bulk restore（一次一個 row）
- 不做 deleted item 修改（只能 restore 或等 cleanup）

## 部署

1. GHA Docker build
2. Zeabur Redeploy（alembic migration 013 自動跑、加 4 columns + 2 indexes）
3. 既有 row 自動 NULL（filter `IS NULL` 等同 active）
4. 部署後新 DELETE 行為自動生效、既有 notes 不受影響

## Chain 策略

F1 是 chain 上最後一條（feat/notes-soft-delete from feat/checklist-item-lock）。F1 done 後 user 確認 → ff-merge main + 一次 push、Zeabur Redeploy 6 條 feature 一起上 prod。
