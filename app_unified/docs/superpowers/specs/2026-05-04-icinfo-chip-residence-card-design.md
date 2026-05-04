# 晶片居留證查詢（icinfo）+ 司法院結果排序

**日期**：2026-05-04
**範圍**：
- 新模組 `icinfo/`（晶片居留證查詢，獨立 blueprint，UI 嵌在 vetting 頁面下方）
- 順手 fix：`vetting/judicial` 結果按關鍵字命中排序

---

## 動機

### 1. 晶片居留證查詢（新功能）

admin 拿到一張實體居留證（員工證件影本、外勞文件等），需驗證真偽 + 取出官方照片做人臉比對。移民署官方提供 [晶片居留證查詢網站](https://niaicinfo.immigration.gov.tw/icinfo-frontend/zh)，輸入 4 欄資料 + 驗證碼即可查到照片。我方做一個 wrapper 整合在現有 vetting 頁面下方，讓 admin 一站式完成所有人員背景查驗。

### 2. 司法院結果排序

目前 `vetting/judicial` 結果列表是按來源頁順序，admin 必須掃完整列表才知道哪幾筆有踩到關鍵字（詐欺/洗錢/有期徒刑/定讞/毒品/搶劫/偷竊）。把命中關鍵字的案件排到最前面，第一眼就看到重點。

---

## 上游 API recon（2026-05-04 確認）

移民署 API 全部走前端 SPA `/icinfo-frontend/`：

| Method | Path | 用途 | 備註 |
|---|---|---|---|
| GET | `/icinfo-frontend/api/captcha?<ts>` | 取驗證碼 JPEG | 第一次呼叫 set `JSESSIONID` cookie（HttpOnly + Secure） |
| GET | `/icinfo-frontend/api/captcha/audio` | 語音驗證碼（不需要） | YAGNI 不接 |
| POST | `/icinfo-frontend/api/search` | 送查詢 | 帶 `JSESSIONID` cookie + JSON body `{uino, issueDate, expiryDate, serialNo, captcha}` |

回傳 JSON 結構（從 page chunk 反編譯）：
```json
{
  "rcode": "<SUCCESS or其他>",
  "data": {
    "searchTime": "...",
    "image": "<base64 jpeg, 只有 SUCCESS 才有>"
  }
}
```

**特殊規則**：永久居留證 `expiryDate` 可空白（從查詢說明頁取得）。

---

## 設計

### A. 新 blueprint `icinfo/`（與 vetting/notes/mynotes 完全分離 + 對外偽裝）

按 `feedback_module_isolation` 規則 + **對外 URL 偽裝成 vetting 子功能**：

```
icinfo/
├── __init__.py        # blueprint = Blueprint('icinfo', __name__, url_prefix='/v/aux')
├── routes.py          # GET /c, POST /s
├── immigration.py     # HTTP wrapper（fetch_captcha / submit_search）
├── errors.py          # CaptchaInvalid / UpstreamError
templates/icinfo/
└── _form.html         # 表單 fragment（給 vetting/index.html include）
```

**對外 URL（偽裝）**：
- `GET /v/aux/c` — 取驗證碼（看起來像 vetting 的 auxiliary 子端點）
- `POST /v/aux/s` — 送查詢

vetting 自己的路由是 `/v/start`、`/v/submit`、`/v/`，不會衝突（不同 path）。Flask 兩個 blueprint 各自有 `before_request` gate，互不干擾。

**程式碼內部命名（不對外洩漏，可以保留辨識性）**：
- 資料夾 `icinfo/`、blueprint name `icinfo`、Python module `icinfo.routes` 等都保留 — 普通使用者永遠看不到原始碼
- HTML/CSS class/id、JS 變數、fetch URL **一律不出現** `icinfo`/`immigration`/`居留證` 字串（會被 F12 看到）

**features.py**：新增
```python
{"key": "v_aux", "label": "晶片居留證", "emoji": "💳"}
```

key 改成 `v_aux`（DB users.features 欄位的內值），跟 vetting 同主題。`label` 是 admin dashboard 勾選 UI 的顯示文字，admin 自己會看到所以保留中文。

**權限**：blueprint `before_request` gate `user_has_feature(current_user, "v_aux")`，失敗 abort 404。

**預設 features 配置**：
- `role='super_admin'` → 自動含 `"v_aux"`
- `role='admin'` → 自動含 `"v_aux"`
- `role='user'` → 預設**不含**，需手動 SQL 加（同 vetting J/JJ/B/C 的做法）

實作位置：`auth/registration` 或 `User` model 的 default features 邏輯加 role-based 補齊。Migration 同時 backfill 既有 super_admin / admin 帳號。

### B. UI 整合到 vetting 頁面（不破壞 vetting）

`templates/vetting/index.html` 在三站結果區下方加：

```jinja
{% if user_has_feature(current_user, 'v_aux') %}
  {% include 'icinfo/_form.html' %}
{% endif %}
```

`_form.html` 是完全獨立的 `<section>`：自己的 form、自己的 JS（不汙染 vetting 全域）、fetch 到 `/v/aux/c` + `/v/aux/s`。**不共用 cookie / model / storage**。

**HTML/CSS/JS 偽裝原則**（F12 偵察會看到的全部過一遍）：
- HTML class/id 用中性命名：`auxBlock`、`auxForm`、`auxResult`、`auxPic`（**不用** `icinfo*`、`immigration*`、`residence*`、`居留*`）
- JS 變數：`loadAux()`、`submitAux()`、`auxState`（**不用** `loadCaptcha`、`icinfoXxx`）
- fetch URL 字串只出現 `/v/aux/c`、`/v/aux/s`，不留 `/icinfo/` 跡象
- 表單 label 文字「請填寫統一證號」「居留期限」這些對 admin 必要，無法偽裝（已接受）— 但這對沒權限進來的 user 根本看不到（features gate 擋住）

### C. 後端流程

**`GET /v/aux/c`**（routes.py 內部仍是 icinfo blueprint）：

```
1. 取 user-scope key：session['icinfo_jsessionid'] = None（每次 GET 重設）
2. requests.get('https://niaicinfo.immigration.gov.tw/icinfo-frontend/api/captcha?<ts>')
   → 拿 JPEG bytes + Set-Cookie 的 JSESSIONID
3. session['icinfo_jsessionid'] = <jsessionid>
4. return JSON {image: base64, ts: <timestamp>}
```

session key `icinfo_jsessionid` 只是 server-side dict key（Flask session 加密儲存於 cookie，使用者看不到內容），無偽裝必要。

**`POST /v/aux/s`**：

Request body:
```json
{
  "uino": "AB12345678",
  "issueDate": "2024-01-15",
  "expiryDate": "2026-01-15",  // 空字串 = 永居證
  "serialNo": "1234567890",
  "captcha": "ab3d"
}
```

處理：
```
1. 從 session 取 icinfo_jsessionid，沒有 → 回 {error: "captcha_expired"}
2. requests.post(
     'https://niaicinfo.immigration.gov.tw/icinfo-frontend/api/search',
     json={...4 欄位 + captcha},
     cookies={'JSESSIONID': <jsessionid>},
     headers=BROWSER_HEADERS,
     timeout=15
   )
3. 解析回傳 JSON
4. 不論結果如何，session['icinfo_jsessionid'] = None（驗證碼一次性）
5. **寫 IcinfoQueryLog**（user_id, queried_uino, matched, queried_at）
6. return:
   - rcode === 'SUCCESS' → {matched: true, image: <base64>, search_time: ...}
   - 其他 → {matched: false, error: null}
```

**錯誤處理**：
| 情境 | 回前端 |
|---|---|
| Session 沒 captcha | `{error: "captcha_expired"}` → 前端提示「請點重新整理」 |
| 移民署 timeout / 5xx | `{error: "upstream"}` → 「移民署服務暫時無法使用」 |
| 上游回傳格式異常（非預期 JSON） | `{error: "upstream"}` |
| `rcode !== SUCCESS` 但格式正常 | `{matched: false}` → 「資料不符」（涵蓋驗證碼錯 + 4 欄位錯，因為移民署不細分） |

### D. 前端 JS（簡單 vanilla，命名中性）

`_form.html` 內嵌 `<script>` 標籤：
- IIFE 包起來（`(function(){ ... })()`）避免汙染全域
- `loadAux()`：fetch `/v/aux/c` → set img.src = `data:image/jpeg;base64,${data.image}`
- `submitAux()`：取 4 欄位 + captcha → POST `/v/aux/s` → 切結果區
- 結果區 3 種狀態：
  - 初始：「相片僅供本證查詢使用」+ 空白底
  - matched: true：顯示 base64 照片 + 「✅ 資料相符」
  - matched: false 或 error：「❌ 資料不符」/「⚠️ {error message}」
- 重新整理按鈕 → `loadAux()`
- 送出後一律 `loadAux()`（驗證碼一次性）

### E. Model + Migration 009

新檔 `migrations/versions/009_v_aux_logs.py`：

```python
class IcinfoQueryLog(db.Model):                # 程式碼內部仍叫 IcinfoQueryLog（清晰）
    __tablename__ = 'v_aux_logs'                # 但 DB table 用偽裝名 v_aux_logs
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    queried_uino = db.Column(db.String(10), nullable=False, index=True)
    matched    = db.Column(db.Boolean, nullable=False)
    queried_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           server_default=db.func.now(), index=True)
```

**注意**：`DateTime(timezone=True)` 對齊 `feedback notes_aware_resolved_at`（5/1 那次 bug fix 教訓）— 新欄位一律用 timezone-aware，避免 model 宣告 vs DB 不一致。

**只記 metadata，不存照片本身**（合規 + 移民署明示「相片僅供本證查詢使用」）。

**保存策略**：90 天清理由後續加 cron job 處理（不在本 spec 範圍）。

**Migration 同時做的權限 backfill**：

```sql
-- 既有 super_admin / admin 的 features 補齊 v_aux
UPDATE users SET features = jsonb_set(...) WHERE role IN ('super_admin', 'admin');
```

實際語法依 `users.features` 的儲存型別（JSONB / TEXT JSON）調整。

### F. 司法院排序（順手 fix，獨立 commit）

`vetting/routes.py` 在 render template 前對司法院 cases 排序：

```python
def _judicial_sort_key(case):
    return (
        0 if case.get('keyword_hits') else 1,  # 命中關鍵字優先
        0 if case.get('id_match') else 1,      # ID 比對 True 次優先
    )

cases.sort(key=_judicial_sort_key)
```

驗證：寫一個 unit test `tests/test_judicial_sort.py`，3 個 case 混合 hit/no-hit、id_match True/False/None，確認排序穩定。

---

## 測試

### Unit
- `tests/test_icinfo_immigration.py`：mock `requests.get/post`，驗證 captcha 解析、search payload 結構、rcode 判斷
- `tests/test_icinfo_routes.py`：用 Flask test client，mock immigration module，驗證 session 存取、權限 gate、log 寫入
- `tests/test_judicial_sort.py`：排序穩定性

### Integration（手動）
- 用 admin 帳號登入 → 進 `/v/` → 看到 vetting 三站結果下方有「💳 晶片居留證查詢」表單 + 驗證碼圖
- 用普通 user 帳號登入 → 進 `/v/` → 表單區塊不應出現
- 用 admin 帳號 POST `/v/aux/s` → 應該 200；用普通 user 打 → 404
- **F12 偽裝驗證**：admin 在表單區 F12 → Elements 全頁搜尋 `icinfo`、`immigration`、`居留證`（class/id 層級）應**找不到**；Network 送查詢時 fetch URL 是 `/v/aux/c`、`/v/aux/s`

### Prod 實機（待）
- 拿一張真實居留證跑一次「資料相符」流程，確認照片正確顯示
- 故意打錯一個欄位跑「資料不符」流程
- 故意打錯驗證碼跑「資料不符」流程（這個會跟欄位錯不可區分，預期）

---

## 不做（YAGNI）

| 項目 | 為何不做 |
|---|---|
| 照片落地 R2 | 移民署明示「相片僅供本證查詢使用」，落地違反原意 + 增加合規風險 |
| 語音驗證碼 | 員工背景查驗用不到，admin 自己看圖即可 |
| Rate limit | 驗證碼本身就是天然限速；super_admin + admin 人數少；未來開放給 user 再回頭考慮 |
| 自動清理 90 天舊 log | 先看實際量，量小就手動刪，量大再加 cron job |
| 查詢 log 後台頁面 | 先寫入 DB，要看 log 用 SQL 直接查；後續再加 admin UI |

---

## 安全考量

1. **JSESSIONID 隔離**：每個 user 在 Flask session 各自一份 `icinfo_jsessionid`，互不干擾
2. **驗證碼一次性**：search 後不論成敗都清掉 session，下次必須重新拿 captcha
3. **權限三道閘**：features gate（blueprint level）+ role 自動補齊（super_admin/admin）+ 無 features 直接 404
4. **Log 不含照片**：只記 metadata（user_id / uino / matched / ts），照片不落地
5. **HTTP 偽裝**：沿用 vetting 的 `BROWSER_HEADERS`（真人 UA），且加 `throttle_sleep` 0.5-1.2s 隨機延遲
6. **錯誤訊息不洩漏內部**：所有失敗對使用者只顯示 4 種文案（資料相符 / 資料不符 / 驗證碼過期 / 服務暫時無法使用）

---

## 開放問題（implementation 階段確認）

- `users.features` 欄位的實際型別（JSONB / TEXT）— 看 model 宣告，再寫 migration backfill SQL
- 移民署 search API 是否回 GET-then-POST 的 CSRF token？— recon 沒看到，但實作時要 watch network 確認
- 驗證碼錯誤時 `rcode` 是什麼值？— 跟一般「資料不符」是否同 rcode？— 第一次 prod 測試時印 log 觀察，必要時前端文案分流
