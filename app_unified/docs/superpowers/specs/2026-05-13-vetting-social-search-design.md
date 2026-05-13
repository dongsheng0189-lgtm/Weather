# Spec: Vetting — 社群與 Google 搜尋連結擴充（純 link-out 版）

**Date**: 2026-05-13
**Owner**: hirain0127
**Status**: 待 user review

---

## 1. 目標

既有 `/vetting/` 人員查詢三段（司法院 / MJIB / NPA）保留不動。**新增**三段純 link-out 區塊：

- 📞 電話標記（Whoscall / TrueCaller / Google 電話搜尋 三個連結）
- 🌐 Google 搜尋連結（員工本人 + 最多 2 關係人，每人 4 種 keyword 切片）
- 📱 FB / IG 搜尋連結（員工 + 關係人各自）

**後端完全不打外部 API**，只生成 URL 字串。使用者按下連結後在自己瀏覽器（建議無痕）開啟、自行判讀。

設計動機：
- 使用者預估每天約 **100 人查詢量**、純後端 API 方案（Google CSE）月成本約 $75 + GCP 設定維護
- Link-out 方案 **$0 / 月、零 API key、零 quota、零維護**
- 已有的 FB / IG link-out 體驗一致、UX 統一

---

## 2. UI / 輸入欄位

### 2.1 欄位變動（`templates/vetting/index.html`）

| 欄位 | 必/選 | 既有/新 | 備註 |
|---|---|---|---|
| 姓名 | **必填** | 既有 | 不變 |
| 身分證 / 居留證 | 選填 | 既有 | 不變 |
| 📞 電話 | 選填 | **新** | TW 手機或市話、後端 normalize 多種寫法 |
| 👥 關係人 | 選填 | **新** | 最多 **2 人**、純姓名、漸進式 input row（預設 1 row、點「+ 新增關係人」出第 2 row、之後鎖死） |
| 🏷 其他特徵 | 選填 | **新** | 縣市 / 公司 / 學校等、單一 free text 欄位、不限格式 |

### 2.2 結果頁版面（手機優先、Option C：摺疊區塊）

順序：
1. **A. 司法院判決書** — 既有、不動
2. **B. 法務部調查局通緝/嫌疑人** — 既有、不動
3. **C. 警政署外逃通緝犯** — 既有、不動
4. **D. 📞 電話標記**（**新**，僅電話有填時）
5. **E. 🌐 Google 搜尋連結**（**新**）
6. **F. 📱 FB / IG 搜尋連結**（**新**）

#### 結果頁頂部固定警語

```
ℹ️ 直接點連結會在當前瀏覽器開啟、登入的 FB / Google 帳號會記錄你查詢過誰。
   強烈建議用「無痕視窗」開連結：

   • 桌機 Chrome / Edge：右鍵連結 → 「在無痕視窗中開啟連結」
   • 桌機 Safari（Mac）：右鍵連結 → 「在新的私密視窗中開啟連結」
   • 桌機 Firefox：右鍵連結 → 「在隱私視窗中開啟連結」
   • 手機 Chrome（Android）：長按連結 → 「在無痕分頁中開啟」
   • 手機 Safari（iPhone / iPad）：長按連結 → 「在私密瀏覽中開啟」
```

警語為固定 banner、永遠顯示在結果頁 D 段（社群區）正上方。可摺疊（點 header 收起）但不可關閉（無 dismiss 按鈕、刷新後仍展開）。

#### 員工 / 關係人 「人」區塊（E + F 段共用）

每個「人」區塊（員工本人 1 個 + 最多 2 關係人各 1 個）顯示：

```
👤 王小明（員工本人）              ← 員工區塊預設展開
┌────────────────────────────────────────────┐
│ [🌐 Google ▾]   [📱 FB 搜尋]   [📱 IG 搜尋] │
└────────────────────────────────────────────┘

👥 李美麗（關係人 1）              ← 關係人區塊預設摺疊
└─ 點擊展開  [🌐 Google ▾] [📱 FB] [📱 IG] [🔗 與員工共同搜尋]
```

**Google ▾** 按鈕點開展開細項：
- **一般搜尋**：`"<name>"` + 已填的 phone / relatives / feature 全部 baked-in（Google 用 disambiguators narrow 同名）
- **🏢 公司/採購**：`"<name>" (公司 OR 商行 OR 負責人 OR 董事 OR 決標 OR 招標)`
- **🏆 獎項/證照**：`"<name>" (獲獎 OR 得獎 OR 冠軍 OR 優勝 OR 公會 OR 證照)`
- **🚨 警方/負面**：`"<name>" (警察 OR 偵辦 OR 起訴 OR 逮捕 OR 通緝 OR 詐欺 OR 詐騙 OR 判決)`

**與員工共同搜尋**（只關係人區塊有）：`"<員工姓名>" "<關係人姓名>"` → Google 自動只回兩名同時出現的結果（disambiguation 主力）。

#### 📞 電話標記區（D 段、僅電話有填時）

```
📞 0912-345-678
[→ Whoscall]   [→ TrueCaller]   [→ Google 電話搜尋]
```

- Whoscall: `https://whoscall.com/zh-TW/number/886-<phone_dashed>`
- TrueCaller: `https://www.truecaller.com/search/tw/<phone_canonical>`
- Google 電話搜尋: `"<v1>" OR "<v2>" OR "<v3>"`（三個格式變體）

> Whoscall / TrueCaller 公開查詢頁 URL 格式實作時實機驗證、可能需要微調。

---

## 3. 後端架構

### 3.1 模組隔離

新建 `vetting/social.py`（單一檔案、~80 行）。**純函數、無 IO、無 DB、無 outbound HTTP**。

### 3.2 核心函數

```python
# vetting/social.py

GOOGLE_SEARCH = "https://www.google.com/search?q={}"
FB_SEARCH     = "https://www.facebook.com/search/people/?q={}"
IG_SEARCH     = "https://www.instagram.com/explore/tags/{}"
WHOSCALL      = "https://whoscall.com/zh-TW/number/886-{}"
TRUECALLER    = "https://www.truecaller.com/search/tw/{}"

BUCKET_KEYWORDS = {
    "company": ["公司", "商行", "負責人", "董事", "決標", "招標"],
    "award":   ["獲獎", "得獎", "冠軍", "優勝", "公會", "證照"],
    "negative":["警察", "偵辦", "起訴", "逮捕", "通緝", "詐欺", "詐騙", "判決"],
}


def normalize_phone(raw: str) -> str:
    """去掉空白/hyphen/paren、+886 / 886 → 0、回 canonical 10 碼國內格式。
    回空字串如果無法 normalize。"""


def phone_query_variants(canonical: str) -> list[str]:
    """canonical 10 碼 → 3 個變體 ['0912345678', '0912-345-678', '+886912345678']"""


def google_search_url(name: str, *,
                      bucket: str | None = None,
                      disambiguators: list[str] | None = None) -> str:
    """組 Google 搜尋 URL。
    bucket 是 'company' / 'award' / 'negative' / None（一般搜尋）。
    disambiguators 是 phone / relative_names / feature_tokens 全 list 起來。"""


def build_url_bundle(name: str,
                     phone: str = "",
                     relatives: list[str] | None = None,
                     feature: str = "") -> dict:
    """產生完整 URL bundle。純函數、deterministic。
    Returns:
        {
          "phone": {
            "normalized": "0912-345-678",
            "whoscall_url": "...",
            "truecaller_url": "...",
            "google_phone_url": "..."
          } or null if no phone,
          "people": [
            {
              "name": "王小明",
              "role": "employee",
              "google_general_url": "...",
              "google_company_url": "...",
              "google_award_url": "...",
              "google_negative_url": "...",
              "fb_search_url": "...",
              "ig_search_url": "...",
              "conjunction_with_employee_url": null  # 員工本人 null
            },
            {
              "name": "李美麗",
              "role": "relative",
              ...
              "conjunction_with_employee_url": "..."  # "王小明" "李美麗"
            }
          ]
        }
    """
```

### 3.3 Routes 變動（最小）

#### `POST /vetting/start`

既有：跑 NPA captcha 準備 + 司法院 + MJIB、暫存 state、回 captcha。

新增：**inline 呼叫 `build_url_bundle()`**（純 CPU、< 1ms）、把回傳塞進 state。

Request body 多收：
```json
{
  "name": "王小明",
  "id": "A123456789",
  "phone": "0912-345-678",       // 選填
  "relatives": ["李美麗", "張三"], // 選填、最多 2
  "feature": "新北市 ABC 餐廳"     // 選填
}
```

Response 不變（仍只回 `query_id` + `captcha_b64`）。

#### `POST /vetting/submit`

既有：送 NPA 驗證碼、整合三段結果回。
新增：response 多 1 個 key `social`，內容就是 `build_url_bundle()` 的 dict。

```json
{
  "judicial": [...],
  "mjib": [...],
  "npa": [...],
  "social": {
    "phone": {...} or null,
    "people": [...]
  }
}
```

### 3.4 為什麼 URL 在後端組（而非 frontend）

| | 後端組 | 前端組 |
|---|---|---|
| 邏輯重用 | tests/可測 | 只能 in-browser test |
| 一致性 | 一份 keyword 清單 | JS 跟 Python 雙份要同步 |
| 改 keyword | 改 1 處 | 改 2 處 |
| 額外 round-trip | 沒有（既有 /start /submit pattern 不變） | — |

→ 選後端組。

---

## 4. 設定 / 環境變數

**無**。本 spec 完全不需要新環境變數、不需要 API key、不需要 GCP project。

---

## 5. Quota / Cost / Rate limit

### 5.1 既有
- `/vetting/start`: 3/分鐘 per user（不變）
- `/vetting/submit`: 1/分鐘 per user（不變）

### 5.2 新功能
- **無外部 API 呼叫** → 無 quota、無 cost
- 後端額外 CPU < 1ms per vetting

---

## 6. Data model

### 6.1 既有 `VettingQueryLog` 不變
記錄 `user_id` + `queried_name`。不另記電話/關係人/特徵（避免敏感資料落 log）。

### 6.2 不開新 table
URL bundle 在 `_QUERY_STATE` 暫存（in-memory dict、5 分鐘 TTL）、跟既有同處。

> ⚠ **單 worker 限制**：既有 `_QUERY_STATE` 已知不能跨 worker（[[feedback_in_memory_state_workers]]）。Zeabur 上 `WORKERS=1` 仍生效、社群擴充不改變這個限制。

---

## 7. Privacy / Security

### 7.1 後端不打 Google / FB / IG / Whoscall / TrueCaller
- 本系統**只生成搜尋連結字串**、不發送任何外部 HTTP
- Server-side 完全沒有外部 outbound traffic（除既有 A/B/C 三家爬蟲）

### 7.2 使用者點擊行為
- FB / IG / Google / Whoscall / TrueCaller 連結都用 `target="_blank" rel="noopener"`
- 使用者按下後**在自己瀏覽器**開啟 → 該瀏覽器的登入帳號**會被該服務記錄**
- 結果頁頂部固定提醒「建議用無痕視窗 / 未登入 profile」

### 7.3 PDPA / 雇主同意書
- 員工背景查詢需有員工書面同意（既有 A/B/C 已實施、雇主端責任）
- 新增 D/E/F 段建議**更新員工同意書**涵蓋「公開網路 / 社群媒體資訊查詢」
- 本 spec **不**強制在 code 端做同意 gate

---

## 8. Out of scope（明確不做）

- ❌ 真實爬 FB / IG 公開頁面（ToS 違規 + 維運成本爆高）
- ❌ Google CSE API 整合（成本超過免費額度、100 人/天 過大）
- ❌ Whoscall / TrueCaller API 整合（沒有公開 API）
- ❌ 後端 keyword 結果分類 / 負面 highlight（改由 Google URL keyword 過濾達成）
- ❌ Confidence score 同名歧異演算法（改由 disambiguator 塞進 Google query、Google 幫忙 narrow）
- ❌ 自動截圖 / 自動下載照片
- ❌ 跨 vetting 的歷史比對
- ❌ 多語系（暫時只支援中文 keyword）
- ❌ 員工同意書數位簽核

---

## 9. 既有功能保護

**強制不變**：
- `/vetting/start` 既有 response shape（多接收新欄位、response 本身不變）
- `/vetting/submit` 既有 response shape（**只加** key `social`、不改既有 keys）
- 司法院 / MJIB / NPA 三個 module 一行都不動
- 既有 result HTML rendering（A / B / C section）一行都不動
- `VettingQueryLog` 既有欄位不動
- 既有 outbound_guard 白名單一行都不動（後端不打外部 URL）

**只加**：
- 3 個新 input field（電話 / 關係人 / 特徵）
- D / E / F 三段新 HTML render block、append 在 C 後面
- `vetting/social.py` 新檔
- 新欄位接收進 `/vetting/start`、URL bundle 進 state、`/vetting/submit` response 多 `social` key

---

## 10. Tests

### 10.1 後端 unit test `tests/test_vetting_social.py`

| 測試 | 覆蓋 |
|---|---|
| `test_normalize_phone_variants` | 6 種輸入格式 → canonical 10 碼（`+886-912-345-678` / `886912345678` / `0912 345 678` / `0912-345-678` / `0912345678` / `(0912)345678`） |
| `test_normalize_phone_invalid` | 空 / 太短 / 太長 / 非數字 → 回空字串 |
| `test_phone_query_variants` | canonical → 3 個變體 |
| `test_google_search_url_basic` | `name` only → URL contains `"王小明"` |
| `test_google_search_url_with_bucket` | bucket=`company` → URL 含 `(公司 OR ...)` 等 6 個 token |
| `test_google_search_url_with_disambiguators` | name + phone + relative + feature → URL 含全部 5 個 token |
| `test_build_url_bundle_employee_only` | 只填姓名 → `people[0]` 員工、`phone=null`、沒 relatives |
| `test_build_url_bundle_with_phone` | 填電話 → `phone` dict 完整、3 個 link |
| `test_build_url_bundle_with_2_relatives` | 2 關係人 → `people` 長度 3、conjunction URL 對 |
| `test_build_url_bundle_relative_count_cap` | 給 3+ 關係人 → 截到 2 |
| `test_build_url_bundle_idempotent` | 同樣 input 兩次跑 → output 完全相同 |
| `test_build_url_bundle_special_chars_in_name` | 姓名含 `&` / `"` / 中文 → URL 正確 encode |

預估 12 條 test、全綠。

### 10.2 Route 整合 test `tests/test_vetting_routes_social.py`

| 測試 | 覆蓋 |
|---|---|
| `test_start_accepts_new_fields` | POST `/start` 帶 phone/relatives/feature → 200、不爆 |
| `test_submit_returns_social_key` | 完整 flow `/start` → `/submit` → response.social 存在 |
| `test_submit_social_phone_null_when_empty` | 不填電話 → social.phone = null |
| `test_submit_social_people_employee_only` | 不填關係人 → social.people 長度 1（員工） |
| `test_submit_social_people_with_relatives` | 填 2 關係人 → social.people 長度 3 |
| `test_existing_response_shape_unchanged` | judicial / mjib / npa 三 key 在 response 內 shape 不變（regression）|

預估 6 條、全綠。

### 10.3 前端 smoke test
- 既有 vetting 6 條 test 全綠（regression）
- 新加 3 條：
  - 結果頁帶 social → D/E/F section 出現 + 警語 banner 出現
  - 結果頁不帶 social（純 backward compat 測試）→ D/E/F 完全不 render
  - 員工本人預設展開、關係人預設摺疊（DOM class 檢查 `details` open 屬性）

---

## 11. 部署

### 11.1 環境變數
**無新增**。

### 11.2 Outbound guard
**不需要動**。後端不會送出任何 Google / FB / IG / Whoscall / TrueCaller HTTP request。

### 11.3 Prod Zeabur
- 純 image refresh（GHA build / push）
- **不需要 Redeploy**（除非順便清緩存）
- 沒環境變數要設、沒 secret 要管

---

## 12. 漸進交付（建議切兩個 PR）

### PR 1：後端 + unit/route tests（無 UI）
- `vetting/social.py` 整檔
- `vetting/routes.py` 接新欄位 + URL bundle 進 state + response 加 `social` key
- 12 條 unit test + 6 條 route test
- 行數：約 +200 lines code / +250 lines test

### PR 2：前端 + UI 整合
- `templates/vetting/index.html`：
  - 3 個新 input field
  - 關係人 2-row 漸進 UI
  - 結果頁頂部警語
  - D / E / F 段 render（含 `<details>` 摺疊、`Google ▾` sub-menu）
- 3 條 smoke test
- Local dev 走 `bash scripts/run_local.sh` 端到端驗證
- 行數：約 +200 lines template/JS / +60 lines test

---

## 13. 開放問題

無（所有設計決策已釘住）。
