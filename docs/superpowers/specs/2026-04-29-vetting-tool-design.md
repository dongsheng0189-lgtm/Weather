# 人員查詢 (Safety Vetting) 設計

**日期**：2026-04-29（修訂版：2026-04-29 實機 recon 後重整）
**模組名稱**：vetting（內部 key）/ 人員查詢（UI label）
**初期權限**：admin 帳號（is_root_admin）測試；未來放給有 `vetting` feature 的使用者

## 動機

協助使用者對特定人員做即時安全性查詢，整合三個公開資料源：
1. **司法院刑事判決**（搜尋 + 深度掃描關鍵字）
2. **法務部調查局通緝/嫌疑人名單**（按姓名匹配，含遮蔽身分證可人工比對）
3. **警政署外逃通緝犯**（按姓名 + 身分證查詢）

不存查詢結果、不留紀錄；每次查詢即時打外部站，結果只在當下 response 回去。

## 範圍

- **輸入**：姓名 + 身分證（後者主要給 NPA 用，司法院/MJIB 只用姓名）
- **輸出**：三段結果
  - 司法院命中清單（案由 / 字號 / 日期 / 法院 / 命中關鍵字）
  - MJIB 命中清單（姓名 / 英文名 / **遮蔽身分證**（人工比對） / 出生年 / 照片 / 詳情連結）
  - NPA 命中清單（罪名 / 通緝資訊 / 詳情連結）
- **不做**：歷史紀錄、PDF 匯出、批次查詢、後端結果存檔（YAGNI）

## 模組隔離（依專案規則）

- 新 blueprint：`vetting/__init__.py` + `vetting/routes.py`
- 新 URL prefix：`/vetting/`
- 新 templates 目錄：`templates/vetting/`
- **不需要新 DB 表**（不存資料）
- **不需要 R2 儲存**（不存附件）
- features.py 新增 vetting entry
- `vetting_bp.before_request` 統一呼叫 `user_has_feature("vetting")` 檢查
- admin (`is_root_admin`) bypass，初期實質上等於 admin only

## 各外部站結構（實機 recon 結果）

### 1. 司法院 `judgment.judicial.gov.tw/FJUD/`
- 首頁：`GET /FJUD/default.aspx` → ASP.NET form (有 `__VIEWSTATE`/`__EVENTVALIDATION`/`judtype`/`whosub` 等 hidden 欄位)
- 送出：`POST /FJUD/default.aspx`，body 含原本 hidden 欄位 + `txtKW=<姓名>`
- 回應：HTML 內含 `<iframe src="qryresultlst.aspx?ty=JUDBOOK&q=<hash>">`
- **無驗證碼**（簡化大關鍵發現）
- 結果列表：`GET /FJUD/qryresultlst.aspx?ty=JUDBOOK&q=<hash>` → 表格 `<tr>` 每兩列一筆
  - 奇數列：序號 / 「法院 + 案號 + 案件類型」 + 日期 + 案由
  - 偶數列：判決書摘要文字
  - 詳情連結：奇數列裡的 `<a href="data.aspx?ty=JD&id=...">`
- 詳情頁：`GET /FJUD/data.aspx?ty=JD&id=...` → 完整判決書 HTML
- **判決書內已不含遮蔽身分證**（隱私強化），只有遮蔽人名（如 `王○○`）與遮蔽地址。**Spec 原本「ID 比對」拿掉**

### 2. MJIB `www.mjib.gov.tw/Crimes/Crimes_List`
- 直接 `GET` 取整頁，含分頁但首頁已有 ~10 筆
- **無驗證碼**
- 列表是 `<div class="card crimes-card ...">`，每張卡含：
  - `<img src="/FileUploads/prisoner_pic/...">` 照片
  - `<h5 class="card-title">姓名</h5>`
  - `<div>英文名</div>`
  - `<div>遮蔽身分證 e.g. A12336****</div>` ← **可拿來人工比對**
  - `<div>出生年</div>`
  - `<a href="/Crimes/Crimes_Detail?uid=N">` 詳情連結
- 過濾：Python 端依姓名做 `in` 比對

### 3. NPA `eze8.npa.gov.tw/NpaE8ServerRWD/`
- 首頁：`GET /CE_Query.jsp` → 含 `<meta name="_csrf" content="...">` + `<input id="_key">`
- **有驗證碼**：頁面 JS 在 init 時呼叫 `reloadCaptcha("1")` 從某 endpoint 拿驗證碼
- 送出：JS 動態加 hidden inputs 後 form submit 到 `POST /NpaE8ServerRWD/doCEQuery`，body：
  - `QS_NAME` + `QS_NAMEA`（雙寫）
  - `QS_ID` + `QS_IDA`（雙寫）
  - `answer` + `answerA`（驗證碼，雙寫）
  - `Authorization` 欄位（值 = `Bearer ` + localStorage 的 `_tok`）
  - `_csrf` token（由首頁 meta 取）
- 規則：姓名/ID 擇一（系統不檢核），但本實作要求兩個都送
- **NPA 是唯一需要驗證碼接力的來源**

## 架構

```
┌─ Browser ───────────────────────────────────────────┐
│ /vetting/  (姓名 + 身分證 表單)                       │
│   ↓ POST /vetting/start  {name, id}                  │
│   ↓                                                  │
│ /vetting/captcha (NPA 驗證碼)                        │
│   ↓ POST /vetting/submit  {query_id, captcha}        │
│ /vetting/result (三段結果)                           │
└──────────────────────────────────────────────────────┘
                ↕ HTTP only (requests + BeautifulSoup)
┌─ External ──────────────────────────────────────────┐
│ judgment.judicial.gov.tw/FJUD/  (no captcha)        │
│ www.mjib.gov.tw/Crimes/         (no captcha)        │
│ eze8.npa.gov.tw/NpaE8ServerRWD/ (captcha relay)     │
└──────────────────────────────────────────────────────┘
```

純 HTTP，不用 Playwright/Chromium（Zeabur 容器跑 OK）。

## UI 流程（三步驟 SPA）

### Step 1 — 姓名 + 身分證輸入 (`GET /vetting/`)
- 兩個 input：姓名（必填）+ 身分證（必填，給 NPA）
- 「開始查詢」按鈕

### Step 2 — NPA 驗證碼接力 (`POST /vetting/start`)
後端做：
1. `GET` NPA 首頁 → 取 `_csrf` meta + `_key` + cookie
2. 嘗試取得 NPA 的 `_tok`（看是否 cookie 帶、或某 endpoint 回）
3. `GET` NPA 驗證碼 image endpoint → bytes
4. 把 (cookies, _csrf, _key, _tok, captcha_bytes) + 使用者輸入的 (name, id) 暫存到 process-local dict（`query_id` key、TTL 5 分鐘）
5. **同步**地：先打 司法院 + MJIB（兩者無驗證碼，可平行先做）並把結果暫存
6. 回 JSON：`{query_id, captcha_b64}` 給前端顯示驗證碼

> **設計選擇**：把 司法院 + MJIB 的查詢提前在 `/start` 階段做完，使用者輸驗證碼期間結果就回來了。也可以放到 `/submit` 階段，但會讓使用者多等幾秒。

### Step 3 — NPA 送出 + 結果頁 (`POST /vetting/submit`)
帶 `{query_id, captcha_text}`：
1. 從 query_state 取出 cookies + tokens + 已查好的 司法院/MJIB 結果
2. POST `doCEQuery` 帶 captcha + name + id + token + csrf
3. 解析 NPA 回應
4. 三段結果整合回前端

## 結果頁顯示

```
A. 司法院刑事判決（N 筆，⚠ 為命中關鍵字）
   1. ⚠ 詐欺
      字號：113上易123  日期：113/03/15  法院：高等法院
      命中關鍵字：詐欺 / 有期徒刑 / 定讞
      [→ 開原判決書]
   ...

B. 法務部調查局通緝/嫌疑人名單
   ⚠ 命中 1 筆：
      姓名：王○○ (LIN, KEN-TI)
      遮蔽身分證：A12336****  ← 自行比對
      出生年：1978
      [照片] [→ 詳情]

C. 警政署外逃通緝犯
   ✅ 查無 / ⚠ 命中 N 筆：
      罪名：xxx
      [→ 詳情]
```

## 後端模組分工

| 檔案 | 責任 |
|------|------|
| `vetting/__init__.py` | blueprint object |
| `vetting/routes.py` | HTTP routes + query_state dict |
| `vetting/http_client.py` | `make_session()` 真人 headers + `throttle_sleep()` |
| `vetting/judicial.py` | 司法院 search + iframe + 結果 parse + 詳情頁深掃 |
| `vetting/mjib.py` | MJIB Crimes_List GET + 姓名過濾 + 解析卡片 |
| `vetting/npa.py` | NPA 首頁取 token + captcha image + doCEQuery POST + 結果 parse |

## 暫存 query_state

簡單的 process-local dict，5 分鐘 TTL（同先前設計）。

## 錯誤處理

- 各外部站 timeout / 5xx → 該段顯示「無法取得」，不影響其他段
- captcha 錯誤（NPA） → 回前端「驗證碼錯誤，請重整圖片再試」
- query_id 過期 → 回「查詢逾時（5 分鐘），請重新開始」

## 防誤判機器人 / 不被外部站擋

（內容同前一版 spec 的「防誤判機器人」段落）

- 真人 headers (`vetting/http_client.py`)
- 每筆詳情頁間 `throttle_sleep()`
- Flask-Limiter：每使用者每分鐘最多 1 次完整查詢
- 偵測 429/403 立即停止
- 不平行打 / 不重試 / 不存資料

司法院 + MJIB 沒驗證碼，唯一通過驗證碼的證明是 NPA — 但每次只查一個人、頻率極低，被擋的機率仍極低。

## PII / 安全

- **不存查詢紀錄**：不寫 DB、不寫檔
- 既有 admin log 只記「執行人員查詢」+ 時間，**不記姓名/ID**
- 結果頁瀏覽器不快取（既有 `Cache-Control: no-store` 全站套用）
- captcha image 用 base64 inline 顯示，**不存檔**
- 入口 feature gate：未授權 abort(404)（看不見即不存在）
- 身分證在 query_state 裡暫存最多 5 分鐘，過期即 GC（不寫檔、不入 log）

## 測試

`tests/test_vetting_*.py`：

- `test_vetting_routes.py`：feature gate + start/submit endpoints
- `test_vetting_http.py`：headers + throttle
- `test_vetting_judicial.py`：parse_home_state / parse_results / scan_detail_html
- `test_vetting_mjib.py`：parse_list / search_by_name_in_html / 遮蔽 ID 抓取
- `test_vetting_npa.py`：parse_home_tokens / parse_results

每個 parser test 用本地 fixture HTML（`tests/fixtures/vetting/*.html`），**不打真實外部站**。

## 已決策

- 純 HTTP，不用 Playwright/Chromium
- 同步處理（30 秒內）不開背景 job
- 不存任何查詢紀錄
- 司法院**無驗證碼** → 流程簡化（recon 後修正）
- MJIB **無驗證碼** → GET + 過濾
- NPA **有驗證碼** → 是唯一需要接力的來源
- 司法院判決書內**已不放遮蔽身分證**（隱私強化），ID 比對改靠 MJIB 的遮蔽 ID
- 入口 admin only（`is_root_admin` bypass）；未來 feature 開關控管

## 不做（YAGNI）

- 查詢歷史 / 收藏
- PDF 匯出
- 多筆姓名批次
- Pandas 匯總
- 把 MJIB/NPA 照片下載到 server（直接連結原站）
- 自動驗證碼解碼（2Captcha 等）
- Playwright fallback
- 多 worker 共享 query_state
