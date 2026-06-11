# 標籤篩選獨立化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 notes 列表的標籤篩選變成跟關鍵字同級的獨立互斥模式——選標籤只看「該店 + 該標籤」全部歷史，忽略時間/種類/班別/狀態。

**Architecture:** 後端 `index()` 把 `tag_id` 從 combined 分支抽成獨立 `elif`（三模式：keyword > tag > combined）；模板依模式顯示/隱藏對應控制項；標籤 chip 點擊導向乾淨 URL（只留 store+tag）。

**Tech Stack:** Flask + SQLAlchemy + Jinja2 + Vanilla JS + Service Worker；測試 pytest（一律 `pytest tests/`，見踩雷 #11）。

**分支：** `feat/tag-filter-independent`（已開）

---

## 檔案結構

| 檔案 | 動作 | 責任 |
|---|---|---|
| `app_unified/notes/routes.py` | 改 `index()` 約 381–410 行 | 三互斥模式的後端篩選邏輯 |
| `app_unified/templates/notes/index.html` | 改 180–250 行 | 依模式顯示/隱藏 keyword 框 / 標籤列 / 6 排 tab |
| `app_unified/static/js/tag_system.js` | 改 `makeChip` 約 299–306 行 | 標籤點擊導向乾淨 URL |
| `app_unified/static/sw.js` | 改 line 8 CACHE_NAME | bump 版本讓 user 拿到新 JS |
| `app_unified/tests/test_tags_filter.py` | 加測試 | 鎖定 tag 模式忽略時間 + keyword 優先序 |
| `app_unified/tests/test_tag_filter_ui.py` | 新增 | 鎖定三模式 UI 顯示/隱藏 |

---

## Task 1: 後端 — 標籤獨立互斥模式（忽略時間/種類/班別/狀態）

**Files:**
- Modify: `app_unified/notes/routes.py:381-410`
- Test: `app_unified/tests/test_tags_filter.py`

- [ ] **Step 1: 寫失敗測試**

加到 `app_unified/tests/test_tags_filter.py` 檔尾（沿用該檔既有 `_mk_user` / `_login` / `_uniq` helper）：

```python
def test_tag_filter_ignores_time_window(client, db):
    """標籤模式不吃時間：10 天前 updated 的 note 選標籤時仍應出現。"""
    from datetime import datetime, timedelta
    from models import Tag, Note, NoteTag
    u = _mk_user(db)
    old_title = _uniq('OLD_TAGGED')
    n = Note(user_id=u.id, store='B店', title=old_title,
             content='', note_type='regular')
    t = Tag(store='B店', name=_uniq('久遠'), color='#E74C3C')
    db.session.add_all([n, t])
    db.session.commit()
    db.session.add(NoteTag(note_id=n.id, tag_id=t.id))
    # updated_at 無 onupdate，手動塞 10 天前不會被 commit 覆蓋
    n.updated_at = datetime.utcnow() - timedelta(days=10)
    db.session.commit()
    _login(client, u)
    # 故意不帶 range=all：證明 tag 模式不再套 today 預設時間窗
    r = client.get(f'/notes/?tag={t.id}')
    assert old_title in r.get_data(as_text=True)


def test_tag_filter_ignores_category(client, db):
    """標籤模式不吃種類：帶 category 也不該縮小標籤結果。"""
    from models import Tag, Note, NoteTag
    u = _mk_user(db)
    title = _uniq('TAG_CAT')
    n = Note(user_id=u.id, store='B店', title=title, content='',
             note_type='regular', category='employee')
    t = Tag(store='B店', name=_uniq('CC'), color='#3498DB')
    db.session.add_all([n, t])
    db.session.commit()
    db.session.add(NoteTag(note_id=n.id, tag_id=t.id))
    db.session.commit()
    _login(client, u)
    # 帶一個不符的 category，tag 模式應忽略它、note 照樣出現
    r = client.get(f'/notes/?tag={t.id}&category=machine')
    assert title in r.get_data(as_text=True)


def test_keyword_takes_precedence_over_tag(client, db):
    """q + tag 同時有值 → keyword 模式，tag 被忽略。"""
    from models import Tag, Note, NoteTag
    u = _mk_user(db)
    kw = _uniq('KWHIT')
    n = Note(user_id=u.id, store='B店', title=kw, content='',
             note_type='regular')
    other = Note(user_id=u.id, store='B店', title=_uniq('TAGONLY'),
                 content='', note_type='regular')
    t = Tag(store='B店', name=_uniq('PX'), color='#E74C3C')
    db.session.add_all([n, other, t])
    db.session.commit()
    # tag 掛在 other（不含關鍵字的那筆）
    db.session.add(NoteTag(note_id=other.id, tag_id=t.id))
    db.session.commit()
    _login(client, u)
    r = client.get(f'/notes/?q={kw}&tag={t.id}')
    html = r.get_data(as_text=True)
    assert kw in html               # 關鍵字命中的出現
    assert other.title not in html  # 只掛 tag、無關鍵字的不出現（tag 被忽略）
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd ~/projects/webapp/app_unified && python -m pytest tests/test_tags_filter.py::test_tag_filter_ignores_time_window tests/test_tags_filter.py::test_tag_filter_ignores_category -v`
Expected: FAIL — `test_tag_filter_ignores_time_window`（10 天前 note 被 today 窗濾掉）與 `test_tag_filter_ignores_category`（category=machine 把 note 濾掉）皆 assert 失敗。
（`test_keyword_takes_precedence_over_tag` 預期此時已 PASS，因 keyword 分支本來就在前。）

- [ ] **Step 3: 改後端 — tag 抽成獨立 elif 分支**

把 `app_unified/notes/routes.py` 的這段（約 381–410 行）：

```python
    # keyword 互斥：有 keyword 時忽略其他篩選
    keyword_mode = False
    active_filter = None
    if keyword:
        active_filter = 'keyword'
        keyword_mode = True
        # AI 摘要的內容是其他 note 的彙整，搜尋會跟原 note 重複出現 → 排除掉
        query = query.filter(Note.is_ai_summary.isnot(True))
    else:
        # AND 組合：category + status + range + tag
        active_filter = 'combined'
        if category_filter in CATEGORY_CHOICES:
            query = query.filter_by(category=category_filter)
        # shift 篩選：'A'/'B'/'N' 才過濾；'none'/未帶/不合法 = 不過濾(顯示全部)
        if shift_filter in SHIFT_CHOICES:
            query = query.filter_by(shift=shift_filter)
        if status_filter in STATUS_CHOICES:
            query = query.filter_by(status=status_filter)
        if tag_id:
            from models import NoteTag, ChecklistItem as _CI, ChecklistItemTag as _CIT
            # 同時 match note-level tag 跟 checklist item-level tag (UNION via subquery)
            note_via_note = db.session.query(NoteTag.note_id).filter(NoteTag.tag_id == tag_id)
            note_via_item = db.session.query(_CI.note_id).join(
                _CIT, _CIT.item_id == _CI.id
            ).filter(_CIT.tag_id == tag_id)
            matching_note_ids = note_via_note.union(note_via_item).subquery()
            query = query.filter(Note.id.in_(db.session.query(matching_note_ids)))
        if not range_param:
            range_param = 'today'
        query = _date_filter(query, range_param)
```

換成（keyword > tag > combined 三互斥）：

```python
    # 三互斥模式：keyword > tag > combined
    keyword_mode = False
    active_filter = None
    if keyword:
        active_filter = 'keyword'
        keyword_mode = True
        # AI 摘要的內容是其他 note 的彙整，搜尋會跟原 note 重複出現 → 排除掉
        query = query.filter(Note.is_ai_summary.isnot(True))
    elif tag_id:
        # 標籤獨立互斥：只看 store + 該標籤的全部歷史，忽略時間/種類/班別/狀態
        active_filter = 'tag'
        from models import NoteTag, ChecklistItem as _CI, ChecklistItemTag as _CIT
        # 同時 match note-level tag 跟 checklist item-level tag (UNION via subquery)
        note_via_note = db.session.query(NoteTag.note_id).filter(NoteTag.tag_id == tag_id)
        note_via_item = db.session.query(_CI.note_id).join(
            _CIT, _CIT.item_id == _CI.id
        ).filter(_CIT.tag_id == tag_id)
        matching_note_ids = note_via_note.union(note_via_item).subquery()
        query = query.filter(Note.id.in_(db.session.query(matching_note_ids)))
    else:
        # AND 組合：category + status + range
        active_filter = 'combined'
        if category_filter in CATEGORY_CHOICES:
            query = query.filter_by(category=category_filter)
        # shift 篩選：'A'/'B'/'N' 才過濾；'none'/未帶/不合法 = 不過濾(顯示全部)
        if shift_filter in SHIFT_CHOICES:
            query = query.filter_by(shift=shift_filter)
        if status_filter in STATUS_CHOICES:
            query = query.filter_by(status=status_filter)
        if not range_param:
            range_param = 'today'
        query = _date_filter(query, range_param)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd ~/projects/webapp/app_unified && python -m pytest tests/test_tags_filter.py -v`
Expected: PASS（含新 3 條 + 既有 3 條）。

- [ ] **Step 5: Commit**

```bash
cd ~/projects/webapp
git add app_unified/notes/routes.py app_unified/tests/test_tags_filter.py
git commit -m "feat(notes): tag filter as independent mutually-exclusive mode

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 模板 — 三模式顯示/隱藏

**Files:**
- Modify: `app_unified/templates/notes/index.html:180-250`
- Test: `app_unified/tests/test_tag_filter_ui.py`（新增）

- [ ] **Step 1: 寫失敗測試**

新檔 `app_unified/tests/test_tag_filter_ui.py`：

```python
"""三互斥模式的進階篩選 UI 顯示/隱藏。"""
import json as _json
import uuid
import pytest


def _uniq(prefix='T'):
    return f'{prefix}_{uuid.uuid4().hex[:6]}'


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_user(db, store='B店'):
    from models import User
    u = User(username=_uniq('u_ui'), role='user', store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def _mk_tag(db, store='B店'):
    from models import Tag
    t = Tag(store=store, name=_uniq('UI'), color='#E74C3C')
    db.session.add(t)
    db.session.commit()
    return t


def test_default_mode_shows_everything(client, db):
    u = _mk_user(db)
    _login(client, u)
    html = client.get('/notes/').get_data(as_text=True)
    assert '🔍 關鍵字：' in html          # 關鍵字框
    assert 'data-role="tag-row"' in html   # 標籤列
    assert '種類：' in html                # 6 排 tab 之一


def test_keyword_mode_hides_tag_row_and_tabs(client, db):
    u = _mk_user(db)
    _login(client, u)
    html = client.get('/notes/?q=anything').get_data(as_text=True)
    assert '🔍 關鍵字：' in html           # 關鍵字框留著
    assert 'data-role="tag-row"' not in html  # 標籤列隱藏
    assert '種類：' not in html            # tab 隱藏


def test_tag_mode_hides_keyword_and_tabs(client, db):
    u = _mk_user(db)
    t = _mk_tag(db)
    _login(client, u)
    html = client.get(f'/notes/?tag={t.id}').get_data(as_text=True)
    assert '🔍 關鍵字：' not in html       # 關鍵字框隱藏
    assert 'data-role="tag-row"' in html   # 標籤列留著
    assert '種類：' not in html            # tab 隱藏
    assert '清除過濾器' in html            # 清除過濾器仍在
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd ~/projects/webapp/app_unified && python -m pytest tests/test_tag_filter_ui.py -v`
Expected: `test_keyword_mode_hides_tag_row_and_tabs` FAIL（標籤列目前在 keyword 模式仍出現）、`test_tag_mode_hides_keyword_and_tabs` FAIL（關鍵字框與 tab 目前在 tag 模式仍出現）。

- [ ] **Step 3: 改模板**

`app_unified/templates/notes/index.html`：

(a) keyword form（約 180–188 行）外面包條件——把整個 `<form ...>...</form>` 包進 `{% if not current_tag %}`：

原：
```jinja
    <form method="get" action="{{ url_for('notes.index') }}" style="display:flex;gap:.4rem;margin-bottom:.75rem;align-items:center;flex-wrap:wrap;">
```
改成在 `<form` 前插一行、`</form>` 後插一行：
```jinja
    {% if not current_tag %}
    <form method="get" action="{{ url_for('notes.index') }}" style="display:flex;gap:.4rem;margin-bottom:.75rem;align-items:center;flex-wrap:wrap;">
```
```jinja
    </form>
    {% endif %}
```

(b) 標籤列（約 190–191 行）包進 `{% if not current_keyword %}`：

原：
```jinja
    {# 標籤 row 預留位、Task C6 fill via JS #}
    <div class="tag-filter-row" data-role="tag-row"></div>
```
改成：
```jinja
    {% if not current_keyword %}
    {# 標籤 row 預留位、Task C6 fill via JS #}
    <div class="tag-filter-row" data-role="tag-row"></div>
    {% endif %}
```

(c) 6 排 tab 區塊條件（約 193 行）：

原：
```jinja
    {% if not current_keyword %}
    {% set cat_tab_labels = [('','全部'),('employee','員工'),('customer','客人'),('machine','機台'),('misc','雜項')] %}
```
改成：
```jinja
    {% if not current_keyword and not current_tag %}
    {% set cat_tab_labels = [('','全部'),('employee','員工'),('customer','客人'),('machine','機台'),('misc','雜項')] %}
```

(d) 清掉 5 個 tab url_for 裡的 `, tag=current_tag`（種類 198 / 班別 211 / 時間 220 / 狀態 229 / 排列 244 行）。這些 tab 只在 `not current_tag` 時出現，`current_tag` 必為空，純清理。逐一把 `tag=current_tag` 從 `url_for(...)` 參數移除，例如種類那行：

原：
```jinja
      <a href="{{ url_for('notes.index', store=current_store, category=val, tag=current_tag) }}"
```
改成：
```jinja
      <a href="{{ url_for('notes.index', store=current_store, category=val) }}"
```
其餘 4 行比照辦理（移除各自的 `, tag=current_tag`）。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd ~/projects/webapp/app_unified && python -m pytest tests/test_tag_filter_ui.py tests/test_filter_panel_collapsible.py -v`
Expected: PASS（含新 UI 測試 + 既有 collapsible 測試不退步）。

- [ ] **Step 5: Commit**

```bash
cd ~/projects/webapp
git add app_unified/templates/notes/index.html app_unified/tests/test_tag_filter_ui.py
git commit -m "feat(notes): hide non-active filters in keyword/tag modes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 前端 JS — 標籤點擊導向乾淨 URL + SW bump

**Files:**
- Modify: `app_unified/static/js/tag_system.js:299-306`
- Modify: `app_unified/static/sw.js:8`

- [ ] **Step 1: 改 makeChip 導向乾淨 URL**

`app_unified/static/js/tag_system.js`，`makeChip` 裡的 click handler（約 299–306 行）：

原：
```javascript
      chip.addEventListener('click', function() {
        var url = new URL(window.location.href);
        if (t.id === currentTagId) url.searchParams.delete('tag');
        else url.searchParams.set('tag', t.id);
        window.location.href = url.toString();
      });
```
改成（只保留 store + tag，丟掉 q/category/shift/range/status/sort）：
```javascript
      chip.addEventListener('click', function() {
        // 標籤是獨立互斥模式：導向乾淨 URL，只保留 store（+ tag）
        var u = new URL(window.location.origin + window.location.pathname);
        var store = new URLSearchParams(window.location.search).get('store');
        if (store) u.searchParams.set('store', store);
        if (t.id !== currentTagId) u.searchParams.set('tag', t.id);
        window.location.href = u.toString();
      });
```

- [ ] **Step 2: bump SW CACHE_NAME**

`app_unified/static/sw.js` line 8，把 `v38` 改成 `v39`：

原：
```javascript
const CACHE_NAME = 'note-weather-v38';
```
改成：
```javascript
const CACHE_NAME = 'note-weather-v39';
```

- [ ] **Step 3: Commit**

```bash
cd ~/projects/webapp
git add app_unified/static/js/tag_system.js app_unified/static/sw.js
git commit -m "feat(notes): tag chip navigates to clean URL; bump SW cache v39

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 全套驗證 + 本機實測

**Files:** 無（驗證用）

- [ ] **Step 1: 跑全套測試（與 CI 一致）**

Run: `cd ~/projects/webapp/app_unified && python -m pytest tests/`
Expected: 全 PASS（既有 + 新增），無退步。

- [ ] **Step 2: 本機實測（見 [[feedback_local_test_before_push]]）**

Run: `cd ~/projects/webapp && bash scripts/run_local.sh`
然後 `/dev` 切換帳號，手動點過：
- 預設模式：關鍵字框 + 標籤列 + 全部 tab 都在。
- 點關鍵字搜尋：只剩關鍵字框（+ 取消），標籤列與 tab 消失。
- 點一個標籤：只剩標籤列（+ 清除過濾器），關鍵字框與 tab 消失；舊的同標籤 note（非今天）也看得到。
- 再點同標籤 / 清除過濾器：回預設模式。
- 標籤模式下 URL 應為乾淨的 `?store=...&tag=...`（無殘留 category/range 等）。

- [ ] **Step 3: user 確認 OK**

等 user 看過本機沒問題，才談 push（見 [[feedback_push_policy]]、尖峰時段見 [[feedback_push_peak_hours]]）。

---

## Self-Review

- **Spec coverage：** 三模式語意（Task 1 後端 + Task 2 模板）✓；tag chip 乾淨 URL（Task 3）✓；SW bump（Task 3）✓；測試含「忽略時間」「keyword 優先」「UI 顯示/隱藏」✓；排序行為（tag 模式同 keyword、UI 不出 sort tab）由 Task 2 隱藏 tab + 後端不變達成 ✓。
- **Placeholder scan：** 無 TBD/TODO，所有 code step 皆有完整程式碼。
- **Type/名稱一致：** `active_filter` 值 `'keyword'/'tag'/'combined'`、`current_tag`/`current_keyword`、`data-role="tag-row"`、CACHE_NAME `v39` 前後一致。
- **不在範圍：** note 卡片 tag chip 為純 `<span>`（無 href）不動；卡片→編輯器 filter context 不變。
