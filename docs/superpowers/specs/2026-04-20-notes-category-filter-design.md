# Notes 類別分類 + 篩選器重構設計文件

**日期**：2026-04-20
**Repo / 目標**：`app_unified/`（github.com/stvlin1988-alt/Weather）
**範圍**：只動 `notes` 模組（店務筆記）。完全不碰 `mynotes`（私人筆記）。
**動機**：移除「優先權 高/中/低」這個實務上用不到的維度，改為「種類（員工/客人/雜事/個人）」分類，同時整理篩選器的邏輯和順序。

---

## 1. 資料模型變更

### Note 表

- **新增欄位**：`category TEXT NOT NULL DEFAULT 'unclassified'`
- **列舉值**：`employee` / `customer` / `misc` / `personal` / `unclassified`
- **不加密**：這是 enum，非敏感資料，且 SQL filter 要能走 index
- **移除欄位**：`priority`（drop column）

### ChecklistItem 表

- 不變。類別跟著父 Note 走，ChecklistItem 本身不加欄位。

### Python 常數（`notes/routes.py`）

```python
CATEGORY_CHOICES = ['employee', 'customer', 'misc', 'personal', 'unclassified']
CATEGORY_LABELS = {
    'employee': '員工',
    'customer': '客人',
    'misc': '雜事',
    'personal': '個人',
    'unclassified': '未分類',
}
CATEGORY_CREATABLE = ['employee', 'customer', 'misc', 'personal']  # 彈窗選項；'unclassified' 只用於舊資料
```

`PRIORITY_CHOICES` 整個刪掉。

---

## 2. 新增流程：彈窗 + 自動標題

### UI：類別選擇彈窗

`templates/notes/index.html:61-62` 的兩個按鈕改成觸發 modal：

```html
<button class="btn btn-primary" data-action="open-category-modal" data-type="note">+ 新增筆記</button>
<button class="btn btn-primary" data-action="open-category-modal" data-type="checklist">☑ 新增確認項目</button>
```

Modal（新增一個 `<dialog>` 在 index.html 底部）：
- 標題：「選擇類別」
- 4 個按鈕：員工 / 客人 / 雜事 / 個人
- 點擊 → `window.location = /notes/new?type={type}&category={cat}`
- 點外面或 ESC → 關閉
- 「未分類」**不出現**（只給舊資料用）

### 後端：`notes/routes.py::new_note()`（routes.py:192-204）

```python
category = request.args.get("category", "unclassified")
if category not in CATEGORY_CREATABLE:
    category = "unclassified"  # 防禦

today = datetime.now(_TW).strftime("%-m/%-d")
type_label = "確認表單" if note_type == "checklist" else "筆記"

if category == "personal":
    default_title = f"{today} {current_user.username}{type_label}"
else:
    default_title = f"{today} {CATEGORY_LABELS[category]}"
```

### POST `/notes/api`（routes.py:256-287）

- 原本讀 `priority` 的地方改讀 `category`
- 驗證：`category` 必須在 `CATEGORY_CHOICES` 裡；不在 → fallback `unclassified`（跟 `new_note()` GET 行為一致，不回 400）
- API 沒帶 category → default `unclassified`

### Editor 頁面（`templates/notes/editor.html:116-122`）

- 拿掉 `priority-selector`
- 加「類別 selector」：4 個 pill（員工/客人/雜事/個人）
- 舊資料若 `category='unclassified'` → 顯示第 5 個「未分類」pill（可切換成其他類別整理舊資料）
- 標題輸入框保留（使用者可自由改）

---

## 3. 列表頁篩選器

### UI 順序（由上到下）

```
┌─────────────────────────────────────────┐
│ [AI 摘要 (近1天)] ← admin/super_admin    │
├─────────────────────────────────────────┤
│ 店別：全部 | 店A | 店B ← super_admin     │
├─────────────────────────────────────────┤
│ 🔍 關鍵字輸入框                            │
├─────────────────────────────────────────┤
│ 種類：全部 | 員工 | 客人 | 雜事 | 個人 | 未分類 │
├─────────────────────────────────────────┤
│ 時間：今天 | 近3天 | 近7天 | 近月          │
├─────────────────────────────────────────┤
│ 狀態：全部 | 待處理 | 待討論 | 已解決      │
└─────────────────────────────────────────┘
```

UI 順序對齊 cascade 依賴（上游影響下游）。

### URL 參數設計（server-side tabs，純 `<a href>`）

| 使用者動作 | 導向的 URL | 效果 |
|---|---|---|
| 點「種類=員工」 | `?category=employee&range=today` | 時間 reset 今天，狀態 reset 全部 |
| 點「時間=近3天」 | `?category={current}&range=3d` | 狀態 reset 全部，種類維持 |
| 點「狀態=待處理」 | `?category={current}&range={current}&status=pending` | 都維持 |
| 點「店別=X」（super_admin）| `?store=X&category={current}&range={current}&status={current}` | 都維持（store 是獨立維度）|
| 輸入關鍵字送出 | `?q=xxx` | 其他全 reset，進關鍵字互斥模式 |
| 清空關鍵字 | `?category=&range=today&status=` | 回到預設篩選模式 |

首次進入 `/notes`：`category=`(空→全部) / `range=today` / `status=`(全部)

### 伺服器端篩選邏輯（`routes.py:152-170`）

```python
# 店別範圍：保持現行（admin 強制自己店 / super_admin 可選）

if keyword:
    # 互斥模式：其他篩選全部失效，進 Python-side substring match
    keyword_mode = True
    active_filter = 'keyword'
else:
    # AND 組合：category + range + status
    if category in CATEGORY_CHOICES:
        query = query.filter_by(category=category)
    if status in STATUS_CHOICES:
        query = query.filter_by(status=status)
    query = _date_filter(query, range_param or 'today')
    active_filter = 'combined'
```

關鍵字互斥邏輯保留現行（title/content 加密，必須 Python 端 filter）。

---

## 4. Migration 與舊資料處理

### 檔案：`migrations/versions/006_replace_priority_with_category.py`

沿用 005 的部署模式（`cd /app && python migrations/versions/006_xxx.py`）。
**DB 是 PostgreSQL**（生產）/ SQLite in-memory（test）。Migration 用 PostgreSQL syntax（跟 003、005 一致，用 `psycopg2`）。

### 步驟（依序，每步 idempotent）

1. **新增 `category` column**
   ```sql
   ALTER TABLE notes ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'unclassified'
   ```
   新舊 row 都自動填 `unclassified`。

2. **Drop `priority` column**
   ```sql
   ALTER TABLE notes DROP COLUMN IF EXISTS priority
   ```
   PostgreSQL 9.0+ 原生支援 `DROP COLUMN IF EXISTS`，失敗直接 throw。

3. **不碰任何加密欄位**：title / content / author_name / ai_summary 完全不動。migration 只動 schema。

### 程式端清理

- `models.py:9`：刪 `PRIORITY_CHOICES`，加 `CATEGORY_CHOICES` / `CATEGORY_LABELS` / `CATEGORY_CREATABLE`
- `models.py:97`：刪 `priority` 欄位，加 `category`
- `notes/routes.py`：刪所有 `PRIORITY_CHOICES` import 和 `priority_filter` 邏輯（7 處）；加 category 對應邏輯
- `notes/ws.py`：同 routes.py，刪所有 `PRIORITY_CHOICES` / `priority` 處理（5 處），改 category
- `templates/notes/editor.html`：刪 priority-selector 的 CSS、HTML、JS 變數、selectPriority() 函式、save body payload，全改 category
- `templates/notes/index.html`：
  - 按鈕改 modal 觸發（`+ 新增筆記` / `☑ 新增確認表單`）
  - 篩選 tab：刪 priority tab、加 category tab、重組順序
  - badge 改 category badge
  - pagination href 同步改
  - 底部加 `<dialog>` modal

### 遷移後行為保證

- 舊資料 `category='unclassified'` 會出現在列表「未分類」tab
- 使用者進 editor 能切到正確類別並儲存
- 搜尋、AI 摘要不受影響

### 回滾方案

- 若部署後才發現問題：寫 006_rollback.py 把 `category` drop、`priority` 加回（舊資料 default `medium`）
- **priority 原值被捨棄，不備份**（使用者選擇 A：直接捨棄）

---

## 5. 測試策略

### 新增測試檔：`tests/test_category.py`

| Test | 目的 |
|---|---|
| `test_new_note_with_category_employee` | `GET /notes/new?type=note&category=employee` → default_title = `4/20 員工` |
| `test_new_note_with_category_personal` | `?category=personal` → default_title = `4/20 {username}筆記` |
| `test_new_checklist_with_category_customer` | `?type=checklist&category=customer` → `4/20 客人` |
| `test_new_note_without_category` | 沒帶 category → default `unclassified`，title fallback |
| `test_new_note_invalid_category` | `?category=foo` → 防禦回 `unclassified` |
| `test_post_api_accepts_valid_category` | POST `/notes/api` body 含 `category=employee` → DB 正確寫入 |
| `test_post_api_rejects_invalid_category` | `category=hacker` → fallback `unclassified`（不回 400）|
| `test_filter_by_category` | 建 3 筆（員工/客人/雜事）→ `/notes?category=employee` → 只回員工 |
| `test_filter_cascade_category_resets_time` | 檢查 category tab 的 href 會 reset range 成 today |
| `test_filter_cascade_time_resets_status` | 檢查 range tab 的 href 會 reset status 成空 |
| `test_filter_keyword_exclusive` | `?q=xxx&category=employee` → 只套用 keyword，category 被忽略 |
| `test_filter_unclassified_visible` | 建一筆 unclassified → `?category=unclassified` 篩得到 |

### Migration 測試

Test DB 是 SQLite in-memory，Migration 是 PostgreSQL-only（用 psycopg2）。不在 pytest 裡跑 migration SQL。改為：

- Model 層測試：新建 Note 時 `category` 預設 `unclassified`，可寫入所有合法 enum 值（已在 `test_filter_by_category` 等涵蓋）
- Migration 手動驗證（部署前）：在 local 開一個 PostgreSQL container，跑 up，驗證：
  1. `\d notes` 顯示 `category` column 存在、`priority` 不存在
  2. 連跑兩次不出錯（`IF NOT EXISTS` / `IF EXISTS` 保證）
  3. 事前 INSERT 幾筆 note，跑 migration 後 SELECT 筆數、title、content 不變

### 手動驗收清單（部署後）

- [ ] 彈窗 4 選項出現
- [ ] 選「員工」→ editor 標題帶「員工」不帶 username
- [ ] 選「個人」→ editor 標題帶 username
- [ ] editor 裡類別 pill 切換可儲存
- [ ] 列表頁 badge 顯示正確類別標籤
- [ ] 「未分類」tab 顯示舊資料
- [ ] cascade：點種類→時間自動 reset
- [ ] cascade：點時間→狀態自動 reset
- [ ] 關鍵字輸入後其他篩選失效
- [ ] super_admin 切店別，其他篩選維持

---

## 6. 不做的事（明確排除）

- **不碰 mynotes**：私人筆記的資料表、templates、routes 全部不動
- **不備份 priority 值**：直接捨棄舊的優先權資料
- **不改 ChecklistItem 模型**：類別在 Note 層級
- **不做 cascade 的 client-side JS**：完全用 server-side `<a href>` tabs
- **不動加密欄位**：title/content/author_name 等在 migration 中完全不碰

---

## 7. 部署順序

1. push code（含 migration 檔）
2. Zeabur 自動部署
3. 進 Zeabur container 執行：`cd /app && python migrations/versions/006_replace_priority_with_category.py`
4. 手動驗收清單走一遍

---

**Spec Status**：ready for implementation plan
