# Notes Index：公告 + AI 摘要可摺疊設計

**日期**: 2026-05-26
**作者**: hirain（with Claude）
**Branch**: `feat/notes-collapsible-sections`
**Status**: Draft for review

## 起點與動機

`/notes/` 首頁頂部有兩塊長期占版面的區塊：

1. **公告區** — 多篇長公告堆起來、user 要滑很久才到下方筆記 grid
2. **AI 摘要區** — 使用率低、但常駐占一張 card 高度

User 要兩塊都做成「可摺疊」、降低 above-the-fold 干擾、加速到達筆記 grid 的視覺路徑。

## 目標

- 公告區預設摺疊、但「有新公告」或「今天第一次來」會自動展開一次
- AI 摘要區永遠預設摺疊（使用率低、不打擾）
- 摺疊狀態仍能讓使用者一眼掃過「有哪些公告」（顯示標題列表、不顯示內文）
- 個別公告可獨立展開（不必全部展開才能讀某一篇）
- 切換動作有清楚的視覺圖示（chevron `▶` ↔ `▼`）

## 非目標（這次不做）

- 5 排 filter tabs（種類/班別/時間/狀態/排列）摺疊 — 5/15 mockup 有畫、未來另開 scope
- 標籤系統 — 完全分開的 scope
- 公告未讀紅點 / 未讀計數 badge — 自動展開觸發已涵蓋
- 個別公告摺疊狀態的跨 session 持久化 — 每次 reload 重新判斷
- AI 摘要記憶上次開關狀態 — user 明確要求永遠摺疊

## 設計

### A. 公告區：兩態 + 個別 toggle

**三種視覺 mode**:

```
Mode 1: 全摺疊（預設、無新公告時）
┌──────────────────────────────────────────────┐
│ 📢 公告 (3)                              ▶  │
├──────────────────────────────────────────────┤
│  • 12/30 上班時間調整                        │
│  • 新進員工訓練說明                          │
│  • 老闆三言兩語·5點結語                      │
└──────────────────────────────────────────────┘

Mode 2: 混合（個別點開某篇）
┌──────────────────────────────────────────────┐
│ 📢 公告 (3)                              ▼  │
├──────────────────────────────────────────────┤
│  • 12/30 上班時間調整                        │
│ ┌──────────────────────────────────────────┐ │
│ │ [A] 新進員工訓練說明                     │ │
│ │ （完整內容、附件、footer）                │ │
│ └──────────────────────────────────────────┘ │
│  • 老闆三言兩語·5點結語                      │
└──────────────────────────────────────────────┘

Mode 3: 全展開（auto-expand 命中、或 user 點 section bar 全開）
┌──────────────────────────────────────────────┐
│ 📢 公告 (3)                              ▼  │
├──────────────────────────────────────────────┤
│ （所有 card 完整顯示）                        │
└──────────────────────────────────────────────┘
```

**互動規則**:

| 動作 | 行為 |
|---|---|
| 點 section header bar (整條、含 chevron + 「📢 公告 (N)」標題) | toggle：任何展開狀態 → 全摺疊；全摺疊 → 全展開 |
| 點個別標題 bullet | 該篇 card 在「標題」與「完整 card」之間 toggle |
| Page load + auto-expand 條件命中 | 直接進入 Mode 3（全展開）+ 寫 localStorage |
| Page load + 條件未命中 | Mode 1（全摺疊） |

**auto-expand 觸發邏輯**:

- localStorage key: `notes_ann_last_seen`
- value: `{date: "YYYY-MM-DD", last_ann_id: <int>}`
- 條件（OR）:
  - `stored.date < today`（今天還沒來過）
  - `stored.last_ann_id < current_max_ann_id`（有比上次更新的公告）
  - stored 不存在（第一次用）
- 命中時：載入後直接渲染 Mode 3 + **立刻寫回** `{date: today, last_ann_id: max_ann_id}`
- 不命中：載入後渲染 Mode 1

**狀態邊界**:

- 「今天」基準 = client local date（`new Date().toISOString().slice(0,10)` 或 `new Date().toLocaleDateString('sv-SE')` 取 YYYY-MM-DD）
- `current_max_ann_id` = template 渲染時帶到 `data-` attribute，JS 從 DOM 讀
- 不需要 server endpoint，純 frontend localStorage
- 不持久化手動 toggle 狀態（每次 reload 都重新跑 auto-expand 判斷）

### B. AI 摘要區：永遠摺疊

```
Mode A: 摺疊（預設）
┌──────────────────────────────────────────────┐
│ 🤖 AI 摘要                              ▶  │
└──────────────────────────────────────────────┘

Mode B: 展開（user 點開）
┌──────────────────────────────────────────────┐
│ 🤖 AI 摘要                              ▼  │
├──────────────────────────────────────────────┤
│ [店別下拉]  [今天][昨天]  [產生摘要]          │
│ ⓘ 以早上 8 點為營業日分界...                 │
│ （產生後輸出區）                             │
│ [💾 儲存為筆記]                              │
└──────────────────────────────────────────────┘
```

**互動規則**:

| 動作 | 行為 |
|---|---|
| Page load | 永遠 Mode A（摺疊） |
| 點 section bar | toggle Mode A ↔ Mode B |
| 在 Mode B 下產生摘要、儲存為筆記 | 行為與現在完全相同（不動 backend） |

不寫 localStorage、不記憶狀態。

### C. 共用視覺/CSS

- chevron 圖示：純 CSS `▶` 字符 + `transform: rotate(90deg)` 切 `▼`、`transition: transform 200ms ease`
- Bar 整條 cursor:pointer + hover 加 background 微亮（visual affordance）
- 公告 bar 跟 AI 摘要 bar 同 style 規格（border-radius / padding / font-size）— 視覺語言一致
- Mobile + Desktop 同一套機制（不分裝置）
- 點擊區域動畫：bar 按下時 `:active` state 加 0.95 scale 給觸感反饋

### D. DOM 結構與檔案佈局

| 改動 | 檔案 |
|---|---|
| 公告 section wrapper 改成 `<details>` 或 `<section class="collapsible">` + 標題 bar + bullet list + card list | `templates/announcements/_card_list.html` |
| 公告 wrapper 帶 `data-max-ann-id` 給 JS 讀 | 同上 |
| AI 摘要 card 改成 `<section class="collapsible">` + bar + 控制區 | `templates/notes/index.html` L103-132 |
| 新 CSS：`.collapsible` / `.collapsible-bar` / `.collapsible-body` / chevron 動畫 | `static/css/collapsible_sections.css`（新檔） |
| 新 JS：auto-expand 判斷 + toggle handler + individual title toggle | `static/js/collapsible_sections.js`（新檔） |
| index.html `<head>` 加 link + 底部 script | `templates/notes/index.html` |

### E. JS 邏輯細節

```javascript
// pseudocode
function initCollapsibleSections() {
  initAnnouncementSection();
  initAiSummarySection();
}

function initAnnouncementSection() {
  const section = document.querySelector('.collapsible[data-section="announcements"]');
  if (!section) return;
  const maxAnnId = parseInt(section.dataset.maxAnnId || '0', 10);
  const today = new Date().toLocaleDateString('sv-SE'); // YYYY-MM-DD
  const stored = JSON.parse(localStorage.getItem('notes_ann_last_seen') || 'null');
  const shouldAutoExpand =
    !stored ||
    stored.date < today ||
    (stored.last_ann_id || 0) < maxAnnId;
  if (shouldAutoExpand) {
    setSectionState(section, 'expanded-all');
    localStorage.setItem('notes_ann_last_seen', JSON.stringify({date: today, last_ann_id: maxAnnId}));
  } else {
    setSectionState(section, 'collapsed');
  }
  // bind section bar click → toggle all
  section.querySelector('.collapsible-bar').addEventListener('click', () => toggleAll(section));
  // bind each title bullet click → toggle that card
  section.querySelectorAll('.ann-title-bullet').forEach(b => {
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleSingleCard(b.dataset.annId);
    });
  });
}

function initAiSummarySection() {
  const section = document.querySelector('.collapsible[data-section="ai-summary"]');
  if (!section) return;
  setSectionState(section, 'collapsed'); // always
  section.querySelector('.collapsible-bar').addEventListener('click', () => toggleAll(section));
}
```

### F. 邊界條件與錯誤情境

- **沒有公告**（`{% if announcements %}` 為 false）→ 整個 section 不渲染（現有行為、不動）
- **`max_ann_id` 為 0 或拿不到** → 直接 auto-expand（保守處理、新使用者就是要看）
- **localStorage 被禁用**（隱私模式）→ `localStorage.setItem` raise 沒接、用 try/catch 包；不寫成功就每次都 auto-expand（可接受）
- **JSON parse error** → 視同 `null`、auto-expand
- **公告被刪除導致 max_ann_id 降低** → stored.last_ann_id > current_max_ann_id → 不命中 auto-expand → 摺疊（正確：沒新東西）
- **關鍵字搜尋模式** (`current_keyword` 為 truthy) → 公告區跟 AI 摘要區整個不渲染（現有行為、不動）

### G. 測試策略

**Backend (pytest)**:
- 不動 model / route / endpoint，理論上不需要新 server-side test
- 加 1-2 條 template smoke：
  - `tests/test_notes_collapsible_sections.py`：notes index 載入後 HTML 含 `class="collapsible"` 兩處（公告 + AI 摘要）
  - 公告 section 帶 `data-max-ann-id` attribute

**Frontend (manual)**:
- 沒有 JS unit test framework → 靠本機 [[feedback_local_test_before_push]] + user 實機驗
- 驗證 case：
  1. 新使用者第一次進 → 公告 auto-expand、AI 摘要摺疊
  2. 同一天 reload → 公告摺疊、bullet 列表可見
  3. 隔天進 → 公告 auto-expand
  4. admin 新增公告後另一 user reload → auto-expand
  5. 點 section bar → toggle all
  6. 點個別標題 → 只該篇展開
  7. AI 摘要點 bar 展開 → 產生摘要 → 點 bar 收起 → 再展開、output 還在（DOM 沒被刪）
  8. 關鍵字搜尋模式 → 兩 section 都不出現

## Risks 與 mitigation

| Risk | 機率 | 影響 | Mitigation |
|---|---|---|---|
| user 不知道可以點 bar 展開 | 中 | 公告漏看 | chevron 圖示明顯 + hover affordance + auto-expand 觸發確保至少每天看一次 |
| localStorage 跨 device 不一致 → 換 device 又 auto-expand | 高 | 輕微（小煩） | 設計接受：換 device 第一次看就是想全展開、合理 |
| AI 摘要產生中（loading）使用者點收起 → 進度遺失感 | 低 | 中 | DOM 不刪、只 display:none、收起再展開狀態還在；不額外處理 |
| max_ann_id 計算錯誤導致 auto-expand 不觸發 | 低 | 中（漏看新公告） | template 帶 `max(a.id for a in announcements)`、簡單可驗 |
| `<details>` 與自訂 JS 互打架 | 低 | 中 | 不用 `<details>`、用純 `<section>` + 自己控 class 切 |

## 部署計畫

1. 開 branch `feat/notes-collapsible-sections`（已開）
2. 實作 + 本機測試（按 [[feedback_local_test_before_push]]）
3. user 本機驗 OK → ff-merge main + push
4. Zeabur 自動 deploy（無 schema 改動、不需 inline ALTER）
5. user prod 實機驗

## 未來延伸（不在本 scope）

- 5 排 filter tabs 用同一個 `.collapsible` pattern 摺疊（5/15 mockup 已畫）
- 公告 bar 加未讀計數 badge（如果發現 auto-expand 不夠用）
- 個別公告摺疊狀態跨 session 記憶（如果 user 反映想要）
- 公告排序：未讀置頂（如果上未讀計數）
