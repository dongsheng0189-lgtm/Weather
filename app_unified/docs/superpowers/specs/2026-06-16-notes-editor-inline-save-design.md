# 筆記/確認表單編輯器「就地儲存 + 未存指示」Design

**日期**：2026-06-16
**範圍**：只動 `templates/notes/editor.html`（notes + checklist 共用編輯器；server-render HTML，不進 SW 靜態快取 → 不用 bump SW）。mynotes 不動。

## 問題
目前按「儲存」會存檔並 **redirect 回列表**。但實際上使用者通常會持續編輯，被踢回列表很不順。

## 目標行為
1. **儲存後不離開**：`doSave()` 成功後留在編輯器，不再 redirect。
2. **儲存鈕 = 未存指示器（dirty indicator）**
   - 乾淨（剛開啟既有筆記 / 剛存完）→ **「已儲存」**（淡色、disabled）
   - 有未存變更 → **「儲存」**（亮色、可按）
   - 全新未存筆記初始 → 「儲存」（可按）；使用者動任何東西前不算 dirty（返回不攔截）
3. **任何變更都標記 dirty**（刻意：養成按儲存習慣）
   - note 欄位：標題、內容、分類、班別、狀態、到期日、標籤
   - checklist 項目操作：新增 / 編輯 / 打勾 / 刪除 / 項目到期日 / 項目標籤 / 項目附件
   - 註：項目操作本身仍各自即時存檔（既有行為不變），但「會把主儲存鈕翻回『儲存』並列入未存提醒」——這是刻意的習慣養成，不是說項目沒存到。
4. **返回只剩兩顆**：最上方 `#btn-back`、浮動 `#float-back-widget`（移除 save 的自動返回）。
5. **未存攔截**
   - 按那兩顆返回鈕時，若 dirty → 自訂中文 `confirm`「尚未儲存變更，確定離開？」。取消則留下。
   - `beforeunload` safety net：dirty 時關分頁 / 重整 / 瀏覽器上一頁 / 改網址 → 原生彈窗（文字無法自訂）。
   - **避免雙重提醒**：點兩顆返回鈕屬「刻意離開」，導頁前先解除 beforeunload（設旗標），所以只會看到自訂 confirm、不會再跳原生彈窗。

## 新筆記流程
第一次儲存：POST 取得 id → 設 `NOTE_ID` → pending 附件照常上傳完 → `setSaved()` 顯示「已儲存」→ `history.replaceState` 把網址換成該筆記編輯 URL（重整不掉內容、之後存檔走 PUT）。**不** redirect。

## 狀態機
- `_dirty`（bool）+ `markDirty()` / `setSaved()`：
  - `markDirty()`：`_dirty=true`；btn-save → 文字「儲存」、enabled、primary 樣式。
  - `setSaved()`：`_dirty=false`；btn-save → 文字「已儲存」、disabled、淡色樣式。
- 掛勾點：
  - note 欄位：對編輯器表單下 `input`/`change` 事件委派 → `markDirty()`（涵蓋標題/內容/到期日/select 類）；按鈕驅動的（分類鈕 `selectCategory`、班別、狀態切換）在各自 handler 內呼叫 `markDirty()`。
  - 項目操作：在新增/編輯(modal 存檔)/打勾 toggle/刪除/項目到期日/項目標籤/項目附件的「成功 callback」內呼叫 `markDirty()`。
- 初始：既有筆記載入 → `setSaved()`（乾淨）；新筆記 → 「儲存」可按、`_dirty=false`，首次 input 才轉 dirty。

## 不做（YAGNI）
- 不做自動存檔 / 草稿。
- 不動 mynotes、不動後端 API。
- 不改 checklist 項目本身的即時存檔機制。

## 測試
- editor.html 行為為前端 JS，主要靠**本機瀏覽器驗證**。
- 加 wiring 斷言（`tests/`）：editor.html 含 `markDirty`/`setSaved` 狀態機、返回鈕 dirty 攔截、`beforeunload` 掛勾、`doSave` 不再 redirect 到列表（移除 `NOTES_INDEX_URL` 跳轉，改成 setSaved）。

## 驗證重點（本機）
1. 既有筆記開啟 → 鈕顯示「已儲存」；改標題 → 變「儲存」；按儲存 → 變「已儲存」、**不離開**。
2. checklist 打勾/加項目/改項目 → 主鈕翻回「儲存」。
3. dirty 狀態按上方返回 / 浮動返回 → 跳「尚未儲存，確定離開？」；乾淨則直接返回不打擾。
4. dirty 時重整/關分頁 → 原生未存彈窗；點返回鈕（確定離開）不會雙重彈窗。
5. 新筆記首次儲存 → 留在編輯器、網址變編輯 URL、顯示「已儲存」、附件正常。
