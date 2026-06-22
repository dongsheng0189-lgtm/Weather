# 浮動返回鈕 dirty 時「儲存並返回」設計

日期：2026-06-22
分支：`feat/float-back-save-return`

## 背景與問題

兩個既有功能疊在一起後產生體驗摩擦：

- **F13 浮動返回鈕**（`a2c3cf9`）：editor 往下捲動、頂部 `#btn-back` 離開視野時，右下角出現浮動返回鈕。
- **6/16 就地儲存 + 未存指示器**（`0e0a3a7`）：editor 存檔後留在頁面；dirty 時返回會跳「尚未儲存變更，確定離開？」confirm。

在確認表單（checklist editor）上連續修改資料、往下拉之後，**唯一搆得到的返回入口是右下浮動鈕，但儲存鈕在最上面**。dirty 狀態下點浮動鈕只會跳「確定離開？」→ 確定就**直接導頁、丟掉變更**。浮動鈕因此變成「放棄變更」鈕，違反使用者預期。

## 目標

dirty 時浮動返回鈕變成「💾 儲存並返回」，點擊**自動存檔成功後才導頁**，使用者不必捲回頂部按儲存、也不會丟資料。

## 範圍決策（已與 user 確認）

1. **適用所有編輯器**（note 與 checklist 一致）——dirty 機制本來就共用，統一最省、體驗一致。
2. **只改浮動鈕**；上方 `#btn-back`（緊鄰儲存鈕）維持原本「確定離開放棄變更」confirm 不變。

## 浮動鈕三態

| 狀態 | 標籤 | 點擊行為 |
|---|---|---|
| review/唯讀模式 (`body.review-mode` / `IS_READONLY`) | `← 返回摘要`（現狀） | 直接導頁（不可編輯、永不 dirty） |
| 乾淨（未變更，`_dirty=false`） | `← 返回`（現狀） | 直接導頁 |
| **dirty（`_dirty=true`）** | **`💾 儲存並返回`** | `doSave()` → **成功才導頁**；失敗留在頁面、不導頁 |

## 行為細節

- 點擊 dirty 浮動鈕：
  1. 防重複點擊（`_fbSaving` guard），標籤暫變 `💾 儲存中…`。
  2. 呼叫 `doSave()`。
  3. resolve 成功（含 pending 附件全部上傳完）→ 設 `_leavingIntentionally=true` → 導向 `#btn-back` 的 href（保留 filter/page query string，與現行返回一致）。
  4. resolve 失敗（被鎖定 / 網路錯 / 附件上傳失敗）→ 還原標籤 `💾 儲存並返回`、解除 `_fbSaving`、**不導頁**；錯誤訊息沿用 `doSave` 既有 `#save-status` 輸出。
- 因為走的是「存檔」而非「放棄」，**浮動鈕移除舊的 `_guardLeave` confirm**；該 confirm 只保留在 `#btn-back`。
- 成功存檔後 `setSaved()` 會把 `_dirty=false`，`beforeunload` safety net 不會誤攔；`_leavingIntentionally=true` 為雙保險。

## 程式改動（皆在 `templates/notes/editor.html` 內嵌 script）

1. **`doSave()` 回傳 Promise**：成功（含附件上傳完）resolve `true`；各失敗路徑（`handleMutationResponse` false、save 失敗、附件上傳失敗、catch）resolve `false`。頂部 `onclick="doSave()"` 不受影響（忽略回傳值）。
2. **浮動鈕標籤跟 dirty 狀態連動**：把標籤決定邏輯併進既有 `refresh()`（讀 `_dirty`；review 模式跳過、不覆蓋 `← 返回摘要`）。在 `markDirty()` 與 `setSaved()` 末尾呼叫 `window._refreshFloatBack && window._refreshFloatBack()`。
3. **改寫浮動鈕 click handler**：dirty 且非唯讀 → 走「存後導頁」；否則直接導頁。把 `_guardLeave` 的 capture 監聽**從浮動鈕移除**，只留在 `#btn-back`。

## 不需要做的事

- **不 bump SW**：只動 editor.html 內嵌 script，沒碰任何 `/static/` 檔；editor 頁是 navigation（SW network-first，不入 cache-first）。
- 不動後端、不動 DB、不動 models。
- 不改上方 `#btn-back` 行為。

## 驗證步驟

1. 本機 `bash scripts/run_local.sh` → `/dev` 切換帳號。
2. 開 checklist editor、連續改資料、往下拉到浮動鈕出現 → 應顯示 `💾 儲存並返回`。
3. 點浮動鈕 → 暫顯 `💾 儲存中…` → 存好後回原列表（filter/page 保留），且該筆變更已落地。
4. 不改任何資料時往下拉 → 浮動鈕仍是 `← 返回`，點擊直接返回、不存檔。
5. note 型 editor 同樣驗一遍（範圍涵蓋所有編輯器）。
6. review 模式（從 AI 摘要 `?from=` 進入）→ 浮動鈕仍是 `← 返回摘要`、行為不變。
7. `pytest tests/`：檢查是否有 wiring test 斷言浮動鈕/`doSave` 字串，若有同步更新。
