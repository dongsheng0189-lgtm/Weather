# 店務提醒日期（reminder_date）+ 稽核 — 設計

> 日期：2026-06-15 ｜ 模組：`notes`（筆記/確認表單）｜ branch：`feat/notes-reminder-due-date`

## 1. 目標 / 範圍

把 `mynotes` 既有的「提醒日期」機制**複製**進 `notes`（筆記/確認表單）模組，改成**全店共用**的到期/今日提醒，並加上**稽核**。

- `mynotes` **完全不動**（個人提醒原樣保留，這是「複製」不是「搬走」）。
- **不做**：月曆/月格視圖、分級提醒（明天/3 天內）、SocketIO 即時同步。（需求 = 純搬移 + log）
- **要做**：到期日欄位 + inline 顯示「最後誰改的」+ `NoteLog` 完整歷史 + 全店「今日提醒」折疊面板。

## 2. 決策摘要（brainstorming 拍板）

| # | 決定 |
|---|---|
| 提醒歸屬 | **全店共用**（per-store 團隊到期/提醒，不是個人的） |
| 強化深度 | 純搬移 + 稽核（不加月曆/分級/即時同步） |
| 稽核形式 | **inline 最後修改者快照 + `NoteLog` 完整歷史**（兩者都要） |
| 搬移方式 | **複製**到 notes，mynotes 保留不動 |
| 套用對象 | **note 級 + 項目級都要**（純文字筆記掛整張到期日；確認表單每項各自到期日） |
| 已完成 | **已勾選項目 / 已 resolved 筆記自動排除**出提醒 |
| 面板位置 | 公告下方，collapsible，邏輯比照公告 auto-expand |

## 3. 資料模型（3 檔對齊 [[feedback_inline_schema_repair]]）

在 `Note` 與 `ChecklistItem` 各加 **4 欄**（沿用 F11 authorship 快照風格）：

| 欄位 | 型別 | 用途 |
|---|---|---|
| `reminder_date` | `Date` nullable | 到期日（`Note` 用於 note_type=`note`；`ChecklistItem` 用於清單項目） |
| `reminder_set_by` | `Integer` FK users `ondelete=SET NULL` nullable | 最後設定/修改者 id |
| `reminder_set_by_name` | `EncryptedText` nullable | 最後設定者帳號快照（帳號刪除後仍保留可讀） |
| `reminder_set_at` | `DateTime`（UTC）nullable | 最後設定/修改時間（顯示時轉 TW） |

**為什麼用獨立快照、不重用 `updated_by`**：`updated_by` 記「最後改任何欄位的人」，改個標題就會蓋掉，導致 inline「誰設的到期日」顯示錯誤。提醒需要自己一組快照。

**三檔對齊清單**（缺一 prod 100% ISE、本機 SQLite 抓不到）：
1. `models.py`：`Note`、`ChecklistItem` 加 4 欄 + relationship（`reminder_setter = relationship("User", foreign_keys=[reminder_set_by])`）。
2. `app.py`：inline schema repair 區段加 `ALTER TABLE notes / checklist_items ADD COLUMN IF NOT EXISTS ...`（PG 14+ 支援）。
3. `migrations/versions/017_note_reminder.py`：對應 alembic migration（僅 dev 用）。

> dev SQLite **不會**自動同步（`IF NOT EXISTS` 被 try/except 吞）→ 本機要手動 `ALTER` 或 `rm instance/dev.db` reseed（[[feedback_dev_sqlite_alter]]）。

## 4. 稽核歷史（重用 `NoteLog`，零新 table）

每次 set / 改 / 清到期日，append 一筆 `NoteLog`（既有表：`note_id` + `user_id` + `action` + `diff` + `created_at`）：

| 動作 | `action` | `diff` 範例 |
|---|---|---|
| 新增到期日 | `reminder_set` | `到期日 (無)→6/20` |
| 修改到期日 | `reminder_change` | `到期日 6/20→6/22` |
| 清除到期日 | `reminder_clear` | `到期日 6/22→(無)` |

- **項目級**：`NoteLog` 只有 `note_id`、無 item_id → diff 帶項目文字定位：`項目「準備食材」到期日 →6/20`。
- 操作者與時間戳由 `user_id` + `created_at` 自動帶入。
- 這些 log 流進 notes 既有歷史記錄，**不另做新 UI**。
- 日期顯示一律 TW（M/D），不顯示 UTC（[[feedback_timezone_taiwan]]）。

## 5. 「今日提醒」面板（全店）

### 後端
- 新 endpoint `GET /notes/api/reminders/today`：
  - 撈**目前 store**、未軟刪、`reminder_date <= 今天(TW)` 的 note 與 checklist item。
  - **排除**：已勾選項目（`is_checked=True`）、已 resolved 筆記（`status='resolved'`）。
  - 逾期（`reminder_date < TW today`）標 `overdue` flag；依「逾期優先 → 日期升冪」排序。
  - super_admin 吃 `?store=` context；per-store 權限對齊（看不到別店提醒）。
  - 回傳含 **`last_sig`**（= 該店所有有效提醒的 `max(reminder_set_at)` ISO 字串，供前端判斷「有人調整」）。

### 前端（`notes/index.html`）
- 公告**下方**新增 `<section class="collapsible" data-section="reminders">`，標題「⏰ 今日提醒（N）」。
- 卡片樣式鏡像 mynotes（`.mn-reminders` 黃底、逾期紅 chip）。
- note-card meta 顯示到期 chip（今日 `reminder-due` 橘 / 逾期 `reminder-overdue` 紅）。

### 折疊 / auto-expand 行為（重用 `collapsible_sections.js` 的公告機制）
複製公告 `notes_ann_last_seen` 那套，新增 key `notes_reminders_last_seen`，存 `{date, last_sig}`，per-store、per-device：

- **每天第一次** 進來 → 全展開。
- 之後 → 自動縮起。
- **`last_sig` 比上次看到的新**（有人新增/改到期日）→ 自動彈開提醒。
- 看過後寫回 `{今天日期, 當前 last_sig}`。

判斷邏輯：`shouldAutoExpand = (storedDate !== todayTW) || (last_sig > stored.last_sig)`（鏡像 `shouldAutoExpandAnn`）。

## 6. UI / 編輯器（`notes/editor.html`）

- **note 級**（note_type=`note`）：編輯器加「⏰ 提醒日期」`<input type=date>` + 清除鈕（鏡像 mynotes）。
- **項目級**（checklist item）：項目編輯 modal 加到期日 picker（鏡像 mynotes 項目 📅）。
- 兩處旁邊 inline 顯示：「最後由 〈reminder_set_by_name〉 於 〈reminder_set_at→TW M/D HH:MM〉 設定」（無值時不顯示）。
- POST/PUT 帶 `reminder_date`；後端比對舊值決定寫哪種 `NoteLog` action + 更新快照欄。

## 7. 權限

能編輯該筆記的人（同店 `user`＋、`super_admin` 帶 store context）即可設/改到期日，沿用既有 note 編輯 gate，不另設權限。

## 8. 測試（`pytest tests/`，與 CI 一致）

- set / change / clear 各正確寫入對應 `NoteLog` action + 更新快照 3 欄。
- `/notes/api/reminders/today`：store 範圍正確、跨店看不到別店、TW today 邊界（含/不含當天）。
- 排除規則：已勾選項目、已 resolved 筆記不出現在提醒。
- `last_sig` 隨提醒異動改變。
- 權限：別店 user 打 API 拿不到本店提醒。
- SW `CACHE_NAME` bump（改了 .js/.css → 踩雷 #5）。

## 9. 容量（[[feedback_capacity_check]]）

8 個新欄位（4×2 表）+ 一條 indexed 查詢（`reminder_date <= today` per store），無重運算、無新外部呼叫，對 2CPU/8GB 影響可忽略。`reminder_date` 視查詢量決定是否加 index（預設先不加，量小）。

## 10. 影響面 / 部署提醒

- 動到 `models.py` / `app.py`（schema）/ template / static JS → 走正常 app build（不碰 base image、requirements，無 [[feedback_warn_build_deploy_impact]] 等級影響）。
- SW 版本要 bump。
- prod 走 app.py inline ALTER（非 alembic），上線時自動補欄位。
