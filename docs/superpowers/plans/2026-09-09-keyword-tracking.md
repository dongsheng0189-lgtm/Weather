# 關鍵字建檔追蹤 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓使用者把某個關鍵字（通常是員工姓名）的所有命中集合成一張持續累積、可標記狀態的「建檔追蹤」表。

**Architecture:** 兩張新表（`tracked_keywords` / `tracked_keyword_hits`），每一列是**指向原始 note / checklist item 的參照**而非複本。比對邏輯抽成不碰資料庫的純函式，完整掃描與增量掃描共用它。因為內文是 `EncryptedText`、SQL 無法比對，掃描一律在 Python 端解密後做子字串比對，並以 `updated_at` 為水位做增量，讓「開頁面」永遠只是一次索引查詢。

**Tech Stack:** Flask 3 + Flask-SQLAlchemy + SQLAlchemy、`crypto.EncryptedText`、gevent（掃描要讓出 hub）、pytest。

**規格：** `docs/superpowers/specs/2026-09-08-quick-tag-and-keyword-tracking-design.md`（後半「需求二」）。階段一（快速標籤鍵）已於 2026-09-08 上線，本計畫是階段二。

---

## Global Constraints

以下每一條都適用於**每一個** task，不再逐一重述。

- **命名固定為「建檔追蹤」**。UI 上不得出現「列管」「關注清單」「監控」等替代說法。
- **每一列是參照，不是複本** —— 不得把命中的文字複製進 `tracked_keyword_hits`。顯示時一律即時 join 回原始 `Note` / `ChecklistItem` 取最新文字。
- **不做跨店** —— 一張追蹤表綁一個 `store`，比對只撈同店資料。
- **比對規則：不分大小寫的子字串比對**。不分詞、不做模糊比對（與 `notes/routes.py:73` 的 `_note_matches` 一致）。
- **不動 `Note` / `ChecklistItem` / 任何既有資料表的欄位** —— 本階段只新增兩張表。理由見下方「為什麼不需要改 `app.py` 的 schema 修復 DDL」。
- **軟刪除**：掃描時跳過 `deleted_at IS NOT NULL` 的資料；顯示時**也要**再過濾一次（資料可能先被比對到、之後才被刪）。已存在的命中列**不主動刪除**。
- **剔除（「不相關」）用軟刪除**（`excluded_at` 有值），不可真刪 —— 真刪的話下一輪增量掃描會再把它抓回來。
- **加密**：`keyword` 與 `note_text`（備註）都可能含個資，必須是 `EncryptedText`。`created_by_name` / `excluded_by_name` 比照 `ChecklistItem.checked_by_name` 也是 `EncryptedText`。
- **時間一律用 `tw_time.tw_iso` / `tw_str` 輸出**，不可用裸 `isoformat()` —— DB 存的是 naive UTC，裸輸出會讓前端差 8 小時（階段一踩過）。
- **權限沿用現有標籤管理的模型**：同店的人都可以建立／刪除追蹤表、剔除、寫備註。不新增權限概念。路由掛在 `notes_bp` 上，自動吃 `_gate_notes_feature` 的模組開關。
- **測試指令**：`cd app_unified && ../venv/bin/python -m pytest`。單檔：`../venv/bin/python -m pytest tests/test_xxx.py -v`。
- **每個 task 結束時全套件必須綠**（階段一結束時是 905 passed）。

### 規格的兩處修正（實作時以本計畫為準）

**1. 規格說「現有搜尋前面有日期範圍（`_date_filter`）擋著，掃描量有上限」—— 這是錯的。**
實際讀 `notes/routes.py:437-465`：keyword 模式走的是 `if keyword:` 分支，該分支**只**排除 AI 摘要，`_date_filter` 只在 `else:`（combined）分支才呼叫。也就是**現有的關鍵字搜尋已經是全歷史掃描**，每搜一次就把該店所有筆記解密一遍。

這不影響增量設計的結論（開頁面仍然必須是索引查詢），但改變了風險評估：**第一次完整掃描的成本 == 使用者現在每按一次「搜尋」的成本**，是已經在天天付、且被接受的代價。不需要為它做背景任務或進度條。

**2. 規格說 super_admin「依所在店別，與現有 `_scope_notes` 一致」—— `_scope_notes` 實際上會撈「所有未隱藏的店」，與「不做跨店」矛盾。**
追蹤表只有一個 `store` 欄位，必須是單一具體店別。**本計畫採 `notes/tags.py:_effective_store()` 的規則**（super_admin 可帶 `?store=`，否則用自己的 `current_user.store`），與標籤管理一致；解析不出店別就回 400。

### 為什麼不需要改 `app.py` 的 schema 修復 DDL

`app.py:265` 的 `db.create_all()` 會建立**缺少的表**（本機 SQLite 與 prod PG 都會跑到）。`app.py:269` 那批 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 被 `if 'postgresql' in URI` 擋著，**只在 prod 跑、SQLite 整段跳過**。

本階段**只新增表、不加既有表的欄位**，所以兩邊都由 `create_all()` 自動搞定，**不需要**在 `app.py` 補任何一句 DDL。

⚠️ 但這是本計畫的一個硬性前提：**如果實作過程中發現需要在 `notes` / `checklist_items` 這種既有表上加欄位，就必須（a）在 `app.py` 的 PG 區塊補一句 `ADD COLUMN IF NOT EXISTS`，並且（b）本機 SQLite 要手動 `ALTER TABLE` 或砍掉 `instance/dev.db` 重 seed** —— 本機沒有 schema 同步機制，2026-09-08 已經被 `no such column: note_tags.tagged_by` 咬過一次。真的走到這一步，請在該 task 明確停下來回報。

---

## 檔案結構

| 檔案 | 職責 |
|---|---|
| `app_unified/notes/tracking_match.py`（新增）| **純比對邏輯**。不 import flask、不 import models、不碰 DB。完整掃描與增量掃描共用，確保兩條路徑不漂移。 |
| `app_unified/notes/tracking_scan.py`（新增）| **掃描引擎**。決定「撈哪些資料」（店別、水位、軟刪、AI 摘要），分批解密、寫入命中列、推進水位。 |
| `app_unified/notes/tracking.py`（新增）| **路由**（頁面 + JSON API），註冊到 `notes_bp`。含店別解析與權限檢查。 |
| `app_unified/notes/__init__.py`（修改）| 加一行 import 讓路由註冊。 |
| `app_unified/models.py`（修改）| 新增 `TrackedKeyword`、`TrackedKeywordHit`。 |
| `app_unified/templates/notes/tracking_list.html`（新增）| 同店所有追蹤表的清單。 |
| `app_unified/templates/notes/tracking_detail.html`（新增）| 單一追蹤表：每一列備註在最上面、[不相關]、[→ 原文]。 |
| `app_unified/templates/notes/index.html`（修改）| 兩個入口：toolbar 的「建檔追蹤」連結、搜尋結果上方的「建檔追蹤／開啟追蹤表」按鈕。 |
| `app_unified/notes/routes.py`（修改）| `index()` 在 keyword 模式時查出「本店是否已有這個關鍵字的追蹤表」傳給模板。 |
| `app_unified/migrations/versions/021_keyword_tracking.py`（新增）| 沿用既有版本檔序列。⚠️ 見 Task 2 Step 6 的說明：這個系列**目前不會被執行**。 |

測試：`tests/test_tracking_match.py`、`tests/test_tracking_models.py`、`tests/test_tracking_scan.py`、`tests/test_tracking_api.py`、`tests/test_tracking_ui_wiring.py`。

---

### Task 1: 比對純函式

規格要求比對邏輯與「從哪裡取資料」解耦：完整掃描與增量掃描是同一套規則、只是輸入不同，抽開之後兩條路徑不會漂移，也能單獨測大小寫／部分比對／空白。

**Files:**
- Create: `app_unified/notes/tracking_match.py`
- Test: `app_unified/tests/test_tracking_match.py`

**Interfaces:**
- Consumes: 無（本 task 不依賴任何既有程式）。
- Produces:
  - `normalize(keyword) -> str` —— `strip()` + `lower()`，`None` 視為 `""`。
  - `text_matches(needle: str, text) -> bool` —— `needle` 必須是已 `normalize` 過的字串；`text` 可為 `None`。
  - `find_hits(keyword: str, records) -> list[tuple[str, int]]` —— `records` 是 `(target_type, target_id, texts)` 的可迭代物，`texts` 是字串序列（可含 `None`）。回傳命中的 `(target_type, target_id)`，順序與輸入一致。

- [ ] **Step 1: 寫失敗的測試**

建立 `app_unified/tests/test_tracking_match.py`：

```python
"""建檔追蹤的比對純函式 —— 不碰 DB、不需要 app fixture。"""
from notes.tracking_match import normalize, text_matches, find_hits


def test_normalize_strips_and_lowers():
    assert normalize('  Wang XiaoMing  ') == 'wang xiaoming'
    assert normalize(None) == ''
    assert normalize('') == ''


def test_text_matches_is_case_insensitive_substring():
    n = normalize('王小明')
    assert text_matches(n, '今天王小明遲到 10 分鐘') is True
    assert text_matches(n, '今天大家都準時') is False


def test_text_matches_ignores_case_for_ascii():
    n = normalize('Wang')
    assert text_matches(n, 'WANG 打卡異常') is True
    assert text_matches(n, 'wang 打卡異常') is True


def test_text_matches_handles_none_text():
    assert text_matches(normalize('王'), None) is False


def test_text_matches_empty_needle_never_matches():
    # 空關鍵字若當成子字串會命中全部資料，必須明確擋掉
    assert text_matches('', '任何文字') is False


def test_find_hits_returns_matching_records_in_order():
    records = [
        ('note', 1, ['王小明的交接', '內容無關']),
        ('item', 7, ['今天準時']),
        ('item', 9, [None, '王小明遲到']),
    ]
    assert find_hits('王小明', records) == [('note', 1), ('item', 9)]


def test_find_hits_matches_each_record_only_once():
    # 同一筆資料的多個欄位都命中，也只能回傳一次
    records = [('note', 1, ['王小明', '王小明又一次'])]
    assert find_hits('王小明', records) == [('note', 1)]


def test_find_hits_with_blank_keyword_returns_nothing():
    records = [('note', 1, ['任何文字'])]
    assert find_hits('   ', records) == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_tracking_match.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'notes.tracking_match'`

- [ ] **Step 3: 寫最小實作**

建立 `app_unified/notes/tracking_match.py`：

```python
"""建檔追蹤的比對規則 —— 刻意不 import flask / models / db。

完整掃描與增量掃描是同一套規則、只有輸入不同（全部資料 vs 水位之後的資料）。
把規則抽在這裡，兩條路徑就不會各自演化出不同的比對行為，也讓大小寫／部分比對
之類的邊界可以單獨測試，不必架 DB。

規則本身：**不分大小寫的子字串比對**，與現有關鍵字搜尋（notes/routes.py 的
_note_matches）一致。不分詞、不做模糊比對 —— 使用者已接受「只是順口提到也會被
抓進來」，那正是「不相關」按鈕存在的理由。
"""


def normalize(keyword):
    """關鍵字正規化：去頭尾空白 + 轉小寫。None 視為空字串。"""
    return (keyword or "").strip().lower()


def text_matches(needle, text):
    """needle 必須是已 normalize 過的字串。

    空 needle 一律回 False —— 空字串是任何字串的子字串，不擋掉的話一個空關鍵字
    就會把全店資料都收進追蹤表。
    """
    if not needle:
        return False
    return needle in (text or "").lower()


def find_hits(keyword, records):
    """records: (target_type, target_id, texts) 的可迭代物，texts 是字串序列。

    回傳命中的 (target_type, target_id) list，順序與輸入一致。每筆資料最多出現
    一次（多個欄位都命中也只算一次）。
    """
    needle = normalize(keyword)
    if not needle:
        return []
    out = []
    for target_type, target_id, texts in records:
        if any(text_matches(needle, t) for t in texts):
            out.append((target_type, target_id))
    return out
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_tracking_match.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: Commit**

```bash
git add app_unified/notes/tracking_match.py app_unified/tests/test_tracking_match.py
git commit -m "feat(tracking): 建檔追蹤的比對純函式"
```

---

### Task 2: 資料模型

**Files:**
- Modify: `app_unified/models.py`（檔尾，接在 `ChecklistItemTag` 之後）
- Create: `app_unified/migrations/versions/021_keyword_tracking.py`
- Test: `app_unified/tests/test_tracking_models.py`

**Interfaces:**
- Consumes: Task 1 無關；用 `models.py` 既有的 `db`、`EncryptedText`、`datetime`。
- Produces:
  - `TrackedKeyword`：`id` / `store` / `keyword`(EncryptedText) / `created_by` / `created_by_name`(EncryptedText) / `created_at` / `scanned_through`(DateTime, nullable) / `hits`(relationship, cascade delete)
  - `TrackedKeywordHit`：`id` / `keyword_id` / `target_type`('note'|'item') / `target_id` / `note_text`(EncryptedText, nullable) / `excluded_at` / `excluded_by` / `excluded_by_name`(EncryptedText) / `matched_at`
  - 唯一約束 `uq_tracked_hit_target` on `(keyword_id, target_type, target_id)`

- [ ] **Step 1: 寫失敗的測試**

建立 `app_unified/tests/test_tracking_models.py`：

```python
"""建檔追蹤的資料模型。"""
import uuid
import pytest
from datetime import datetime


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_tracked(db, store='B店', keyword=None):
    from models import TrackedKeyword
    t = TrackedKeyword(store=store,
                       keyword=keyword or f'王小明_{uuid.uuid4().hex[:6]}',
                       created_by_name='tester')
    db.session.add(t)
    db.session.commit()
    return t


def test_tracked_keyword_roundtrip(db):
    from models import TrackedKeyword
    kw = f'王小明_{uuid.uuid4().hex[:6]}'
    t = _mk_tracked(db, keyword=kw)
    got = db.session.get(TrackedKeyword, t.id)
    assert got.keyword == kw          # EncryptedText：Python 端拿到明文
    assert got.store == 'B店'
    assert got.scanned_through is None  # 尚未掃過


def test_keyword_column_is_encrypted_at_rest(db):
    """keyword 可能是員工姓名（個資），DB 裡不可以是明文。"""
    from models import TrackedKeyword
    kw = f'極機密姓名_{uuid.uuid4().hex[:6]}'
    t = _mk_tracked(db, keyword=kw)
    raw = db.session.execute(
        db.text('SELECT keyword FROM tracked_keywords WHERE id = :i'),
        {'i': t.id},
    ).scalar()
    assert kw not in (raw or '')


def test_hit_unique_per_target(db):
    from models import TrackedKeywordHit
    t = _mk_tracked(db)
    db.session.add(TrackedKeywordHit(keyword_id=t.id, target_type='item', target_id=5))
    db.session.commit()
    db.session.add(TrackedKeywordHit(keyword_id=t.id, target_type='item', target_id=5))
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


def test_same_target_id_across_types_is_allowed(db):
    """note 5 與 item 5 是不同的東西，不可以互相擋。"""
    from models import TrackedKeywordHit
    t = _mk_tracked(db)
    db.session.add(TrackedKeywordHit(keyword_id=t.id, target_type='note', target_id=5))
    db.session.add(TrackedKeywordHit(keyword_id=t.id, target_type='item', target_id=5))
    db.session.commit()
    assert TrackedKeywordHit.query.filter_by(keyword_id=t.id).count() == 2


def test_deleting_tracked_keyword_cascades_hits(db):
    from models import TrackedKeyword, TrackedKeywordHit
    t = _mk_tracked(db)
    db.session.add(TrackedKeywordHit(keyword_id=t.id, target_type='item', target_id=1))
    db.session.commit()
    kid = t.id
    db.session.delete(db.session.get(TrackedKeyword, kid))
    db.session.commit()
    assert TrackedKeywordHit.query.filter_by(keyword_id=kid).count() == 0


def test_hit_note_text_is_encrypted_at_rest(db):
    """備註是使用者對人的評語，同樣屬個資。"""
    from models import TrackedKeywordHit
    t = _mk_tracked(db)
    memo = f'已口頭告誡_{uuid.uuid4().hex[:6]}'
    h = TrackedKeywordHit(keyword_id=t.id, target_type='item', target_id=42,
                          note_text=memo)
    db.session.add(h)
    db.session.commit()
    raw = db.session.execute(
        db.text('SELECT note_text FROM tracked_keyword_hits WHERE id = :i'),
        {'i': h.id},
    ).scalar()
    assert memo not in (raw or '')


def test_exclusion_fields_default_null(db):
    from models import TrackedKeywordHit
    t = _mk_tracked(db)
    h = TrackedKeywordHit(keyword_id=t.id, target_type='note', target_id=1)
    db.session.add(h)
    db.session.commit()
    assert h.excluded_at is None
    assert h.excluded_by_name is None
    assert isinstance(h.matched_at, datetime)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_tracking_models.py -v`
Expected: FAIL —— `ImportError: cannot import name 'TrackedKeyword' from 'models'`

- [ ] **Step 3: 寫最小實作**

在 `app_unified/models.py` **檔尾**（`ChecklistItemTag` 之後）加：

```python
# ─────────────────────────────────────────────────────────────
# 建檔追蹤（2026-09-09）
#
# 一張 TrackedKeyword = 一個持續累積的關鍵字追蹤表；TrackedKeywordHit = 一筆命中。
# 命中列存的是**指向原始資料的參照**（target_type + target_id），不是複本 ——
# 複本會與原文脫節（原始項目改成「遲到 5 分鐘」，追蹤表還寫著 10 分鐘），對一個
# 要當依據的表來說是致命的。
# ─────────────────────────────────────────────────────────────

class TrackedKeyword(db.Model):
    __tablename__ = "tracked_keywords"

    id = db.Column(db.Integer, primary_key=True)
    store = db.Column(db.String(8), nullable=False, index=True)
    # 追蹤的關鍵字通常是員工姓名 → 個資，必須加密
    keyword = db.Column(EncryptedText, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_name = db.Column(EncryptedText, nullable=True)  # 快照，比照 ChecklistItem.checked_by_name
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # 增量水位：上次掃描「開始」的時刻。下次只掃 updated_at > scanned_through 的資料。
    # 存的是掃描開始而非結束的時間，這樣掃描期間被改動的資料下一輪還會再掃到。
    # ⚠️ 這條路徑依賴「內容有變 → updated_at 會被 bump」。日後若有人讓某個會改
    # 文字的操作停止更新 updated_at，這裡就會靜默漏掃。
    scanned_through = db.Column(db.DateTime, nullable=True)

    hits = db.relationship("TrackedKeywordHit", backref="tracked", lazy="select",
                           cascade="all, delete-orphan")


class TrackedKeywordHit(db.Model):
    __tablename__ = "tracked_keyword_hits"

    id = db.Column(db.Integer, primary_key=True)
    keyword_id = db.Column(db.Integer, db.ForeignKey("tracked_keywords.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    # 'note' | 'item'。指向兩張不同的表，所以 target_id 不能是真的 FK。
    target_type = db.Column(db.String(8), nullable=False)
    target_id = db.Column(db.Integer, nullable=False)
    # 使用者對這一筆寫的備註，顯示在該列最上面。含對人的評語 → 加密
    note_text = db.Column(EncryptedText, nullable=True)
    # 「不相關」的剔除紀錄。用軟刪除而非真刪：真刪的話下一輪增量掃描會再把它抓回來
    excluded_at = db.Column(db.DateTime, nullable=True)
    excluded_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    excluded_by_name = db.Column(EncryptedText, nullable=True)
    matched_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("keyword_id", "target_type", "target_id",
                            name="uq_tracked_hit_target"),
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_tracking_models.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: 確認本機 dev DB 也拿得到新表**

本機 `instance/dev.db` 是持久檔案，不像測試那樣每次重建。新表由 `db.create_all()` 自動建立（不像加欄位會被 `'postgresql' in URI` 擋掉），驗一下：

Run:
```bash
cd app_unified && set -a && source .env.local && set +a && \
../venv/bin/python -c "
from app import create_app
from extensions import db
app = create_app()
with app.app_context():
    print(db.session.execute(db.text(
        \"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'tracked%'\"
    )).fetchall())
"
```
Expected: `[('tracked_keywords',), ('tracked_keyword_hits',)]`

⚠️ 若少了任何一張表，**停下來回報** —— 代表 `create_all()` 沒跑到，後面每個 task 的本機驗證都會壞。

- [ ] **Step 6: 補 migration 版本檔**

建立 `app_unified/migrations/versions/021_keyword_tracking.py`：

```python
"""keyword tracking: 建檔追蹤的兩張表

Revision ID: 021
Revises: 020
Create Date: 2026-09-09

⚠️ 這個 migrations/ 系列目前**不會被執行** —— 全 repo 沒有任何地方 import
flask_migrate、Migrate() 一次都沒被呼叫，requirements 也沒有它。實際維護 schema
的是 app.py 開機時的 db.create_all() 加上那批 PG-only 的 ADD COLUMN DDL。
這個檔案是為了維持版本序列不斷、將來若真的接回 Alembic 時有跡可循。
"""
from alembic import op
import sqlalchemy as sa

revision = '021'
down_revision = '020'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'tracked_keywords',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('store', sa.String(length=8), nullable=False),
        sa.Column('keyword', sa.Text(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_by_name', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('scanned_through', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_tracked_keywords_store', 'tracked_keywords', ['store'])

    op.create_table(
        'tracked_keyword_hits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('keyword_id', sa.Integer(), nullable=False),
        sa.Column('target_type', sa.String(length=8), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=False),
        sa.Column('note_text', sa.Text(), nullable=True),
        sa.Column('excluded_at', sa.DateTime(), nullable=True),
        sa.Column('excluded_by', sa.Integer(), nullable=True),
        sa.Column('excluded_by_name', sa.Text(), nullable=True),
        sa.Column('matched_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['keyword_id'], ['tracked_keywords.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['excluded_by'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('keyword_id', 'target_type', 'target_id',
                            name='uq_tracked_hit_target'),
    )
    op.create_index('ix_tracked_keyword_hits_keyword_id', 'tracked_keyword_hits', ['keyword_id'])


def downgrade():
    op.drop_index('ix_tracked_keyword_hits_keyword_id', 'tracked_keyword_hits')
    op.drop_table('tracked_keyword_hits')
    op.drop_index('ix_tracked_keywords_store', 'tracked_keywords')
    op.drop_table('tracked_keywords')
```

- [ ] **Step 7: 跑全套件**

Run: `cd app_unified && ../venv/bin/python -m pytest -q`
Expected: 全綠（階段一結束時是 905 passed；本 task 後應為 920 passed 上下）

- [ ] **Step 8: Commit**

```bash
git add app_unified/models.py app_unified/tests/test_tracking_models.py \
        app_unified/migrations/versions/021_keyword_tracking.py
git commit -m "feat(tracking): TrackedKeyword / TrackedKeywordHit 兩張表"
```

---

### Task 3: 掃描引擎（完整 + 增量）

這是本功能的核心，也是唯一有效能風險的地方。

**Files:**
- Create: `app_unified/notes/tracking_scan.py`
- Test: `app_unified/tests/test_tracking_scan.py`

**Interfaces:**
- Consumes: Task 1 的 `find_hits`；Task 2 的 `TrackedKeyword` / `TrackedKeywordHit`。
- Produces:
  - `SCAN_BATCH = 200`（每批解密的筆數）
  - `scan_tracked_keyword(tracked) -> int` —— 掃描並寫入新命中，推進水位，`commit()`，回傳**本次新增的命中數**。`tracked.scanned_through` 為 `None` 時自動走完整掃描。

- [ ] **Step 1: 寫失敗的測試**

建立 `app_unified/tests/test_tracking_scan.py`：

```python
"""建檔追蹤的掃描引擎：完整掃描、增量、剔除不復活、跨店隔離。"""
import uuid
from datetime import datetime, timedelta
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_note(db, store='B店', title='日報', content='', note_type='note',
             is_ai_summary=False, deleted=False):
    from models import Note
    n = Note(store=store, title=title, content=content, note_type=note_type,
             is_ai_summary=is_ai_summary,
             deleted_at=datetime.utcnow() if deleted else None)
    db.session.add(n)
    db.session.commit()
    return n


def _mk_item(db, note, text, deleted=False):
    from models import ChecklistItem
    i = ChecklistItem(note_id=note.id, text=text,
                      deleted_at=datetime.utcnow() if deleted else None)
    db.session.add(i)
    db.session.commit()
    return i


def _mk_tracked(db, keyword, store='B店'):
    from models import TrackedKeyword
    t = TrackedKeyword(store=store, keyword=keyword, created_by_name='tester')
    db.session.add(t)
    db.session.commit()
    return t


def _hits(db, tracked, include_excluded=False):
    from models import TrackedKeywordHit
    q = TrackedKeywordHit.query.filter_by(keyword_id=tracked.id)
    if not include_excluded:
        q = q.filter(TrackedKeywordHit.excluded_at.is_(None))
    return q.all()


def test_full_scan_finds_note_and_item(db):
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title=f'{kw} 的交接', content='')
    other = _mk_note(db, title='與此無關')
    _mk_item(db, other, f'{kw} 遲到 10 分鐘')
    _mk_item(db, other, '大家都準時')
    t = _mk_tracked(db, kw)

    added = scan_tracked_keyword(t)

    assert added == 2
    got = {(h.target_type, h.target_id) for h in _hits(db, t)}
    assert ('note', n.id) in got
    assert len(got) == 2


def test_full_scan_matches_note_content_not_only_title(db):
    """有些事寫在本文、不在項目裡。"""
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title='一般日報', content=f'另外 {kw} 今天請假')
    t = _mk_tracked(db, kw)

    scan_tracked_keyword(t)

    assert [(h.target_type, h.target_id) for h in _hits(db, t)] == [('note', n.id)]


def test_full_scan_sets_watermark(db):
    from notes.tracking_scan import scan_tracked_keyword
    t = _mk_tracked(db, f'王{uuid.uuid4().hex[:6]}')
    assert t.scanned_through is None

    scan_tracked_keyword(t)

    assert isinstance(t.scanned_through, datetime)


def test_scan_skips_soft_deleted(db):
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    dn = _mk_note(db, title=f'{kw} 已刪筆記', deleted=True)
    live = _mk_note(db, title='存活的筆記')
    _mk_item(db, live, f'{kw} 已刪項目', deleted=True)
    t = _mk_tracked(db, kw)

    scan_tracked_keyword(t)

    assert _hits(db, t) == []
    assert dn.id  # 資料還在，只是不該被掃到


def test_scan_skips_ai_summary_notes(db):
    """AI 摘要是其他 note 的彙整，會與原 note 重複命中（與搜尋的處理一致）。"""
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    _mk_note(db, title=f'{kw} 的摘要', is_ai_summary=True)
    t = _mk_tracked(db, kw)

    scan_tracked_keyword(t)

    assert _hits(db, t) == []


def test_scan_is_store_scoped(db):
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    _mk_note(db, store='A店', title=f'{kw} 在 A 店')
    t = _mk_tracked(db, kw, store='B店')

    scan_tracked_keyword(t)

    assert _hits(db, t) == []


def test_incremental_scan_picks_up_new_item_without_duplicating(db):
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title=f'{kw} 的交接')
    t = _mk_tracked(db, kw)
    assert scan_tracked_keyword(t) == 1

    _mk_item(db, n, f'{kw} 又遲到')
    added = scan_tracked_keyword(t)

    assert added == 1                     # 只新增了那一筆
    assert len(_hits(db, t)) == 2         # 舊的沒有被重複插入


def test_incremental_scan_catches_edited_item(db):
    """既有項目被編輯後才開始含關鍵字 —— 水位用 updated_at（而非最大 id）的理由。"""
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title='一般日報')
    item = _mk_item(db, n, '某人遲到')
    t = _mk_tracked(db, kw)
    assert scan_tracked_keyword(t) == 0

    item.text = f'{kw} 遲到'
    item.updated_at = datetime.utcnow() + timedelta(seconds=1)
    db.session.commit()

    assert scan_tracked_keyword(t) == 1
    assert [(h.target_type, h.target_id) for h in _hits(db, t)] == [('item', item.id)]


def test_excluded_hit_is_not_resurrected_by_rescan(db):
    """剔除用軟刪除的唯一理由：真刪的話下一輪會再抓回來。"""
    from notes.tracking_scan import scan_tracked_keyword
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title=f'{kw} 的交接')
    t = _mk_tracked(db, kw)
    scan_tracked_keyword(t)

    h = _hits(db, t)[0]
    h.excluded_at = datetime.utcnow()
    h.excluded_by_name = 'tester'
    db.session.commit()

    # 讓原始資料重新落入水位範圍，模擬它又被編輯過
    n.updated_at = datetime.utcnow() + timedelta(seconds=1)
    db.session.commit()

    assert scan_tracked_keyword(t) == 0
    assert _hits(db, t) == []                        # 頁面上看不到
    assert len(_hits(db, t, include_excluded=True)) == 1   # 剔除紀錄仍在


def test_scan_yields_to_hub_between_batches(db, monkeypatch):
    """掃描是同步解密迴圈，跑在 gevent worker 上。不讓出 hub 的話，大店的完整
    掃描會把整個 worker 卡住（hub_watchdog 會告警、gunicorn timeout=60 會 SIGKILL）。"""
    import notes.tracking_scan as scan_mod
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title='一般日報')
    for _ in range(5):
        _mk_item(db, n, f'{kw} 出現')
    t = _mk_tracked(db, kw)

    calls = []
    monkeypatch.setattr(scan_mod, 'SCAN_BATCH', 2)
    monkeypatch.setattr(scan_mod.gevent, 'sleep', lambda *a: calls.append(a))

    scan_tracked = scan_mod.scan_tracked_keyword
    scan_tracked(t)

    assert calls, 'scan 沒有在批次之間讓出 hub'
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_tracking_scan.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'notes.tracking_scan'`

- [ ] **Step 3: 寫最小實作**

建立 `app_unified/notes/tracking_scan.py`：

```python
"""建檔追蹤的掃描引擎。

為什麼一定要增量：Note.title / Note.content / ChecklistItem.text 都是
EncryptedText，DB 裡是密文、SQL 無法比對，所以比對只能把資料撈出來在 Python 端
解密後做。開一次頁面就全掃一遍的話，一店一年三萬多筆項目會愈來愈慢。改成
「命中結果存起來、只掃水位之後異動的資料」，開頁面就永遠只是一次索引查詢。

⚠️ 為什麼要分批 + gevent.sleep(0)：這是一個同步的解密迴圈，跑在 gevent worker
上。一次把幾萬列 .all() 出來，SQLAlchemy 會在 result processing 階段一口氣解密
全部，期間整個 hub 動不了 —— 同一個 worker 上其他人的請求全部停擺，hub_watchdog
會判定阻塞、gunicorn 的 timeout=60 甚至可能 SIGKILL 這個 worker。所以先只撈
primary key（不觸發解密），再一批一批載入物件，批與批之間讓出 hub。
"""
from datetime import datetime

import gevent

from extensions import db
from models import Note, ChecklistItem, TrackedKeywordHit
from notes.tracking_match import find_hits

SCAN_BATCH = 200


def _target_ids(tracked, since):
    """回傳 (note_ids, item_ids) —— 只查 id，不載入加密欄位（不觸發解密）。

    since=None 代表完整掃描（第一次建檔）。
    """
    note_q = db.session.query(Note.id).filter(
        Note.store == tracked.store,
        Note.deleted_at.is_(None),
        Note.is_ai_summary.isnot(True),
    )
    item_q = db.session.query(ChecklistItem.id).join(
        Note, ChecklistItem.note_id == Note.id
    ).filter(
        Note.store == tracked.store,
        Note.deleted_at.is_(None),
        Note.is_ai_summary.isnot(True),
        ChecklistItem.deleted_at.is_(None),
    )
    if since is not None:
        note_q = note_q.filter(Note.updated_at > since)
        item_q = item_q.filter(ChecklistItem.updated_at > since)
    return [r[0] for r in note_q.all()], [r[0] for r in item_q.all()]


def _batches(ids):
    for i in range(0, len(ids), SCAN_BATCH):
        yield ids[i:i + SCAN_BATCH]


def scan_tracked_keyword(tracked):
    """掃描並寫入新命中，推進水位，commit。回傳本次新增的命中數。

    tracked.scanned_through 是 None 時自動走完整掃描。
    """
    # 水位取「掃描開始」而非結束的時刻：掃描期間被改動的資料下一輪還會再掃到，
    # 寧可重掃一次（有唯一約束擋著、不會重複），也不要漏。
    started = datetime.utcnow()
    note_ids, item_ids = _target_ids(tracked, tracked.scanned_through)

    # 已存在的命中（含已被剔除的）—— 剔除的必須留在這個集合裡，否則下一輪會把
    # 使用者判定為「不相關」的資料重新抓回來。
    seen = {
        (h.target_type, h.target_id)
        for h in TrackedKeywordHit.query.filter_by(keyword_id=tracked.id).all()
    }

    added = 0
    for batch in _batches(note_ids):
        rows = Note.query.filter(Note.id.in_(batch)).all()
        records = [("note", n.id, [n.title, n.content]) for n in rows]
        added += _persist(tracked, records, seen)
        gevent.sleep(0)

    for batch in _batches(item_ids):
        rows = ChecklistItem.query.filter(ChecklistItem.id.in_(batch)).all()
        records = [("item", i.id, [i.text]) for i in rows]
        added += _persist(tracked, records, seen)
        gevent.sleep(0)

    tracked.scanned_through = started
    db.session.commit()
    return added


def _persist(tracked, records, seen):
    added = 0
    for target_type, target_id in find_hits(tracked.keyword, records):
        if (target_type, target_id) in seen:
            continue
        db.session.add(TrackedKeywordHit(
            keyword_id=tracked.id,
            target_type=target_type,
            target_id=target_id,
        ))
        seen.add((target_type, target_id))
        added += 1
    return added
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_tracking_scan.py -v`
Expected: PASS（11 passed）

- [ ] **Step 5: 跑全套件**

Run: `cd app_unified && ../venv/bin/python -m pytest -q`
Expected: 全綠

- [ ] **Step 6: Commit**

```bash
git add app_unified/notes/tracking_scan.py app_unified/tests/test_tracking_scan.py
git commit -m "feat(tracking): 完整/增量掃描引擎（分批解密、批間讓出 hub）"
```

---

### Task 4: 追蹤表的建立／列表／刪除 API

**Files:**
- Create: `app_unified/notes/tracking.py`
- Modify: `app_unified/notes/__init__.py`
- Test: `app_unified/tests/test_tracking_api.py`

**Interfaces:**
- Consumes: Task 2 的模型、Task 3 的 `scan_tracked_keyword`。
- Produces（給 Task 5 / 6 / 7 用）：
  - `_tracking_store() -> str | None` —— super_admin 可帶 `?store=`，否則 `current_user.store`。
  - `_get_tracked_or_404(kid) -> TrackedKeyword` —— 同時做同店權限檢查（不同店 403）。
  - `find_tracking_for_keyword(keyword) -> int | None` —— 本店是否已有這個關鍵字的追蹤表，回傳 id。
  - 路由：`POST /notes/api/tracking`、`GET /notes/api/tracking`、`DELETE /notes/api/tracking/<int:kid>`

- [ ] **Step 1: 寫失敗的測試**

建立 `app_unified/tests/test_tracking_api.py`：

```python
"""建檔追蹤 API：建立／列表／刪除／權限。"""
import json as _json
import uuid
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _uniq(prefix='T'):
    return f'{prefix}_{uuid.uuid4().hex[:6]}'


def _mk_user(db, role='user', store='B店'):
    from models import User
    u = User(username=_uniq(role), role=role, store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def _mk_note(db, store='B店', title='日報'):
    from models import Note
    n = Note(store=store, title=title, content='', note_type='note')
    db.session.add(n)
    db.session.commit()
    return n


def test_create_tracking_runs_full_scan(client, db):
    kw = _uniq('王小明')
    n = _mk_note(db, title=f'{kw} 的交接')
    u = _mk_user(db)
    _login(client, u)

    r = client.post('/notes/api/tracking', json={'keyword': kw})

    assert r.status_code == 200
    data = r.get_json()
    assert data['hit_count'] == 1
    from models import TrackedKeyword, TrackedKeywordHit
    t = db.session.get(TrackedKeyword, data['id'])
    assert t.keyword == kw
    assert t.store == 'B店'
    assert t.created_by == u.id
    assert t.created_by_name == u.username
    assert TrackedKeywordHit.query.filter_by(keyword_id=t.id).count() == 1
    assert n.id


def test_create_tracking_rejects_blank_keyword(client, db):
    u = _mk_user(db)
    _login(client, u)
    r = client.post('/notes/api/tracking', json={'keyword': '   '})
    assert r.status_code == 400
    assert '關鍵字' in r.get_json()['error']


def test_create_tracking_returns_existing_when_duplicate(client, db):
    """同店同關鍵字不重複建表 —— 直接把既有那張回給前端。"""
    kw = _uniq('王小明')
    u = _mk_user(db)
    _login(client, u)
    first = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()

    r = client.post('/notes/api/tracking', json={'keyword': f'  {kw.upper()}  '})

    assert r.status_code == 200
    assert r.get_json()['id'] == first['id']
    assert r.get_json()['existing'] is True


def test_list_tracking_is_store_scoped(client, db):
    from models import TrackedKeyword
    other = TrackedKeyword(store='A店', keyword=_uniq('別店'), created_by_name='x')
    db.session.add(other)
    db.session.commit()
    kw = _uniq('本店')
    u = _mk_user(db, store='B店')
    _login(client, u)
    client.post('/notes/api/tracking', json={'keyword': kw})

    r = client.get('/notes/api/tracking')

    assert r.status_code == 200
    names = [row['keyword'] for row in r.get_json()['tracking']]
    assert kw in names
    assert all('別店' not in n for n in names)


def test_delete_tracking_removes_hits(client, db):
    from models import TrackedKeywordHit
    kw = _uniq('王小明')
    _mk_note(db, title=f'{kw} 的交接')
    u = _mk_user(db)
    _login(client, u)
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']

    r = client.delete(f'/notes/api/tracking/{kid}')

    assert r.status_code == 200
    from models import TrackedKeyword
    assert db.session.get(TrackedKeyword, kid) is None
    assert TrackedKeywordHit.query.filter_by(keyword_id=kid).count() == 0


def test_cross_store_access_is_forbidden(client, db):
    kw = _uniq('王小明')
    owner = _mk_user(db, store='B店')
    _login(client, owner)
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']

    intruder = _mk_user(db, store='A店')
    _login(client, intruder)

    assert client.delete(f'/notes/api/tracking/{kid}').status_code == 403
    assert client.get(f'/notes/tracking/{kid}').status_code == 403


def test_tracking_requires_notes_feature(client, db):
    """未啟用 notes 模組的人看不見這個功能（沿用 blueprint 的模組開關）。"""
    from models import User
    u = User(username=_uniq('nofeat'), role='user', store='B店',
             features=_json.dumps([]))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    _login(client, u)

    assert client.get('/notes/api/tracking').status_code == 404


def test_store_page_lists_tracking(client, db):
    kw = _uniq('王小明')
    u = _mk_user(db)
    _login(client, u)
    client.post('/notes/api/tracking', json={'keyword': kw})

    r = client.get('/notes/tracking/')

    assert r.status_code == 200
    assert kw in r.get_data(as_text=True)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_tracking_api.py -v`
Expected: FAIL —— 全部 404（路由還不存在）

- [ ] **Step 3: 寫最小實作**

建立 `app_unified/notes/tracking.py`：

```python
"""建檔追蹤的路由（頁面 + JSON API），註冊到 notes_bp。

流程刻意分兩段（規格的命名決策）：**搜尋**是暫時的、關掉就沒了；按下**建檔追蹤**
才真的建立一張持續累積的表。兩者都是輸入關鍵字、都列出命中，很容易混淆，所以
UI 上必須維持這個分界。

權限沿用標籤管理：同店的人都可以建立／刪除／剔除／寫備註，不新增權限概念。
"""
from datetime import datetime

from flask import request, jsonify, abort, render_template
from flask_login import login_required, current_user

from extensions import db
from models import TrackedKeyword, TrackedKeywordHit
from notes.routes import notes_bp
from notes.tracking_match import normalize
from notes.tracking_scan import scan_tracked_keyword
from tw_time import tw_str


def _tracking_store():
    """追蹤表綁單一店別。super_admin 可用 ?store= 指定，其他人鎖在自己的店。

    與 notes/tags.py 的 _effective_store() 同一套規則 —— 規格原文說「與
    _scope_notes 一致」，但 _scope_notes 對 super_admin 會撈「所有未隱藏的店」，
    與「不做跨店」相矛盾，且一張追蹤表只有一個 store 欄位可填。
    """
    if current_user.role == 'super_admin':
        s = request.args.get('store')
        if s:
            return s
    return current_user.store


def _get_tracked_or_404(kid):
    t = db.session.get(TrackedKeyword, kid)
    if not t:
        abort(404)
    store = _tracking_store()
    if not store or t.store != store:
        abort(403)
    return t


def find_tracking_for_keyword(keyword):
    """本店是否已有這個關鍵字的追蹤表？有就回 id，沒有回 None。

    keyword 是 EncryptedText、SQL 比不了，只能撈出來在 Python 端比。一店的追蹤表
    數量是「使用者手動建立的關鍵字」量級（幾個到幾十個），不是資料量級，撈全部
    無妨 —— 不需要為它加雜湊欄位（那還會多一個可被離線比對的側channel）。
    """
    needle = normalize(keyword)
    if not needle:
        return None
    store = _tracking_store()
    if not store:
        return None
    for t in TrackedKeyword.query.filter_by(store=store).all():
        if normalize(t.keyword) == needle:
            return t.id
    return None


def _tracked_to_dict(t):
    return {
        'id': t.id,
        'keyword': t.keyword,
        'created_by_name': t.created_by_name,
        'created_at': tw_str(t.created_at),
        'hit_count': TrackedKeywordHit.query.filter_by(
            keyword_id=t.id).filter(TrackedKeywordHit.excluded_at.is_(None)).count(),
    }


@notes_bp.route('/api/tracking', methods=['POST'])
@login_required
def create_tracking():
    store = _tracking_store()
    if not store:
        return jsonify({'error': '找不到店別，請先選擇店別'}), 400
    data = request.get_json(silent=True) or {}
    keyword = (data.get('keyword') or '').strip()
    if not keyword:
        return jsonify({'error': '關鍵字不可為空'}), 400

    existing_id = find_tracking_for_keyword(keyword)
    if existing_id:
        t = db.session.get(TrackedKeyword, existing_id)
        return jsonify(dict(_tracked_to_dict(t), existing=True))

    t = TrackedKeyword(store=store, keyword=keyword,
                       created_by=current_user.id,
                       created_by_name=current_user.username)
    db.session.add(t)
    db.session.commit()
    # 第一次是完整掃描（會慢，僅此一次）。成本與使用者現在按一次「搜尋」相同 ——
    # 現有的關鍵字搜尋本來就是全歷史解密掃描（notes/routes.py 的 keyword 分支
    # 不套 _date_filter）。
    scan_tracked_keyword(t)
    return jsonify(dict(_tracked_to_dict(t), existing=False))


@notes_bp.route('/api/tracking', methods=['GET'])
@login_required
def list_tracking():
    store = _tracking_store()
    if not store:
        return jsonify({'tracking': []})
    rows = TrackedKeyword.query.filter_by(store=store).order_by(
        TrackedKeyword.created_at.desc()).all()
    return jsonify({'tracking': [_tracked_to_dict(t) for t in rows]})


@notes_bp.route('/api/tracking/<int:kid>', methods=['DELETE'])
@login_required
def delete_tracking(kid):
    t = _get_tracked_or_404(kid)
    db.session.delete(t)   # cascade 清掉命中列
    db.session.commit()
    return jsonify({'ok': True})


@notes_bp.route('/tracking/')
@login_required
def tracking_list_page():
    store = _tracking_store()
    rows = TrackedKeyword.query.filter_by(store=store).order_by(
        TrackedKeyword.created_at.desc()).all() if store else []
    return render_template('notes/tracking_list.html',
                           tracking=[_tracked_to_dict(t) for t in rows],
                           current_store=store)


@notes_bp.route('/tracking/<int:kid>')
@login_required
def tracking_detail_page(kid):
    t = _get_tracked_or_404(kid)
    scan_tracked_keyword(t)   # 每次開啟跑一次增量
    return render_template('notes/tracking_detail.html',
                           tracked=_tracked_to_dict(t),
                           rows=[])
```

在 `app_unified/notes/__init__.py` 的 `from . import tags` 之後加一行：

```python
from . import tracking  # noqa: F401  (建檔追蹤路由註冊到 notes_bp)
```

- [ ] **Step 4: 建立最小模板讓頁面測試能過**

建立 `app_unified/templates/notes/tracking_list.html`（Task 6 會做完整版，這裡先讓路由可渲染）：

```html
{% extends "base.html" %}
{% block title %}建檔追蹤{% endblock %}
{% block content %}
<div class="toolbar"><h2>建檔追蹤</h2></div>
<div class="notes-grid">
  {% for t in tracking %}
  <div class="note-card"><h3>{{ t.keyword }}</h3></div>
  {% endfor %}
</div>
{% endblock %}
```

建立 `app_unified/templates/notes/tracking_detail.html`（同樣先最小）：

```html
{% extends "base.html" %}
{% block title %}{{ tracked.keyword }} 建檔追蹤{% endblock %}
{% block content %}
<div class="toolbar"><h2>{{ tracked.keyword }} 建檔追蹤</h2></div>
{% endblock %}
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_tracking_api.py -v`
Expected: PASS（8 passed）

- [ ] **Step 6: 跑全套件**

Run: `cd app_unified && ../venv/bin/python -m pytest -q`
Expected: 全綠

- [ ] **Step 7: Commit**

```bash
git add app_unified/notes/tracking.py app_unified/notes/__init__.py \
        app_unified/templates/notes/tracking_list.html \
        app_unified/templates/notes/tracking_detail.html \
        app_unified/tests/test_tracking_api.py
git commit -m "feat(tracking): 追蹤表建立/列表/刪除 API 與頁面骨架"
```

---

### Task 5: 命中列的備註與「不相關」

**Files:**
- Modify: `app_unified/notes/tracking.py`
- Test: `app_unified/tests/test_tracking_hits_api.py`

**Interfaces:**
- Consumes: Task 4 的 `_get_tracked_or_404`。
- Produces:
  - `_get_hit_or_404(hid) -> TrackedKeywordHit` —— 經由所屬追蹤表做同店檢查。
  - `PUT /notes/api/tracking/hits/<int:hid>/note`，body `{"text": "..."}` → `{"note_text": "..."}`
  - `POST /notes/api/tracking/hits/<int:hid>/exclude`，body `{"excluded": true|false}` → `{"excluded": bool, "excluded_by_name": str|None}`

- [ ] **Step 1: 寫失敗的測試**

建立 `app_unified/tests/test_tracking_hits_api.py`：

```python
"""命中列的備註與剔除。"""
import json as _json
import uuid
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _uniq(p='T'):
    return f'{p}_{uuid.uuid4().hex[:6]}'


def _mk_user(db, store='B店'):
    from models import User
    u = User(username=_uniq('u'), role='user', store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def _mk_note(db, store='B店', title='日報'):
    from models import Note
    n = Note(store=store, title=title, content='', note_type='note')
    db.session.add(n)
    db.session.commit()
    return n


def _setup_one_hit(client, db, store='B店'):
    kw = _uniq('王小明')
    _mk_note(db, store=store, title=f'{kw} 的交接')
    u = _mk_user(db, store=store)
    _login(client, u)
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']
    from models import TrackedKeywordHit
    hit = TrackedKeywordHit.query.filter_by(keyword_id=kid).first()
    return u, kid, hit


def test_set_note_text(client, db):
    u, kid, hit = _setup_one_hit(client, db)
    memo = f'已口頭告誡_{uuid.uuid4().hex[:4]}'

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': memo})

    assert r.status_code == 200
    assert r.get_json()['note_text'] == memo
    db.session.expire_all()
    from models import TrackedKeywordHit
    assert db.session.get(TrackedKeywordHit, hit.id).note_text == memo


def test_note_text_can_be_cleared(client, db):
    u, kid, hit = _setup_one_hit(client, db)
    client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': '先寫一段'})

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': ''})

    assert r.status_code == 200
    assert r.get_json()['note_text'] == ''


def test_exclude_and_restore(client, db):
    u, kid, hit = _setup_one_hit(client, db)
    from models import TrackedKeywordHit

    r = client.post(f'/notes/api/tracking/hits/{hit.id}/exclude',
                    json={'excluded': True})
    assert r.status_code == 200
    assert r.get_json()['excluded'] is True
    db.session.expire_all()
    row = db.session.get(TrackedKeywordHit, hit.id)
    assert row.excluded_at is not None
    assert row.excluded_by == u.id
    assert row.excluded_by_name == u.username

    r = client.post(f'/notes/api/tracking/hits/{hit.id}/exclude',
                    json={'excluded': False})
    assert r.status_code == 200
    assert r.get_json()['excluded'] is False
    db.session.expire_all()
    row = db.session.get(TrackedKeywordHit, hit.id)
    assert row.excluded_at is None
    assert row.excluded_by is None
    assert row.excluded_by_name is None


def test_exclusion_is_soft_delete_row_survives(client, db):
    """剔除不可以真刪 —— 真刪的話下一輪增量掃描會把它抓回來。"""
    u, kid, hit = _setup_one_hit(client, db)
    from models import TrackedKeywordHit
    client.post(f'/notes/api/tracking/hits/{hit.id}/exclude', json={'excluded': True})
    assert db.session.get(TrackedKeywordHit, hit.id) is not None


def test_cross_store_hit_is_forbidden(client, db):
    u, kid, hit = _setup_one_hit(client, db, store='B店')
    intruder = _mk_user(db, store='A店')
    _login(client, intruder)

    assert client.put(f'/notes/api/tracking/hits/{hit.id}/note',
                      json={'text': 'x'}).status_code == 403
    assert client.post(f'/notes/api/tracking/hits/{hit.id}/exclude',
                       json={'excluded': True}).status_code == 403
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_tracking_hits_api.py -v`
Expected: FAIL —— 404（端點不存在）

- [ ] **Step 3: 寫最小實作**

在 `app_unified/notes/tracking.py` 的 `delete_tracking` 之後加：

```python
def _get_hit_or_404(hid):
    h = db.session.get(TrackedKeywordHit, hid)
    if not h:
        abort(404)
    _get_tracked_or_404(h.keyword_id)   # 同店檢查（不同店 403）
    return h


@notes_bp.route('/api/tracking/hits/<int:hid>/note', methods=['PUT'])
@login_required
def set_hit_note(hid):
    """備註：整店共用，同店的人都能寫、也能覆蓋別人寫的（與剔除同一套規則）。"""
    h = _get_hit_or_404(hid)
    data = request.get_json(silent=True) or {}
    h.note_text = (data.get('text') or '').strip()
    db.session.commit()
    return jsonify({'note_text': h.note_text})


@notes_bp.route('/api/tracking/hits/<int:hid>/exclude', methods=['POST'])
@login_required
def toggle_hit_exclude(hid):
    """「不相關」：軟刪除，不可改成真刪 —— 真刪的話下一輪增量掃描會再抓回來
    （那筆原始資料仍然含有關鍵字）。整店共用：一個人剔除，大家都不再看到。"""
    h = _get_hit_or_404(hid)
    data = request.get_json(silent=True) or {}
    if data.get('excluded'):
        h.excluded_at = datetime.utcnow()
        h.excluded_by = current_user.id
        h.excluded_by_name = current_user.username
    else:
        h.excluded_at = None
        h.excluded_by = None
        h.excluded_by_name = None
    db.session.commit()
    return jsonify({'excluded': h.excluded_at is not None,
                    'excluded_by_name': h.excluded_by_name})
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_tracking_hits_api.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 跑全套件**

Run: `cd app_unified && ../venv/bin/python -m pytest -q`
Expected: 全綠

- [ ] **Step 6: Commit**

```bash
git add app_unified/notes/tracking.py app_unified/tests/test_tracking_hits_api.py
git commit -m "feat(tracking): 命中列的備註與「不相關」剔除"
```

---

### Task 6: 追蹤表頁面（清單 + 詳細）

**Files:**
- Modify: `app_unified/notes/tracking.py`（`tracking_detail_page` 組出真正的 rows）
- Modify: `app_unified/templates/notes/tracking_list.html`
- Modify: `app_unified/templates/notes/tracking_detail.html`
- Test: `app_unified/tests/test_tracking_detail_rows.py`

**Interfaces:**
- Consumes: Task 4 / 5 的所有端點。
- Produces:
  - `_detail_rows(tracked) -> list[dict]`，每個 dict 有 `hit_id` / `target_type` / `note_id` / `title` / `text` / `memo` / `when`（`tw_str`），依原始資料的 `updated_at` 由新到舊排序。

- [ ] **Step 1: 寫失敗的測試**

建立 `app_unified/tests/test_tracking_detail_rows.py`：

```python
"""詳細頁的資料組裝：即時取原文、過濾軟刪與已剔除、排序。"""
import uuid
from datetime import datetime, timedelta
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_note(db, store='B店', title='日報', content=''):
    from models import Note
    n = Note(store=store, title=title, content=content, note_type='note')
    db.session.add(n)
    db.session.commit()
    return n


def _mk_item(db, note, text):
    from models import ChecklistItem
    i = ChecklistItem(note_id=note.id, text=text)
    db.session.add(i)
    db.session.commit()
    return i


def _mk_tracked_and_scan(db, keyword, store='B店'):
    from models import TrackedKeyword
    from notes.tracking_scan import scan_tracked_keyword
    t = TrackedKeyword(store=store, keyword=keyword, created_by_name='tester')
    db.session.add(t)
    db.session.commit()
    scan_tracked_keyword(t)
    return t


def test_rows_read_live_text_not_a_copy(db):
    """原始項目被修正後，追蹤表必須顯示最新文字（參照，不是複本）。"""
    from notes.tracking import _detail_rows
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title='日報')
    item = _mk_item(db, n, f'{kw} 遲到 10 分鐘')
    t = _mk_tracked_and_scan(db, kw)

    item.text = f'{kw} 遲到 5 分鐘'
    db.session.commit()

    rows = _detail_rows(t)
    assert len(rows) == 1
    assert '5 分鐘' in rows[0]['text']


def test_rows_hide_soft_deleted_targets(db):
    """一筆資料可能是先被比對到、之後才被刪 —— 顯示時要再過濾一次。"""
    from notes.tracking import _detail_rows
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title='日報')
    item = _mk_item(db, n, f'{kw} 遲到')
    t = _mk_tracked_and_scan(db, kw)
    assert len(_detail_rows(t)) == 1

    item.deleted_at = datetime.utcnow()
    db.session.commit()

    assert _detail_rows(t) == []


def test_rows_hide_excluded(db):
    from notes.tracking import _detail_rows
    from models import TrackedKeywordHit
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    _mk_note(db, title=f'{kw} 的交接')
    t = _mk_tracked_and_scan(db, kw)

    h = TrackedKeywordHit.query.filter_by(keyword_id=t.id).first()
    h.excluded_at = datetime.utcnow()
    db.session.commit()

    assert _detail_rows(t) == []


def test_rows_carry_memo_and_link_target(db):
    from notes.tracking import _detail_rows
    from models import TrackedKeywordHit
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    n = _mk_note(db, title='日報')
    item = _mk_item(db, n, f'{kw} 遲到')
    t = _mk_tracked_and_scan(db, kw)
    h = TrackedKeywordHit.query.filter_by(keyword_id=t.id).first()
    h.note_text = '已口頭告誡'
    db.session.commit()

    row = _detail_rows(t)[0]
    assert row['memo'] == '已口頭告誡'
    assert row['note_id'] == n.id       # [→ 原文] 連回母筆記
    assert row['target_type'] == 'item'
    assert row['hit_id'] == h.id
    assert row['title'] == '日報'
    assert item.id


def test_rows_sorted_newest_first(db):
    from notes.tracking import _detail_rows
    kw = f'王小明{uuid.uuid4().hex[:6]}'
    old = _mk_note(db, title=f'{kw} 舊的')
    new = _mk_note(db, title=f'{kw} 新的')
    old.updated_at = datetime.utcnow() - timedelta(days=3)
    new.updated_at = datetime.utcnow()
    db.session.commit()
    t = _mk_tracked_and_scan(db, kw)

    rows = _detail_rows(t)
    assert [r['note_id'] for r in rows] == [new.id, old.id]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_tracking_detail_rows.py -v`
Expected: FAIL —— `ImportError: cannot import name '_detail_rows'`

- [ ] **Step 3: 寫最小實作**

在 `app_unified/notes/tracking.py` 頂端的 import 加上 `Note, ChecklistItem`：

```python
from models import TrackedKeyword, TrackedKeywordHit, Note, ChecklistItem
```

在 `tracking_detail_page` **之前**插入：

```python
def _detail_rows(tracked):
    """組出詳細頁的每一列。

    每一列是**參照**：文字一律即時從原始 Note / ChecklistItem 讀，不是命中當下的
    複本 —— 原文被修正後追蹤表要跟著變，否則會出現「追蹤表寫著遲到 10 分鐘、原始
    項目已更正成 5 分鐘」的落差。

    過濾兩層：已剔除的（excluded_at 有值）、以及原始資料已軟刪的。後者必須在
    顯示時再檢查一次 —— 命中列是掃描當下寫的，資料可能之後才被刪；而命中列刻意
    不主動清除（原始資料可能只是暫時被刪、24 小時內可還原）。
    """
    hits = TrackedKeywordHit.query.filter_by(keyword_id=tracked.id).filter(
        TrackedKeywordHit.excluded_at.is_(None)).all()
    note_ids = [h.target_id for h in hits if h.target_type == 'note']
    item_ids = [h.target_id for h in hits if h.target_type == 'item']

    notes = {n.id: n for n in Note.query.filter(
        Note.id.in_(note_ids), Note.deleted_at.is_(None)).all()} if note_ids else {}
    items = {i.id: i for i in ChecklistItem.query.filter(
        ChecklistItem.id.in_(item_ids),
        ChecklistItem.deleted_at.is_(None)).all()} if item_ids else {}

    rows = []
    for h in hits:
        if h.target_type == 'note':
            n = notes.get(h.target_id)
            if not n:
                continue          # 原文已軟刪或已硬刪 → 這一列自然消失
            title, text, note_id, when = n.title, n.content, n.id, n.updated_at
        else:
            i = items.get(h.target_id)
            if not i or not i.note or i.note.deleted_at is not None:
                continue
            title, text, note_id, when = i.note.title, i.text, i.note_id, i.updated_at
        rows.append({
            'hit_id': h.id,
            'target_type': h.target_type,
            'note_id': note_id,
            'title': title or '（未命名）',
            'text': text or '',
            'memo': h.note_text or '',
            'when': tw_str(when, fmt='%m/%d'),
            'sort_key': when,
        })
    rows.sort(key=lambda r: r['sort_key'], reverse=True)
    for r in rows:
        r.pop('sort_key')
    return rows
```

把 `tracking_detail_page` 的 `rows=[]` 改成 `rows=_detail_rows(t)`：

```python
@notes_bp.route('/tracking/<int:kid>')
@login_required
def tracking_detail_page(kid):
    t = _get_tracked_or_404(kid)
    scan_tracked_keyword(t)   # 每次開啟跑一次增量
    return render_template('notes/tracking_detail.html',
                           tracked=_tracked_to_dict(t),
                           rows=_detail_rows(t))
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_tracking_detail_rows.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 寫完整的清單頁模板**

覆寫 `app_unified/templates/notes/tracking_list.html`：

```html
{% extends "base.html" %}
{% block title %}建檔追蹤{% endblock %}
{% block extra_head %}
<style>
.tracking-card { display:flex; align-items:center; gap:.6rem; padding:.8rem 1rem;
  background:#fff; border:1px solid #e4e4e4; border-radius:8px; margin-bottom:.6rem; }
.tracking-card h3 { margin:0; font-size:1rem; }
.tracking-meta { color:#888; font-size:.8rem; }
.tracking-empty { color:#888; padding:1.2rem; text-align:center; }
</style>
{% endblock %}
{% block content %}
<div class="toolbar">
  <h2>🔎 建檔追蹤</h2>
  <div style="margin-left:auto;">
    <a href="{{ url_for('notes.index') }}" class="btn btn-secondary"
       style="text-decoration:none;">← 回筆記</a>
  </div>
</div>

{% if tracking %}
{% for t in tracking %}
<div class="tracking-card">
  <div style="flex:1;">
    <h3><a href="{{ url_for('notes.tracking_detail_page', kid=t.id) }}"
           style="text-decoration:none;color:inherit;">{{ t.keyword }}</a></h3>
    <div class="tracking-meta">
      {{ t.hit_count }} 筆 · {{ t.created_by_name or '—' }} 於 {{ t.created_at }} 建立
    </div>
  </div>
  <button type="button" class="btn btn-secondary" data-role="del-tracking"
          data-id="{{ t.id }}" data-keyword="{{ t.keyword }}"
          style="font-size:.8rem;padding:.3rem .7rem;">刪除</button>
</div>
{% endfor %}
{% else %}
<div class="tracking-empty">
  還沒有任何追蹤表。到筆記頁搜尋一個關鍵字，在結果上方按「建檔追蹤」即可建立。
</div>
{% endif %}

<script>
document.querySelectorAll('[data-role="del-tracking"]').forEach(function (btn) {
  btn.addEventListener('click', function () {
    // 刻意不用 confirm() —— 瀏覽器 modal 會卡住自動化工具；改用行內二次確認
    if (btn.dataset.armed !== '1') {
      btn.dataset.armed = '1';
      btn.textContent = '再按一次確認刪除';
      setTimeout(function () {
        btn.dataset.armed = '0';
        btn.textContent = '刪除';
      }, 4000);
      return;
    }
    fetch('/notes/api/tracking/' + btn.dataset.id, {method: 'DELETE'})
      .then(function (r) { return r.json(); })
      .then(function () { location.reload(); });
  });
});
</script>
{% endblock %}
```

- [ ] **Step 6: 寫完整的詳細頁模板**

覆寫 `app_unified/templates/notes/tracking_detail.html`：

```html
{% extends "base.html" %}
{% block title %}{{ tracked.keyword }} 建檔追蹤{% endblock %}
{% block extra_head %}
<style>
/* 備註放在每一列最上面：追蹤的重點是「我對這件事做了什麼」，那比原始文字更需要
   第一眼看到（規格明確指定）。 */
.hit-card { background:#fff; border:1px solid #e4e4e4; border-radius:8px;
  padding:.8rem 1rem; margin-bottom:.6rem; }
.hit-memo { display:block; width:100%; box-sizing:border-box; border:1px dashed #d7c48a;
  background:#fffdf3; border-radius:6px; padding:.4rem .6rem; font-size:.88rem;
  margin-bottom:.5rem; resize:vertical; min-height:2.2rem; }
.hit-meta { color:#888; font-size:.8rem; margin-bottom:.25rem; }
.hit-text { font-size:.95rem; white-space:pre-wrap; word-break:break-word; }
.hit-actions { display:flex; gap:.5rem; justify-content:flex-end; margin-top:.5rem; }
.tracking-empty { color:#888; padding:1.2rem; text-align:center; }
</style>
{% endblock %}
{% block content %}
<div class="toolbar">
  <h2>{{ tracked.keyword }} 建檔追蹤</h2>
  <div style="margin-left:auto;">
    <a href="{{ url_for('notes.tracking_list_page') }}" class="btn btn-secondary"
       style="text-decoration:none;">← 所有追蹤表</a>
  </div>
</div>

{% if rows %}
{% for r in rows %}
<div class="hit-card" data-hit="{{ r.hit_id }}">
  <textarea class="hit-memo" data-role="memo" data-hit="{{ r.hit_id }}"
            placeholder="備註…">{{ r.memo }}</textarea>
  <div class="hit-meta">
    {{ r.when }} · {{ r.title }}{% if r.target_type == 'item' %} · 項目{% endif %}
  </div>
  <div class="hit-text">{{ r.text }}</div>
  <div class="hit-actions">
    <button type="button" class="btn btn-secondary" data-role="exclude"
            data-hit="{{ r.hit_id }}"
            style="font-size:.8rem;padding:.3rem .7rem;">不相關</button>
    <a class="btn btn-secondary" style="font-size:.8rem;padding:.3rem .7rem;text-decoration:none;"
       href="{{ url_for('notes.edit_note', note_id=r.note_id) }}">→ 原文</a>
  </div>
</div>
{% endfor %}
{% else %}
<div class="tracking-empty">目前沒有命中的資料。</div>
{% endif %}

<script>
// 備註：失焦才送，避免每打一個字就打一次 API
document.querySelectorAll('[data-role="memo"]').forEach(function (ta) {
  var initial = ta.value;
  ta.addEventListener('blur', function () {
    if (ta.value === initial) return;
    fetch('/notes/api/tracking/hits/' + ta.dataset.hit + '/note', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: ta.value})
    }).then(function (r) { return r.json(); })
      .then(function (d) { initial = d.note_text; });
  });
});

document.querySelectorAll('[data-role="exclude"]').forEach(function (btn) {
  btn.addEventListener('click', function () {
    fetch('/notes/api/tracking/hits/' + btn.dataset.hit + '/exclude', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({excluded: true})
    }).then(function (r) { return r.json(); })
      .then(function () {
        var card = document.querySelector('.hit-card[data-hit="' + btn.dataset.hit + '"]');
        if (card) card.remove();
      });
  });
});
</script>
{% endblock %}
```

- [ ] **Step 7: 跑全套件**

Run: `cd app_unified && ../venv/bin/python -m pytest -q`
Expected: 全綠

- [ ] **Step 8: Commit**

```bash
git add app_unified/notes/tracking.py \
        app_unified/templates/notes/tracking_list.html \
        app_unified/templates/notes/tracking_detail.html \
        app_unified/tests/test_tracking_detail_rows.py
git commit -m "feat(tracking): 追蹤表清單頁與詳細頁"
```

---

### Task 7: 兩個入口 + 人工測試腳本

**Files:**
- Modify: `app_unified/notes/routes.py`（`index()` 傳 `tracking_existing_id`）
- Modify: `app_unified/templates/notes/index.html`（toolbar 連結、搜尋結果上方按鈕）
- Create: `app_unified/tests/test_tracking_ui_wiring.py`
- Create: `docs/superpowers/plans/2026-09-09-keyword-tracking-manual-test.md`

**Interfaces:**
- Consumes: Task 4 的 `find_tracking_for_keyword`。
- Produces: `index()` 模板變數 `tracking_existing_id`（`int | None`）。

- [ ] **Step 1: 寫失敗的測試**

建立 `app_unified/tests/test_tracking_ui_wiring.py`：

```python
"""入口接線：toolbar 連結、搜尋結果上方的建檔追蹤按鈕。"""
import json as _json
import uuid
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _uniq(p='T'):
    return f'{p}_{uuid.uuid4().hex[:6]}'


def _mk_user(db, store='B店'):
    from models import User
    u = User(username=_uniq('u'), role='user', store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def _mk_note(db, store='B店', title='日報'):
    from models import Note
    n = Note(store=store, title=title, content='', note_type='note')
    db.session.add(n)
    db.session.commit()
    return n


def test_index_has_tracking_entry_link(client, db):
    _login(client, _mk_user(db))
    html = client.get('/notes/').get_data(as_text=True)
    assert '/notes/tracking/' in html
    assert '建檔追蹤' in html


def test_search_page_offers_create_button(client, db):
    kw = _uniq('王小明')
    _mk_note(db, title=f'{kw} 的交接')
    _login(client, _mk_user(db))

    html = client.get(f'/notes/?q={kw}').get_data(as_text=True)

    assert 'data-role="create-tracking"' in html
    assert '建檔追蹤' in html


def test_search_page_offers_open_button_when_already_tracked(client, db):
    kw = _uniq('王小明')
    _mk_note(db, title=f'{kw} 的交接')
    _login(client, _mk_user(db))
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']

    html = client.get(f'/notes/?q={kw}').get_data(as_text=True)

    assert f'/notes/tracking/{kid}' in html
    assert '開啟追蹤表' in html
    assert 'data-role="create-tracking"' not in html


def test_non_search_page_has_no_create_button(client, db):
    """建檔追蹤只從搜尋結果進入 —— 這是「搜尋=暫時 / 建檔=持續」分界的一部分。"""
    _login(client, _mk_user(db))
    html = client.get('/notes/').get_data(as_text=True)
    assert 'data-role="create-tracking"' not in html
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_tracking_ui_wiring.py -v`
Expected: FAIL —— 找不到 `建檔追蹤` / `data-role="create-tracking"`

- [ ] **Step 3: 路由傳變數**

在 `app_unified/notes/routes.py` 的 `index()` 裡，緊接在 keyword 分頁那段（`pagination = _PyPagination(filtered, page=page, per_page=per_page)`）之後加：

```python
        # 本店是否已有這個關鍵字的追蹤表 → 決定按鈕顯示「建檔追蹤」還是「開啟追蹤表」。
        # 函式內 import：notes.tracking 會 import notes.routes 的 notes_bp，模組層級
        # import 會形成循環（與本檔其他 deferred import 同一個理由）。
        from notes.tracking import find_tracking_for_keyword
        tracking_existing_id = find_tracking_for_keyword(keyword)
```

並在同一個函式的非 keyword 分支把它設成 `None`。最小改法是在 `keyword_mode = False` 那行附近先給預設值：

```python
    keyword_mode = False
    active_filter = None
    tracking_existing_id = None
```

再把 `tracking_existing_id=tracking_existing_id` 加進 `render_template(...)` 的參數（就在既有的 `current_keyword=keyword,` 那一行旁邊）。

- [ ] **Step 4: 模板加兩個入口**

在 `app_unified/templates/notes/index.html` 的 toolbar（`{% if current_user.is_admin() %}` 那個公告按鈕之後、`</div>` 之前）加：

```html
    <a href="{{ url_for('notes.tracking_list_page') }}" class="btn btn-secondary"
       style="text-decoration:none;display:inline-block;">🔎 建檔追蹤</a>
```

在筆記列表之前（`{% if notes %}` 那一行**之前**）加搜尋結果上方的按鈕：

```html
{# 建檔追蹤入口：只在關鍵字搜尋時出現。搜尋是暫時的、按下這顆才建立持續累積的表 —— #}
{# 這個分界是刻意的，兩者都是「輸入關鍵字、列出命中」，很容易被混為一談。 #}
{% if current_keyword %}
<div style="display:flex;align-items:center;gap:.6rem;margin:.6rem 0;padding:.6rem .8rem;
            background:#f6f9ff;border:1px solid #d6e4ff;border-radius:8px;">
  <span style="font-size:.85rem;color:#555;">「{{ current_keyword }}」</span>
  {% if tracking_existing_id %}
  <a href="{{ url_for('notes.tracking_detail_page', kid=tracking_existing_id) }}"
     class="btn btn-primary" style="font-size:.82rem;padding:.3rem .8rem;text-decoration:none;">
    開啟追蹤表
  </a>
  {% else %}
  <button type="button" class="btn btn-primary" data-role="create-tracking"
          data-keyword="{{ current_keyword }}"
          style="font-size:.82rem;padding:.3rem .8rem;">建檔追蹤</button>
  <span style="font-size:.78rem;color:#888;">建立後會持續累積，可寫備註、剔除不相關的</span>
  {% endif %}
</div>
<script>
(function () {
  var btn = document.querySelector('[data-role="create-tracking"]');
  if (!btn) return;
  btn.addEventListener('click', function () {
    btn.disabled = true;
    btn.textContent = '建立中…';   // 第一次是完整掃描，會花幾秒
    fetch('/notes/api/tracking', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({keyword: btn.dataset.keyword})
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { btn.disabled = false; btn.textContent = '建檔追蹤'; alert(d.error); return; }
        location.href = '/notes/tracking/' + d.id;
      })
      .catch(function () { btn.disabled = false; btn.textContent = '建檔追蹤'; });
  });
})();
</script>
{% endif %}
```


- [ ] **Step 5: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_tracking_ui_wiring.py -v`
Expected: PASS（4 passed）

- [ ] **Step 6: 跑全套件**

Run: `cd app_unified && ../venv/bin/python -m pytest -q`
Expected: 全綠

- [ ] **Step 7: 本機實跑一次**

Run: `cd app_unified && bash scripts/run_local.sh`
然後在瀏覽器：
1. <http://localhost:5000/dev> 選一個有 notes 權限的身分。
2. `/notes/` 應看到 toolbar 有「🔎 建檔追蹤」。
3. 搜尋一個 seed 資料裡出現過的詞 → 結果上方出現「建檔追蹤」按鈕。
4. 按下去 → 跳到 `/notes/tracking/<id>`，列出命中。
5. 在某一列寫備註 → 點別處失焦 → 重整頁面，備註還在。
6. 按「不相關」→ 該列消失 → 重整仍然不在。
7. 回 `/notes/` 新增一個含該關鍵字的 checklist 項目 → 再開追蹤表 → 新項目有出現。

Expected: 七步都符合。任何一步不符就停下來回報。

- [ ] **Step 8: 寫人工測試腳本**

建立 `docs/superpowers/plans/2026-09-09-keyword-tracking-manual-test.md`，內容為上一步那七項，改寫成 prod 版本（網址換成正式站、資料改用真實店別），並補上這幾條只有 prod 驗得到的：

```markdown
# 建檔追蹤 — 上線後人工測試

## 前置
- 用**有 notes 權限、非種子 admin** 的帳號登入（種子 admin 會 bypass 權限，驗不到模組開關）。
- 記下自己的店別；本功能完全綁店別。

## 步驟
1. `/notes/` toolbar 有「🔎 建檔追蹤」→ 點進去，空清單應顯示引導文字。
2. 回 `/notes/`，搜尋一個確定有多筆命中的關鍵字（例如某位同仁的名字）。
3. 結果上方出現「建檔追蹤」→ 按下。**第一次會跑完整掃描，可能等幾秒**，按鈕會變「建立中…」。
4. 跳轉到追蹤表，確認命中筆數與搜尋結果數量相當（不必完全相同：搜尋是以「筆記」為單位，
   追蹤表是以「筆記／項目」為單位，同一篇筆記裡兩個命中項目會是兩列）。
5. 對一列寫備註 → 點別處 → 重整 → 備註仍在，且顯示在該列**最上面**。
6. 對一列按「不相關」→ 消失 → 重整仍不在。
7. 回筆記新增一個含該關鍵字的 checklist 項目 → 重開追蹤表 → 新項目出現。
8. **修改**一個原本不含關鍵字的既有項目，讓它含關鍵字 → 重開追蹤表 → 該項目出現
   （這條驗的是水位用 updated_at 而非最大 id）。
9. 把步驟 6 剔除的那一筆原始資料編輯一下（隨便改個字）→ 重開追蹤表 →
   **它不可以復活**。
10. 回 `/notes/` 再搜尋同一個關鍵字 → 上方按鈕應變成「開啟追蹤表」。
11. 用**別間店**的帳號登入 → `/notes/tracking/` 看不到這張表；直接打
    `/notes/tracking/<id>` 應回 403。

## 效能觀察（只在 prod 看得到）
- 步驟 3 的完整掃描期間，另開一個分頁操作其他頁面應**不會卡住** ——
  掃描是分批解密、批間 `gevent.sleep(0)` 讓出 hub。若明顯卡住，
  到 monitor service 看有沒有 `hub_block` 事件，並回報。
```

- [ ] **Step 9: Commit**

```bash
git add app_unified/notes/routes.py app_unified/templates/notes/index.html \
        app_unified/tests/test_tracking_ui_wiring.py \
        docs/superpowers/plans/2026-09-09-keyword-tracking-manual-test.md
git commit -m "feat(tracking): 筆記頁與搜尋結果的建檔追蹤入口"
```

---

### Task 8: 文件

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 更新資料表清單**

在 `CLAUDE.md` 的「資料表（`models.py`）」那一段，把 `Tag` · `NoteTag` · `ChecklistItemTag` 後面接上：

```
`TrackedKeyword` · `TrackedKeywordHit`
```

- [ ] **Step 2: 更新 notes 藍圖的功能描述**

把 notes 那一列的功能欄從

```
店務筆記 CRUD、checklist、附件、標籤、AI 摘要、公告
```

改成

```
店務筆記 CRUD、checklist、附件、標籤、快速標籤鍵、建檔追蹤、AI 摘要、公告
```

- [ ] **Step 3: 修掉技術棧裡過期的 Flask-Migrate**

CLAUDE.md 的技術棧寫著 `Flask-Migrate`，但全 repo 沒有任何地方 import 它、`Migrate()` 一次都沒被呼叫、`requirements` 也沒有它 —— `migrations/` 那 21 個版本檔從來沒被執行過。把技術棧那行的 `+ Flask-Migrate` 拿掉，並在「應用入口與組態」表的 `app.py` 那列補一句：

```
| `app.py` | `create_app()` 工廠、藍圖註冊、開機 schema 修復（跨 worker advisory lock 序列化）。**schema 的真實來源就是這裡**：`db.create_all()` 建缺少的表（本機與 prod 都跑），加欄位的 `ADD COLUMN IF NOT EXISTS` 只在 PG 跑（本機 SQLite 跳過）。`migrations/` 是保留但未接線的 Alembic 檔案，不會被執行。|
```

- [ ] **Step 4: 跑全套件最後確認**

Run: `cd app_unified && ../venv/bin/python -m pytest -q`
Expected: 全綠

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 補建檔追蹤、修正 Flask-Migrate 的過期描述"
```

---

## 上線後的必辦事項

- 建檔追蹤**沒有**「每店要先設定」這種前置（不像階段一的快速標籤鍵要各店自己釘選標籤），部署後直接可用。
- 第一位使用者建第一張表時會跑完整掃描。若 prod 上出現 `hub_block` 事件，就是 `SCAN_BATCH` 太大，調小即可（不需要改架構）。
