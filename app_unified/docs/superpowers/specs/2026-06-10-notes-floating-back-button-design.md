# 浮動返回鈕 + 修正分頁返回 Design

> Date: 2026-06-10 · Module: `notes`（店務筆記 + 確認表單，共用 `notes/editor.html`）· Status: approved（待 user review spec）

## Goal

筆記 / 確認表單編輯頁資料多時，要捲到最上面才能按「← 返回」。新增一顆**右下角浮動返回鈕**，向下捲動後出現，讓使用者隨時一鍵回上一頁。同時修正一個分頁 bug：從列表第 2 頁點進筆記後返回會跳回第 1 頁。

## Context（現況，已驗證）

- **筆記與確認表單共用 `templates/notes/editor.html`**（`note_type` = `note` / `checklist`）。
- **頂部「← 返回」鈕** `#btn-back`（editor.html L150-159，`{% block content %}` 開頭、**無條件 render**）：`url_for('notes.index', q,tag,category,shift,range,status,sort,store)` — **缺 `page`**。檢視模式時由 JS 改寫成 AI 摘要 URL。
- **現有浮動返回鈕** `#review-back-widget`（L185-192）：`position:fixed; bottom:20px; right:20px; z-index:1000` 的 pill，**只在檢視模式**（`body.review-mode`，CSS L54-55）顯示，label「← 返回摘要」。檢視模式由 JS（L792-800）判斷 `?from=/n/\d+` 後設定 `#btn-back` + `#review-back-widget` 的 href 並加 `body.review-mode`。
- **`#search-hit-widget`**（L165-170）：確認表單 + 搜尋 + 有 hits 時顯示的浮動 widget，也在 `bottom:20px; right:20px; z-index:1000`（會與浮動返回鈕撞角）。
- **`index.html` note-card 點擊**（L266）：`url_for('notes.edit_note', note_id, sort,q,tag,category,shift,range,status,store)` — **缺 `page`**。
- **分頁**：`index.html` 的 pagination prev/next/數字連結（L300/308/316）有帶 `page`；後端 index route 用 `request.args.get('page')` + 自製 Pagination 物件。
- 踩雷參考：filter context preservation（改 back 連結要帶完整 query）、no setTimeout race（等 render 用 observer 不要賭時間）。

## 範圍決策（已與 user 拍板）

| 項目 | 決定 |
|---|---|
| 返回目標 | 鏡像頂部 `#btn-back`（回列表 + 保留所有 filter + `page`；檢視模式跟著回 AI 摘要） |
| 顯示時機 | 向下捲動、頂部 `#btn-back` 捲出畫面後才淡入；捲回頂端隱藏 |
| 位置 | **右下角**（建立「右下角 = 返回」共同習慣） |
| 整併 | **合併成一顆通用浮動鈕**，移除舊的 `#review-back-widget` |
| 一起修 | 分頁返回 bug（`page` 在兩個 hop 遺失） |
| 不碰 | mynotes；無新 DB 欄位；無新 static 檔 |

## 設計

### A. 通用浮動返回鈕（`notes/editor.html`）

- 新增單一元素 `#float-back-widget`（取代 `#review-back-widget`）：
  - `position:fixed; bottom:20px; right:20px; z-index:1000`、pill 樣式（沿用原 review-back-widget 外觀：藍底 `#1f4e79`、圓角 24px、陰影），label 預設「← 返回」。
  - 預設 `display:none`（捲動後才顯示）。
- **點擊行為**：`location.href = document.getElementById('btn-back').href`（鏡像頂部鈕 = 單一真相來源）。因為鏡像，Part B 修好 `page` 後浮動鈕自動帶 `page`；檢視模式下 `#btn-back` 已被既有 JS 改成摘要 URL，浮動鈕也自動跟著。
- **顯示/隱藏**：用 `IntersectionObserver` 監看 `#btn-back`。`#btn-back` 離開視窗（捲過去）→ 顯示浮動鈕；回到視窗 → 隱藏。短頁面（不需捲動）→ 頂部鈕一直在視窗內 → 浮動鈕不顯示。**不用固定 setTimeout**。
- **檢視模式 label**：既有 review-mode JS（偵測 `?from=`）除了改 `#btn-back` href，另把 `#float-back-widget` 的文字改成「← 返回摘要」。其餘行為（href 鏡像、捲動顯示）不變。
- **移除**：`#review-back-widget` 元素（L185-192）、其 CSS（L54-55 `#review-back-widget` 兩條）、review-mode JS 裡設定 `#review-back-widget` href 的那行（L798-799）→ 改為設定 `#float-back-widget` 的 label。

### B. 修正分頁返回（`page` 保留）

兩個 hop 各補 `page`：
1. **`index.html` L266**：note-card onclick 的 `url_for('notes.edit_note', ...)` 加 `page=pagination.page`。
2. **`editor.html` `#btn-back`**（L150-159）：`url_for('notes.index', ...)` 加 `page=request.args.get('page')`。

→ 第 2 頁點筆記、返回回第 2 頁。`page` 為 None 時 `url_for` 省略該參數 = 回第 1 頁（維持第 1 頁進來者的現況）。浮動鈕鏡像 `#btn-back` 故自動受惠。

### C. 與 `#search-hit-widget` 互斥（不疊放、不動它）

`#search-hit-widget`（確認表單 + 搜尋 + hits>0 時顯示）本身已內含三件：`🔍 N/M 命中`、`▶ 下一個`（`#search-hit-next` → `jumpToHit(_currentHit+1)`，循環跳下一個命中項）、`← 返回`（`#search-hit-back` → `history.back()`）。

→ 為避免右下角出現**兩顆返回**，`#float-back-widget` 與 `#search-hit-widget` **互斥**：
- `#float-back-widget` 的顯示條件 = （`#btn-back` 捲出視窗）**且**（`#search-hit-widget` 目前未顯示，即 `display === 'none'`）。
- 確認表單搜尋時 → search-hit-widget 顯示（自帶返回 + 下一個）→ 通用浮動鈕**不出現**。
- 其餘情況（一般筆記、確認表單無搜尋）→ search-hit 不顯示 → 通用浮動鈕照常捲動出現。
- **完全不修改 `#search-hit-widget`**（markup / next / back / 計數全部原封不動，零風險保留「▶ 下一個」）。

> 註：`#search-hit-back` 用 `history.back()`，回到的是瀏覽器真正的上一頁（含當時的 `page`/filter），本來就不受 Part B 的 `page` bug 影響；Part B 修的是 `url_for` 型的 `#btn-back`（通用浮動鈕鏡像它）。

## 錯誤處理 / 邊界
- `#btn-back` 不存在時（理論上不會）浮動鈕點擊 no-op，不報錯。
- `IntersectionObserver` 不支援的舊瀏覽器 → fallback：直接顯示浮動鈕（degrade，不壞）。
- 後端為列表分頁真相：`page` 超出範圍時 Pagination 物件已 clamp（`max(1, page)` / items slice 空），不會壞。

## 測試
- **`tests/test_notes_floating_back.py`（新）**：
  - render：`editor.html` 含 `id="float-back-widget"`、含 `getElementById('btn-back')`（鏡像）、含 `IntersectionObserver`；**不含** `id="review-back-widget"`（已移除）。
  - `index.html` note-card 連結含 `page=`（render 帶分頁時）。
  - 後端：帶 `?page=2` GET 編輯頁 → `#btn-back` href 含 `page=2`；不帶 page → href 不含 `page=`。
- 浮動鈕的捲動顯示/互斥是純前端互動 → 手動測：① 長筆記捲動出現、點擊回正確頁；② 列表第 2 頁點筆記 → 返回回第 2 頁；③ 確認表單 + 搜尋 → 只出現 search-hit-widget（▶ 下一個、計數、← 返回 都在）、**不出現第二顆通用返回鈕**。
- 註：wiring 測試斷言 DOM/字串較脆（踩雷 #12），改 template 要同步更新。

## 部署影響
- 只動 `notes/editor.html` + `notes/index.html`（template）。無新 DB 欄位（不需 schema repair）、無新 static js/css（inline）→ **不用 bump SW**。
