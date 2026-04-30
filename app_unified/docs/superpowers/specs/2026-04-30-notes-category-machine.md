# 店務筆記分類調整：個人 → 機台、移除未分類、AI 摘要不再被吃

**日期**：2026-04-30
**範圍**：店務筆記（`notes/` blueprint），不動 mynotes / vetting

---

## 動機

1. 「個人」分類在實際使用上沒有作用，使用者要把它換成「機台」（與業務相關的機台議題）
2. 「未分類」目前 DB 0 筆，不再需要這個 fallback 選項
3. 之前 AI 摘要儲存的筆記會在下次 AI 摘要時又被當素材吃進去（摘要的摘要），造成噪音

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

```python
CATEGORY_CHOICES   = ["employee", "customer", "misc", "machine"]       # 移除 personal, unclassified；新增 machine
CATEGORY_LABELS    = {"employee": "員工", "customer": "客人", "misc": "雜事", "machine": "機台"}
CATEGORY_CREATABLE = ["employee", "customer", "misc", "machine"]
```

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

- `new_note()`：移除 `if category in ("personal", "unclassified")` 分支。所有 creatable category 都走統一格式 `f"{date} {shift_glue}{CATEGORY_LABELS[category]}"`（即「4/30 機台」「4/30 A機台」與員工/客人/雜事一致）
- `_format_note_for_ai()`：刪除 `if getattr(n, "category", None) == "personal"` 加 cat_hint 的分支
- `_build_categorized_checklist()`：刪除 personal 強制歸「其他雜事」的分支，所有 checklist 走 `_classify_by_title()`
- `_build_all_content()`：query 加 `.filter(Note.is_ai_summary == False)`，排除 AI 摘要產生的筆記
- `list_notes()` / `create_note()` / GET handlers：fallback 從 `"unclassified"` 改 `"misc"`
- `create_note()` POST：接收 `is_ai_summary` 欄位（bool, default False），寫入 Note
- 種類 query 參數驗證：移除 `unclassified`/`personal`，只認 CATEGORY_CHOICES

**`notes/ws.py`：**

- 與 routes.py 同步：default 改 `misc`、create_note WS 接收 `is_ai_summary`、移除 personal/unclassified default

### 4. 前端變動

**`templates/notes/index.html`：**

- Modal（新增筆記/確認表單）：`個人` 按鈕 → `機台`，`data-cat="personal"` → `data-cat="machine"`
- localStorage validation：`['employee','customer','misc','personal']` → `['employee','customer','misc','machine']`
- Filter tabs：
  ```
  [('','全部'),('employee','員工'),('customer','客人'),('misc','雜事'),('machine','機台')]
  ```
  （移除 `unclassified` tab）
- Badge CSS：新增 `.category-badge.category-machine { background:#9b59b6; }`（沿用原本紫色）
  - 移除 `.category-unclassified` 跟 `.category-personal`
- Note row 預設 fallback：`note.category or 'misc'`（原本是 `'unclassified'`）
- `saveSummaryAsNote()`：POST body 改 `category: 'misc', is_ai_summary: true`（原本 `category: 'personal'`）
- JS 種類顏色 map：`personal: '#9b59b6'` → `machine: '#9b59b6'`

**`templates/notes/editor.html`：**

- `個人` 按鈕 → `機台`，`data-category="personal"` → `data-category="machine"`
- 移除 `{% if cur_category == 'unclassified' %}` pill 整段（不再支援 unclassified 顯示）
- CSS class：
  - `.category-btn.selected[data-category="personal"]` → `[data-category="machine"]`（紫色 #9b59b6 沿用）
  - 移除 `[data-category="unclassified"]` CSS
- `cur_category` fallback 從 `'unclassified'` 改 `'misc'`
- `currentCategory` JS 變數初始化 fallback 同步改

### 5. 測試

- `tests/test_category.py`：personal/unclassified 相關 case 改寫成 machine；確認 modal CREATABLE 為 4 個分類
- 新增 1-2 個 case：`is_ai_summary=True` 的筆記不會出現在 `_build_all_content` query
- `tests/test_paragraph_diff.py` / `tests/test_shift.py` / `tests/test_cleanup.py`：grep `personal`/`unclassified` 看是否有 reference 要更新

### 6. AI 摘要儲存流程的標題（不變）

`saveSummaryAsNote` 產生的標題仍是 `YYYY-MM-DD 摘要`（例如 `2026-04-30 摘要`）。這是 backfill 比對的依據。

---

## 隔離 / 不影響的東西

- 不動 mynotes（私人筆記沒有 category）
- 不動 vetting
- 不動 PrivateNote / PrivateChecklistItem
- shift A/B/N 邏輯維持
- AI 摘要本身的 prompt 結構（`MANAGER_PROMPT`、輸出格式）不動

## 部署順序

1. push code（含 migration 008 檔案，但**不執行**）
2. 等 Zeabur 自動部署
3. 進 Zeabur container 跑：`cd /app && python migrations/versions/008_machine_category_and_ai_summary.py`
4. 驗證：
   - 進 admin 登入，看 filter tab 沒有「個人」「未分類」、有「機台」
   - 點「+ 新增筆記」，看 modal 有 機台 按鈕、沒個人/未分類
   - 用機台類建一筆、用雜事建一筆，看標題格式正確
   - 跑 AI 摘要，看 prompt 不包含先前 saveSummaryAsNote 存進去的「YYYY-MM-DD 摘要」筆記

## 風險 / 邊界

- **本機開發環境跑 migration 會失敗**：因本機沒 `DB_ENCRYPTION_KEY` 解不出 title。Migration 步驟 4 必須在 Zeabur container 跑。設計上 migration 的步驟 1-3 用 raw SQL（不需解密），步驟 4 用 ORM 在能解密的環境跑。
- **既有 71 筆 personal 一旦變 misc 就無法回溯**：使用者已確認 OK
- **Backfill 漏判風險**：若使用者改過 AI 摘要筆記的標題（從 `YYYY-MM-DD 摘要` 改成別的），backfill 不會標記為 `is_ai_summary=True`，下次仍會被吃進 AI 摘要。可接受（標題改過代表使用者主動把它當一般筆記了）。
