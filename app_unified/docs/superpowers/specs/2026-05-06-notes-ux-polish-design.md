# 店務筆記 UX 優化（鎖機制 / Checklist 排序 / 日期區間 / AI 摘要 fullscreen）

**日期**：2026-05-06
**範圍**：5 個獨立但同樣集中在 `notes/` 模組的 UX 改善。第 6 項 AI 摘要併發 timeout 暫緩（缺 reproduction log）。

---

## 動機

| # | 問題 | 影響 |
|---|------|------|
| 1 | 進入編輯頁立刻 acquire lock，使用者只想看就鎖住別人 | 多人協作體驗差 |
| 2 | 第一個人 save 後鎖釋放，但第二個人需要 reload 才能編輯 | UX 不順暢 |
| 3 | Checklist 永遠按 `updated_at` 排，勾選會把項目推到最上方 | 跟外層 sort 不一致，使用者期待按建立時間時不要跳動 |
| 4 | 日期區間最多只到「近月」（30 天），跨月資料看不到 | 資料完整性 |
| 5 | AI 摘要查看區塊太小，內容多時要捲很久還被頂部 nav/分類佔空間 | 閱讀體驗差 |
| 6 | 兩個人同時跑 AI 摘要 → 第二個 timeout（暫緩） | 等 log 抓 |

---

## 設計

### 1. 編輯鎖 lazy acquire（先 readonly，動到才鎖）

#### 現況
- `GET /notes/<id>/edit` (routes.py:1411) 進入時立即 `_acquire_lock(note)`
- editor.html 接 `is_readonly` flag 顯示鎖定訊息

#### 變更
- `edit_note` route **不再** 在 GET 時 acquire lock
- 所有 mutation API 統一走新 helper `_ensure_lock_for_mutation(note)`：
  - 若無人持鎖 → acquire（current_user）
  - 若自己持鎖 → 通過（順便 refresh `locked_at` 等同心跳）
  - 若別人持鎖且未過期 → 回 409 + locker name
- mutation 範圍（**列舉清單，每個都要走 `_ensure_lock_for_mutation`**）：
  - `POST /notes/<id>/save`（標題/內容/狀態/分類/班別等更新）
  - `POST /api/<id>/items` (checklist add)
  - `PATCH /api/<id>/items/<item_id>` (checklist edit / check)
  - `DELETE /api/<id>/items/<item_id>` (checklist delete)
  - `POST /api/<id>/items/<item_id>/attachments` (附件 upload)
  - `DELETE /api/<id>/items/<item_id>/attachments/<aid>` (附件 delete)
  - 註：notes 本身的 attachment 路徑（如有）也比照
- editor.html 預設 readonly：
  - 第一次任何 mutation 成功 → 後端回 `{lock_acquired: true}` → 前端切 editable mode
  - 任何 mutation 回 409 → 前端顯示「目前由 X 編輯中」+ 維持 readonly
- 心跳 heartbeat 維持現狀（acquire lock 之後才開始 ping）

#### 取捨
- **Pros**：純查看不打擾別人；改動接觸面集中在 mutation route 與 editor.html
- **Cons**：每個 mutation route 多 1~2 行檢查（用 helper 抽掉重複）；前端要追蹤「是否已 acquire」狀態

### 2. Save 後 broadcast 即時轉可編輯

#### 現況
- save 後 release lock（routes.py:416）
- 第二個 readonly 的人需 F5 reload 才會看到「已可編輯」

#### 變更
- save 端點 release lock 之後，透過 `notes/ws.py` 既有 channel broadcast：
  ```json
  {"type": "lock_released", "note_id": <id>, "by_user_id": <int>}
  ```
- editor.html JS 監聽該 event：
  - 若 `note_id` 等於目前頁面筆記 → 把 readonly UI 切到 editable，清掉 「由 X 編輯中」訊息、移除 force-release 按鈕
  - 若不是 → 忽略
- 不主動 acquire（仍是 lazy）；第二個人**動到欄位**才會 trigger acquire
- Race 防護：兩人同時收到 `lock_released` 並各自開始 mutation，server 端 `_ensure_lock_for_mutation` 仍是 atomic check（先到先得，後到拿 409）

#### 取捨
- 需確認 `notes/ws.py` 已具備 per-note channel + broadcast helper（先看 code，若無需小改）
- 沒 WS 連線（網路斷）時 fallback：使用者下次 mutation 嘗試會自動 acquire 成功（lazy 機制本身就 forgiving）

### 3. Checklist 排序跟外層 sort 共用

#### 現況
- `list_checklist_items` (routes.py:1153) 永遠 `ORDER BY updated_at DESC, id DESC`
- 勾選 / 編輯 → `updated_at` 變動 → 浮到最上面

#### 變更
- notes list 的 `sort=updated|created` URL 參數傳到 editor 頁面（前端拿到 `current_sort`）
- editor.html 載入 / 重整 checklist 時，呼叫 `GET /api/<id>/items?sort=<mode>`
- `list_checklist_items` 接 `sort` query param：
  - `sort=created` → `ORDER BY created_at DESC, id DESC`（勾選不影響排序）
  - `sort=updated` → 維持現況（default for backward compat）
- WebSocket 增量更新（既有的）也要 honor 排序模式：
  - 簡單做法：updated_at sort 時保持 prepend；created_at sort 時 frontend 直接重排或追加

#### 取捨
- 所有 checklist mutation 仍會更新 `updated_at`（因為 model 有 `onupdate`），只是不影響 created_at 排序
- 不新增 schema 欄位

### 4. 日期區間「所有」取代「近月」

#### 現況
- `RANGE_DAYS = {"today": 0, "3d": 3, "7d": 7, "30d": 30}` (routes.py:22)
- index.html:136 `('30d','近月')`

#### 變更
- `RANGE_DAYS["all"] = None`，移除 `"30d"` key
- index.html: `('all','所有')` 取代 `('30d','近月')`
- backend filter：
  - `days = RANGE_DAYS.get(range_param, 0)`
  - `if days is None: 不加 since 過濾`
  - `else if days == 0: today only`（既有）
  - `else: since = now - days`（既有）

#### 取捨
- 「所有」會撈整店所有筆記（pagination 已存在所以效能可接受）
- 移除 30d 不保留 → 沿用 spec 第 #4 答 (a) 的決定

### 5. AI 摘要 fullscreen（兩處都加）

#### 現況
- AI 摘要產生完的 modal 大小固定，內容超過 viewport 要捲動
- 已存的 AI 摘要筆記（is_ai_summary=True）詳情頁 = 一般筆記檢視，內容區塊小

#### 變更
- (a) **AI 摘要 modal**（`templates/notes/index.html` 內 modal）：
  - 右上加 `⛶` 按鈕 → toggle CSS class `summary-fullscreen`
  - fullscreen 樣式：`position: fixed; inset: 0; z-index: 9999; padding: 1rem; overflow-y: auto;` 隱藏 navbar/sidebar
- (b) **AI 摘要筆記詳情頁**：
  - 在編輯頁（`editor.html`）內容區塊上方加同款 `⛶` 按鈕
  - 點擊 → fullscreen modal 顯示內容（pre-formatted）+ 關閉按鈕
  - 只在 `note.is_ai_summary == True` 時出現此按鈕
- 共用一個 CSS class（放在 `static/css/notes.css` 或 base.html style）

#### 取捨
- 不用 browser Fullscreen API（瀏覽器全螢幕會把 OS 也一起接管，UX 過頭）
- 純 CSS overlay 最簡單，所有元素仍在同一個 DOM tree

### 6. AI 摘要併發 timeout（暫緩）

待使用者下次重現時提供以下資料：
- `logger ai_summary` 兩個 task_id 的完整 log（從 `_ai_tasks[task_id] = "processing"` 到 `done|error`）
- 前端錯誤訊息 / network 響應 status code
- Gemini API 實際呼叫位置 + timeout 設定（待 implementation 階段 grep）

可能解法（待診斷）：
- (A) gevent.spawn 是 cooperative，IO wait 應該 yield，不該互相 block — 若真 block 表示 Gemini SDK 用 sync requests 沒 monkey-patch
- (B) Gemini API rate limit / quota
- (C) 前端 polling timeout（client-side 等待時間）

---

## 範圍外（YAGNI）

- 跨多筆筆記的 bulk lock 機制
- Mobile-specific UX 調整
- AI 摘要 prompt 微調（屬於另一個 backlog）
- 使用 browser Fullscreen API
- AI 摘要 timeout 修復（待 reproduction log）

---

## 影響檔案

| 檔案 | 改動內容 |
|------|----------|
| `notes/routes.py` | (#1) 移除 edit_note 的 acquire；新增 `_ensure_lock_for_mutation`；mutation routes 接這個 helper。(#3) `list_checklist_items` 接 `sort` param。(#4) `RANGE_DAYS` 改 `"all":None` + filter 邏輯 |
| `notes/ws.py` | (#2) save 後 broadcast `lock_released` event；其他 mutation 也保持 broadcast 既有 changes |
| `templates/notes/index.html` | (#4) range_labels 改「所有」。(#5a) summary modal 加 fullscreen 按鈕 + CSS class |
| `templates/notes/editor.html` | (#1) 預設 readonly，第一次 mutation 切 editable。(#2) 監聽 `lock_released` event。(#3) 從 URL 拿 sort 並傳到 list_checklist_items 呼叫。(#5b) `is_ai_summary` 時顯示 fullscreen 按鈕 |
| (新) `static/css/notes.css` 或 `base.html` `<style>` | fullscreen modal CSS class |
| `tests/test_notes_*.py` | 為 #1 #3 #4 加 unit test |

無 schema migration（不需要 model 改動）。

---

## 測試計劃

### Unit / Integration
- `test_lock_lazy_acquire`：GET edit 不 acquire；mutation 觸發 acquire；別人持鎖時 mutation 回 409
- `test_checklist_sort_modes`：sort=created vs sort=updated 兩種 ORDER BY 結果不同
- `test_range_all`：range=all 不過濾日期，回所有筆記
- `test_save_releases_lock_and_broadcasts`：save 後 lock 是 None + broadcast 有 fire（mock WS layer）

### 手動 / 實機驗證
- 兩個 browser 同時開同一筆筆記：A 動 → 鎖；B 動 → 409；A save → B 自動轉 editable
- Checklist 切 sort=created：勾選不再上浮
- 日期選「所有」可看到 1 個月以前的筆記
- AI 摘要 modal 與筆記詳情頁兩處 fullscreen 切換正常

---

## Worktree 與分支

- 分支：`feat/notes-ux-polish`
- Worktree：`/home/hirain0126/projects/webapp/.worktrees/feat-notes-ux-polish/`
- 完成 + 全測試 + 使用者實機驗證後 merge ff 到 main

---

## Open questions

1. ~~mutation 範圍清單是否完整~~ — 2026-05-06 使用者確認 OK（含 checklist 全部動作 + 附件）
2. 第 #6 暫緩，待使用者下次重現抓 log
