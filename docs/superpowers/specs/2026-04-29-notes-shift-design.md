# Notes 班別 (shift) 設計

**日期**：2026-04-29
**模組**：店務筆記 (`/notes/`)，不含私人筆記 (`/mynotes/`)

## 動機

現行新增筆記/確認表單流程只能選類別（員工/客人/雜事/個人），標題僅含日期 + 類別（或 username）。實際排班會分 A/B/N 三班，需要在標題中體現班別並支援依班別篩選。

## 範圍

- 新增筆記、新增確認表單流程：選類別 + 班別後再建立
- 列表頁加班別篩選列（cascade）
- 班別套用於**全部 4 個類別**（員工/客人/雜事/個人）
- 班別四選一：`A` / `B` / `N` / `不需要`（不需要 = NULL）

## 資料模型

### 新欄位
- `Note.shift VARCHAR(1) NULL`
  - 值域：`'A'` / `'B'` / `'N'` / `NULL`（NULL 即「不需要」）
  - 不加密（與 `category` 一致：是 enum 不是內容）
- 常數 `models.py`：
  - `SHIFT_CHOICES = ('A', 'B', 'N')`（不含 NULL；NULL 由 nullable 表達）
  - `SHIFT_LABELS = {'A': 'A', 'B': 'B', 'N': 'N'}`（先給單字母，未來要全名再加）

### Migration 007
- 新建 `migrations/versions/007_add_note_shift.py`
- `ALTER TABLE note ADD COLUMN shift VARCHAR(1) NULL`
- 舊資料維持 NULL（= 不需要）
- 不需要 backfill

## 標題產生規則

`notes/routes.py::new_note()` 改寫，依 `category` 與 `shift` 組合：

| category | shift | 標題 |
|----------|-------|------|
| employee/customer/misc | A/B/N | `M/D <S><label>` 例：`4/29 A員工` |
| employee/customer/misc | NULL  | `M/D <label>` 例：`4/29 員工`（不變） |
| personal | A/B/N | `M/D <S> <username>筆記` 例：`4/29 A hirain筆記` |
| personal | NULL  | `M/D <username>筆記`（不變） |
| unclassified | (任意) | 沿用舊 default（unclassified 不開放新建，僅舊資料用） |

`type_label` (筆記/確認表單) 規則維持原狀：personal 才在標題加，其他類別不加。

## 新增 modal UX

### 結構（取代 `templates/notes/index.html` 既有 `<dialog id="category-modal">`）

```
┌─ 選擇類別與班別 ─────────────┐
│  類別                       │
│  [員工] [客人] [雜事] [個人]   │
│                              │
│  班別                       │
│  [不需要] [A] [B] [N]        │
│                              │
│         [取消]  [確認]       │
└─────────────────────────────┘
```

### 行為
- 兩列各自單選，點到的按鈕高亮（class=`selected`，邊框/底色變色）
- 開啟時的預設：
  - 類別：`localStorage["notes_new_category"]`（無記憶 → 不選）
  - 班別：`localStorage["notes_new_shift"]`（無記憶 → 預設 `不需要`）
- **確認**按鈕兩列皆有選後 enable
- 確認後寫回 localStorage，導向 `/notes/new?type={note|checklist}&category={cat}&shift={A|B|N|none}`
  - URL 用 `none` 表示「不需要」（避免 empty string 與「未提供」混淆）
- 取消 / 點背景 → 關閉，不寫 localStorage

### 「+ 新增筆記」「☑ 新增確認表單」按鈕
不變（仍兩個按鈕，分別開 modal 並設 `_pendingType = 'note' | 'checklist'`）

## 列表頁班別 filter

### 位置
排序由上到下：店別 → 關鍵字 → **種類** → **班別（新）** → 時間 → 狀態 → 排列

### 按鈕
`[不需要] [A] [B] [N]` —— 4 個（無「全部」）。首次進頁面預設高亮 `不需要`。

### URL 參數
- 參數名 `shift`
- 值：`none` / `A` / `B` / `N`
- 未帶 `shift` 參數時：server 視為 `none`（即首次進頁面）

### Cascade
- 點店別 → 重置 category + shift + range + status
- 點種類 → 帶 store + category，重置 shift + range + status
  - shift 重置成 `none`（不是「全部」，因為沒有「全部」）
- 點班別 → 帶 store + category + shift，重置 range + status
- 點時間 → 帶 store + category + shift + range，重置 status
- 點狀態 → 帶全部
- 排列（sort）走 session，不影響 cascade

### Server-side filter（`notes/routes.py::index()`）
```python
shift_filter = request.args.get("shift", "none")  # default 'none'
if shift_filter == "none":
    query = query.filter(Note.shift.is_(None))
elif shift_filter in SHIFT_CHOICES:
    query = query.filter_by(shift=shift_filter)
# 其他值（含未來新值） → 不過濾，回退顯示全部（防呆）
```

## 後端改動清單

### `models.py`
- 加 `Note.shift = db.Column(db.String(1), nullable=True)`
- 新常數 `SHIFT_CHOICES = ('A', 'B', 'N')`、`SHIFT_LABELS`

### `notes/routes.py`
- import `SHIFT_CHOICES`
- `new_note()`：
  - 收 `request.args.get("shift", "none")`，驗證 → `'A'/'B'/'N'/None`
  - 標題組裝依 (category, shift) 走新規則
  - 傳 `default_shift` 給 template
- `create_note()` (POST)：寫入 `note.shift`
- `index()`：加 shift 過濾邏輯（同上 SQL）
- 模板 context 多帶：`current_shift`, `shift_choices=('none','A','B','N')`, `shift_labels`

### `notes/ws.py`
- WebSocket 廣播 note 時帶上 `shift` 欄位（與 `category` 處理同套路）
- 列表 JSON / dict 序列化加 `"shift": n.shift`

## 前端改動清單

### `templates/notes/index.html`
- modal HTML 改成兩列（類別 + 班別）+ 確認/取消
- JS：
  - `_pendingType` 既有
  - 新增 `_selCategory`, `_selShift` 狀態
  - `selectCategory(cat)` / `selectShift(s)` 更新狀態 + 高亮
  - `confirmCreate()` 取代原 `chooseCategoryAndGo`，組 URL 含 shift
  - localStorage 寫/讀
- 班別 filter 列：4 個 `<a>` tab，cascade URL 重置依規則
- 時間/狀態 cascade URL 帶 `shift=current_shift`（保留）
- 種類 cascade URL 不帶 shift（重置成 `none`）
- 店別 cascade URL 不帶 shift（重置成 `none`）

### `templates/notes/editor.html`
- 不顯示 shift badge（YAGNI）
- 不提供修改 shift 的 UI（YAGNI）
- 隱藏 input 帶 shift（編輯時不變更，建立時由 query string 帶來）

## 不做（YAGNI）

1. editor 顯示/編輯 shift（未來如要加，再開新工作）
2. 列表頁每筆 note 顯示 shift badge（未來如要加，再開新工作）
3. 私人筆記 mynotes 的 shift（使用者僅要求店務筆記）
4. 班別「全部」選項（使用者明說 4 個就好）
5. 班別中文/全名標籤（先用 `A` `B` `N`，未來加）
6. AI 摘要按班別分組（未來再說）

## 測試

新建 `tests/test_shift.py`：
- **標題組裝**：四種 (category × shift) 矩陣（共 8 case）
  - employee + A → `M/D A員工`
  - employee + None → `M/D 員工`
  - personal + A → `M/D A {username}筆記`
  - personal + None → `M/D {username}筆記`
  - employee/customer/misc + B/N 抽樣
- **班別 filter SQL**：
  - `?shift=none` → 只回 `Note.shift.is_(None)`
  - `?shift=A` → 只回 `shift='A'`
  - 不帶 → 同 `?shift=none`
  - `?shift=invalid` → 不過濾（回退）
- **Cascade URL 重置**：
  - 點店別後 URL 不帶 shift（重置成 `none`）
  - 點種類後 URL `shift=current_shift`（不重置）

## 已決策

- 班別不加密（enum 不是 PII）
- 「不需要」用 NULL 表達（不用 `''` 或 `'none'` 字串值）
- URL 用 `shift=none` 而非空字串（避免歧義）
- Filter 預設高亮 `不需要`（使用者明確要求）
- 不需要 / A / B / N 按鈕順序（使用者明確要求）
- 4 個 modal/filter 按鈕（無「全部」）
