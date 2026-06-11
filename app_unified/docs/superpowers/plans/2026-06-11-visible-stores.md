# 可視店家 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓每個 super_admin 自行設定「可視店家」，設定後筆記/公告/AI摘要/後台清單與各店家 dropdown 全部只顯示可視店資料。

**Architecture:** `User.hidden_stores`（JSON Text，存隱藏集；空=全可視、新店自動可視）+ `User.visible_store_names()` 集中 helper。把既有「全部店家清單」來源（notes `_get_stores()`、admin dashboard `stores`、announcement `_visibility_query()`）改成經 helper 過濾，多處一次更新。店家管理表加「可視」checkbox 欄（super_admin 可改、admin disabled），toggle 走新 endpoint 改 `current_user.hidden_stores`。

**Tech Stack:** Flask + SQLAlchemy + Jinja2 + Vanilla JS；schema 走 [[feedback_inline_schema_repair]] 三檔對齊；測試 `pytest tests/`。

**分支：** `feat/visible-stores`（已開，從 main）

---

## 檔案結構

| 檔案 | 動作 | 責任 |
|---|---|---|
| `app_unified/models.py` | 改 `User`（49 行後） | `hidden_stores` 欄 + `get/set_hidden_stores` + `visible_store_names` |
| `app_unified/app.py` | 加 inline ALTER（273 行後） | prod schema repair |
| `app_unified/migrations/versions/016_user_hidden_stores.py` | 新增 | dev alembic migration |
| `app_unified/admin/routes.py` | 改 `list_stores`、`dashboard`；加 `toggle_store_visible` | API visible 旗標 + toggle + 後台 user 清單過濾 |
| `app_unified/templates/admin/dashboard.html` | 改 store 表頭 + `loadStores` JS + 加 `toggleStoreVisible` | 可視 checkbox 欄 |
| `app_unified/notes/routes.py` | 改 `_get_stores`、`index()` super_admin 分支 | 筆記列表/切換器/dropdown 吃可視店 |
| `app_unified/notes/announcement_routes.py` | 改 `_visibility_query` | 公告吃可視店（保留全站） |
| `app_unified/tests/test_visible_stores.py` | 新增 | 全功能測試 |

每個 task 從 `/home/hirain0126/projects/webapp` commit、從 `app_unified` 跑 `python -m pytest`（imports 失敗先 `source /home/hirain0126/projects/webapp/venv/bin/activate`）。一律 `pytest tests/`（踩雷 #11）。**不需 SW bump**（無改動 static/*.js）。

---

## Task 1: 資料模型 + schema 對齊

**Files:**
- Modify: `app_unified/models.py`（User class，約 49 行 features 後）
- Modify: `app_unified/app.py`（約 273 行 features ALTER 後）
- Create: `app_unified/migrations/versions/016_user_hidden_stores.py`
- Test: `app_unified/tests/test_visible_stores.py`（新檔）

- [ ] **Step 1: 寫失敗測試**

新檔 `app_unified/tests/test_visible_stores.py`：

```python
"""可視店家（super_admin per-user 店家過濾）測試。"""
import json as _json
import uuid
import pytest


def _uniq(p='x'):
    return f'{p}_{uuid.uuid4().hex[:6]}'


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk(db, role='super_admin', store=None):
    from models import User
    u = User(username=_uniq('u'), role=role, store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    import time
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())


def test_hidden_stores_roundtrip(db):
    u = _mk(db)
    assert u.get_hidden_stores() == []
    u.set_hidden_stores(['B', 'C'])
    db.session.commit()
    assert sorted(u.get_hidden_stores()) == ['B', 'C']


def test_visible_super_admin_subtracts_hidden(db):
    u = _mk(db, role='super_admin')
    u.set_hidden_stores(['C'])
    db.session.commit()
    # 保序：傳入順序維持，扣掉隱藏的 C
    assert u.visible_store_names(['B', 'C', 'E']) == ['B', 'E']


def test_visible_new_store_auto_visible(db):
    """新店不在隱藏集 → 自動可視。"""
    u = _mk(db, role='super_admin')
    u.set_hidden_stores(['C'])
    db.session.commit()
    assert 'NEW' in u.visible_store_names(['B', 'C', 'NEW'])


def test_visible_admin_only_own_store(db):
    u = _mk(db, role='admin', store='B')
    u.set_hidden_stores(['B', 'E'])  # admin 的 hidden 應被忽略
    db.session.commit()
    assert u.visible_store_names(['B', 'C', 'E']) == ['B']


def test_visible_user_no_store_empty(db):
    u = _mk(db, role='user', store=None)
    assert u.visible_store_names(['B', 'C']) == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_visible_stores.py -v`
Expected: FAIL — `AttributeError: 'User' object has no attribute 'get_hidden_stores'`（欄位/helper 尚未存在）。

- [ ] **Step 3: models.py 加欄位 + helper**

在 `app_unified/models.py` 的 `User` class，`features` 欄位定義那行（約 49 行）之後加一欄：

原：
```python
    features = db.Column(db.Text, nullable=False, default='["notes", "vetting"]')
```
之後插入：
```python
    # 每個 super_admin 個人隱藏的店名（JSON 陣列字串）。NULL/空=全可視；
    # 新增的店家不在此集合 → 自動可視。admin/user 一律忽略（只看自己店）。
    hidden_stores = db.Column(db.Text, nullable=True)
```

接著在 `set_features` method 之後（約 64 行）加三個 method：
```python
    def get_hidden_stores(self) -> list:
        import json
        try:
            val = json.loads(self.hidden_stores or "[]")
            return val if isinstance(val, list) else []
        except Exception:
            return []

    def set_hidden_stores(self, names: list):
        import json
        self.hidden_stores = json.dumps(list(names))

    def visible_store_names(self, all_store_names: list) -> list:
        """此 user 可視的店名（保持傳入順序）。
        super_admin: all_store_names 扣掉 get_hidden_stores()；
        admin/user: [self.store] if self.store else []。"""
        if self.is_super_admin():
            hidden = set(self.get_hidden_stores())
            return [n for n in all_store_names if n not in hidden]
        return [self.store] if self.store else []
```

- [ ] **Step 4: app.py 加 inline ALTER**

在 `app_unified/app.py` 的 features ALTER try/except 區塊（約 273 行）之後，加一段同樣 pattern：

原（約 270–276 行）:
```python
                # users.features：模組開關 JSON 陣列
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE users ADD COLUMN IF NOT EXISTS features TEXT NOT NULL DEFAULT '[\"notes\"]'"
                    ))
                except Exception:
                    pass
```
在其後插入：
```python
                # users.hidden_stores：每個 super_admin 個人隱藏店家 JSON（NULL=全可視）
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE users ADD COLUMN IF NOT EXISTS hidden_stores TEXT"
                    ))
                except Exception:
                    pass
```

- [ ] **Step 5: 建 migration 016**

新檔 `app_unified/migrations/versions/016_user_hidden_stores.py`：
```python
"""User.hidden_stores: per-super_admin hidden store names (JSON text)

Revision ID: 016
Revises: 015
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa


revision = '016'
down_revision = '015'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('hidden_stores', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('users', 'hidden_stores')
```

- [ ] **Step 6: 本機 dev SQLite 手動 ALTER**（[[feedback_dev_sqlite_alter]]，否則本機測試抓不到欄位）

Run:
```bash
cd /home/hirain0126/projects/webapp/app_unified && source .env.local && \
/home/hirain0126/projects/webapp/venv/bin/python -c "from app import create_app; from extensions import db; app=create_app(); ctx=app.app_context(); ctx.push(); \
import sqlalchemy as sa; \
[db.session.execute(sa.text('ALTER TABLE users ADD COLUMN hidden_stores TEXT')) if 'hidden_stores' not in [c['name'] for c in sa.inspect(db.engine).get_columns('users')] else None]; \
db.session.commit(); print('dev users.hidden_stores OK')"
```
Expected: `dev users.hidden_stores OK`（或已存在則不報錯）。

- [ ] **Step 7: 跑測試確認通過**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_visible_stores.py -v`
Expected: 5 passed。

- [ ] **Step 8: 三檔對齊 grep 驗證**（[[feedback_inline_schema_repair]]）

Run: `cd /home/hirain0126/projects/webapp/app_unified && grep -rn "hidden_stores" models.py app.py migrations/versions/016_user_hidden_stores.py | grep -c hidden_stores`
Expected: ≥ 5（models 欄位+helper、app.py ALTER、migration 各有）。確認三檔都有。

- [ ] **Step 9: Commit**
```bash
cd /home/hirain0126/projects/webapp
git add app_unified/models.py app_unified/app.py app_unified/migrations/versions/016_user_hidden_stores.py app_unified/tests/test_visible_stores.py
git commit -m "feat(admin): User.hidden_stores column + visible_store_names helper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: toggle endpoint + GET /admin/stores 回 visible 旗標

**Files:**
- Modify: `app_unified/admin/routes.py`（`list_stores` ~765 行；新 route 接在 `toggle_store_login` ~804 行後）
- Test: `app_unified/tests/test_visible_stores.py`

- [ ] **Step 1: 寫失敗測試**（append 到 `tests/test_visible_stores.py`）

```python
def test_list_stores_includes_visible_flag(client, db):
    from models import Store
    db.session.add_all([Store(name='B'), Store(name='C')])
    db.session.commit()
    su = _mk(db, role='super_admin')
    su.set_hidden_stores(['C'])
    db.session.commit()
    _login(client, su)
    rows = client.get('/admin/stores').get_json()
    by = {r['name']: r for r in rows}
    assert by['B']['visible'] is True
    assert by['C']['visible'] is False   # 全列出全部店家，但 C 標記不可視


def test_toggle_visible_super_admin(client, db):
    from models import Store
    db.session.add_all([Store(name='B'), Store(name='C')])
    db.session.commit()
    su = _mk(db, role='super_admin')
    _login(client, su)
    r = client.post('/admin/stores/C/toggle-visible')
    assert r.get_json()['visible'] is False     # C 變隱藏
    su2 = client.get('/admin/stores').get_json()
    assert {x['name']: x['visible'] for x in su2}['C'] is False
    r2 = client.post('/admin/stores/C/toggle-visible')
    assert r2.get_json()['visible'] is True      # 再切回可視


def test_toggle_visible_admin_forbidden(client, db):
    from models import Store
    db.session.add(Store(name='B'))
    db.session.commit()
    a = _mk(db, role='admin', store='B')
    _login(client, a)
    assert client.post('/admin/stores/B/toggle-visible').status_code == 403


def test_toggle_visible_cannot_hide_last(client, db):
    from models import Store
    db.session.add(Store(name='B'))
    db.session.commit()
    su = _mk(db, role='super_admin')
    _login(client, su)
    r = client.post('/admin/stores/B/toggle-visible')   # 只有一間，隱藏會清空
    assert r.status_code == 400
    assert '至少' in r.get_json().get('message', '')
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_visible_stores.py -k "list_stores or toggle_visible" -v`
Expected: FAIL（`visible` key 不存在；`/toggle-visible` 404 Not Found）。

- [ ] **Step 3: 改 `list_stores` 回 visible 旗標**

`app_unified/admin/routes.py`，把現有：
```python
@admin_bp.route("/stores", methods=["GET"])
@login_required
def list_stores():
    require_admin()
    stores = Store.query.order_by(Store.name).all()
    return jsonify([{"name": s.name, "login_enabled": s.login_enabled} for s in stores])
```
改成：
```python
@admin_bp.route("/stores", methods=["GET"])
@login_required
def list_stores():
    require_admin()
    stores = Store.query.order_by(Store.name).all()
    hidden = set(current_user.get_hidden_stores()) if current_user.is_super_admin() else set()
    out = []
    for s in stores:
        if current_user.is_super_admin():
            visible = s.name not in hidden
        else:  # admin：唯讀，只有自己店算可視
            visible = (s.name == current_user.store)
        out.append({"name": s.name, "login_enabled": s.login_enabled, "visible": visible})
    return jsonify(out)
```

- [ ] **Step 4: 加 `toggle_store_visible` route**

在 `toggle_store_login`（約 804 行 `return jsonify({"status": "ok", "login_enabled": store.login_enabled})` 那個 function）之後插入：
```python
@admin_bp.route("/stores/<name>/toggle-visible", methods=["POST"])
@login_required
def toggle_store_visible(name):
    # 僅 super_admin 可操作可視店家；admin/user 一律 403
    if not current_user.is_super_admin():
        abort(403)
    Store.query.filter_by(name=name).first_or_404()
    all_names = [s.name for s in Store.query.all()]
    hidden = current_user.get_hidden_stores()
    if name in hidden:
        hidden = [h for h in hidden if h != name]          # 取消隱藏 = 可視
    else:
        hidden_after = set(hidden) | {name}
        visible_after = [n for n in all_names if n not in hidden_after]
        if not visible_after:                              # 防呆：至少保留一間
            return jsonify({"status": "error",
                            "message": "至少要保留一間可視店家"}), 400
        hidden = hidden + [name]
    current_user.set_hidden_stores(hidden)
    is_visible = name not in hidden
    _log_user_action("toggle_visible_store", current_user,
                     detail=f"{name}={'visible' if is_visible else 'hidden'}")
    db.session.commit()
    return jsonify({"status": "ok", "visible": is_visible})
```

確認 `app_unified/admin/routes.py` 頂部已 import `abort`、`current_user`。若無，於既有 import 補上（Flask `abort`、flask_login `current_user`）。

- [ ] **Step 5: 跑測試確認通過**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_visible_stores.py -v`
Expected: 全 PASS（9 條）。

- [ ] **Step 6: Commit**
```bash
cd /home/hirain0126/projects/webapp
git add app_unified/admin/routes.py app_unified/tests/test_visible_stores.py
git commit -m "feat(admin): toggle-visible store endpoint + visible flag in list_stores

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 店家管理表「可視」checkbox 欄

**Files:**
- Modify: `app_unified/templates/admin/dashboard.html`（表頭 ~263 行；`loadStores` ~349 行）
- Test: `app_unified/tests/test_visible_stores.py`

- [ ] **Step 1: 寫失敗測試**（dashboard render 應含可視欄；append）

```python
def test_dashboard_has_visible_column(client, db):
    su = _mk(db, role='super_admin')
    _login(client, su)
    html = client.get('/admin/dashboard').get_data(as_text=True)
    assert '<th>可視</th>' in html               # 店家管理表頭新增欄
    assert 'toggleStoreVisible' in html          # JS handler 存在
    assert 'IS_SUPER_ADMIN' in html              # 角色旗標注入
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_visible_stores.py::test_dashboard_has_visible_column -v`
Expected: FAIL（表頭/JS 尚未加）。

- [ ] **Step 3: 改表頭**

`app_unified/templates/admin/dashboard.html` 約 263 行：
```jinja
    <thead><tr><th>店名</th><th>登入狀態</th><th>操作</th></tr></thead>
```
改成（在 店名 後插入 可視）：
```jinja
    <thead><tr><th>店名</th><th style="white-space:nowrap;">可視</th><th>登入狀態</th><th>操作</th></tr></thead>
```

- [ ] **Step 4: 改 `loadStores` JS + 注入角色旗標 + 加 toggle handler**

把現有 `loadStores`（約 349–363 行）整個 function 換成：
```javascript
const IS_SUPER_ADMIN = {{ current_user.is_super_admin()|tojson }};
async function loadStores() {
  const res = await fetch('/admin/stores');
  const stores = await res.json();
  const tbody = document.getElementById('stores-tbody');
  tbody.innerHTML = stores.map(s => `
    <tr>
      <td><strong>${s.name}</strong></td>
      <td style="text-align:center;white-space:nowrap;">
        <input type="checkbox" ${s.visible ? 'checked' : ''} ${IS_SUPER_ADMIN ? '' : 'disabled'}
               onchange="toggleStoreVisible('${s.name}', this)">
      </td>
      <td><span class="badge badge-${s.login_enabled ? 'active' : 'inactive'}" id="store-login-${s.name}">${s.login_enabled ? '開啟' : '關閉'}</span></td>
      <td>
        <button class="btn btn-secondary" style="font-size:.8rem;padding:.3rem .7rem;" onclick="toggleStoreLogin('${s.name}')">切換登入</button>
        <button class="btn btn-secondary" style="font-size:.8rem;padding:.3rem .7rem;margin-left:.3rem;color:#dc3545;" onclick="deleteStore('${s.name}')">刪除</button>
      </td>
    </tr>
  `).join('');
}
```
接著在 `toggleStoreLogin` function 之後插入新 handler：
```javascript
async function toggleStoreVisible(name, el) {
  const res = await fetch(`/admin/stores/${name}/toggle-visible`, { method: 'POST' });
  const data = await res.json();
  if (data.status === 'ok') {
    el.checked = data.visible;
  } else {
    el.checked = !el.checked;            // 還原 UI
    alert(data.message || '操作失敗');
  }
}
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_visible_stores.py::test_dashboard_has_visible_column -v`
Expected: PASS。

- [ ] **Step 6: Commit**
```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/admin/dashboard.html app_unified/tests/test_visible_stores.py
git commit -m "feat(admin): visible-store checkbox column in store management table

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 筆記列表/切換器/dropdown 吃可視店

**Files:**
- Modify: `app_unified/notes/routes.py`（`_get_stores` ~328 行；`index()` super_admin 分支 ~373 行）
- Test: `app_unified/tests/test_visible_stores.py`

- [ ] **Step 1: 寫失敗測試**（append）

```python
def test_notes_super_admin_hidden_store_excluded(client, db):
    from models import Store, Note
    db.session.add_all([Store(name='B'), Store(name='C')])
    db.session.commit()
    su = _mk(db, role='super_admin')
    nb = Note(user_id=su.id, store='B', title=_uniq('NOTE_B'),
              content='', note_type='regular')
    nc = Note(user_id=su.id, store='C', title=_uniq('NOTE_C'),
              content='', note_type='regular')
    db.session.add_all([nb, nc])
    db.session.commit()
    su.set_hidden_stores(['C'])
    db.session.commit()
    _login(client, su)
    # 無 ?store：只看可視店（B），不看 C
    html = client.get('/notes/?range=all').get_data(as_text=True)
    assert nb.title in html
    assert nc.title not in html


def test_notes_super_admin_hidden_store_param_ignored(client, db):
    from models import Store, Note
    db.session.add_all([Store(name='B'), Store(name='C')])
    db.session.commit()
    su = _mk(db, role='super_admin')
    nc = Note(user_id=su.id, store='C', title=_uniq('ONLY_C'),
              content='', note_type='regular')
    db.session.add(nc)
    db.session.commit()
    su.set_hidden_stores(['C'])
    db.session.commit()
    _login(client, su)
    # 顯式 ?store=C（已隱藏）→ 忽略、回退可視全集，仍看不到 C
    html = client.get('/notes/?store=C&range=all').get_data(as_text=True)
    assert nc.title not in html
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_visible_stores.py -k notes_super_admin -v`
Expected: FAIL（目前 super_admin 看全部，C 仍出現）。

- [ ] **Step 3: 改 `_get_stores`**

`app_unified/notes/routes.py` 約 328 行：
```python
def _get_stores():
    return [s.name for s in Store.query.order_by(Store.name).all()]
```
改成：
```python
def _get_stores():
    """回傳目前登入者『可視』的店名（super_admin 吃 hidden_stores；其餘只有自己店）。"""
    all_names = [s.name for s in Store.query.order_by(Store.name).all()]
    return current_user.visible_store_names(all_names)
```
確認 `notes/routes.py` 頂部已 import `current_user`（flask_login）。若無則補。

- [ ] **Step 4: 改 `index()` super_admin 店別範圍**

`app_unified/notes/routes.py` 約 372–379 行：
```python
    # 店別範圍（不變）
    if current_user.is_super_admin():
        if store_filter in stores:
            query = query.filter_by(store=store_filter)
    elif current_user.is_admin():
        query = query.filter_by(store=current_user.store)
    else:
        query = query.filter_by(store=current_user.store)
```
改成（super_admin 無有效 store → 限縮可視全集，而非看全部）：
```python
    # 店別範圍：super_admin 吃可視店（stores 已是 _get_stores() 過濾後清單）
    if current_user.is_super_admin():
        if store_filter in stores:
            query = query.filter_by(store=store_filter)
        else:
            # 無指定 / 指定到隱藏店 → 回退成可視店全集
            query = query.filter(Note.store.in_(stores))
    elif current_user.is_admin():
        query = query.filter_by(store=current_user.store)
    else:
        query = query.filter_by(store=current_user.store)
```
（`stores` 在 index() 上方已是 `_get_stores()` 的回傳；確認該行存在，約 363 行 `stores = _get_stores()`。）

- [ ] **Step 5: 跑測試確認通過**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_visible_stores.py tests/test_notes.py tests/test_shift.py tests/test_category.py -v`
Expected: 全 PASS（新 visible 測試 + 既有 notes/shift/category 篩選測試不退步）。

- [ ] **Step 6: Commit**
```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/routes.py app_unified/tests/test_visible_stores.py
git commit -m "feat(notes): super_admin notes list + store switcher respect visible stores

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 公告吃可視店（保留全站公告）

**Files:**
- Modify: `app_unified/notes/announcement_routes.py`（`_visibility_query` ~85 行）
- Test: `app_unified/tests/test_visible_stores.py`

- [ ] **Step 1: 寫失敗測試**（append）

```python
def test_announcements_super_admin_respects_visible(client, db):
    from models import Store, Announcement
    db.session.add_all([Store(name='B'), Store(name='C')])
    db.session.commit()
    su = _mk(db, role='super_admin')
    ab = Announcement(store='B', title=_uniq('ANN_B'), body='x',
                      author_id=su.id, author_name=su.username)
    ac = Announcement(store='C', title=_uniq('ANN_C'), body='x',
                      author_id=su.id, author_name=su.username)
    ag = Announcement(store=None, title=_uniq('ANN_GLOBAL'), body='x',
                      author_id=su.id, author_name=su.username)
    db.session.add_all([ab, ac, ag])
    db.session.commit()
    su.set_hidden_stores(['C'])
    db.session.commit()
    _login(client, su)
    data = client.get('/notes/announcements?per_page=100').get_json()
    titles = {a['title'] for a in data['announcements']}
    assert ab.title in titles
    assert ag.title in titles      # 全站公告永遠在
    assert ac.title not in titles  # 隱藏店公告不出現
```

註：先確認 `Announcement` 欄位名（`store`/`title`/`body`/`author_id`/`author_name`）與 list endpoint 路徑 + 回傳 JSON 結構（key 名 `announcements`、每筆 `title`）。實作前 grep `notes/announcement_routes.py` 的 list route 與序列化函式對齊；若 key 不同，依實際調整此測試的存取路徑（不改語意）。

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_visible_stores.py::test_announcements_super_admin_respects_visible -v`
Expected: FAIL（C 公告目前會出現）。

- [ ] **Step 3: 改 `_visibility_query`**

`app_unified/notes/announcement_routes.py` 約 85–95 行：
```python
def _visibility_query():
    """回傳已套用 visibility filter 的 query（給 list / 詳情用）。"""
    if current_user.is_super_admin():
        return Announcement.query
    return Announcement.query.filter(
        or_(
            Announcement.store.is_(None),
            Announcement.store == current_user.store,
        )
    )
```
改成：
```python
def _visibility_query():
    """回傳已套用 visibility filter 的 query（給 list / 詳情用）。"""
    from models import Store
    if current_user.is_super_admin():
        all_names = [s.name for s in Store.query.all()]
        visible = current_user.visible_store_names(all_names)
        return Announcement.query.filter(
            or_(
                Announcement.store.is_(None),       # 全站公告永遠顯示
                Announcement.store.in_(visible),
            )
        )
    return Announcement.query.filter(
        or_(
            Announcement.store.is_(None),
            Announcement.store == current_user.store,
        )
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_visible_stores.py::test_announcements_super_admin_respects_visible -v`
Expected: PASS。

- [ ] **Step 5: Commit**
```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/announcement_routes.py app_unified/tests/test_visible_stores.py
git commit -m "feat(announcements): super_admin announcement list respects visible stores

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: admin 後台使用者清單 + dropdown 吃可視店

**Files:**
- Modify: `app_unified/admin/routes.py`（`dashboard` ~186–198 行）
- Test: `app_unified/tests/test_visible_stores.py`

- [ ] **Step 1: 寫失敗測試**（append）

```python
def test_dashboard_user_list_respects_visible(client, db):
    from models import Store, User
    db.session.add_all([Store(name='B'), Store(name='C')])
    db.session.commit()
    su = _mk(db, role='super_admin')
    ub = _mk(db, role='user', store='B')
    uc = _mk(db, role='user', store='C')
    su.set_hidden_stores(['C'])
    db.session.commit()
    _login(client, su)
    html = client.get('/admin/dashboard').get_data(as_text=True)
    assert ub.username in html       # 可視店 B 的 user 在
    assert uc.username not in html   # 隱藏店 C 的 user 不在
    assert su.username in html       # super_admin 自己（store=None）永遠在
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_visible_stores.py::test_dashboard_user_list_respects_visible -v`
Expected: FAIL（C 的 user 目前會列出）。

- [ ] **Step 3: 改 `dashboard` user 查詢 + stores 清單**

`app_unified/admin/routes.py` 約 186–198 行：
```python
    if current_user.is_super_admin():
        user_query = User.query
    else:
        user_query = User.query.filter_by(store=current_user.store)
    # 店別篩選（只對 super_admin 有效，admin 已限定本店）
    if current_user.is_super_admin() and user_store_filter:
        user_query = user_query.filter_by(store=user_store_filter)
    user_pagination = user_query.order_by(User.created_at.desc()).paginate(
        page=user_page, per_page=per_page, error_out=False
    )
    stores = [s.name for s in Store.query.order_by(Store.name).all()]
```
改成：
```python
    all_store_names = [s.name for s in Store.query.order_by(Store.name).all()]
    if current_user.is_super_admin():
        visible = current_user.visible_store_names(all_store_names)
        # 可視店的 user + 未綁店者/其他 super_admin（store IS NULL）永遠顯示
        user_query = User.query.filter(
            db.or_(User.store.in_(visible), User.store.is_(None))
        )
    else:
        user_query = User.query.filter_by(store=current_user.store)
    # 店別篩選（只對 super_admin 有效，admin 已限定本店）
    if current_user.is_super_admin() and user_store_filter:
        user_query = user_query.filter_by(store=user_store_filter)
    user_pagination = user_query.order_by(User.created_at.desc()).paginate(
        page=user_page, per_page=per_page, error_out=False
    )
    # 傳給 template 的店家清單（dropdown 等）= 可視店；admin 走自己店
    stores = current_user.visible_store_names(all_store_names)
```
確認 `admin/routes.py` 已 import `db`（from extensions import db）；若無，補 import 後用 `db.or_`，或改 `from sqlalchemy import or_` 並用 `or_`。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_visible_stores.py -v`
Expected: 全 PASS。

- [ ] **Step 5: Commit**
```bash
cd /home/hirain0126/projects/webapp
git add app_unified/admin/routes.py app_unified/tests/test_visible_stores.py
git commit -m "feat(admin): dashboard user list + store dropdowns respect visible stores

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 全套驗證 + 本機實測

**Files:** 無（驗證）

- [ ] **Step 1: 全套測試**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/`
Expected: 全 PASS（既有 + 新增），無退步。

- [ ] **Step 2: 本機實測**（[[feedback_local_test_before_push]]）

確認 dev SQLite 已有 `hidden_stores` 欄（Task 1 Step 6 已加；若是 fresh reseed 過則 create_all 會建）。
Run: `cd /home/hirain0126/projects/webapp && bash scripts/run_local.sh`，`/dev` 選 `super_admin`，手動驗：
- 店家管理表：每店一個「可視」checkbox、預設全勾、super_admin 可點。
- 取消某店（如南店）→ /notes 看不到該店筆記、店家切換 tab 不出現該店、公告該店不出現、後台使用者清單不列該店的人。
- 全站公告（store=None）仍在。
- 嘗試把店全部取消 → 最後一間擋下（提示「至少要保留一間」）。
- 換 `/dev` 登 admin（如 admin_north）→ 店家管理「可視」欄為 disabled、只有自己店打勾；notes/後台只看自己店（與現狀一致）。
- 手機寬度（DevTools 響應式）→ 店家管理那列不跑板、維持單行。

- [ ] **Step 3: user 確認 OK**

等 user 看過本機沒問題才談 push（[[feedback_push_policy]]、尖峰 [[feedback_push_peak_hours]]）；push 後注意 prod 走 app.py inline ALTER 自動加欄（[[feedback_inline_schema_repair]]）。

---

## Self-Review

- **Spec coverage：** 資料模型+helper（T1）✓；hidden-set 存法/新店自動可視（T1 test）✓；UI checkbox 欄+super_admin可改/admin disabled/手機不跑板（T3）✓；toggle endpoint+last-store防呆+403（T2）✓；notes 列表/切換器/dropdown（T4）✓；`?store=隱藏` 回退（T4 test）✓；公告+全站永遠顯示（T5）✓；後台 user 清單+未綁店永遠顯示+dropdown（T6）✓；店家管理表永遠全列（T2 list_stores 不過濾）✓；schema 三檔對齊+dev ALTER（T1）✓；測試涵蓋 helper/notes/公告/後台/toggle/schema ✓。
- **Placeholder scan：** 無 TBD；所有 code step 具完整程式碼。T5 Step1 有「先 grep 對齊 Announcement 欄位/JSON key」的指示（因 announcement 序列化結構未逐欄驗證）——這是明確的對齊動作非 placeholder。
- **Type/名稱一致：** `hidden_stores`、`get_hidden_stores`/`set_hidden_stores`/`visible_store_names`、`toggle_store_visible`/`/toggle-visible`、`toggleStoreVisible`、`IS_SUPER_ADMIN`、API key `visible` 全文一致。
- **Announcement 欄位假設：** T5 測試假設 `Announcement(store/title/body/author_id/author_name)` 與 list endpoint 回傳 `{announcements:[{title}]}`；T5 Step1 已要求實作前 grep 對齊，若不符依實際調整存取路徑（不改語意）。
