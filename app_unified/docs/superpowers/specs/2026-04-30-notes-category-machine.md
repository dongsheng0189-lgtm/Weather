# 店務筆記分類調整：個人 → 機台、移除未分類、AI 摘要不再被吃

**日期**：2026-04-30
**範圍**：店務筆記（`notes/` blueprint），不動 mynotes / vetting

---

## 動機

1. 「個人」分類在實際使用上沒有作用，使用者要把它換成「機台」（與業務相關的機台議題）
2. 「未分類」目前 DB 0 筆，不再需要這個 fallback 選項
3. 之前 AI 摘要儲存的筆記會在下次 AI 摘要時又被當素材吃進去（摘要的摘要），造成噪音
4. AI 摘要的分類目前一般筆記用【人事/修繕/店務/雜項】、確認表單用【店務/員工/客人/其他雜事】兩套不一致；改成統一 4 類：**員工 / 客人 / 機台 / 雜項**（與筆記建立時的分類完全對齊）

## 資料現況（2026-04-30 抓的 prod DB）

```
employee: 85
customer: 97
personal: 71  → 全部歸 misc
misc:     71
unclassified: 0
```

## 設計

### 1. Model 變動 (`models.py`)

順序統一為 **員工 / 客人 / 機台 / 雜項**（modal、filter、AI 摘要都用這個順序）：

```python
CATEGORY_CHOICES   = ["employee", "customer", "machine", "misc"]       # 移除 personal, unclassified；新增 machine
CATEGORY_LABELS    = {"employee": "員工", "customer": "客人", "machine": "機台", "misc": "雜項"}  # misc 顯示文字從「雜事」改「雜項」
CATEGORY_CREATABLE = ["employee", "customer", "machine", "misc"]
```

注意：`misc` 的中文 label 從「雜事」改成「雜項」，與 AI 摘要 prompt 一致。

`Note` 加新欄位：
```python
is_ai_summary = db.Column(db.Boolean, nullable=False, default=False, server_default="false")
```

`Note.category` server default 從 `"unclassified"` 改為 `"misc"`。

### 2. Migration 008

檔案：`migrations/versions/008_machine_category_and_ai_summary.py`

步驟（必須按順序）：

1. `ALTER TABLE notes ADD COLUMN IF NOT EXISTS is_ai_summary BOOLEAN NOT NULL DEFAULT FALSE`
2. `ALTER TABLE notes ALTER COLUMN category SET DEFAULT 'misc'`
3. `UPDATE notes SET category='misc' WHERE category IN ('personal', 'unclassified')`
4. **Backfill `is_ai_summary`**（用 SQLAlchemy ORM，因為 title 是 EncryptedText 必須走 Python 解密）：
   - 走過所有 `category='misc'` 筆記，title 符合 `^\d{4}-\d{2}-\d{2}\s*摘要\s*$` 的 → `is_ai_summary=True`
   - 必須在 Zeabur container 跑（有 `DB_ENCRYPTION_KEY`），本機解不了密
5. 印出統計：移轉幾筆、backfill 標記幾筆

### 3. 後端邏輯變動

**`notes/routes.py`：**

- `new_note()`：移除 `if category in ("personal", "unclassified")` 分支。所有 creatable category 都走統一格式 `f"{date} {shift_glue}{CATEGORY_LABELS[category]}"`（「4/30 員工」「4/30 A機台」「4/30 雜項」一致）
- `_format_note_for_ai()`：刪除 `if getattr(n, "category", None) == "personal"` 加 cat_hint 的分支
- `_build_all_content()`：query 加 `.filter(Note.is_ai_summary == False)`，排除 AI 摘要產生的筆記
- `list_notes()` / `create_note()` / GET handlers：fallback 從 `"unclassified"` 改 `"misc"`
- `create_note()` POST：接收 `is_ai_summary` 欄位（bool, default False），寫入 Note
- 種類 query 參數驗證：移除 `unclassified`/`personal`，只認 CATEGORY_CHOICES

#### AI 摘要分類重構（一般筆記與確認表單統一 4 類）

**`MANAGER_PROMPT`**（一般筆記 prompt）改為：

```
# Categories（4 類，依筆記/表單建立時選的分類為準）
- 【員工】：員工請假、排班、人事、獎懲、員工互動。
- 【客人】：客訴、顧客反應、會員、客人需求。
- 【機台】：機台故障、保養、操作、設備修繕、報修進度。
- 【雜項】：不屬於上述三類的所有事項（含進貨、庫存、活動、衛生等店務雜事）。

# 分類規則
- 一般筆記：依筆記原始分類輸出（員工→員工、客人→客人、機台→機台、雜項→雜項）。
- 若無法判斷，一律放入【雜項】。

# Output Style
依「類別」分組輸出，每個類別用 `## 類別名` 作為標題；
類別下用數字列表（1. 2. 3.）；沒有事項的類別省略。
```

範例段落同步改成員工/客人/機台/雜項。

**`_classify_by_title()`** 重寫為 4 類，且依使用者選擇的 `category` 為主，標題只在 `category` 非 creatable 時才用做 fallback：

```python
def _classify_by_title(title):
    t = (title or "").lower()
    if any(k in t for k in ("客人", "顧客", "客訴", "客戶")):
        return "客人"
    if any(k in t for k in ("同事", "員工", "人事", "排班", "請假")):
        return "員工"
    if any(k in t for k in ("機台", "保養", "故障", "設備", "修繕", "維修")):
        return "機台"
    return "雜項"
```

**`_build_categorized_checklist()`**：

- categories dict 改 4 鍵：`{"員工": ..., "客人": ..., "機台": ..., "雜項": ...}`
- 分類來源**優先用 `note.category`**（因使用者建表單時已選），把 enum 對應到中文：
  - `employee → 員工`、`customer → 客人`、`machine → 機台`、`misc → 雜項`
  - 若 `category` 不在 4 個之中（不會發生但保險），fallback 到 `_classify_by_title(title)`
- 移除 personal 強制歸「其他雜事」的分支

**`HEADER_BY_CAT`**（`_build_ai_sections` 用的中文 emoji header）改：

```python
{
  "員工": "## 👥 員工",
  "客人": "## 🧑‍🤝‍🧑 客人",
  "機台": "## 🔧 機台",
  "雜項": "## 📋 雜項",
}
```

順序：`("員工", "客人", "機台", "雜項")`

**`_build_output_structure_prompt`** 描述文字同步改：「分為 👥員工 / 🧑‍🤝‍🧑客人 / 🔧機台 / 📋雜項 四大類」。

**`notes/ws.py`：**

- 與 routes.py 同步：default 改 `misc`、create_note WS 接收 `is_ai_summary`、移除 personal/unclassified default

### 4. 前端變動

**`templates/notes/index.html`：**

- Modal（新增筆記/確認表單）按鈕順序改成 **員工 / 客人 / 機台 / 雜項**：`個人` 按鈕移除、新增 `機台`，`data-cat="machine"`
- localStorage validation：`['employee','customer','machine','misc']`（順序對齊 modal）
- Filter tabs（順序同步）：
  ```
  [('','全部'),('employee','員工'),('customer','客人'),('machine','機台'),('misc','雜項')]
  ```
  （移除 `unclassified` tab、移除 `personal` tab）
- Badge CSS：
  - 新增 `.category-badge.category-machine { background:#9b59b6; }`（沿用原本紫色）
  - 移除 `.category-unclassified` 跟 `.category-personal`
  - `misc` badge 文字會跟著 CATEGORY_LABELS 自動顯示「雜項」
- Note row 預設 fallback：`note.category or 'misc'`（原本是 `'unclassified'`）
- `saveSummaryAsNote()`：POST body 改 `category: 'misc', is_ai_summary: true`（原本 `category: 'personal'`）
- JS 種類顏色 map：`personal: '#9b59b6'` → `machine: '#9b59b6'`

**`templates/notes/editor.html`：**

- 類別按鈕順序改成 **員工 / 客人 / 機台 / 雜項**（編輯既有筆記時可切換）：
  - `個人` 按鈕移除、新增 `機台`，`data-category="machine"`
  - `雜事` 按鈕文字改成 `雜項`（misc 中文 label 變更）
- 移除 `{% if cur_category == 'unclassified' %}` pill 整段（不再支援 unclassified 顯示）
- CSS class：
  - `.category-btn.selected[data-category="personal"]` → `[data-category="machine"]`（紫色 #9b59b6 沿用）
  - 移除 `[data-category="unclassified"]` CSS
- `cur_category` fallback 從 `'unclassified'` 改 `'misc'`
- `currentCategory` JS 變數初始化 fallback 同步改

### 5. 測試

- `tests/test_category.py`：personal/unclassified 相關 case 改寫成 machine；確認 modal CREATABLE 為 4 個分類；確認 CATEGORY_LABELS["misc"] == "雜項"
- 新增 case：`is_ai_summary=True` 的筆記不會出現在 `_build_all_content` query
- 新增 case：`_build_categorized_checklist` 對 4 種 category 的 Note 各回到正確中文桶（員工/客人/機台/雜項）
- `tests/test_paragraph_diff.py` / `tests/test_shift.py` / `tests/test_cleanup.py`：grep `personal`/`unclassified` 看是否有 reference 要更新

### 6. AI 摘要儲存流程的標題（不變）

`saveSummaryAsNote` 產生的標題仍是 `YYYY-MM-DD 摘要`（例如 `2026-04-30 摘要`）。這是 backfill 比對的依據。

---

## 隔離 / 不影響的東西

- 不動 mynotes（私人筆記沒有 category）
- 不動 vetting
- 不動 PrivateNote / PrivateChecklistItem
- shift A/B/N 邏輯維持
- AI 摘要的呼叫流程（split summary、retry、PII redact、systemInstruction）不動，只動 prompt 內的分類定義與 `_build_categorized_checklist`/`_classify_by_title`/`HEADER_BY_CAT` 的中文桶

## 部署順序

1. push code（含 migration 008 檔案，但**不執行**）
2. 等 Zeabur 自動部署
3. 進 Zeabur container 跑：`cd /app && python migrations/versions/008_machine_category_and_ai_summary.py`
4. 驗證：
   - 進 admin 登入，看 filter tab 順序為「全部/員工/客人/機台/雜項」（沒有「個人」「未分類」）
   - 點「+ 新增筆記」，看 modal 按鈕順序為「員工/客人/機台/雜項」
   - 用 4 種類別各建一筆，看標題格式正確（4/30 員工、4/30 A機台、4/30 雜項 等）
   - 跑 AI 摘要，看輸出分類為「員工/客人/機台/雜項」4 類，且 prompt 不包含先前 saveSummaryAsNote 存進去的「YYYY-MM-DD 摘要」筆記
   - 確認表單預分類也走 4 類（建一張標題含「機台」的確認表單，看 AI 摘要把它放到「機台」桶）

## 風險 / 邊界

- **本機開發環境跑 migration 會失敗**：因本機沒 `DB_ENCRYPTION_KEY` 解不出 title。Migration 步驟 4 必須在 Zeabur container 跑。設計上 migration 的步驟 1-3 用 raw SQL（不需解密），步驟 4 用 ORM 在能解密的環境跑。
- **既有 71 筆 personal 一旦變 misc 就無法回溯**：使用者已確認 OK
- **Backfill 漏判風險**：若使用者改過 AI 摘要筆記的標題（從 `YYYY-MM-DD 摘要` 改成別的），backfill 不會標記為 `is_ai_summary=True`，下次仍會被吃進 AI 摘要。可接受（標題改過代表使用者主動把它當一般筆記了）。
