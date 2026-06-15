# 店務提醒日期（reminder_date）+ 稽核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 mynotes 的「提醒日期」機制複製進 notes（筆記/確認表單）模組，做成全店共用的到期/今日提醒，並加稽核（inline 最後修改者快照 + `NoteLog` 完整歷史）。

**Architecture:** `Note` 與 `ChecklistItem` 各加 4 欄（`reminder_date` + 3 欄修改者快照）。設/改/清到期日各 append 一筆 `NoteLog`（action `reminder_set`/`reminder_change`/`reminder_clear`），零新 table。前端在公告下方加一個 collapsible「今日提醒」面板，重用 `collapsible_sections.js` 的公告 auto-expand 機制（每天首開、之後縮、有人調整就彈開）。mynotes 完全不動。

**Tech Stack:** Flask + SQLAlchemy（prod PG / dev SQLite）、Jinja2 + Vanilla JS、pytest。schema 走 prod app.py inline ALTER（非 alembic）+ dev migration。

**Spec:** `docs/superpowers/specs/2026-06-15-notes-reminder-due-date-design.md`

---

## File Structure

| 檔案 | 變更 | 責任 |
|---|---|---|
| `models.py` | Modify | `Note` + `ChecklistItem` 各加 4 欄 + `reminder_setter` relationship |
| `app.py` | Modify | inline schema repair 加 8 條 `ALTER ... ADD COLUMN IF NOT EXISTS` |
| `migrations/versions/017_note_reminder.py` | Create | dev alembic migration（對應上面欄位） |
| `notes/routes.py` | Modify | 日期 helper、reminder 套用/log helper、create/update note、create/patch item、serializers、`/api/reminders/today` endpoint |
| `templates/notes/editor.html` | Modify | note 級 + 項目 modal 到期日 picker + inline「最後由 X 設定」+ 存檔帶 `reminder_date` |
| `templates/notes/index.html` | Modify | 公告下方加「今日提醒」collapsible section |
| `static/js/collapsible_sections.js` | Modify | `initRemindersSection()`：fetch + render + auto-expand |
| `static/css/collapsible_sections.css` | Modify | 提醒卡片樣式（逾期/今日色） |
| `static/sw.js` | Modify | bump `CACHE_NAME`（改了 .js/.css，踩雷 #5） |
| `tests/test_notes_reminder.py` | Create | 後端行為測試 |
| `tests/test_note_reminder_schema_alter.py` | Create | app.py ALTER guard（仿 `test_checklist_item_schema_alter.py`） |

## Conventions（先看，後面 task 會用到）

- TW 時區 helper：`notes/routes.py` 已有 `_TW = timezone(timedelta(hours=8))`（line 30）。本計畫新增 `_today_tw()` 與 `_parse_date()`（Task 4 之前先在 Task 2 建立）。
- `reminder_set_by_name` 在 model 用 `EncryptedText`，對應 PG 欄位型別 `TEXT`（同 `author_name` 慣例）。
- NoteLog 既有欄位：`note_id` / `note_title` / `user_id` / `action` / `diff` / `created_at`（`created_at` 自動帶 UTC 時間戳）。
- 本機驗證一律 `pytest tests/`（不是裸 `pytest`，踩雷 #11）。

---

### Task 1: Schema — models + app.py ALTER + migration + guard test

**Files:**
- Modify: `models.py`（`Note` class line 118-174、`ChecklistItem` class line 210-251）
- Modify: `app.py`（inline ALTER 區，line 283 後）
- Create: `migrations/versions/017_note_reminder.py`
- Create: `tests/test_note_reminder_schema_alter.py`

- [ ] **Step 1: 寫 guard test（失敗）** — `tests/test_note_reminder_schema_alter.py`

```python
"""Guard: app.py schema repair has reminder_date + audit ALTERs for notes + checklist_items.

漏掉任一條 → prod 首次部署 schema repair 靜默缺欄 → 寫入 reminder 欄位 500 ISE。
本機 SQLite 抓不到（feedback_inline_schema_repair）。
"""
from pathlib import Path

APP_PY = Path(__file__).parent.parent / 'app.py'
COLS = ['reminder_date', 'reminder_set_by', 'reminder_set_by_name', 'reminder_set_at']


def test_reminder_alters_present():
    src = APP_PY.read_text(encoding='utf-8')
    for tbl in ('notes', 'checklist_items'):
        for col in COLS:
            token = f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col}"
            assert token in src, f'missing ALTER for {tbl}.{col}'
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_note_reminder_schema_alter.py -v`
Expected: FAIL（`missing ALTER for notes.reminder_date`）

- [ ] **Step 3: models.py 加欄位** — `Note` class，在 `deleted_by`（line 141）後、relationships（line 143）前插入：

```python
    # 到期提醒 + 稽核快照（2026-06-15）
    reminder_date = db.Column(db.Date, nullable=True)
    reminder_set_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reminder_set_by_name = db.Column(EncryptedText, nullable=True)
    reminder_set_at = db.Column(db.DateTime, nullable=True)
```

在 `Note` relationships 區（line 143-148 附近，`deleter` relationship 後）加：

```python
    reminder_setter = db.relationship("User", foreign_keys=[reminder_set_by])
```

`ChecklistItem` class，在 `updated_at`（line 231）後插入相同 4 欄：

```python
    # 到期提醒 + 稽核快照（2026-06-15）
    reminder_date = db.Column(db.Date, nullable=True)
    reminder_set_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reminder_set_by_name = db.Column(EncryptedText, nullable=True)
    reminder_set_at = db.Column(db.DateTime, nullable=True)
```

在 `ChecklistItem` relationships 區（line 239-240 `modifier` 後）加：

```python
    reminder_setter = db.relationship("User", foreign_keys=[reminder_set_by])
```

- [ ] **Step 4: app.py 加 inline ALTER** — 在 line 283（`hidden_stores` 的 `except Exception: pass` 之後）插入。**逐條明確寫**（不要用迴圈/f-string，guard test 抓字面字串）：

```python
                # 店務筆記到期提醒 reminder_date + 稽核快照（2026-06-15）— notes
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS reminder_date DATE"))
                except Exception:
                    pass
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS reminder_set_by INTEGER "
                        "REFERENCES users(id) ON DELETE SET NULL"))
                except Exception:
                    pass
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS reminder_set_by_name TEXT"))
                except Exception:
                    pass
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS reminder_set_at TIMESTAMP"))
                except Exception:
                    pass
                # 同上 — checklist_items
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS reminder_date DATE"))
                except Exception:
                    pass
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS reminder_set_by INTEGER "
                        "REFERENCES users(id) ON DELETE SET NULL"))
                except Exception:
                    pass
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS reminder_set_by_name TEXT"))
                except Exception:
                    pass
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS reminder_set_at TIMESTAMP"))
                except Exception:
                    pass
```

- [ ] **Step 5: 建 migration 017** — `migrations/versions/017_note_reminder.py`

```python
"""notes + checklist_items reminder_date + audit snapshot

Revision ID: 017
Revises: 016
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa


revision = '017'
down_revision = '016'
branch_labels = None
depends_on = None


def upgrade():
    for tbl in ('notes', 'checklist_items'):
        op.add_column(tbl, sa.Column('reminder_date', sa.Date(), nullable=True))
        op.add_column(tbl, sa.Column('reminder_set_by', sa.Integer(),
                                     sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))
        op.add_column(tbl, sa.Column('reminder_set_by_name', sa.Text(), nullable=True))
        op.add_column(tbl, sa.Column('reminder_set_at', sa.DateTime(), nullable=True))


def downgrade():
    for tbl in ('notes', 'checklist_items'):
        op.drop_column(tbl, 'reminder_set_at')
        op.drop_column(tbl, 'reminder_set_by_name')
        op.drop_column(tbl, 'reminder_set_by')
        op.drop_column(tbl, 'reminder_date')
```

- [ ] **Step 6: dev SQLite 同步**（`IF NOT EXISTS` SQLite 不支援、被吞，feedback_dev_sqlite_alter）

Run: `rm -f instance/dev.db`（下次啟動 `db.create_all()` 會用新 model 重建；若有 seed 腳本接著跑）
或手動：`sqlite3 instance/dev.db "ALTER TABLE notes ADD COLUMN reminder_date DATE; ..."`（8 條）

- [ ] **Step 7: 跑 guard test 確認通過**

Run: `pytest tests/test_note_reminder_schema_alter.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add models.py app.py migrations/versions/017_note_reminder.py tests/test_note_reminder_schema_alter.py
git commit -m "feat(notes): reminder_date + audit columns schema (notes + checklist_items)"
```

---

### Task 2: note 級 reminder 後端（create / update / serializers + helper）

**Files:**
- Modify: `notes/routes.py`（imports line 4、helper 區 line 30 後、`create_note` 623-650、`update_note` 691-744、`list_notes` 594-603、`get_note` 667-676）
- Test: `tests/test_notes_reminder.py`

- [ ] **Step 1: 寫失敗測試** — `tests/test_notes_reminder.py`（新檔，先放 note 級三例）

```python
"""店務筆記到期提醒 + 稽核（reminder_date / NoteLog）。"""
import json
from datetime import date, timedelta

from models import db, Note, NoteLog


def _login(client, app, username="u1", store="A店", role="user"):
    # 依專案既有測試 fixture 慣例建立/登入 user；參考 tests/test_notes.py 的 helper。
    from tests.test_notes import login_as  # 若無此 helper，改用該檔實際的登入方式
    return login_as(client, app, username=username, store=store, role=role)


def test_set_note_reminder_writes_snapshot_and_log(client, app):
    _login(client, app)
    r = client.post("/notes/api", json={"note_type": "note", "title": "月報", "store": "A店"})
    nid = r.get_json()["id"]
    due = (date.today() + timedelta(days=5)).isoformat()
    r = client.put(f"/notes/api/{nid}", json={"reminder_date": due})
    assert r.status_code == 200
    with app.app_context():
        n = Note.query.get(nid)
        assert n.reminder_date.isoformat() == due
        assert n.reminder_set_by_name == "u1"
        assert n.reminder_set_at is not None
        logs = NoteLog.query.filter_by(note_id=nid, action="reminder_set").all()
        assert len(logs) == 1
        assert "到期日" in logs[0].diff


def test_change_note_reminder_logs_change(client, app):
    _login(client, app)
    nid = client.post("/notes/api", json={"note_type": "note", "title": "x", "store": "A店"}).get_json()["id"]
    d1 = (date.today() + timedelta(days=2)).isoformat()
    d2 = (date.today() + timedelta(days=4)).isoformat()
    client.put(f"/notes/api/{nid}", json={"reminder_date": d1})
    client.put(f"/notes/api/{nid}", json={"reminder_date": d2})
    with app.app_context():
        assert NoteLog.query.filter_by(note_id=nid, action="reminder_change").count() == 1


def test_clear_note_reminder_logs_clear(client, app):
    _login(client, app)
    nid = client.post("/notes/api", json={"note_type": "note", "title": "x", "store": "A店"}).get_json()["id"]
    d1 = (date.today() + timedelta(days=2)).isoformat()
    client.put(f"/notes/api/{nid}", json={"reminder_date": d1})
    client.put(f"/notes/api/{nid}", json={"reminder_date": None})
    with app.app_context():
        assert Note.query.get(nid).reminder_date is None
        assert NoteLog.query.filter_by(note_id=nid, action="reminder_clear").count() == 1
```

> 註：`client`/`app` fixture 與登入 helper 請對齊 `tests/test_notes.py` 既有寫法（先讀該檔確認 fixture 名稱與登入方式，調整 `_login`）。

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_notes_reminder.py -v`
Expected: FAIL（reminder_date 未被寫入 / 無 reminder_set log）

- [ ] **Step 3: imports + helper** — `notes/routes.py`

line 4 改為（加 `date`）：

```python
from datetime import datetime, timedelta, timezone, date
```

在 line 30（`_TW = ...`）之後加 helper：

```python
def _today_tw():
    return datetime.now(_TW).date()


def _parse_date(val):
    if not val:
        return None
    try:
        return date.fromisoformat(str(val)[:10])
    except (ValueError, TypeError):
        return None


def _fmt_reminder(d):
    return f"{d.month}/{d.day}" if d else "(無)"


def _reminder_action_diff(old_date, new_date, label_prefix=""):
    """回傳 (action, diff)；action ∈ reminder_set/change/clear。"""
    if old_date is None:
        action = "reminder_set"
    elif new_date is None:
        action = "reminder_clear"
    else:
        action = "reminder_change"
    diff = f"{label_prefix}到期日 {_fmt_reminder(old_date)}→{_fmt_reminder(new_date)}"
    return action, diff


def _apply_reminder(obj, new_date):
    """寫 obj 的 reminder_date + 修改者快照（呼叫前已確認有變更）。"""
    obj.reminder_date = new_date
    obj.reminder_set_by = current_user.id
    obj.reminder_set_by_name = current_user.username
    obj.reminder_set_at = datetime.utcnow()
```

- [ ] **Step 4: create_note 帶 reminder** — `notes/routes.py` `create_note`，在 `db.session.add(note)`（line 639）之前，於 `Note(...)` 之後加：

```python
    rd = _parse_date(data.get("reminder_date")) if note_type == "note" else None
    if rd:
        _apply_reminder(note, rd)
```

在第一個 `db.session.commit()`（line 640）之後加 log（此時 note.id 已生成）：

```python
    if rd:
        action, diff = _reminder_action_diff(None, rd)
        db.session.add(NoteLog(note_id=note.id, note_title=note.title,
                               user_id=current_user.id, action=action, diff=diff))
        db.session.commit()
```

- [ ] **Step 5: update_note 帶 reminder** — `notes/routes.py` `update_note`，在 `note.updated_by = current_user.id`（line 727）之前插入：

```python
    reminder_log = None
    if "reminder_date" in data and note.note_type == "note":
        new_rd = _parse_date(data.get("reminder_date"))
        if new_rd != note.reminder_date:
            action, diff = _reminder_action_diff(note.reminder_date, new_rd)
            _apply_reminder(note, new_rd)
            reminder_log = (action, diff)
```

在既有 `if diff_parts:` NoteLog 區塊（line 734-742）之後、`db.session.commit()`（line 744）之前加（reminder 走**獨立** NoteLog，不混進 diff_parts）：

```python
    if reminder_log:
        db.session.add(NoteLog(note_id=note.id, note_title=note.title,
                               user_id=current_user.id,
                               action=reminder_log[0], diff=reminder_log[1]))
```

- [ ] **Step 6: serializers 露出 reminder** — `list_notes`（line 594-603 dict）加一行：

```python
        "reminder_date": n.reminder_date.isoformat() if n.reminder_date else None,
```

`get_note`（line 667-676 dict）加三行：

```python
        "reminder_date": note.reminder_date.isoformat() if note.reminder_date else None,
        "reminder_set_by_name": note.reminder_set_by_name or (note.reminder_setter.username if note.reminder_setter else None),
        "reminder_set_at": tw_iso(note.reminder_set_at, fallback=None),
```

- [ ] **Step 7: 跑測試確認通過**

Run: `pytest tests/test_notes_reminder.py -v`
Expected: PASS（3 note 級測試）

- [ ] **Step 8: Commit**

```bash
git add notes/routes.py tests/test_notes_reminder.py
git commit -m "feat(notes): note-level reminder_date set/change/clear + NoteLog audit"
```

---

### Task 3: 項目級 reminder 後端（create item / patch item / serializer）

**Files:**
- Modify: `notes/routes.py`（`_checklist_item_json` 1499-1524、`create_checklist_item` 1597-1623、`update_checklist_item` 1648-1685）
- Test: `tests/test_notes_reminder.py`（append）

- [ ] **Step 1: 加失敗測試** — append 到 `tests/test_notes_reminder.py`

```python
def test_set_item_reminder_logs_with_item_text(client, app):
    _login(client, app)
    nid = client.post("/notes/api", json={"note_type": "checklist", "title": "進貨", "store": "A店"}).get_json()["id"]
    iid = client.post(f"/notes/api/{nid}/items", json={"text": "點收"}).get_json()["item"]["id"]
    due = (date.today() + timedelta(days=3)).isoformat()
    r = client.patch(f"/notes/api/items/{iid}", json={"reminder_date": due})
    assert r.status_code == 200
    assert r.get_json()["item"]["reminder_date"] == due
    with app.app_context():
        from models import ChecklistItem
        it = ChecklistItem.query.get(iid)
        assert it.reminder_set_by_name == "u1"
        logs = NoteLog.query.filter_by(note_id=nid, action="reminder_set").all()
        assert len(logs) == 1
        assert "點收" in logs[0].diff
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_notes_reminder.py::test_set_item_reminder_logs_with_item_text -v`
Expected: FAIL

- [ ] **Step 3: _checklist_item_json 露出 reminder** — 在 return dict（line 1515 `updated_at` 後）加：

```python
        "reminder_date": item.reminder_date.isoformat() if item.reminder_date else None,
        "reminder_set_by_name": item.reminder_set_by_name or (item.reminder_setter.username if item.reminder_setter else None),
        "reminder_set_at": tw_iso(item.reminder_set_at, fallback=None),
```

- [ ] **Step 4: create_checklist_item 帶 reminder** — 在 `ChecklistItem(...)` 之後、第一個 `db.session.commit()`（line 1613）之前加：

```python
    rd = _parse_date(data.get("reminder_date"))
    if rd:
        _apply_reminder(item, rd)
```

在 line 1613 `db.session.commit()` 之後加 log（item.id 已生成）：

```python
    if rd:
        action, diff = _reminder_action_diff(None, rd, label_prefix=f"項目「{(item.text or '')[:20]}」")
        db.session.add(NoteLog(note_id=note.id, note_title=note.title,
                               user_id=current_user.id, action=action, diff=diff))
        db.session.commit()
```

- [ ] **Step 5: update_checklist_item 帶 reminder** — 在 `note.updated_at = datetime.utcnow()`（line 1676）之前插入：

```python
    if "reminder_date" in data:
        new_rd = _parse_date(data.get("reminder_date"))
        if new_rd != item.reminder_date:
            action, diff = _reminder_action_diff(
                item.reminder_date, new_rd,
                label_prefix=f"項目「{(item.text or '')[:20]}」")
            _apply_reminder(item, new_rd)
            item.updated_at = now
            db.session.add(NoteLog(note_id=note.id, note_title=note.title,
                                   user_id=current_user.id, action=action, diff=diff))
```

> 註：`now` 是 line 1648 既定義的 shared timestamp；reminder 變更也視為 modification，bump `item.updated_at`。

- [ ] **Step 6: 跑測試確認通過**

Run: `pytest tests/test_notes_reminder.py -v`
Expected: PASS（含 note + item 共 4 例）

- [ ] **Step 7: Commit**

```bash
git add notes/routes.py tests/test_notes_reminder.py
git commit -m "feat(notes): item-level reminder_date + NoteLog audit with item text"
```

---

### Task 4: `/notes/api/reminders/today` 全店今日提醒 endpoint

**Files:**
- Modify: `notes/routes.py`（新 endpoint，建議放 `list_notes` 之後 line 604 附近）
- Test: `tests/test_notes_reminder.py`（append）

- [ ] **Step 1: 加失敗測試** — append

```python
def test_reminders_today_scopes_and_excludes(client, app):
    _login(client, app, username="u1", store="A店")
    today = date.today().isoformat()
    past = (date.today() - timedelta(days=1)).isoformat()
    # 到期 note（今天）→ 應出現
    n1 = client.post("/notes/api", json={"note_type": "note", "title": "今天到期", "store": "A店"}).get_json()["id"]
    client.put(f"/notes/api/{n1}", json={"reminder_date": today})
    # resolved note 設過去到期 → 應排除
    n2 = client.post("/notes/api", json={"note_type": "note", "title": "已完成", "store": "A店"}).get_json()["id"]
    client.put(f"/notes/api/{n2}", json={"reminder_date": past})
    client.put(f"/notes/api/{n2}", json={"status": "resolved"})
    r = client.get("/notes/api/reminders/today")
    assert r.status_code == 200
    body = r.get_json()
    ids = [(x["kind"], x["id"]) for x in body["reminders"]]
    assert ("note", n1) in ids
    assert ("note", n2) not in ids
    assert body["last_sig"]  # 有提醒 → 非空簽章


def test_reminders_today_excludes_checked_item(client, app):
    _login(client, app, username="u1", store="A店")
    past = (date.today() - timedelta(days=1)).isoformat()
    nid = client.post("/notes/api", json={"note_type": "checklist", "title": "c", "store": "A店"}).get_json()["id"]
    iid = client.post(f"/notes/api/{nid}/items", json={"text": "做完的事"}).get_json()["item"]["id"]
    client.patch(f"/notes/api/items/{iid}", json={"reminder_date": past})
    client.patch(f"/notes/api/items/{iid}", json={"is_checked": True})
    r = client.get("/notes/api/reminders/today")
    ids = [(x["kind"], x["id"]) for x in r.get_json()["reminders"]]
    assert ("item", iid) not in ids
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_notes_reminder.py -k reminders_today -v`
Expected: FAIL（404，endpoint 不存在）

- [ ] **Step 3: 實作 endpoint** — `notes/routes.py`，`list_notes` 之後加：

```python
@notes_bp.route("/api/reminders/today", methods=["GET"])
@login_required
def reminders_today():
    """全店今日（及之前）到期提醒。
    - note 級：note_type='note'、status != 'resolved'
    - 項目級：未勾選項目、母筆記未軟刪
    回傳 {"reminders": [...], "last_sig": "<max reminder_set_at iso>"}
    """
    today = _today_tw()

    def _scope_notes(q):
        if current_user.is_super_admin():
            store_filter = request.args.get("store", "")
            stores = _visible_stores()
            if store_filter in stores:
                return q.filter(Note.store == store_filter)
            hidden = current_user.get_hidden_stores()
            if hidden:
                return q.filter(db.or_(Note.store.is_(None), Note.store.notin_(hidden)))
            return q
        return q.filter(Note.store == current_user.store)

    out, sigs = [], []

    note_q = _scope_notes(Note.active().filter(
        Note.is_ai_summary.isnot(True),
        Note.note_type == "note",
        Note.status != "resolved",
        Note.reminder_date.isnot(None),
        Note.reminder_date <= today,
    ))
    for n in note_q.all():
        out.append({
            "kind": "note", "id": n.id, "note_id": n.id,
            "title": n.title, "text": "", "store": n.store,
            "reminder_date": n.reminder_date.isoformat(),
            "overdue": n.reminder_date < today,
            "set_by_name": n.reminder_set_by_name or (n.reminder_setter.username if n.reminder_setter else None),
        })
        if n.reminder_set_at:
            sigs.append(n.reminder_set_at.isoformat())

    item_q = _scope_notes(
        ChecklistItem.active().join(Note, ChecklistItem.note_id == Note.id).filter(
            Note.deleted_at.is_(None),
            ChecklistItem.is_checked.is_(False),
            ChecklistItem.reminder_date.isnot(None),
            ChecklistItem.reminder_date <= today,
        )
    )
    for i in item_q.all():
        out.append({
            "kind": "item", "id": i.id, "note_id": i.note_id,
            "title": i.note.title, "text": i.text, "store": i.note.store,
            "reminder_date": i.reminder_date.isoformat(),
            "overdue": i.reminder_date < today,
            "set_by_name": i.reminder_set_by_name or (i.reminder_setter.username if i.reminder_setter else None),
        })
        if i.reminder_set_at:
            sigs.append(i.reminder_set_at.isoformat())

    out.sort(key=lambda x: (not x["overdue"], x["reminder_date"]))
    return jsonify({"reminders": out, "last_sig": max(sigs) if sigs else ""})
```

> 註：`_scope_notes` 對 item query 也可用，因為 join 後 `Note.store` 在同一 query 上可過濾。

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_notes_reminder.py -v`
Expected: PASS（全部 reminder 後端測試）

- [ ] **Step 5: Commit**

```bash
git add notes/routes.py tests/test_notes_reminder.py
git commit -m "feat(notes): store-wide reminders/today endpoint (exclude resolved/checked)"
```

---

### Task 5: 編輯器 UI — note 級 + 項目 modal 到期日 picker + inline 最後修改者

**Files:**
- Modify: `templates/notes/editor.html`（note 級區 line 263-292、item-modal line 310-340、note 存檔 PUT 與 item 存檔 PATCH 的 JS）

> 實作前先讀 `templates/notes/editor.html`，定位：①note 存檔送 `PUT /notes/api/<id>` 的 body 組裝處；②item modal 開啟（填入既有值）與送 `POST/PATCH /notes/api/items` 的 body 組裝處。mynotes 版本可參考 `templates/mynotes/editor.html`（line 78-85 note 級、line 135-137 item modal）。

- [ ] **Step 1: note 級到期日 UI** — 在 note 級內容區（`{% if editor_type != 'checklist' %}` 區塊，line 266 之後）加：

```html
  <div class="ed-reminder-row" style="display:flex;gap:.5rem;align-items:center;margin:.5rem 0;font-size:.9rem;">
    <label style="margin:0;">⏰ 到期日：</label>
    <input type="date" id="reminder-date" style="width:auto;flex:0 0 auto;"
           value="{{ note.reminder_date.isoformat() if note and note.reminder_date else '' }}">
    <button type="button" class="btn btn-secondary" style="font-size:.82rem;padding:.25rem .6rem;"
            onclick="document.getElementById('reminder-date').value='';">清除</button>
    <span id="reminder-meta" style="color:#888;font-size:.82rem;"></span>
  </div>
```

- [ ] **Step 2: note 級 inline 最後修改者 + 存檔帶值** — 在編輯器載入 note 資料的 JS（fetch `GET /notes/api/<id>` 後填表處）加：

```javascript
  if (data.reminder_set_by_name && data.reminder_set_at) {
    var rm = document.getElementById('reminder-meta');
    if (rm) rm.textContent = '最後由 ' + data.reminder_set_by_name + ' 於 ' + data.reminder_set_at + ' 設定';
  }
```

在 note 存檔組 `PUT` body 處加：

```javascript
  var rdEl = document.getElementById('reminder-date');
  if (rdEl) body.reminder_date = rdEl.value || null;
```

- [ ] **Step 3: item modal 到期日 UI** — 在 `#item-modal` 內（line 328 textarea 之後）加：

```html
      <div class="ed-reminder-row" style="display:flex;gap:.5rem;align-items:center;margin:.5rem 0;font-size:.88rem;">
        <label style="margin:0;">⏰ 到期日：</label>
        <input type="date" id="item-modal-reminder" style="width:auto;">
        <button type="button" class="btn btn-secondary" style="font-size:.78rem;padding:.2rem .55rem;"
                onclick="document.getElementById('item-modal-reminder').value='';">清除</button>
        <span id="item-modal-reminder-meta" style="color:#888;font-size:.8rem;"></span>
      </div>
```

- [ ] **Step 4: item modal 開啟填值 + 存檔帶值** — 在開啟 item modal（填既有項目資料）的 JS 加：

```javascript
  var imr = document.getElementById('item-modal-reminder');
  if (imr) imr.value = (item && item.reminder_date) ? item.reminder_date : '';
  var imrm = document.getElementById('item-modal-reminder-meta');
  if (imrm) imrm.textContent = (item && item.reminder_set_by_name && item.reminder_set_at)
      ? ('最後由 ' + item.reminder_set_by_name + ' 於 ' + item.reminder_set_at) : '';
```

在 item 存檔（`POST`/`PATCH /notes/api/items`）body 組裝處加：

```javascript
  var imrEl = document.getElementById('item-modal-reminder');
  if (imrEl) body.reminder_date = imrEl.value || null;
```

- [ ] **Step 5: 手動驗證**（本機，feedback_local_test_before_push）

Run: `bash scripts/run_local.sh`，瀏覽器設一筆到期日 → reload → 確認值還在、顯示「最後由 X 設定」。

- [ ] **Step 6: Commit**

```bash
git add templates/notes/editor.html
git commit -m "feat(notes): editor note + item reminder date picker with last-set meta"
```

---

### Task 6: 今日提醒折疊面板 + auto-expand + CSS + SW bump

**Files:**
- Modify: `templates/notes/index.html`（line 107 公告 include 之後）
- Modify: `static/js/collapsible_sections.js`（加 `initRemindersSection`）
- Modify: `static/css/collapsible_sections.css`（提醒卡片樣式）
- Modify: `static/sw.js`（bump `CACHE_NAME`）
- Test: `tests/test_notes_collapsible_sections.py`（append 一個 DOM/字串存在性斷言）

- [ ] **Step 1: index.html 加面板** — 在 `{% include 'announcements/_card_list.html' %}`（line 107）之後、`<section class="collapsible" data-section="ai-summary">`（line 111）之前加：

```html
<section class="collapsible" data-section="reminders" id="reminders-section">
  <div class="collapsible-bar" data-role="section-bar">
    <span class="collapsible-title">⏰ 今日提醒 <span id="reminders-count">(0)</span></span>
    <span class="collapsible-chevron">▾</span>
  </div>
  <div class="collapsible-body">
    <div id="reminders-box"><div style="color:#888;font-size:.85rem;padding:.5rem;">載入中…</div></div>
  </div>
</section>
```

> 註：`.collapsible-title`/`.collapsible-chevron` class 比照 `announcements/_card_list.html` 與 ai-summary section（line 111-116）既有結構；若實際 class 名不同，對齊之。

- [ ] **Step 2: collapsible_sections.js 加 reminders init** — 在 `init()`（line 113-118）中 `initFilterSection();` 之後加 `initRemindersSection();`，並在檔案內（`init` 之前）加：

```javascript
  var REM_KEY = 'notes_reminders_last_seen';

  function readRemSeen() {
    try {
      var p = JSON.parse(localStorage.getItem(REM_KEY) || 'null');
      if (!p || typeof p !== 'object') return null;
      return { date: String(p.date || ''), last_sig: String(p.last_sig || '') };
    } catch (e) { return null; }
  }

  function renderReminders(box, list) {
    if (!list.length) {
      box.innerHTML = '<div style="color:#888;font-size:.85rem;padding:.5rem;">目前沒有到期提醒</div>';
      return;
    }
    var html = '<ul class="rem-list">';
    list.forEach(function(r) {
      var chip = r.overdue ? '<span class="rem-chip overdue">逾期</span>'
                           : '<span class="rem-chip">今日</span>';
      var label = r.kind === 'item' ? (r.title + ' — ' + r.text) : r.title;
      html += '<li>' + chip + '<a href="/notes/' + r.note_id + '">' + label + '</a>'
            + '<span class="rem-date">' + r.reminder_date + '</span></li>';
    });
    html += '</ul>';
    box.innerHTML = html;
  }

  function initRemindersSection() {
    var section = document.querySelector('.collapsible[data-section="reminders"]');
    if (!section) return;
    bindSectionBar(section);
    var box = document.getElementById('reminders-box');
    var store = (window.CURRENT_STORE || '');
    var url = '/notes/api/reminders/today' + (store ? ('?store=' + encodeURIComponent(store)) : '');
    fetch(url).then(function(r) { return r.json(); }).then(function(data) {
      var list = (data && data.reminders) || [];
      var lastSig = (data && data.last_sig) || '';
      var cnt = document.getElementById('reminders-count');
      if (cnt) cnt.textContent = '(' + list.length + ')';
      renderReminders(box, list);
      var stored = readRemSeen();
      var expand;
      if (!list.length) {
        expand = false;
      } else if (!stored || stored.date < todayStr() || stored.last_sig < lastSig) {
        expand = true;  // 每天首開 OR 有人調整（last_sig 變新）
      } else {
        expand = false;
      }
      setSectionExpanded(section, expand);
      if (expand) {
        try { localStorage.setItem(REM_KEY, JSON.stringify({date: todayStr(), last_sig: lastSig})); } catch (e) {}
      }
    }).catch(function() {
      if (box) box.innerHTML = '<div style="color:#c00;font-size:.85rem;padding:.5rem;">提醒載入失敗</div>';
      setSectionExpanded(section, false);
    });
  }
```

- [ ] **Step 3: CSS** — `static/css/collapsible_sections.css` 末尾加：

```css
.rem-list { list-style: none; padding: 0; margin: 0; }
.rem-list li { display: flex; align-items: center; gap: .5rem; padding: .35rem .5rem; border-bottom: 1px dashed #ffe082; }
.rem-list li:last-child { border-bottom: none; }
.rem-list a { flex: 1; color: #333; text-decoration: none; font-size: .9rem; }
.rem-list .rem-date { color: #e65100; font-size: .8rem; }
.rem-chip { font-size: .7rem; padding: .1rem .45rem; border-radius: 8px; background: #fff8e1; color: #e65100; border: 1px solid #ffd54f; }
.rem-chip.overdue { background: #ef5350; color: #fff; border-color: #ef5350; }
```

- [ ] **Step 4: SW bump** — `static/sw.js`，找 `CACHE_NAME`（目前 v39，踩雷 #5），改 `v39`→`v40`。

Run: `grep -n "CACHE_NAME" static/sw.js`（確認改到）

- [ ] **Step 5: collapsible test append** — `tests/test_notes_collapsible_sections.py` 加：

```python
def test_reminders_section_present():
    from pathlib import Path
    idx = Path(__file__).parent.parent / 'templates' / 'notes' / 'index.html'
    js = Path(__file__).parent.parent / 'static' / 'js' / 'collapsible_sections.js'
    assert 'data-section="reminders"' in idx.read_text(encoding='utf-8')
    assert 'initRemindersSection' in js.read_text(encoding='utf-8')
```

- [ ] **Step 6: 跑測試 + 手動驗證**

Run: `pytest tests/test_notes_collapsible_sections.py -v`（PASS）
Run: `bash scripts/run_local.sh` → 首次進入今日提醒展開、reload 縮起、改一筆到期日後 reload 又彈開。

- [ ] **Step 7: Commit**

```bash
git add templates/notes/index.html static/js/collapsible_sections.js static/css/collapsible_sections.css static/sw.js tests/test_notes_collapsible_sections.py
git commit -m "feat(notes): store reminders panel below announcements with daily/on-change auto-expand"
```

---

### Task 7: 全套驗證 + 收尾

**Files:** 無新檔（驗證 + 可能補 wiring 斷言）

- [ ] **Step 1: 跑全套測試**

Run: `pytest tests/`
Expected: 全綠（原 614 passed + 新增 reminder/schema/collapsible 測試；無 regression）。若 `test_notes_editor_*_wiring.py` 因 template 改動失敗，依踩雷 #12 同步更新斷言。

- [ ] **Step 2: 本機完整流程驗證**（feedback_local_test_before_push）

```
bash scripts/run_local.sh
```
切多帳號（/dev）驗：① note 設到期日 → 今日提醒面板出現、稽核 inline 顯示；② checklist 項目設到期日 → 出現；③ 勾選項目 / note 設 resolved → 從提醒消失；④ 另一帳號改到期日 → 對方 reload 面板彈開；⑤ 跨店看不到別店提醒；⑥ 時間顯示為台灣時間。

- [ ] **Step 3: 等 user 確認後再決定 push**（feedback_push_policy / feedback_branch_workflow）

> 不自動 push。user 看過本機結果說 OK，才 merge main + push。注意 build 影響僅 app 層（不碰 base image / requirements）。

---

## Self-Review

**Spec coverage：**
- §3 資料模型（4 欄×2 表 + 對齊 3 檔）→ Task 1 ✓
- §4 稽核 NoteLog（set/change/clear + 項目文字）→ Task 2/3 ✓
- §5 今日提醒 endpoint（store scope、排除 resolved/checked、last_sig）→ Task 4 ✓
- §5 面板 + auto-expand（公告下方、每日首開/調整彈開）→ Task 6 ✓
- §6 編輯器 note 級 + 項目級 picker + inline 最後修改者 → Task 5 ✓
- §7 權限（沿用既有 edit gate）→ create/update/patch 都走既有 `_get_note_for_edit` / first_or_404 store gate ✓
- §8 測試 → Task 2/3/4/6 + Task 7 全套 ✓
- §10 SW bump → Task 6 Step 4 ✓
- mynotes 不動 → 本計畫未觸碰 `mynotes/` 與 `templates/mynotes/` ✓

**Placeholder scan：** 無 TBD/TODO；每個 code step 附實際程式碼。前端 anchor 處要求實作者先讀檔定位（editor.html 1984 行），但插入內容皆為完整片段。

**Type consistency：** helper 命名一致（`_today_tw` / `_parse_date` / `_fmt_reminder` / `_reminder_action_diff` / `_apply_reminder`）；action 字串 `reminder_set`/`reminder_change`/`reminder_clear` 跨 Task 2/3/4 一致；欄位名 `reminder_date`/`reminder_set_by`/`reminder_set_by_name`/`reminder_set_at` 跨 models/app.py/migration/serializer 一致；API 回傳 `{"reminders": [...], "last_sig": ...}` 與前端 `data.reminders`/`data.last_sig` 一致。

**已知待實作者確認的小事：** `tests/test_notes.py` 的 client/app fixture 與登入 helper 實際名稱（Task 2 Step 1 的 `_login` 需對齊）；`collapsible-title`/`collapsible-chevron` class 是否與既有 section 完全同名。
