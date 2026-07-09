# 站內判決書檢視器（vetting judgment viewer）— 設計

日期：2026-07-09
模組：`app_unified/vetting`
分支：`feat/vetting-judgment-viewer`

## 背景與問題

人員查詢（vetting）司法院結果卡片已能在「摘要」重現司法院列表頁的命中人名黃底。
但使用者點「→ 開原判決書」會跳到**司法院自己的判決書內頁**（`data.aspx`），
實測該頁**完全不上黃底**（原始 HTML 0 個 highlight、`functions.js`/`UrlShare.js` 無任何 highlight JS），
連司法院自己都沒在內頁上色。使用者想要「打開判決書全文時人名也黃底、且能像 notes 查詢一樣跳到命中處」。

因為那頁是司法院跨網域網站、我們無法對它的 DOM 上色，所以改成**把判決書全文抓進我們自己的頁面**、
由我們自己上色與導覽。這是新功能（加值），不是改司法院。

## 目標

1. 每筆司法院結果卡片新增「📖 站內檢視」，開啟我們自己的判決書全文頁，命中人名黃底。
2. 提供命中跳轉（沿用 notes 的 `search-hit-widget`）：`🔍 k/N 命中`、`▶ 下一個`（循環置中）、`← 返回`。
3. 全程安全（防 XSS）、容量友善（不預存、不增加依賴）。

## 非目標

- 不修改司法院的頁面（跨網域，做不到）。
- 不做站內編輯/標註/收藏判決書。
- 不保留判決書 HTML 排版（純文字 + 保留間距即可，忠實排版走「開原判決書」）。
- 複姓（如「歐陽」）暫以單字姓處理，之後再擴充。

## 使用者流程

1. 查詢完成 → 司法院結果卡片。每張卡片按鈕順序：**`📖 站內檢視`（前）** → **`→ 開原判決書`（後）**。
2. 點「站內檢視」→ `window.open` 開**新分頁** `GET /vetting/judgment?qid=<qid>&i=<index>`（原查詢結果頁不受影響）。
3. 新分頁載入判決書全文（純文字、保留間距換行），命中人名黃底。
4. 右下角浮動 widget：`🔍 k/N 命中`、`▶ 下一個`（捲到該命中置中、目前命中加外框、循環）、`← 返回`（關閉分頁）。

## 命中比對規則（兩段式，避免雜訊）

搜尋名字 = 該筆查詢輸入的名字（例：`王小明`），姓 = 名字第一個字（`王`）。

1. 先在判決書內文找**完整名字**（`王小明`）字面出現。
2. **完整命中數 ≥ 1** → 只標記/跳轉完整名字（不碰遮罩姓；判決書常有大量不相干 `王○○`，會雜）。
3. **完整命中數 == 0**（當事人全被遮罩）→ fallback **模糊比對姓**：標記 `王○`、`王○○`、`王○○○`
   （遮罩符號 `○` = U+25CB，與現有 `MASKED_NAME_RE = [一-龥]○{1,3}` 一致）。每個遮罩名算一個命中。

> 門檻採 A 案：完整命中 ≥1 用完整、=0 才 fallback。

## 架構與元件

### 後端 `app_unified/vetting/`

**新路由** `GET /vetting/judgment`（`@vetting_bp.route`，登入 + vetting feature gate，與現有 `/vetting/` 一致）
- 參數：`qid`（查詢 state id）、`i`（第幾筆，int）。
- 從既有 state（`_take_state`/peek，Redis 或 process dict，TTL 5 分）取 `judicial[i]` 的 `detail_url` 與查詢名字。
  - **URL 來自 server 端 state、不吃 client 傳的網址 → 無 SSRF。**
- state 過期/查無/index 越界 → 回友善錯誤頁（HTTP 200 + 提示「查詢已逾時，請重新查詢」，或 410）。
- Lazy 抓：`make_session()` → `GET detail_url`（帶 `User-Agent` + `Referer=HOME_URL`，已驗證冷 session 即可）。
  - 抓取失敗（連線/被擋）→ 友善錯誤頁。
- 交給 `judicial.extract_judgment_text()` 抽內文純文字（保留換行）。
- 交給 `judicial.build_highlighted_judgment(text, name)`：兩段式比對 → 回 `(safe_html, hit_count)`。
- `render_template("vetting/judgment.html", body_html=safe_html, name=name, hit_count=…)`。

**`judicial.py` 新函式**
- `extract_judgment_text(html) -> str`
  - 用 BeautifulSoup 找判決書內文容器（實作時 recon 確認 selector，例如主文/事實/理由所在 div），
    以換行分隔（`<br>`/`<p>`/`<div>`/`<tr>` 邊界 → `\n`），回純文字（保留段距）。
  - 找不到容器 → 退回整頁 `get_text("\n")`。
- `build_highlighted_judgment(text, name) -> (str, int)`
  - **先全部 `html.escape(text)`**（零注入面）。
  - 兩段式決定 pattern：
    - `full = re.escape(name)`；先數 `text` 內完整命中。
    - `≥1` → 用完整 pattern；`0` → 用姓 fallback pattern `re.escape(surname) + "○{1,3}"`。
  - 對 escape 後字串，用該 pattern 把每個命中包成
    `<span class="vet-hl" id="vhit-{k}">…</span>`（k 從 0 遞增）。回 `(html, k)`。
  - 注意：對 escape 後的文字做 regex，pattern 內容是中文/○ 不含被 escape 的字元，安全。

### 前端 `app_unified/templates/vetting/`

**`index.html`（改）**
- 司法院卡片：在「→ 開原判決書」**之前**插入
  `<button ... onclick="openJudgment('{{qid}}', idx)">📖 站內檢視</button>`
  （需要把 `query_id` 帶進 renderResult；目前 `d` 有 `query_id`，forEach 用 index）。
- `openJudgment(qid, i)` = `window.open('/vetting/judgment?qid='+encodeURIComponent(qid)+'&i='+i, '_blank')`。

**`judgment.html`（新，繼承 base.html）**
- 內容區：`<div style="white-space:pre-wrap; ...">{{ body_html|safe }}</div>`
  （`body_html` 已於後端 escape + 只含自家 `vet-hl` span，`|safe` 安全）。
- `.vet-hl { background:#ff3; color:#000; ... }`（與 index 一致）；`.vet-hl.current { outline:2px solid #e67e22; }`。
- 浮動 widget 沿用 notes `search-hit-widget` 結構（`🔍 k/N 命中`、`▶ 下一個`、`← 返回`）。
- JS：蒐集 `#vhit-*`，`▶` 循環 `scrollIntoView({behavior:'smooth',block:'center'})` + 切換 `.current` + 更新計數；
  `← 返回` = `window.close()`（開新分頁時可關；關不掉則提示自行關閉分頁）。
  - 命中數 0 → widget 顯示「0 命中」或隱藏 `▶`，仍正常顯示全文。

## 資料流

```
卡片[📖 站內檢視] --window.open--> GET /vetting/judgment?qid&i
  → state 反查 detail_url + name (no SSRF)
  → make_session().get(detail_url)               # lazy, cold session OK
  → extract_judgment_text(html)                  # 純文字保留換行
  → build_highlighted_judgment(text, name)       # escape → 兩段式 → vet-hl spans
  → render judgment.html
前端 JS: 蒐集 #vhit-* → ▶ 下一個循環置中 / 計數 / ← 返回關分頁
```

## 安全

- **XSS**：內文**全部 escape**，只注入自家固定字面量 `<span class="vet-hl" id="vhit-k">`；不保留任何外站標籤/屬性 → 無注入面。
- **SSRF**：抓取的 `detail_url` 來自 server 端既有 state（由我們搜尋產生），不接受 client 傳入的任意 URL。
  - 加保險：抓取前驗證 `detail_url` host == `judgment.judicial.gov.tw` 且 path 以 `/FJUD/` 開頭。
- **權限**：路由沿用 vetting feature gate + 登入（與 `/vetting/` 相同）。

## 容量（[[feedback_capacity_check]]）

- Lazy：只有使用者實際點檢視才多打 1 次司法院 GET（~11–50KB）+ 一次 escape/regex。使用者一次查詢通常只看 1–2 篇。
- **不**預存 15 篇判決書到 state（避免每次查詢 serialize/encrypt 大 blob）。
- 無新依賴（只用既有 beautifulsoup4 + 標準庫 re/html）。

## 測試（TDD）

`tests/test_vetting_judicial.py` 增加：
- `extract_judgment_text`：從 detail fixture 抽出含換行的純文字、不含標籤。
- `build_highlighted_judgment`：
  - 完整名字命中 ≥1 → 只包完整名字、`vet-hl` 數 = 完整出現數、**不**包遮罩姓。
  - 完整名字 0 命中 → fallback 包 `王○/王○○/王○○○`、計數正確。
  - XSS：內文含 `<script>`/`onerror` → 輸出全 escape、只有自家 `vet-hl` span。
  - `id="vhit-0..n"` 連續遞增。
- 需要一份判決書 detail fixture（可用既有 `judicial_detail.html`，或補一份含完整名字 + 遮罩名的）。

## 未決/實作時 recon

- 判決書內文容器的實際 selector（`extract_judgment_text` 用）— 實作時抓 live 頁確認。
- `window.close()` 在部分瀏覽器對非 script 開啟的分頁可能無效 → 用 `window.open` 開啟即可 close；備援顯示提示。
- state TTL 5 分：若使用者看結果超過 5 分才點檢視，state 可能過期 → 友善錯誤頁提示重查（v1 接受；必要時之後延長 TTL）。
