# 標籤篩選獨立化設計（Tag filter as independent mode）

日期：2026-06-11
分支：`feat/tag-filter-independent`

## 問題

notes 列表 `index()` 的標籤篩選被放在 combined 分支裡，跟 category / shift / status / **時間 range** 一起 AND，而且 range 未帶時預設 `today`。結果：選一個標籤＝「今天 updated 過 + 該標籤」，昨天以前的同標籤筆記被時間窗藏起來。

相對地，關鍵字 `q` 是互斥模式——有關鍵字時只比對 store + 文字，忽略所有其他條件。兩者行為不一致。

## 目標

標籤變成跟關鍵字同級的**獨立互斥模式**：選一個標籤＝只看「該店 + 該標籤」的全部歷史，忽略時間 / 種類 / 班別 / 狀態。

## 設計：三種互斥模式

| 模式 | 觸發 | 後端套用 | UI（進階篩選區內顯示） |
|---|---|---|---|
| **關鍵字** | `q` 有值 | store + 文字比對（排除 AI 摘要）。忽略時間/種類/班別/狀態/標籤 | 只有 🔍 關鍵字框 + 取消 |
| **標籤**（新） | `tag` 有值且無 `q` | store + 該標籤（note-level ∪ item-level UNION）。忽略時間/種類/班別/狀態 | 只有 🏷️ 標籤 chip 列 + 清除過濾器 |
| **一般**（預設） | 兩者皆無 | category + shift + status + range(預設 today) AND 組合 | 關鍵字框 + 標籤列 + 全部 6 排 tab |

- **兩者皆無**時：全部篩選器可見、彼此 AND 一路關聯（不額外隱藏）。
- **用關鍵字或標籤**時：其他全隱藏，只剩選中的那個 + 清除過濾器。
- **防呆**：`q` 與 `tag` 同時有值（只有手動湊 URL 才會發生）→ 關鍵字優先（跟現狀一致）。
- **排序**：標籤模式跟關鍵字模式一樣——backend 照套 `sort_order`（預設「照更新」），但排列 tab 在 UI 隱藏、不可改。一致行為，不另外處理。

## 改動範圍

### 1. `notes/routes.py` `index()`（約 381–410 行）
把 `tag_id` 從 combined 分支抽出成獨立 `elif`：

```
if keyword:                # 現狀不動
    active_filter = 'keyword'; keyword_mode = True
    query = query.filter(Note.is_ai_summary.isnot(True))
elif tag_id:               # ← 新增分支
    active_filter = 'tag'
    # note-level ∪ item-level tag UNION subquery（沿用原本 combined 分支那段）
    query = query.filter(Note.id.in_(...))
    # 不預設 today、不套 category/shift/status/range
else:                      # tag 已從這裡移除
    active_filter = 'combined'
    category + shift + status + range(預設 today)
```

排序與分頁邏輯（412–418）維持：keyword_mode 走 in-memory `_PyPagination`，其餘走 SQL `paginate`。標籤模式走 SQL paginate（與 combined 同路）。

### 2. `templates/notes/index.html`
- 標籤列（約 191 行 `tag-filter-row`）：包進 `{% if not current_keyword %}` → 關鍵字模式不顯示。
- 關鍵字 form（約 180–188 行）：包進 `{% if not current_tag %}` → 標籤模式不顯示。
- 6 排 tab 區塊（約 193 行的 `{% if not current_keyword %}`）：改成 `{% if not current_keyword and not current_tag %}` → 標籤模式也隱藏。
- 那些 tab 連結裡的 `tag=current_tag`（198/211/220/229/244 行）：拿掉（tab 只在無 tag 時出現，本來就是空，純清理避免誤導）。
- `active_filter_count`（164–171 行）：邏輯不變；標籤模式下只剩 tag 被計入（其他參數因導向乾淨 URL 而不存在）→ 自然顯示 (1)。

### 3. `static/js/tag_system.js` `makeChip`（約 290–298 行）
點標籤改成導向**乾淨 URL**：只保留 `store` + `tag`，丟掉 `q`/`category`/`shift`/`range`/`status`/`sort`。再點一次同標籤 → 只剩 `store`（回預設模式）。

```js
chip.addEventListener('click', function() {
  var u = new URL(window.location.origin + window.location.pathname);
  var store = new URLSearchParams(window.location.search).get('store');
  if (store) u.searchParams.set('store', store);
  if (t.id !== currentTagId) u.searchParams.set('tag', t.id);
  window.location.href = u.toString();
});
```

### 4. `sw.js`
bump `CACHE_NAME`（改了 tag_system.js，避免 user 看舊版；踩雷 #5）。

## 測試

新增測試（`pytest tests/` 一致）：
- 建一筆「updated_at 為 10 天前 + 帶某標籤」的 note，請求 `?tag=<id>`，斷言該 note **出現在結果**（證明標籤模式不再被 today 時間窗藏住）。
- 補一條：同時帶 `?q=foo&tag=<id>` 時走關鍵字模式（tag 被忽略），驗證防呆優先序。
- 既有 index 篩選相關測試跑過不退步。

## 不在範圍

- note 卡片上的標籤 chip 是純 `<span>`（無 href），不需改。
- 卡片點擊帶 filter context 給編輯器（返回用）邏輯不變，標籤模式下其他參數自然為空，會正確帶回標籤模式。
- 不動 keyword 既有行為。
