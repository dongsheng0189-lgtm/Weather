# AI 摘要 clickable links + back-restore — Spec

**Branch**: `feat/ai-summary-clickable-links`
**Date**: 2026-05-26
**Scope**: notes AI 摘要 markdown render + sessionStorage 還原 + checklist item anchor jump

## 問題

`/notes/` 的 AI 摘要目前是 pre-wrap 純文字 markdown，每條 bullet 無法直接點到對應 note。使用者交接 review 時需要邊念邊看圖、邊查狀況、邊討論，要去查某條的細節得：

1. 記住 note title / id
2. 滑到 notes list
3. 手動找
4. 點進去看
5. 看完返回時摘要 scroll 位置可能掉

整個 flow 被打斷、回來找不到剛剛念到哪。

## 設計

3 個改動方向：

### 1. Pipeline 輸出 markdown link

`ai_local.py` bucket item dict 加 `note_id`（一般筆記）+ `item_id`（checklist item），`_format_cluster` 把代表那筆寫成 markdown link。Cluster ≥2 筆時，尾註的 author 名單每個都各自 link 到自己那筆 note。

範例輸出：
```markdown
## 🔧 機台
### ❌ 今天未完成
- [【婉婷】冰箱漏水 — 已約23號開刀](/n/986) 🟡（3 筆 / [小明](/n/992)、[阿華](/n/995)）
- [【小華】機台 5 號當機 — 重開機 OK](/n/987)
```

Checklist item 用 fragment anchor：`/n/986#item-42`。

### 2. Frontend markdown render + sessionStorage 還原

`templates/notes/index.html` 加：

- CDN script: `marked.min.js` (~30KB) + `dompurify.min.js` (~20KB)
- `generateSummary` poll 拿到 result 後：`output.innerHTML = DOMPurify.sanitize(marked.parse(md))`，取代現在的 `textContent`
- 加 `#summary-timestamp` 顯示「此摘要產生於 14:30」
- sessionStorage key: `notes_summary_<store>_<business_day>` (e.g. `notes_summary_B_today`)
- value 結構：`{markdown, generated_at_iso, range_label, business_day_date}`
- generate 成功後 save；`DOMContentLoaded` 時若 key 存在 → restore + 顯示「已恢復 14:30 摘要」hint

### 3. Editor 支援 `#item-<id>` anchor

`templates/notes/editor.html` DOMContentLoaded handler：
- 讀 `location.hash`，match `^#item-(\d+)$` 抓 id
- `querySelector('[data-item-id="<id>"]').scrollIntoView({behavior:'smooth', block:'center'})`
- 加 CSS class `flash-hl`：2 秒黃色 fade animation 提示「就是這條」

## 資料流

```
產生摘要：
  user click → POST /notes/ai/summary
  → _run_local_summary (gevent.threadpool worker, 已修 child watcher)
  → summarize_local 輸出含 [text](/n/id) 的 markdown
  → AiTask.result 存 markdown string
  → frontend poll → DOMPurify(marked(md)) → innerHTML
  → sessionStorage.setItem('notes_summary_<store>_<day>', JSON.stringify({...}))
  → display timestamp

點 bullet：
  <a href="/n/986"> click → 標準 navigation
  /n/986#item-42 → editor.html scroll to item-42 + flash-hl

返回：
  case A: bfcache hit → DOM 原狀、無 reload
  case B: bfcache miss → page reload
    → DOMContentLoaded → sessionStorage.getItem(...)
    → 若存在 → hydrate output + timestamp + 顯示「已恢復」
    → 若 > 1 小時 → 額外加紅色「⚠ 超過 1 小時」hint
```

## 改動清單

| 檔案 | 內容 | ~行 |
|---|---|---|
| `notes/ai_local.py` | bucket dict 加 `note_id`/`item_id`；`_format_cluster` 改寫 markdown link；cluster tail 每個 author 各自 link | 30 |
| `templates/notes/index.html` | marked/DOMPurify CDN；`generateSummary` 改 innerHTML render；timestamp UI；sessionStorage save/restore | 50 |
| `templates/notes/editor.html` | DOMContentLoaded hash anchor handler；flash-hl class 套用 | 15 |
| inline CSS (editor.html 或 base.html) | `.flash-hl` 2 秒黃色 fade keyframes | 5 |
| **Total** | | **~100** |

## 邊界情境

| 情境 | 行為 |
|---|---|
| marked / DOMPurify CDN 載入失敗 | try/catch，fallback 既有 `output.textContent = md` pre-wrap 渲染，link 失效但摘要可讀 |
| sessionStorage 滿（QuotaExceededError）| catch 後靜默不存（下次重新 generate） |
| sessionStorage disabled（隱私模式）| 同上 |
| Stale summary（generated > 1h）| Timestamp 旁加紅色 ⚠ 提醒 |
| `#item-42` anchor 對不到 item（已刪/權限不同）| `querySelector` 回 null，silent，editor 正常顯示頁頂 |
| Title 含 markdown meta 字 `]`/`(`/`)` | marked.escape 自動處理；多重 layer DOMPurify 砍 `<script>`/`on*` |
| Author / content 內惡意 HTML（XSS） | DOMPurify 預設 strict mode 已擋；不做白名單擴充 |
| 跨店 link 點到無權限 note | editor 既有 store 權限檢查 → 404，符合預期 |
| Cluster N 筆但同人寫多次 | author 去重後仍只看到一個 author link；多人時各自 link |
| Bfcache miss 但 sessionStorage 也 miss | 顯示空白 + 「請按產生摘要」（既有行為） |

## 不做（YAGNI）

- 不做 modal 預覽（方案 C）— 既有 navigation + sessionStorage 已解決「不被打斷」核心
- 不做「已 review tick」per-bullet 標記 — user 沒提
- 不做歷史摘要永久存檔 — 既有「💾 儲存為筆記」可用
- sessionStorage 不跨分頁同步 — 每分頁自己有自己的 review session
- 不改 `_run_local_summary` 函式簽名 — pipeline 輸出仍是純 markdown 字串

## Capacity

- CDN: marked + DOMPurify 各一次（瀏覽器 cache）；本機 ~50KB JS
- sessionStorage: 每筆摘要 ~5-30KB markdown，每個 store/day 一筆，整個 session 上限 5-10MB（browser 預設）— 完全在預算內
- Server: 0 變動（pipeline 輸出格式相容，AiTask schema 不動）

## 測試

**Manual local test（使用者自跑）：**
1. `/notes/` 產生摘要 → 確認 markdown link 變藍色超連結、可點
2. 點一條 bullet → 跳轉 `/n/<id>` editor
3. 編輯或不編輯都行，按 browser back
4. 摘要還在原位置（bfcache 命中）或瞬間恢復（sessionStorage hydrate）+ timestamp 顯示
5. checklist item link → editor 滾動到該 item、黃色 flash 動畫
6. 開新分頁（無 sessionStorage 共享）`/notes/` → 摘要區為空、按產生摘要 → 正常產生

**回歸測試**：
- 既有 `生成摘要 → 儲存為筆記` flow 不受影響（仍取 markdown plain text）
- AI 摘要 fullscreen toggle ⛶ 仍正常

## 部署

- 純 frontend + ai_local.py pipeline 輸出格式微調，無 schema migration
- 無新 Python dep（marked/DOMPurify 都 frontend CDN）
- sessionStorage 跨 deploy 沒問題（client-side state，server 重啟不影響）
