# Notes 返回時還原捲動位置（scroll restore）— 設計

- 日期：2026-06-24
- 模組：`notes/`（店務工作筆記列表）
- 分支：`feat/notes-back-scroll-restore`

## 痛點

在 notes 列表往下滑、點進某一篇筆記/確認表單，看完按「返回」後，列表頁重新載入並**捲動歸零到最上面**。使用者要重新往下滑才能找到原本那篇下面的文章，體驗差。

現有機制：
- 列表卡片點擊是 `location.href=...edit_note...` 的**整頁導航**進編輯器。
- 編輯器「← 返回」鈕（含右下角浮動鈕）是 `<a href>` 整頁導回 `notes.index`，URL 已帶完整 filter + `page=`（卡片 onclick 傳 `page=pagination.page`、返回鈕用 `request.args.get('page')`），所以**返回後落在正確的那一頁**，只是停在該頁最上面。

→ 要補的只有「回到正確那頁後，再捲到剛點的那張卡片」。

## 目標

返回 notes 列表後，自動捲到「剛剛點進去的那張卡片」並短暫 highlight，取代跳回最上面。需同時涵蓋：
1. 畫面上的「← 返回」鈕（頂部 `#btn-back` + 右下角 `#float-back-widget`）。
2. 手機螢幕邊緣滑動 / 瀏覽器上一頁手勢。

## 方案

採 **sessionStorage 記住「剛點的那篇」+ 回列表時自動捲到它**。

理由：返回鈕與滑動/上一頁兩種路徑都回到**同一個列表 URL、同一頁**（卡片必在該頁），所以同一段還原邏輯都能定位。純前端、不動後端、不動編輯器。

（已否決：純記 `scrollY` 數值還原 → 濾鏡/視窗高度一變就跑位；靠 bfcache 自動還原 → SW + unload handler 常讓 bfcache 失效，手機不可控。）

## 運作流程

1. **離開列表（點卡片時）**：卡片 `onclick` 在現有 `location.href=...` 導航**之前**先
   `sessionStorage.setItem('notes_return_to', <note.id>)`。
2. **回到列表（頁面載入時）**：`templates/notes/index.html` 內嵌 script 於 `DOMContentLoaded`
   與 `pageshow`（涵蓋 bfcache 還原）時執行還原：
   - 讀 `notes_return_to`。
   - 若有值 **且** 該頁存在 `#note-<id>` 元素：
     - `el.scrollIntoView({block:'center'})`（**瞬間**，不用 smooth，避免還原時長動畫）。
     - 加 highlight class，約 2 秒後移除。
     - **清掉** `notes_return_to`（用過即清）。
   - 若該卡片不在這頁（被濾掉 / 已刪 / 換了濾鏡）：直接清掉記號、不捲動（安靜停在最上面 = 現狀，不會壞）。

## 改動範圍

只動 `templates/notes/index.html`：

1. 每張 `.note-card` 加 `id="note-{{ note.id }}"`。
2. 卡片 `onclick` 在導航前加 `sessionStorage.setItem('notes_return_to','{{ note.id }}')`。
3. 新增內嵌還原 script（`DOMContentLoaded` + `pageshow`）。
4. 新增一小段 highlight CSS：`.note-card.note-return-highlight`（黃底閃一下、~2 秒淡出）。
   - 註：現有 `.tag-highlight` 綁定 `.cl-item`（checklist 項目）專用，**不可**直接套到 `.note-card`，故另開一個卡片專用 class。

**不動編輯器、不動後端、無 DB schema 變動、無 `/static/` 變動 → 不 bump Service Worker。**
（還原 script 刻意內嵌在 index.html，不放進 static JS，避免動到 SW 快取。）

## 邊界處理

- **筆記被刪 / resolved / 被濾掉**：找不到 `#note-<id>` → 安靜不捲（degrade 成現狀）。
- **用過即清**：避免之後從導覽列正常進列表時被舊記號誤觸發亂捲。
- **可接受的小邊界**：點卡片後不按返回、改用導覽列「筆記」再進列表，若該卡片剛好在當頁會被捲到 + highlight 一次。無害（本來就是剛看過的那篇），不額外處理。
- **bfcache 已還原捲動**（`pageshow` `event.persisted=true`）時仍跑還原邏輯：捲到卡片 + highlight 是冪等且無害的，確保兩條路徑行為一致。

## 範圍界線

- **只做 notes 列表**（本次指定）。
- mynotes 私人筆記是另一獨立模組，本次不碰；若日後要同樣行為再另開 spec。

## 驗證

本機 `bash scripts/run_local.sh` →
1. notes 列表往下滑，點一篇下面的筆記 → 按「← 返回」→ 回到該卡片位置 + 黃底閃一下。
2. 重複，改用手機螢幕滑動 / 瀏覽器上一頁 → 同樣定位到該卡片。
3. 點一篇 → 換濾鏡 / 換頁讓該卡片不在當頁 → 返回 → 安靜停在最上面、不報錯。
4. 從導覽列正常進列表（未經返回）→ 不應因舊記號亂捲（用過即清驗證）。
