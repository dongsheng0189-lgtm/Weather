# 人員查詢 (Safety Vetting) 設計

**日期**：2026-04-29
**模組名稱**：vetting（內部 key）/ 人員查詢（UI label）
**初期權限**：admin 帳號（is_root_admin）測試；未來放給有 `vetting` feature 的使用者

## 動機

協助使用者對特定姓名做兩項即時安全性查詢：
1. 司法院刑事判決（搜尋 + 深度掃描關鍵字 + 顯示遮蔽身分證讓使用者自行比對）
2. 法務部調查局通緝/嫌疑人名單（按姓名匹配）

不存查詢結果、不留紀錄；每次查詢即時打外部站，結果只在當下 response 回去。

## 範圍

- 輸入：**僅姓名**（不收身分證——不做 ID 比對，使用者自己看判決書內遮蔽 ID 比）
- 輸出：兩段結果
  - 司法院命中清單（案由 / 字號 / 日期 / 法院 / 命中關鍵字 / 遮蔽 ID）
  - MJIB 命中清單（罪名 / 公告照片 / 詳情連結）
- 不做：歷史紀錄、PDF 匯出、批次查詢、後端結果存檔（YAGNI）

## 模組隔離（依專案規則）

- 新 blueprint：`vetting/__init__.py` + `vetting/routes.py`
- 新 URL prefix：`/vetting/`
- 新 templates 目錄：`templates/vetting/`
- **不需要新 DB 表**（不存資料）
- **不需要 R2 儲存**（不存附件）
- features.py 新增：
  ```python
  {
      "key": "vetting",
      "label": "人員查詢",
      "emoji": "🛡️",
      "url": "/vetting/",
      "enabled": True,
  }
  ```
- `vetting_bp.before_request` 統一呼叫 `require_feature("vetting")`
- admin (`is_root_admin`) bypass，初期實質上等於 admin only

## 架構

```
┌─ Browser ───────────────────────────────────────────┐
│ /vetting/ (姓名表單)                                 │
│   ↓ POST /vetting/start  {name}                      │
│ /vetting/captcha (顯示 captcha image)                │
│   ↓ POST /vetting/submit  {query_id, captcha_text}   │
│ /vetting/result (兩段結果)                           │
└──────────────────────────────────────────────────────┘
                ↕ HTTP only (requests + BeautifulSoup)
┌─ External ──────────────────────────────────────────┐
│ judgment.judicial.gov.tw  (有 captcha)              │
│ www.mjib.gov.tw/Crimes/Crimes_List  (無 captcha)    │
└──────────────────────────────────────────────────────┘
```

**為何純 HTTP（不用 Playwright）**：
- Zeabur 容器無 GUI，Playwright headless 又重又難維運
- 司法院 captcha 用「網頁接力」UX（後端拿圖、前端輸入），不需 GUI 自動化
- MJIB 無 captcha → 直接 GET + parse

## UI 流程

### Step 1 — 姓名輸入頁 (`GET /vetting/`)
- 單一 input：姓名
- 提示文字：「將同時查詢司法院刑事判決與調查局通緝名單」
- 「開始查詢」按鈕

### Step 2 — 司法院驗證碼接力 (`POST /vetting/start`)

後端做：
1. `GET https://judgment.supreme.court.gov.tw/wkw/commonQuery.jsp`
2. 從 HTML 抓出表單必要 hidden 欄位（具體名稱以實機抓為準，JSP 站常見：seq/csrf/session token）
3. 抓 captcha image URL → `GET` 圖片 → 取 bytes
4. 把 cookie + 必要欄位 + captcha image bytes 暫存到記憶體 dict（key = 隨機 `query_id`，TTL 5 分鐘自動清）
5. 回 JSON：`{query_id, captcha_b64}`

前端：
- 顯示 captcha image（`<img src="data:image/jpeg;base64,...">`）
- 「重整」按鈕 → 重打 `/vetting/start`
- 輸入框 + 送出

### Step 3 — 結果頁 (`POST /vetting/submit`)

帶 `{query_id, name, captcha_text}`：

1. 從記憶體 dict 取出 cookie/viewstate
2. POST 司法院搜尋表單（`姓名`、`law_class=刑事`、captcha）
3. 解結果頁 → N 筆 `{案由, 字號, 日期, 法院, detail_url}`
4. 對每筆 GET 詳情頁 → 解析出：
   - 關鍵字命中：`詐欺` / `洗錢` / `有期徒刑` / `定讞`
   - 馬賽克 ID：regex `[A-Z][12]\d{2,3}\*+\d{3,4}` 抓出來
5. 同時 GET MJIB Crimes_List（地區=全選，看實際 form 怎麼帶 → 必要時帶所有 region 值）
6. Python 端過濾出姓名包含使用者輸入的列 → 抓 `{罪名, 照片 URL, 詳情 URL}`
7. 把兩段結果一次回前端

**同步處理**（不開背景 job）：
- 預估 10 筆判決書 ≈ 10-15 秒、MJIB 1-2 秒、總計 < 30 秒
- Flask 預設 timeout 夠
- 前端 spinner + 「查詢中（最多 30 秒）...」訊息

### 結果頁顯示

```
A. 司法院刑事判決（N 筆，⚠ 為命中關鍵字）

  1. ⚠ 詐欺
     字號：113年度上易字第123號  日期：113/03/15  法院：臺灣高等法院
     命中關鍵字：詐欺 / 有期徒刑 / 定讞
     判決書內 ID（遮蔽）：A12****5678 ← 自行比對
     [展開判決書全文 ▾]
  2. 竊盜
     字號：...
     ...

B. 法務部調查局通緝/嫌疑人名單

  ⚠ 命中 1 筆 / ✅ 查無
     罪名：xxx
     [照片預覽] [前往詳情]
```

無命中時顯示「查無紀錄」。

## 後端模組分工

| 檔案 | 責任 |
|------|------|
| `vetting/__init__.py` | blueprint object |
| `vetting/routes.py` | HTTP routes（start/submit）、暫存 query_state dict |
| `vetting/judicial.py` | 司法院相關：拿 captcha、送表單、解結果頁、深度掃描詳情頁 |
| `vetting/mjib.py` | MJIB Crimes_List 抓取與姓名過濾 |
| `vetting/parser.py` | 共用解析 helper（regex、BeautifulSoup utility） |

每個檔小而專注，方便單元測試。

## 外部連線細節

### 司法院
- Base URL：`https://judgment.supreme.court.gov.tw/wkw/commonQuery.jsp`
- 法院類別：「全台法院」、判別類別：「刑事」（具體 form 欄位名以實機抓為準）
- User-Agent：用一般 Chrome UA，避免被擋

### MJIB
- URL：`https://www.mjib.gov.tw/Crimes/Crimes_List`
- 請求方式：先 GET 一次看 form 結構；若是 query string 帶 region → 一次帶全部地區值；若是 POST + 多選 → 用 `requests` POST 全選
- 解析：每筆通緝對應一個 `<div>` 或 `<tr>`，提取姓名/罪名/照片/詳情連結

## 暫存 query_state

簡單的 process-local dict：

```python
# vetting/routes.py
_QUERY_STATE = {}  # {query_id: {"cookies": ..., "viewstate": ..., "expires_at": ...}}

def _gc_expired():
    now = time.time()
    for qid in list(_QUERY_STATE.keys()):
        if _QUERY_STATE[qid]["expires_at"] < now:
            del _QUERY_STATE[qid]

def _put_state(state):
    qid = secrets.token_urlsafe(16)
    state["expires_at"] = time.time() + 300  # 5 min TTL
    _QUERY_STATE[qid] = state
    _gc_expired()
    return qid
```

僅 process-local，重啟即清空、跨 worker 不共享 → 對單機 Zeabur deploy 沒問題。未來多 worker 部署再考慮 Redis。

## 錯誤處理

- 司法院連線逾時 / 5xx → 回前端「司法院系統暫時無法連線，請稍後再試」
- MJIB 連線失敗 → 不擋整個流程，回司法院結果 + MJIB 段落顯示「無法取得通緝名單」
- captcha 錯誤 → 回前端「驗證碼錯誤，請重整圖片再試」
- query_id 過期 → 回「查詢逾時（5 分鐘），請重新開始」

## PII / 安全

- **不存查詢紀錄**：不寫 DB、不寫檔
- 既有 admin log 只記「執行人員查詢」+ 時間，**不記姓名**
- 結果頁瀏覽器不快取（既有 `Cache-Control: no-store` 全站套用）
- captcha image 用 base64 inline 顯示，**不存檔**
- 入口 feature gate：未授權 abort(404)（看不見即不存在）

## 測試

`tests/test_vetting.py`：

1. **司法院 captcha 抓取**（mock HTML）
   - 給定首頁 HTML 字串 → `judicial.fetch_captcha_state()` 回 `{cookies, viewstate, captcha_bytes}`
2. **司法院結果頁解析**（mock HTML）
   - 給定結果頁 HTML → `judicial.parse_results()` 回 N 個 dict
3. **司法院深度掃描**（mock 詳情頁 HTML）
   - 含「詐欺/有期徒刑/定讞」→ 命中清單 ✓
   - 含遮蔽 ID `A12****5678` → 抓出 `A12****5678`
   - 不含關鍵字 → 命中清單空
4. **MJIB parsing**（mock HTML）
   - 列表含某姓名 → `mjib.search_by_name("王小明")` 回該筆
   - 列表不含 → 回空 list
5. **routes integration**（用 `requests-mock` 或 `responses` 假外部站）
   - `/vetting/start` 流程
   - `/vetting/submit` 流程
   - 過期 query_id 處理

不打真實外部 URL，所有測試 mock。

## 已決策

- 純 HTTP，不用 Playwright/Chromium
- 同步處理（30 秒內）不開背景 job
- 不存任何查詢紀錄（PII 顧慮）
- 司法院 captcha 用網頁接力（後端拿圖、前端輸入）
- MJIB 用 `mjib.gov.tw/Crimes/Crimes_List`（不是 spec 原寫的警政署）
- 入口 admin only（`is_root_admin` bypass）；未來 feature 開關控管

## 不做（YAGNI）

- 查詢歷史 / 收藏
- PDF 匯出
- 多筆姓名批次
- Pandas 匯總（spec 原本提，實際用不到）
- 把 MJIB 照片下載到 server（直接連結原站）
- 自動驗證碼解碼（2Captcha 等）
- Playwright fallback
- 多 worker 共享 query_state（未來再說）
