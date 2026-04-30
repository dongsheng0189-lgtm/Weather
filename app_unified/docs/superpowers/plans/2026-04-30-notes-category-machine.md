# Notes Category 個人→機台 + AI 摘要不再被吃 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把店務筆記分類從「員工/客人/雜事/個人(+未分類)」改成「員工/客人/機台/雜項」（4 類），AI 摘要的一般筆記與確認表單預分類也統一成這 4 類；新增 `Note.is_ai_summary` 欄位讓 AI 摘要產生的筆記下次不被當素材吃進去。

**Architecture:**
- Models 層：`CATEGORY_CHOICES` 移除 `personal`/`unclassified`、新增 `machine`；`misc` label 從「雜事」改「雜項」；新增 `Note.is_ai_summary BOOLEAN`。
- Migration 008：本機只跑步驟 1-3（純 SQL）、Zeabur container 跑步驟 4（用 ORM 解密 title 比對「YYYY-MM-DD 摘要」backfill `is_ai_summary`）。
- Backend：`_classify_by_title`/`_build_categorized_checklist`/`MANAGER_PROMPT`/`HEADER_BY_CAT` 統一 4 類；`_build_all_content` 加 `is_ai_summary == False` 過濾。
- Frontend：modal/filter/editor 按鈕順序與 label 全改；`saveSummaryAsNote` POST 帶 `category=misc, is_ai_summary=true`。

**Tech Stack:** Flask + SQLAlchemy + Flask-SocketIO + Jinja2 + PostgreSQL（Zeabur） + AES-256-GCM EncryptedText + pytest

**Spec:** `docs/superpowers/specs/2026-04-30-notes-category-machine.md`

---

## File Structure

| 檔案 | 動作 | 主要責任 |
|---|---|---|
| `models.py` | Modify | CATEGORY 常數 + `Note.is_ai_summary` 欄位 |
| `migrations/versions/008_machine_category_and_ai_summary.py` | Create | DB schema + data migration |
| `notes/routes.py` | Modify | `_classify_by_title`/`_build_categorized_checklist`/`MANAGER_PROMPT`/`_build_output_structure_prompt`/`_format_note_for_ai`/`_build_all_content`/`new_note`/`list_notes`/`create_note`/`get_note`/`update_note` |
| `notes/ws.py` | Modify | `_list_notes`/`_create_note`/`_get_note`/`_update_note` defaults + `is_ai_summary` |
| `templates/notes/index.html` | Modify | Modal 4 類 + filter tabs + badge + `saveSummaryAsNote` |
| `templates/notes/editor.html` | Modify | 4 個分類按鈕 + CSS + fallback |
| `tests/test_category.py` | Modify | 既有 case 改寫 + 新增 4 類 / `is_ai_summary` / `_classify_by_title` 4 桶 / `_build_categorized_checklist` 走 `note.category` 等 case |

---

### Task 1: Models — CATEGORY 常數與 `Note.is_ai_summary` 欄位

**Files:**
- Modify: `models.py:9-17` (CATEGORY 常數), `models.py:108`-附近 (Note.category default), `models.py` (新增 `is_ai_summary` Column)

- [ ] **Step 1: 改 `CATEGORY_CHOICES` / `CATEGORY_LABELS` / `CATEGORY_CREATABLE`**

把 `models.py` 第 9-17 行改成：

```python
CATEGORY_CHOICES = ["employee", "customer", "machine", "misc"]
CATEGORY_LABELS = {
    "employee": "員工",
    "customer": "客人",
    "machine": "機台",
    "misc": "雜項",
}
CATEGORY_CREATABLE = ["employee", "customer", "machine", "misc"]
```

- [ ] **Step 2: 改 `Note.category` server default**

在 `models.py` 找到 `Note` model 中：
```python
category = db.Column(db.Text, nullable=False, default="unclassified")
```
改成：
```python
category = db.Column(db.Text, nullable=False, default="misc")
```

- [ ] **Step 3: 新增 `Note.is_ai_summary` 欄位**

在 `Note` model 中、`shift` 欄位下面新增：

```python
is_ai_summary = db.Column(db.Boolean, nullable=False, default=False, server_default="false")
```

（位置：`Note` class 中，與其他 column 並列；找到 `shift = db.Column(db.String(1), nullable=True)` 那一行下面加。）

- [ ] **Step 4: 跑 sanity import 確認語法**

Run: `cd /home/hirain0126/projects/webapp && source venv/bin/activate && cd app_unified && python -c "from models import CATEGORY_CHOICES, CATEGORY_LABELS, CATEGORY_CREATABLE, Note; print(CATEGORY_CHOICES); print(CATEGORY_LABELS); print(hasattr(Note, 'is_ai_summary'))"`

Expected:
```
['employee', 'customer', 'machine', 'misc']
{'employee': '員工', 'customer': '客人', 'machine': '機台', 'misc': '雜項'}
True
```

- [ ] **Step 5: Commit**

```bash
git add models.py
git commit -m "feat(notes): swap personal→machine, drop unclassified, add Note.is_ai_summary

- CATEGORY_CHOICES = [employee, customer, machine, misc]
- misc label 雜事 → 雜項 (matches AI summary prompt)
- Note.is_ai_summary BOOL default false (excludes AI-summary-saved notes from future AI summaries)"
```

---

### Task 2: 更新既有 test_category.py（讓既有 test 對齊新常數，非新功能 test）

**Files:**
- Modify: `tests/test_category.py:21` (`_mk_note` default), `tests/test_category.py:46-60` (前兩個 test)

- [ ] **Step 1: `_mk_note` default category 改 `"misc"`**

把第 21 行：
```python
def _mk_note(db_session, user, category="unclassified", **kwargs):
```
改成：
```python
def _mk_note(db_session, user, category="misc", **kwargs):
```

- [ ] **Step 2: 改 `test_category_default_is_unclassified` 名稱與斷言**

把第 46-51 行整個 test 改成：

```python
def test_category_default_is_misc(app, db_session):
    u = _mk_user(db_session)
    n = Note(user_id=u.id, author_name=u.username, title="x", content="y", store=u.store)
    db_session.add(n)
    db_session.commit()
    assert n.category == "misc"
```

- [ ] **Step 3: 改 `test_category_constants_defined` 內容**

把第 53-60 行整個 test 改成：

```python
def test_category_constants_defined():
    assert CATEGORY_CHOICES == ["employee", "customer", "machine", "misc"]
    assert CATEGORY_LABELS["employee"] == "員工"
    assert CATEGORY_LABELS["customer"] == "客人"
    assert CATEGORY_LABELS["machine"] == "機台"
    assert CATEGORY_LABELS["misc"] == "雜項"
    assert "personal" not in CATEGORY_LABELS
    assert "unclassified" not in CATEGORY_LABELS
    assert CATEGORY_CREATABLE == ["employee", "customer", "machine", "misc"]
```

- [ ] **Step 4: 全文搜尋 `tests/test_category.py` 殘留的 personal / unclassified 引用**

Run: `grep -n "personal\|unclassified" tests/test_category.py`

如有任何 hit（除 import 行外），把 `"personal"` 改 `"machine"`、把 `"unclassified"` 改 `"misc"`、把中文 `"個人"` 改 `"機台"`、把 `"未分類"` 改 `"雜項"`。

- [ ] **Step 5: 跑這支 test 確認 pass**

Run: `cd /home/hirain0126/projects/webapp && source venv/bin/activate && cd app_unified && DB_ENCRYPTION_KEY="$(python -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" pytest tests/test_category.py -v 2>&1 | tail -30`

Expected: 所有 test pass（如果有失敗，可能是 fixture/route 還沒改，先記下來，會在後面 task 中修）。

注意：若有依賴 routes.py 中還沒改的舊 `unclassified` fallback 的 test 暫時失敗，跳過 Step 5 的失敗，到 Task 5 完成後再回來跑全套。

- [ ] **Step 6: Commit**

```bash
git add tests/test_category.py
git commit -m "test(category): align existing cases with new 4-category scheme"
```

---

### Task 3: 新增 `_classify_by_title` / `_build_categorized_checklist` / `_build_all_content` 的失敗測試（TDD）

**Files:**
- Modify: `tests/test_category.py` (在檔案末尾新增 test)

- [ ] **Step 1: 在 test_category.py 末尾新增 5 個 test**

附加到檔案末尾（用 Read 確認檔案末尾再 Append；或 Edit 把這段插在最後一個 test 之後）：

```python
# ─── 新增：_classify_by_title 4 桶測試 ───
from notes.routes import _classify_by_title

def test_classify_by_title_employee():
    assert _classify_by_title("員工排班") == "員工"
    assert _classify_by_title("4/30 員工") == "員工"
    assert _classify_by_title("請假紀錄") == "員工"

def test_classify_by_title_customer():
    assert _classify_by_title("客人意見") == "客人"
    assert _classify_by_title("客訴處理") == "客人"
    assert _classify_by_title("顧客需求") == "客人"

def test_classify_by_title_machine():
    assert _classify_by_title("機台保養") == "機台"
    assert _classify_by_title("故障排除") == "機台"
    assert _classify_by_title("設備維修") == "機台"

def test_classify_by_title_fallback_to_misc():
    assert _classify_by_title("隨便寫的") == "雜項"
    assert _classify_by_title("") == "雜項"
    assert _classify_by_title(None) == "雜項"


# ─── 新增：_build_categorized_checklist 走 note.category 測試 ───
from notes.routes import _build_categorized_checklist
from models import ChecklistItem

def _mk_checklist_note(db_session, user, category, items):
    n = Note(
        user_id=user.id, author_name=user.username,
        title=f"標題-{category}", content="",
        store=user.store, status="pending", category=category,
        note_type="checklist",
    )
    db_session.add(n)
    db_session.flush()
    for text, checked in items:
        ci = ChecklistItem(note_id=n.id, text=text, is_checked=checked,
                           checked_by_name=user.username if checked else None)
        db_session.add(ci)
    db_session.commit()
    return n

def test_build_categorized_checklist_routes_by_category(app, db_session):
    u = _mk_user(db_session)
    _mk_checklist_note(db_session, u, "employee", [("員工A 任務", False)])
    _mk_checklist_note(db_session, u, "customer", [("客人B 任務", False)])
    _mk_checklist_note(db_session, u, "machine", [("機台C 任務", False)])
    _mk_checklist_note(db_session, u, "misc", [("雜事D 任務", False)])

    cls = Note.query.filter_by(note_type="checklist").all()
    block = _build_categorized_checklist(cls)

    assert "## 👥 員工" in block and "員工A 任務" in block
    assert "## 🧑‍🤝‍🧑 客人" in block and "客人B 任務" in block
    assert "## 🔧 機台" in block and "機台C 任務" in block
    assert "## 📋 雜項" in block and "雜事D 任務" in block


# ─── 新增：_build_all_content 過濾 is_ai_summary ───
from notes.routes import _build_all_content

def test_build_all_content_excludes_ai_summary(app, db_session):
    u = _mk_user(db_session, store="B")
    _mk_note(db_session, u, category="misc", title="一般筆記", content="一般內容", store="B")
    s = _mk_note(db_session, u, category="misc", title="2026-04-30 摘要", content="AI 結果", store="B")
    s.is_ai_summary = True
    db_session.commit()

    block, count, has_cl = _build_all_content("B")
    assert "一般筆記" in block
    assert "2026-04-30 摘要" not in block
    assert count == 1
```

- [ ] **Step 2: 跑這 5 個新 test，確認**全部 fail

Run: `cd /home/hirain0126/projects/webapp && source venv/bin/activate && cd app_unified && DB_ENCRYPTION_KEY="$(python -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" pytest tests/test_category.py -k "classify_by_title or categorized_checklist or excludes_ai_summary" -v 2>&1 | tail -30`

Expected: 5 個 test 都 fail（理由：目前的 `_classify_by_title` 還沒有「機台」、會回「店務」；`_build_categorized_checklist` 用標題 keyword 不是 category；`_build_all_content` 沒過濾 is_ai_summary）。

如果有 collection error（例如 import 失敗），先修 import 再跑。

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/test_category.py
git commit -m "test(category): add failing tests for 4-bucket classification + is_ai_summary filter"
```

---

### Task 4: Backend — `_classify_by_title` + `_build_categorized_checklist` + `HEADER_BY_CAT`

**Files:**
- Modify: `notes/routes.py:686-694` (`_classify_by_title`), `notes/routes.py:697-761` (`_build_categorized_checklist`)

- [ ] **Step 1: 重寫 `_classify_by_title`**

把第 685-694 行整個函式（找到 `def _classify_by_title(title):`）替換成：

```python
def _classify_by_title(title):
    """根據表單標題關鍵字分類，回傳中文類別名（員工/客人/機台/雜項）。

    僅在 `note.category` 不在 CATEGORY_CHOICES 時做為 fallback 使用。
    """
    t = (title or "").lower()
    if any(k in t for k in ("客人", "顧客", "客訴", "客戶")):
        return "客人"
    if any(k in t for k in ("同事", "員工", "人事", "排班", "請假")):
        return "員工"
    if any(k in t for k in ("機台", "保養", "故障", "設備", "修繕", "維修")):
        return "機台"
    return "雜項"
```

- [ ] **Step 2: 重寫 `_build_categorized_checklist`**

把第 697-761 行整個函式替換成：

```python
def _build_categorized_checklist(checklists, since=None):
    """Python 端預分類 + 跨表單去重，回傳已分好類的文字區塊。

    分類來源：優先用 note.category（建立時使用者已選），無對應時 fallback 標題 keyword。
    去重：跨表單同字面文字只保留一次，已完成優先於未完成。
    """
    # enum → 中文桶
    CATEGORY_TO_BUCKET = {
        "employee": "員工",
        "customer": "客人",
        "machine": "機台",
        "misc": "雜項",
    }
    categories = {
        "員工": {"done": [], "undone": []},
        "客人": {"done": [], "undone": []},
        "機台": {"done": [], "undone": []},
        "雜項": {"done": [], "undone": []},
    }
    seen = set()

    for n in checklists:
        cat_enum = getattr(n, "category", None)
        cat = CATEGORY_TO_BUCKET.get(cat_enum) or _classify_by_title(n.title)
        items = sorted(n.checklist_items or [], key=lambda i: (i.updated_at or i.created_at), reverse=True)
        done_all = [i for i in items if i.is_checked]
        if since is not None:
            done = [i for i in done_all if (i.updated_at or i.created_at) >= since]
        else:
            done = done_all
        undone = [i for i in items if not i.is_checked]

        for i in done:
            key = i.text.strip()
            if key not in seen:
                seen.add(key)
                who = i.checked_by_name or "?"
                categories[cat]["done"].append(f"- [{who}] {i.text}")

        for i in undone:
            key = i.text.strip()
            if key not in seen:
                seen.add(key)
                author = n.author_name or (n.author.username if n.author else "?")
                categories[cat]["undone"].append(f"- [{author}] {i.text}")

    # 組裝輸出（順序：員工/客人/機台/雜項）
    cat_labels = {
        "員工": "## 👥 員工",
        "客人": "## 🧑‍🤝‍🧑 客人",
        "機台": "## 🔧 機台",
        "雜項": "## 📋 雜項",
    }
    blocks = []
    for cat_key in ("員工", "客人", "機台", "雜項"):
        done_lines = categories[cat_key]["done"]
        undone_lines = categories[cat_key]["undone"]
        if not done_lines and not undone_lines:
            continue
        lines = [cat_labels[cat_key]]
        if done_lines:
            lines.append("### ✅ 已完成")
            lines.extend(done_lines)
        if undone_lines:
            lines.append("### ❌ 未完成")
            lines.extend(undone_lines)
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks) if blocks else "（無項目）"
```

- [ ] **Step 3: 跑剛才寫的 4+1 個 test 確認前 4 個 pass（`_classify_by_title` + `_build_categorized_checklist`）**

Run: `cd /home/hirain0126/projects/webapp && source venv/bin/activate && cd app_unified && DB_ENCRYPTION_KEY="$(python -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" pytest tests/test_category.py -k "classify_by_title or categorized_checklist" -v 2>&1 | tail -20`

Expected: 5 個 test pass（excludes_ai_summary 仍會 fail 因為還沒處理）。

- [ ] **Step 4: Commit**

```bash
git add notes/routes.py
git commit -m "feat(notes): unify checklist pre-classification into 4 buckets (員工/客人/機台/雜項)

Use note.category first; fall back to title keywords only when category is unset/unknown."
```

---

### Task 5: Backend — `_format_note_for_ai` + `_build_all_content` + `MANAGER_PROMPT` + `_build_output_structure_prompt`

**Files:**
- Modify: `notes/routes.py:625-650` (`_format_note_for_ai`), `notes/routes.py:838-845` (`_build_all_content`), `notes/routes.py:501-543` (`MANAGER_PROMPT`), `notes/routes.py:797-835` (`_build_output_structure_prompt`)

- [ ] **Step 1: 移除 `_format_note_for_ai` 中的 personal cat_hint 分支**

把第 645-647 行：
```python
    cat_hint = ""
    if getattr(n, "category", None) == "personal":
        cat_hint = "[分類提示：此筆記為個人類別，請歸入雜項處理]\n"
    return f"{_note_source_label(n)}\n{cat_hint}{n.title}\n{content}"
```
改成：
```python
    return f"{_note_source_label(n)}\n{n.title}\n{content}"
```
（同時移除 `cat_hint` 變數定義）

- [ ] **Step 2: `_build_all_content` 加 `is_ai_summary != True` 過濾**

把第 838-845 行整個函式替換成：

```python
def _build_all_content(store_name=None):
    """收集同店所有筆記（排除 AI 摘要產生的 is_ai_summary=True 筆記），
    合併成 all_content（舊介面，供 summarize 使用）"""
    query = Note.query.filter(Note.is_ai_summary == False)  # noqa: E712
    if store_name:
        query = query.filter_by(store=store_name)
    notes = query.order_by(Note.updated_at.desc()).all()
    data_block, has_cl, nreg, ncl = _build_ai_sections(notes)
    return data_block, len(notes), has_cl
```

注意：用 `Note.is_ai_summary == False` 而非 `is False`（SQLAlchemy 慣用）；`# noqa: E712` 抑制 pep8 對「比較 False」的警告。

- [ ] **Step 3: 改寫 `MANAGER_PROMPT`**

替換第 501-543 行整個 `MANAGER_PROMPT = """..."""`：

```python
MANAGER_PROMPT = """# Role
你是一位專業的店務連鎖店總管，負責將各分店或各部門的雜亂筆記，快速整理成給老闆看的「每日彙整表」。

# Task
請將輸入的所有原始文字進行分類摘要。要求：
1. **不准遺漏**：每個提到的事件都必須轉換成一條列點。
2. **保留關鍵資訊**：每條必須保留人名（員工名、顧客名），刪除口語、形容詞、抱怨，保留「誰 + 做了什麼 + 結果」。
3. **依分類輸出**：依筆記原始分類（員工/客人/機台/雜項）放入對應段落；無法判斷的一律放入【雜項】。

# Categories（4 類）
- 【員工】：員工請假、排班、人事、獎懲、員工互動、人力短缺。
- 【客人】：客訴、顧客反應、會員、客人需求、消費。
- 【機台】：機台故障、保養、操作、設備修繕、報修進度、維修紀錄。
- 【雜項】：不屬於上述三類的所有事項（含進貨、庫存、營收、活動、衛生等店務雜事）。

# Output Style
依「類別」分組輸出，每個類別用 `## 類別名` 作為標題，
該類別下方所有事項用數字列表（1. 2. 3. ...）依序排列。
沒有事項的類別請直接省略，不要輸出空標題。

# 去重規則（極度重要）
- 若多篇筆記提到同一事件（如 A 寫「冰箱壞了」、B 寫「冰箱還沒修」），
  請合併成**一條**列點，不要每篇各列一次。
- 內容高度重疊的事項請合併；互補資訊併入同一條後面。
- **絕對禁止**在輸出中連續重複同一句話或同一條目。
- **絕對禁止**同一事件在同一類別下列出兩次以上。每個事項只能出現一次。

# 禁止事項
- 禁止憑空生成輸入資料中不存在的事項。
- 若輸入沒有對應類別的事項，該類別省略，不得虛構。
- 禁止為了填滿輸出而重複貼相同列點。

範例：
## 員工
1. 小明午班請假：已協調小華代班。
2. 阿華遲到 30 分鐘，已口頭告誡。

## 機台
1. 廚房冰箱漏水：已請廠商週三來修。

# 結尾（必須遵守）
整理完所有內容後，在最後一行輸出：「--- 摘要整理結束 ---」
"""
```

- [ ] **Step 4: 改寫 `_build_output_structure_prompt`**

把第 825-834 行（`parts.append("**僅限**處理...")` 那一段）的描述句改成新的 4 類 emoji：

找到：
```
"確認表單的資料**已經由系統預先分類並去重完成**，分為 🏪店務 / 👥員工 / 🧑‍🤝‍🧑客人 / 📋其他雜事 四大類，每類下有 ✅已完成 和 ❌未完成。\n\n"
```
改成：
```
"確認表單的資料**已經由系統預先分類並去重完成**，分為 👥員工 / 🧑‍🤝‍🧑客人 / 🔧機台 / 📋雜項 四大類，每類下有 ✅已完成 和 ❌未完成。\n\n"
```

- [ ] **Step 5: 跑全套 test_category.py 確認 7 個新 test + 既有 test 全綠**

Run: `cd /home/hirain0126/projects/webapp && source venv/bin/activate && cd app_unified && DB_ENCRYPTION_KEY="$(python -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" pytest tests/test_category.py -v 2>&1 | tail -30`

Expected: 全綠（含 `test_build_all_content_excludes_ai_summary`）。如有 fail，先看 traceback 是否因 SQL DDL（is_ai_summary 欄位本機 SQLite 沒建）。SQLite 用 `db.create_all()` 應自動建欄位（因為 model 已加），所以理論上 OK。

- [ ] **Step 6: Commit**

```bash
git add notes/routes.py
git commit -m "feat(notes): unify AI summary into 4 categories + exclude is_ai_summary

- MANAGER_PROMPT now uses 員工/客人/機台/雜項 (replaces 人事/修繕/店務/雜項)
- _format_note_for_ai drops personal cat hint
- _build_all_content filters out is_ai_summary=True notes
- _build_output_structure_prompt emoji updated"
```

---

### Task 6: Backend — `notes/routes.py` GET/POST handlers + 預設值

**Files:**
- Modify: `notes/routes.py:208-238` (`new_note`), `notes/routes.py:243-291` (`list_notes`), `notes/routes.py:296-330` (`create_note`), `notes/routes.py:332-356` (`get_note`), `notes/routes.py:358-422` (`update_note`)

- [ ] **Step 1: `new_note()` 移除 personal/unclassified 分支，統一標題格式**

替換第 208-238 行整個 `def new_note():` 函式：

```python
@notes_bp.route("/new", methods=["GET"])
@login_required
def new_note():
    stores = _get_stores()
    note_type = request.args.get("type", "note")
    if note_type not in ("note", "checklist"):
        note_type = "note"
    category = request.args.get("category", "misc")
    if category not in CATEGORY_CREATABLE:
        category = "misc"
    # shift: 'A'/'B'/'N' or 'none' (= NULL)；其他值一律回退成 None
    shift_param = request.args.get("shift", "none")
    shift = shift_param if shift_param in SHIFT_CHOICES else None

    today = datetime.now(_TW).strftime("%-m/%-d")
    type_label = "確認表單" if note_type == "checklist" else "筆記"
    # 統一格式：「{date} {shift}{label}」例如 4/30 員工、4/30 A機台、4/30 雜項
    shift_glue = f"{shift}" if shift else ""
    default_title = f"{today} {shift_glue}{CATEGORY_LABELS[category]}"

    return render_template("notes/editor.html", note=None, stores=stores,
                           status_choices=STATUS_CHOICES,
                           category_choices=CATEGORY_CHOICES,
                           category_labels=CATEGORY_LABELS,
                           default_title=default_title,
                           note_type=note_type,
                           default_category=category,
                           default_shift=shift)
```

注意 `type_label` 變數雖然定義了但這版不再使用（4 個分類都走 label 模式，不再有 username 模式）。把它一起移除：刪除 `type_label = "確認表單" if note_type == "checklist" else "筆記"` 那一行。

- [ ] **Step 2: `list_notes()` 預設 fallback 改 `misc`（line 286）**

把：
```python
"category": n.category or "unclassified",
```
改成：
```python
"category": n.category or "misc",
```

- [ ] **Step 3: `create_note()` 預設 fallback 改 `misc` + 接收 `is_ai_summary`**

找到第 307 行：
```python
category = data.get("category") if data.get("category") in CATEGORY_CHOICES else "unclassified"
```
改成：
```python
category = data.get("category") if data.get("category") in CATEGORY_CHOICES else "misc"
is_ai_summary = bool(data.get("is_ai_summary", False))
```

然後找到 `Note(...)` 建構（在 `create_note` 內部）：在 kwargs 中加 `is_ai_summary=is_ai_summary,`。例如本來：
```python
note = Note(
    user_id=current_user.id,
    author_name=current_user.username,
    note_type=note_type,
    title=..., content=..., store=store, status=status, category=category, shift=shift,
    created_at=now, updated_at=now,
)
```
改成：
```python
note = Note(
    user_id=current_user.id,
    author_name=current_user.username,
    note_type=note_type,
    title=..., content=..., store=store, status=status, category=category, shift=shift,
    is_ai_summary=is_ai_summary,
    created_at=now, updated_at=now,
)
```
（用 Read 看實際參數名再 Edit；不要照抄省略號）

- [ ] **Step 4: `get_note()` 預設 fallback 改 `misc`（line 348）**

把：
```python
"category": note.category or "unclassified",
```
改成：
```python
"category": note.category or "misc",
```

- [ ] **Step 5: `update_note()` 與 `index()` route 中其他殘留**

Run: `grep -n "unclassified" notes/routes.py`
逐個 hit 改成 `"misc"`（仔細看 context 再改）。

- [ ] **Step 6: Pyflakes / quick import sanity**

Run: `cd /home/hirain0126/projects/webapp && source venv/bin/activate && cd app_unified && python -c "import notes.routes" && echo OK`

Expected: `OK`

- [ ] **Step 7: 跑 test_category.py + test_paragraph_diff.py + test_shift.py**

Run: `cd /home/hirain0126/projects/webapp && source venv/bin/activate && cd app_unified && DB_ENCRYPTION_KEY="$(python -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" pytest tests/test_category.py tests/test_paragraph_diff.py tests/test_shift.py -v 2>&1 | tail -40`

Expected: 全綠（如有 unclassified 殘留導致 fail，回上面 Step 5 grep 補修）。

- [ ] **Step 8: Commit**

```bash
git add notes/routes.py
git commit -m "feat(notes): default category misc, accept is_ai_summary, unify title format"
```

---

### Task 7: Backend — `notes/ws.py`

**Files:**
- Modify: `notes/ws.py:90` (list_notes JSON), `notes/ws.py:107` (create_note default), `notes/ws.py:117` (Note kwargs), `notes/ws.py:217` (get_note JSON)

- [ ] **Step 1: 改 3 處 `unclassified` → `misc`**

逐個改：

第 90 行 `'category': n.category or 'unclassified',` → `'category': n.category or 'misc',`
第 107 行 `category = data.get('category') if data.get('category') in CATEGORY_CHOICES else 'unclassified'` → `else 'misc'`
第 217 行 `'category': note.category or 'unclassified',` → `'category': note.category or 'misc',`

- [ ] **Step 2: WS `_create_note` 接收 `is_ai_summary`**

在第 107 行下面，與 `category` / `note_type` / `shift` 並列，新增：

```python
is_ai_summary = bool(data.get('is_ai_summary', False))
```

然後找到（第 111-119 附近）`note = Note(...)` 建構，在 kwargs 中加 `is_ai_summary=is_ai_summary,`。

- [ ] **Step 3: 全文最後一掃**

Run: `grep -n "unclassified" notes/ws.py`

Expected: 沒有 hit（除註解中可能殘留）。

- [ ] **Step 4: Sanity import**

Run: `cd /home/hirain0126/projects/webapp && source venv/bin/activate && cd app_unified && python -c "import notes.ws" && echo OK`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add notes/ws.py
git commit -m "feat(notes/ws): align WS handlers with new defaults + is_ai_summary"
```

---

### Task 8: Frontend — `templates/notes/index.html`（modal + filter + badge + saveSummaryAsNote）

**Files:**
- Modify: `templates/notes/index.html:39-44` (badge CSS), `templates/notes/index.html:116` (filter tabs), `templates/notes/index.html:185` (badge fallback), `templates/notes/index.html:225-240` (modal buttons), `templates/notes/index.html:256` (color map), `templates/notes/index.html:299` (localStorage validation), `templates/notes/index.html:444-455` (saveSummaryAsNote)

- [ ] **Step 1: 改 badge CSS（第 39-44 行）**

把：
```css
.category-badge.category-employee    { background:#3498db; }
.category-badge.category-customer    { background:#e67e22; }
.category-badge.category-misc        { background:#95a5a6; }
.category-badge.category-personal    { background:#9b59b6; }
.category-badge.category-unclassified{ background:#bdc3c7; color:#555; }
```
改成：
```css
.category-badge.category-employee { background:#3498db; }
.category-badge.category-customer { background:#e67e22; }
.category-badge.category-machine  { background:#9b59b6; }
.category-badge.category-misc     { background:#95a5a6; }
```

- [ ] **Step 2: 改 filter tabs（第 116 行）**

把：
```jinja
{% set cat_tab_labels = [('','全部'),('employee','員工'),('customer','客人'),('misc','雜事'),('personal','個人'),('unclassified','未分類')] %}
```
改成：
```jinja
{% set cat_tab_labels = [('','全部'),('employee','員工'),('customer','客人'),('machine','機台'),('misc','雜項')] %}
```

- [ ] **Step 3: 改 badge fallback（第 185 行）**

把：
```jinja
<span class="category-badge category-{{ note.category or 'unclassified' }}">{{ category_labels.get(note.category or 'unclassified', note.category) }}</span>
```
改成：
```jinja
<span class="category-badge category-{{ note.category or 'misc' }}">{{ category_labels.get(note.category or 'misc', note.category) }}</span>
```

- [ ] **Step 4: 改 modal 按鈕順序（員工/客人/機台/雜項）**

找到第 225-240 行的 `<dialog id="category-modal">` 區塊。把按鈕順序與內容改成 4 顆，順序：員工、客人、機台、雜項。

替換：
```html
<button type="button" class="cm-cat-btn" data-cat="employee" onclick="selectCategory('employee')" style="...">員工</button>
<button type="button" class="cm-cat-btn" data-cat="customer" onclick="selectCategory('customer')" style="...">客人</button>
<button type="button" class="cm-cat-btn" data-cat="misc" onclick="selectCategory('misc')" style="...">雜事</button>
<button type="button" class="cm-cat-btn" data-cat="personal" onclick="selectCategory('personal')" style="...">個人</button>
```
改成（保持原有 inline style；雜事文字改雜項，個人改機台並交換順序）：
```html
<button type="button" class="cm-cat-btn" data-cat="employee" onclick="selectCategory('employee')" style="padding:.55rem;border:2px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;font-weight:600;">員工</button>
<button type="button" class="cm-cat-btn" data-cat="customer" onclick="selectCategory('customer')" style="padding:.55rem;border:2px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;font-weight:600;">客人</button>
<button type="button" class="cm-cat-btn" data-cat="machine" onclick="selectCategory('machine')" style="padding:.55rem;border:2px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;font-weight:600;">機台</button>
<button type="button" class="cm-cat-btn" data-cat="misc" onclick="selectCategory('misc')" style="padding:.55rem;border:2px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;font-weight:600;">雜項</button>
```

- [ ] **Step 5: 改 JS 顏色 map（第 254-260 附近）**

找到 `var categoryColors = { employee: '#3498db', customer: '#e67e22', misc: '#95a5a6', personal: '#9b59b6' };`（行號可能略有不同），改成：

```javascript
var categoryColors = {
  employee: '#3498db',
  customer: '#e67e22',
  machine:  '#9b59b6',
  misc:     '#95a5a6',
};
```

- [ ] **Step 6: 改 localStorage validation（第 299 行）**

把：
```javascript
if (_selCategory && ['employee','customer','misc','personal'].indexOf(_selCategory) < 0) _selCategory = null;
```
改成：
```javascript
if (_selCategory && ['employee','customer','machine','misc'].indexOf(_selCategory) < 0) _selCategory = null;
```

- [ ] **Step 7: 改 `saveSummaryAsNote()` POST body（第 444-455 行附近）**

找到：
```javascript
body: JSON.stringify({
  title: title,
  content: content,
  store: store,
  status: 'pending',
  category: 'personal'
})
```
改成：
```javascript
body: JSON.stringify({
  title: title,
  content: content,
  store: store,
  status: 'pending',
  category: 'misc',
  is_ai_summary: true
})
```

- [ ] **Step 8: 全文掃殘留**

Run: `grep -n "personal\|unclassified\|雜事\|個人\|未分類" templates/notes/index.html`

Expected:
- 沒有任何 `personal` / `unclassified` 引用
- 沒有「雜事」字樣（label 改「雜項」）
- 沒有「個人」「未分類」字樣
- 中文「雜項」「機台」應有出現

如有殘留逐個修。

- [ ] **Step 9: Commit**

```bash
git add templates/notes/index.html
git commit -m "feat(notes/index): modal + filter reorder to 員工/客人/機台/雜項

- Replace 個人 with 機台 button
- Drop 未分類 filter tab
- saveSummaryAsNote stores as misc + is_ai_summary=true
- misc badge label 雜事 → 雜項"
```

---

### Task 9: Frontend — `templates/notes/editor.html`

**Files:**
- Modify: `templates/notes/editor.html:42-43` (CSS), `templates/notes/editor.html:118-127` (按鈕區塊), `templates/notes/editor.html:201` (currentCategory init)

- [ ] **Step 1: 改類別 CSS（第 42-43 行）**

把：
```css
.category-btn.selected[data-category="personal"]     { background: #9b59b6; color: #fff; border-color: transparent; }
.category-btn.selected[data-category="unclassified"] { background: #bdc3c7; color: #fff; border-color: transparent; }
```
改成：
```css
.category-btn.selected[data-category="machine"] { background: #9b59b6; color: #fff; border-color: transparent; }
```

- [ ] **Step 2: 改按鈕區塊順序與內容（第 118-127 行）**

把整個：
```jinja
{% set cur_category = note.category if note and note.category else (default_category or 'unclassified') %}
<div class="category-selector">
  <label>類別</label>
  <button class="category-btn {% if cur_category == 'employee' %}selected{% endif %}" data-category="employee" type="button" onclick="selectCategory(this)">員工</button>
  <button class="category-btn {% if cur_category == 'customer' %}selected{% endif %}" data-category="customer" type="button" onclick="selectCategory(this)">客人</button>
  <button class="category-btn {% if cur_category == 'misc' %}selected{% endif %}" data-category="misc" type="button" onclick="selectCategory(this)">雜事</button>
  <button class="category-btn {% if cur_category == 'personal' %}selected{% endif %}" data-category="personal" type="button" onclick="selectCategory(this)">個人</button>
  {% if cur_category == 'unclassified' %}
  <button class="category-btn selected" data-category="unclassified" type="button" onclick="selectCategory(this)">未分類</button>
  {% endif %}
</div>
```
改成（順序 員工/客人/機台/雜項；移除個人按鈕；雜事文字改雜項；移除 unclassified pill）：
```jinja
{% set cur_category = note.category if note and note.category else (default_category or 'misc') %}
<div class="category-selector">
  <label>類別</label>
  <button class="category-btn {% if cur_category == 'employee' %}selected{% endif %}" data-category="employee" type="button" onclick="selectCategory(this)">員工</button>
  <button class="category-btn {% if cur_category == 'customer' %}selected{% endif %}" data-category="customer" type="button" onclick="selectCategory(this)">客人</button>
  <button class="category-btn {% if cur_category == 'machine' %}selected{% endif %}" data-category="machine" type="button" onclick="selectCategory(this)">機台</button>
  <button class="category-btn {% if cur_category == 'misc' %}selected{% endif %}" data-category="misc" type="button" onclick="selectCategory(this)">雜項</button>
</div>
```

- [ ] **Step 3: 改 `currentCategory` JS init fallback（第 201 行）**

把：
```javascript
var currentCategory = {{ (note.category if note else (default_category or 'unclassified'))|tojson }};
```
改成：
```javascript
var currentCategory = {{ (note.category if note else (default_category or 'misc'))|tojson }};
```

- [ ] **Step 4: 全文掃殘留**

Run: `grep -n "personal\|unclassified\|雜事\|個人\|未分類" templates/notes/editor.html`

Expected:
- 沒有 `personal` / `unclassified` 引用
- 沒有「雜事」「個人」「未分類」字樣

如有殘留逐個修。

- [ ] **Step 5: Commit**

```bash
git add templates/notes/editor.html
git commit -m "feat(notes/editor): 4-button category row (員工/客人/機台/雜項), drop personal+unclassified"
```

---

### Task 10: Migration 008（schema + data + ORM backfill）

**Files:**
- Create: `migrations/versions/008_machine_category_and_ai_summary.py`

- [ ] **Step 1: 寫 migration 檔**

新建 `migrations/versions/008_machine_category_and_ai_summary.py`：

```python
"""Add Note.is_ai_summary + migrate personal/unclassified → misc + backfill is_ai_summary

Revision ID: 008
Revises: 007
Create Date: 2026-04-30
"""
revision = '008'
down_revision = '007'

import os
import re
import sys
import psycopg2
from dotenv import load_dotenv

load_dotenv()

AI_SUMMARY_TITLE_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*摘要\s*$")


def upgrade():
    url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)

    # Step 1-3: 純 SQL DDL/DML（不需解密）
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS is_ai_summary BOOLEAN NOT NULL DEFAULT FALSE")
    cur.execute("ALTER TABLE notes ALTER COLUMN category SET DEFAULT 'misc'")
    cur.execute("UPDATE notes SET category='misc' WHERE category IN ('personal', 'unclassified')")
    moved = cur.rowcount
    cur.close()
    conn.close()
    print(f"[step 1-3] schema OK; moved {moved} rows from personal/unclassified to misc")

    # Step 4: ORM backfill is_ai_summary=True for "YYYY-MM-DD 摘要" titles
    # 必須在有 DB_ENCRYPTION_KEY 的環境跑（Zeabur container）
    if not os.environ.get("DB_ENCRYPTION_KEY"):
        print("[step 4] DB_ENCRYPTION_KEY not set — skipping is_ai_summary backfill (run again on Zeabur)")
        return

    # 延遲匯入：app context 在這時才需要
    from app import create_app
    from models import db, Note

    app = create_app()
    with app.app_context():
        rows = Note.query.filter_by(category="misc").all()
        flagged = 0
        for n in rows:
            title = n.title or ""
            if AI_SUMMARY_TITLE_RE.match(title):
                n.is_ai_summary = True
                flagged += 1
        db.session.commit()
        print(f"[step 4] backfilled is_ai_summary=True for {flagged} notes (out of {len(rows)} misc)")


if __name__ == "__main__":
    upgrade()
```

- [ ] **Step 2: 本機 dry run（步驟 1-3 should work，步驟 4 should skip）**

⚠️ 本機 DB 是 SQLite（in-memory test 用）或無；prod 是 PostgreSQL on Zeabur。本機若 `.env` 指向 prod DB 不要跑！

先確認 `.env` 內容：

Run: `cd /home/hirain0126/projects/webapp/app_unified && cat .env | grep -E "DATABASE_URL|DB_ENCRYPTION_KEY" | sed 's/=.*/=***/'`

如果 `DATABASE_URL` 指向 prod，**跳過此 Step 2**，直接 commit 後在 Zeabur container 跑。

- [ ] **Step 3: Commit**

```bash
git add migrations/versions/008_machine_category_and_ai_summary.py
git commit -m "migrate(008): personal/unclassified→misc, add is_ai_summary, backfill via title

Run on Zeabur container after deploy: cd /app && python migrations/versions/008_machine_category_and_ai_summary.py"
```

---

### Task 11: 全套 test 跑完 + 最終 sanity

**Files:** 不修改檔案，只跑驗證

- [ ] **Step 1: 跑全套 pytest（除已知 22 個 auth.login stub fail 外應全綠）**

Run: `cd /home/hirain0126/projects/webapp && source venv/bin/activate && cd app_unified && DB_ENCRYPTION_KEY="$(python -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" pytest tests/ -v --ignore=tests/test_auth.py --ignore=tests/test_notes.py 2>&1 | tail -50`

Expected: 全綠或近全綠（不該有 `personal` / `unclassified` 相關失敗）。

如果有失敗：先 grep 殘留 → 對照 spec 補修 → 重跑。

- [ ] **Step 2: 全 repo 殘留掃描**

Run: `cd /home/hirain0126/projects/webapp/app_unified && grep -rn "unclassified" --include="*.py" --include="*.html" --include="*.js" 2>/dev/null | grep -v "test_" | grep -v migrations/versions/006_ | grep -v migrations/versions/008_`

Expected: 沒有任何 hit（除 migration 006/008 外）。

Run: `grep -rn "category.*personal\|'personal'\|\"personal\"" --include="*.py" --include="*.html" --include="*.js" 2>/dev/null | grep -v migrations/versions/`

Expected: 沒有任何 hit。

如有殘留 → 修 → 補 commit。

- [ ] **Step 3: Smoke 模板渲染（jinja syntax 檢查）**

Run: `cd /home/hirain0126/projects/webapp && source venv/bin/activate && cd app_unified && python -c "
from app import create_app
from jinja2 import TemplateSyntaxError
app = create_app()
with app.app_context():
    try:
        app.jinja_env.get_template('notes/index.html')
        app.jinja_env.get_template('notes/editor.html')
        print('jinja templates OK')
    except TemplateSyntaxError as e:
        print(f'TemplateSyntaxError: {e}')
"`

Expected: `jinja templates OK`

- [ ] **Step 4: Final commit（如有殘留修補）**

如果 Step 1-3 都過則跳過。如果有殘留修補：

```bash
git add -p   # 互動加，仔細看 diff
git commit -m "fix(notes): clean up residual personal/unclassified references"
```

- [ ] **Step 5: Push to remote（手動，等使用者說可以推）**

⚠️ 預設不 push。等使用者說「push」再推。

部署 SOP（給使用者參考）：
1. Push 後 Zeabur 自動部署
2. 進 Zeabur container：
   ```bash
   cd /app && python migrations/versions/008_machine_category_and_ai_summary.py
   ```
3. 看輸出：
   - `[step 1-3] schema OK; moved N rows from personal/unclassified to misc`（N 應 = 71）
   - `[step 4] backfilled is_ai_summary=True for M notes (out of K misc)`（M = 標題符合「YYYY-MM-DD 摘要」的數量）
4. UI 驗證：admin 登入 → 看 modal/filter 順序為「員工/客人/機台/雜項」→ 跑 AI 摘要看 4 類輸出 + 不含先前的「YYYY-MM-DD 摘要」
