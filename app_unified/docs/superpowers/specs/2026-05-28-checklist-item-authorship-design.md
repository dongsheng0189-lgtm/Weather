# Spec — ChecklistItem Authorship (F11)

**Date**: 2026-05-28
**Scope**: 確認表單（checklist note）的每個項目 (ChecklistItem) 顯示「建立人 + 建立時間」與「最後修改人 + 修改時間」共最多 **2 行** meta。「打勾完成」併入「修改」行、顯示成「修改(完成)」、不再單獨一行。
**Base**: main HEAD `d9e3a1d` (F10 swipe gallery 收尾)

---

## 1. Problem

目前 ChecklistItem 只 track `checked_by` + `checked_at`（勾選人 / 勾選時間）。當 user 想知道「**這個項目原本是誰新增的**」或「**最後是誰改過內容**」，DB 完全沒記錄、UI 也沒顯示。實務上巡店時遇到不對的項目，要追蹤回問誰加的、誰改的、誰最後簽掉的、現在做不到。

目標：每個 item 顯示**最多 2 行** meta — 建立 / 修改 — 條件式 render（資料缺就不顯示對應行）。「打勾完成」被視為一種 modification，併入「修改」行、若最後一次動作就是 check 則顯示成「修改(完成): name · time」。

## 2. Scope

In-scope：

- 加 4 個 column 到 `ChecklistItem`：`created_by` / `created_by_name` / `updated_by` / `updated_by_name`（snapshot pattern 與 Note 一致）
- 任何 modification — POST 新增 item / PATCH 編輯 text / PATCH `is_checked` 勾選或取消 / POST 加附件 / DELETE 移除附件 — 都 set `updated_by` + `updated_by_name`、bump `updated_at`
- POST 新增 item 額外 set `created_by` + `created_by_name`（建立人 snapshot）
- API `_serialize_item` response 多 4 個 fields
- WebSocket `'iu'` (item update) broadcast 自動帶新 fields
- editor.html `renderChecklistItem` 改寫 meta 區、2 行條件式 render；「修改」行若最後一次動作是 check 則顯示 `修改(完成): name · time`
- 舊 row 不 backfill：`created_by_name = NULL` → 建立行不顯示

Out-of-scope：

- Note-level 作者顯示（DB 已存、本次不動 UI、Note header 已有「由 [updated_by] 修改」一行就夠）
- Audit log / 修改歷史（只存「最後一個」修改者、不記過去）
- mynotes 的 PrivateChecklistItem 作者顯示（不同 model、本次不動）

## 3. UX Decisions (User-confirmed)

| 決策 | 選項 | User pick |
|---|---|---|
| Scope | 只 ChecklistItem 層、不動 Note header | ✓ |
| 加附件算修改 | 是、bump updated_by | ✓ |
| **打勾完成也算修改** | 是、bump updated_by；併入「修改」行、顯示 `修改(完成): ...` | ✓ (revised) |
| 修改顯示策略 | 只顯示最後 modifier（覆蓋式、不存歷史） | ✓ |
| 舊項目 fallback | created_by_name NULL → 建立行不顯示；之後若有 modify 才出現修改行 | ✓ |
| Meta 樣式 | 全文字（內容已多 emoji）；`建立: name · time` / `修改{(完成)?}: name · time` | ✓ (revised) |

## 4. Data Model

### 4.1 ChecklistItem 4 個新 column

```python
# app_unified/models.py ChecklistItem
created_by      = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
created_by_name = db.Column(EncryptedText, nullable=True)     # snapshot, user 刪後保留
updated_by      = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
updated_by_name = db.Column(EncryptedText, nullable=True)
```

對齊既有 Note model 的 `author_name` + `updated_by` pattern（line 99 / 110）。Snapshot 是為了 user 帳號被刪後仍可顯示（user 帳號刪除流程已存在、不會 cascade delete items）。

### 4.2 Schema repair

按 [[feedback_inline_schema_repair]] BLOCKING rule — webapp prod 不跑 alembic、必須 3-file 對齊：

1. `app_unified/models.py` — class ChecklistItem 加 4 column
2. `app_unified/app.py` schema repair block 加 4 條 inline ALTER：
   ```sql
   ALTER TABLE checklist_items ADD COLUMN created_by INTEGER REFERENCES users(id) ON DELETE SET NULL;
   ALTER TABLE checklist_items ADD COLUMN created_by_name TEXT;
   ALTER TABLE checklist_items ADD COLUMN updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL;
   ALTER TABLE checklist_items ADD COLUMN updated_by_name TEXT;
   ```
   (使用既有 `IF NOT EXISTS` pattern、idempotent；EncryptedText 在 PG 是 TEXT、加密在 app 層處理)
3. `app_unified/migrations/015_checklist_item_authorship.py` — alembic upgrade/downgrade（給本機 dev SQLite 重建 db 用、prod 不執行）

### 4.3 Mutation 行為

| 動作 | endpoint | created_by | updated_by | updated_at | checked_at |
|---|---|---|---|---|---|
| POST new item | `POST /notes/api/<note_id>/items` | set | set（同 created_by 同人同時） | set (= created_at) | NULL |
| PATCH text | `PATCH /notes/api/items/<id>` | 不動 | set | bump | 不動 |
| POST attachment | `POST /notes/api/attachments` (with checklist_item_id) | 不動 | set | bump | 不動 |
| DELETE attachment | `DELETE /notes/api/attachments/<id>` | 不動 | set | bump | 不動 |
| PATCH is_checked=True | `PATCH /notes/api/items/<id>` | 不動 | **set** | **bump** (`= now`) | **set** (`= now`、與 updated_at 同 value) |
| PATCH is_checked=False (uncheck) | `PATCH /notes/api/items/<id>` | 不動 | set | bump | NULL |
| PATCH text + is_checked 同一 request | 同上 | 不動 | set | bump | set/NULL 依 is_checked |

`updated_by` 一律 `current_user.id`、`updated_by_name = current_user.username`。

**關鍵**：check 動作把 `updated_at` 與 `checked_at` 設為 **同一個 `now` value**（同 transaction 內 `now = datetime.utcnow()` 共用）。UI 偵測「最後動作是 check」靠 `updated_at === checked_at && is_checked` 判斷、用來決定是否加 `(完成)` suffix。

### 4.4 API serializer

`_serialize_item(item)` 共用 helper（`notes/routes.py`）回傳的 dict 多 4 個 fields：

```python
{
  # 既有...
  "created_at": item.created_at.isoformat() if item.created_at else None,
  "created_by_name": item.created_by_name,
  "updated_at": item.updated_at.isoformat() if item.updated_at else None,
  "updated_by_name": item.updated_by_name,
  # 既有 checked_*
}
```

(`created_at` + `updated_at` 既有 column；serializer **必須**包含這 4 個 fields + 2 個 timestamp、即使其中某些之前已 expose 也明確列出避免靠運氣。)

### 4.5 WebSocket

`notes/ws.py` `'iu'` (item update) event payload 從 `_serialize_item()` build → 自動帶新 fields。確認 ws.py 是否真的呼叫 shared serializer，若是手刻 dict 要同步加 4 個 field。

## 5. UI

`templates/notes/editor.html` `renderChecklistItem(item)` 約 line 1445-1451 meta 區改寫（**最多 2 行**）：

```js
var meta = document.createElement('div');
meta.style.cssText = 'font-size:.75rem;color:#999;margin-top:.4rem;line-height:1.5;';
var rows = [];
if (item.created_by_name) {
  rows.push('建立: ' + escapeHtml(item.created_by_name) + ' · ' + fmtDateTime(item.created_at));
}
if (item.updated_by_name && item.updated_at !== item.created_at) {
  // 若最後一次動作是 check (打勾完成)、加 "(完成)" suffix
  var isLastActionCheck = item.is_checked && item.checked_at && item.checked_at === item.updated_at;
  var label = isLastActionCheck ? '修改(完成): ' : '修改: ';
  rows.push(label + escapeHtml(item.updated_by_name) + ' · ' + fmtDateTime(item.updated_at));
}
meta.innerHTML = rows.join('<br>');
content.appendChild(meta);
```

### 5.1 `updated_at !== item.created_at` 條件

POST new item 時 created/updated 同人同時間（簡化 backend 邏輯）、那一刻顯示「建立 + 修改」會 redundant、所以 UI 用 `updated_at !== created_at` 過濾、只有真的有後續修改才顯示「修改」行。

### 5.2 `(完成)` suffix 偵測

當 user 打勾時、backend 在同 transaction 內把 `updated_at` 與 `checked_at` 都設成 `now` 同一個值；UI 用 `item.checked_at === item.updated_at` 字串比對（ISO 格式相同）+ `item.is_checked === true` 判斷「最後一次動作就是這次 check」。其他情境：

- 純文字 edit：`updated_at` bumps、`checked_at` 維持原值（或 NULL）→ 兩者不等 → 顯示「修改: ...」
- 純取消勾選：`updated_at` bumps、`checked_at` = NULL → 兩者不等 → 顯示「修改: ...」
- 已勾後又 edit text：`updated_at` 新 bumped、`checked_at` 維持舊勾選時間 → 兩者不等 → 顯示「修改: ...」（不再帶 `(完成)`、因為最後動作是 text edit）

不另設「修改(取消勾選)」suffix — uncheck 顯示成普通「修改: ...」即可、user 看 item 沒勾就知道是取消狀態。

### 5.3 `escapeHtml` 來源

editor.html 既有 `escapeHtml` 或類似 helper（grep 確認）。若無就用 textContent 拼字串 + `<br>` 用 `appendChild(document.createElement('br'))`，避免 XSS。

### 5.4 同 broadcast re-render path

`notes/ws.py` 廣播 `'iu'` 後、前端 handler 改 cache 後重 render 該 row、meta 區會跟著 2 行更新 — 不另寫程式碼。

### 5.5 既有 `✓ 由 ... 完成` 一行的廢除

T2/T3 task 2 個 callsite 都改用上面的 2-row 邏輯；既有 `if (item.is_checked && item.checked_by_name)` 那行整段刪除。

## 6. Tests

| Test 檔 | 內容 |
|---|---|
| `tests/test_checklist_item_authorship.py` | (1) POST item → 4 fields 都 = current user；(2) PATCH text → updated_by 變、created_by 不變；(3) POST attachment for item → updated_by 變；(4) DELETE attachment → updated_by 變；(5) PATCH is_checked=True → updated_by + checked_by 都變、`updated_at === checked_at`（同 now value）；(6) PATCH is_checked=False → updated_by 變、checked_at = NULL；(7) PATCH text + is_checked 同 request → updated_by 變、updated_at = checked_at 同 now value |
| `tests/test_checklist_item_serializer.py` | `_serialize_item` 回傳 dict 含 4 個 新 fields + `created_at`、`updated_at` (timestamp ISO format) |
| `tests/test_notes_editor_meta_smoke.py` | editor.html JS source 含 `'建立: '`、`'修改: '`、`'updated_at !== item.created_at'` 條件、`rows.join('<br>')`、`escapeHtml(item.created_by_name)` |
| `tests/test_checklist_item_schema_alter.py` | app.py schema repair block 含 4 條新 ALTER (`ADD COLUMN created_by`、`ADD COLUMN created_by_name`、`ADD COLUMN updated_by`、`ADD COLUMN updated_by_name` for `checklist_items` table) — 守住未來不漏改 |
| `tests/test_notes_editor_meta_smoke.py` | editor.html 含 2 行 meta 條件 (`'建立: '` / `'修改: '` / `'修改(完成): '`)、`updated_at !== created_at` 判定、`checked_at === updated_at && is_checked` 判定 `(完成)` suffix、`rows.join('<br>')` |

不寫：
- jsdom integration test（webapp 沒設施、靠 manual verify）
- 既有 prod data backfill 測試（user 選 no backfill）

## 7. Rollout

- Branch `feat/checklist-item-authorship`
- Inline ALTER on prod 第一次 schema repair 時自動跑、SQLite/PG 都支援
- 不 bump SW v37：editor.html 是 navigation request（network-first per sw.js）、reload 即可拿新版
- 按 [[feedback_local_test_before_push]] + [[feedback_push_policy]]：本機 `bash app_unified/scripts/run_local.sh` → user 開 `/n/1` checklist 開新項 / 編輯 / 加附件 / 勾選 驗 4 個情境 → ff-merge main + push（user 明說 push 才推）
- 不在 [[feedback_push_peak_hours]] 19:30-21:00 禁區才能 push

## 8. Risk / Open

| Risk | Mitigation |
|---|---|
| Inline ALTER prod 第一次跑卡 PG | 4 個 column 都 nullable + ondelete SET NULL、安全、PG ALTER ADD COLUMN nullable 是 fast operation |
| `updated_at` 既有意義是「排序用、新增/編輯/附件變更時 bump」(per model docstring) — 加 attachment 算 updated 跟既有語意一致、不破 sort | OK |
| `_serialize_item` 多 4 fields → response 大小 +~200 bytes/item | 可忽略；checklist 通常 < 50 items / response < 10 KB net 增加 |
| 既有舊 item 沒 created_by → 第一次 modify 後出現「修改」行但沒「建立」行 | by design、user 已接受、UI 看起來像「歷史不明、最近 X 改過」也合理 |
| `created_at` / `updated_at` ISO format 在 UI fmtDateTime() 顯示 — 確認 fmtDateTime 處理 timezone (TW 時間)（按 [[feedback_timezone_taiwan]]）| 既有 fmtDateTime 已存在 + 處理 TW timezone、grep 確認 |
| Multi-worker WebSocket broadcast：`'iu'` event 已走 Redis pub/sub (F2)、新 fields 自動跨 worker 同步 | OK |

## 9. Not in this spec

- mynotes PrivateChecklistItem 同類功能（另案、私人 scope decision）
- Audit log（修改歷史 list 而非只存 last modifier）
- UI 上點作者名字看該人的所有筆記（YAGNI）
- 顯示「建立」「修改」次數 stats（YAGNI）
- Note header 加「建立人」顯示（DB 已有資料、本次不動 UI、Note level 不在 user scope 內）
