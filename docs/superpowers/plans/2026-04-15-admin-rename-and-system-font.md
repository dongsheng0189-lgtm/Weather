# Admin 改名級聯 + 全站系統字體 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在管理後台使用者列表加「改名」按鈕（同步更新該使用者名下所有 TrustedDevice.device_name），並讓全站字體跟隨 OS 系統字體大小。

**Architecture:**
- Feature 1：新增 `POST /admin/users/<id>/rename` 路由，沿用 `require_admin()` + 跨店檢查；前端 inline edit。
- Feature 2：移除 `base.html` viewport 的 `maximum-scale=1.0`，把模板 inline `<style>` 的字體 px 改 rem（input `font-size:16px` 保留防 iOS focus 放大）。

**Tech Stack:** Flask, SQLAlchemy, Flask-Login, Jinja2, pytest（`app_unified/tests/conftest.py` 已提供 `app` / `client` / `admin_user` / `logged_in_admin_client` fixtures，in-memory SQLite）。

**Spec:** `docs/superpowers/specs/2026-04-15-admin-rename-and-system-font-design.md`

**Working directory:** `/home/hirain0126/projects/webapp/app_unified`（repo root: `/home/hirain0126/projects/webapp`）

---

## 檔案概覽

### Feature 1
- Modify: `app_unified/admin/routes.py`
  - 新 helper `_assert_can_manage_target(target)`（可選；若 inline 簡單直接 inline 即可）
  - 新 route `rename_user(user_id)`
  - `list_user_logs` 的 `action_labels` 加 `"rename": "改名"`
- Modify: `app_unified/templates/admin/dashboard.html`
  - 第 52 行 `<td>{{ user.username }}</td>` 改為含 ✏️ button
  - 加對應 JS function `renameUser(uid, oldName)`
- Create: `app_unified/tests/test_admin_rename.py`

### Feature 2
- Modify: `app_unified/templates/base.html`
  - viewport meta
  - inline CSS 字體 px → rem
- Modify: `app_unified/templates/notes/editor.html`
- Modify: `app_unified/templates/notes/index.html`
- Modify: `app_unified/templates/mynotes/editor.html`
- Modify: `app_unified/templates/mynotes/index.html`
- Modify: `app_unified/templates/weather/index.html`（目前已多為 rem，掃過確認）
- Modify: `app_unified/templates/admin/dashboard.html`（與 Feature 1 共用檔）
- Modify: `app_unified/templates/auth/*.html`（登入相關）

---

## Feature 1：改使用者名稱 + 級聯

### Task 1：後端 — 寫測試（FAIL 先行）

**Files:**
- Create: `app_unified/tests/test_admin_rename.py`

- [ ] **Step 1: 寫 failing tests**

```python
# app_unified/tests/test_admin_rename.py
"""Admin rename user + cascade to TrustedDevice.device_name"""
import uuid
import pytest
from models import User, TrustedDevice, UserLog
from extensions import db


def _mk_user(db_session, username=None, role="user", store=None, active=True):
    u = User(
        username=username or f"u_{uuid.uuid4().hex[:6]}",
        role=role,
        store=store,
        is_active=active,
    )
    u.set_password("1234")
    db_session.add(u)
    db_session.commit()
    return u


def _mk_device(db_session, user, device_name):
    d = TrustedDevice(
        fingerprint=f"fp_{uuid.uuid4().hex[:8]}",
        device_name=device_name,
        user_id=user.id,
        is_approved=True,
    )
    db_session.add(d)
    db_session.commit()
    return d


def _login(client, user, pin="1234"):
    return client.post("/auth/login", json={"username": user.username, "pin": pin})


def test_admin_rename_cascades_device_name(app, client, db_session):
    admin = _mk_user(db_session, username=f"admin_{uuid.uuid4().hex[:6]}", role="admin", store="B")
    admin.set_password("0000")
    db_session.commit()
    target = _mk_user(db_session, username="alice", store="B")
    d1 = _mk_device(db_session, target, device_name="alice")
    d2 = _mk_device(db_session, target, device_name="alice")
    _login(client, admin, pin="0000")

    r = client.post(f"/admin/users/{target.id}/rename", json={"new_username": "alice2"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["status"] == "ok"

    db_session.expire_all()
    assert User.query.get(target.id).username == "alice2"
    assert TrustedDevice.query.get(d1.id).device_name == "alice2"
    assert TrustedDevice.query.get(d2.id).device_name == "alice2"

    log = UserLog.query.filter_by(target_id=target.id, action="rename").first()
    assert log is not None
    assert "alice" in (log.detail or "") and "alice2" in (log.detail or "")


def test_admin_cannot_rename_other_store(app, client, db_session):
    admin = _mk_user(db_session, username=f"admin_{uuid.uuid4().hex[:6]}", role="admin", store="B")
    admin.set_password("0000")
    db_session.commit()
    target = _mk_user(db_session, username="bob", store="C")
    _login(client, admin, pin="0000")

    r = client.post(f"/admin/users/{target.id}/rename", json={"new_username": "bob2"})
    assert r.status_code == 403
    db_session.expire_all()
    assert User.query.get(target.id).username == "bob"


def test_super_admin_rename_any_store(app, client, db_session):
    sa = _mk_user(db_session, username=f"sa_{uuid.uuid4().hex[:6]}", role="super_admin", store=None)
    sa.set_password("0000")
    db_session.commit()
    target = _mk_user(db_session, username="carol", store="C")
    _login(client, sa, pin="0000")

    r = client.post(f"/admin/users/{target.id}/rename", json={"new_username": "carol2"})
    assert r.status_code == 200
    db_session.expire_all()
    assert User.query.get(target.id).username == "carol2"


def test_rename_conflict_returns_409(app, client, db_session):
    admin = _mk_user(db_session, username=f"admin_{uuid.uuid4().hex[:6]}", role="admin", store="B")
    admin.set_password("0000")
    db_session.commit()
    _mk_user(db_session, username="taken", store="B")
    target = _mk_user(db_session, username="dave", store="B")
    _login(client, admin, pin="0000")

    r = client.post(f"/admin/users/{target.id}/rename", json={"new_username": "taken"})
    assert r.status_code == 409
    db_session.expire_all()
    assert User.query.get(target.id).username == "dave"


def test_rename_empty_returns_400(app, client, db_session):
    admin = _mk_user(db_session, username=f"admin_{uuid.uuid4().hex[:6]}", role="admin", store="B")
    admin.set_password("0000")
    db_session.commit()
    target = _mk_user(db_session, username="eve", store="B")
    _login(client, admin, pin="0000")

    r = client.post(f"/admin/users/{target.id}/rename", json={"new_username": "   "})
    assert r.status_code == 400


def test_cannot_rename_self(app, client, db_session):
    admin = _mk_user(db_session, username=f"admin_{uuid.uuid4().hex[:6]}", role="admin", store="B")
    admin.set_password("0000")
    db_session.commit()
    _login(client, admin, pin="0000")

    r = client.post(f"/admin/users/{admin.id}/rename", json={"new_username": "admin_renamed"})
    assert r.status_code == 403


def test_rename_noop_same_name(app, client, db_session):
    admin = _mk_user(db_session, username=f"admin_{uuid.uuid4().hex[:6]}", role="admin", store="B")
    admin.set_password("0000")
    db_session.commit()
    target = _mk_user(db_session, username="frank", store="B")
    _login(client, admin, pin="0000")

    r = client.post(f"/admin/users/{target.id}/rename", json={"new_username": "frank"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_non_admin_forbidden(app, client, db_session):
    import numpy as np
    # Non-admin user with face encoding so login works via face mock
    user = _mk_user(db_session, username=f"user_{uuid.uuid4().hex[:6]}", store="B")
    from tests.conftest import FAKE_ENCODING
    user.set_face_encoding(FAKE_ENCODING)
    db_session.commit()
    target = _mk_user(db_session, username="gina", store="B")
    client.post("/auth/login", json={
        "username": user.username, "pin": "1234",
        "face_image": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="
    })

    r = client.post(f"/admin/users/{target.id}/rename", json={"new_username": "gina2"})
    assert r.status_code in (302, 401, 403)
```

- [ ] **Step 2: 跑測試，確認全部 FAIL**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_admin_rename.py -v`

Expected: 所有 test 失敗，原因是 `POST /admin/users/<id>/rename` 路由不存在（404）。

- [ ] **Step 3: Commit 測試**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/tests/test_admin_rename.py
git commit -m "test: admin rename user + device_name cascade (failing)"
```

---

### Task 2：後端 — 實作 rename route

**Files:**
- Modify: `app_unified/admin/routes.py`

- [ ] **Step 1: import TrustedDevice**

找到檔案頂部 import 行（line 8）：

```python
from models import User, Note, Store, NoteLog, UserLog, STATUS_CHOICES
```

改成：

```python
from models import User, Note, Store, NoteLog, UserLog, TrustedDevice, STATUS_CHOICES
```

- [ ] **Step 2: 加 `rename_user` route**

在 `set_store` route 之後（約 line 282 之後、`list_user_logs` 之前）插入：

```python
@admin_bp.route("/users/<int:user_id>/rename", methods=["POST"])
@login_required
def rename_user(user_id):
    require_admin()
    user = User.query.get_or_404(user_id)

    # 不可改自己
    if user.id == current_user.id:
        return jsonify({"status": "error", "message": "不可修改自己的帳號名稱"}), 403

    # admin（非 super_admin）：限本店
    if not current_user.is_super_admin():
        if user.store != current_user.store:
            return jsonify({"status": "error", "message": "無權修改他店使用者"}), 403

    data = request.get_json(silent=True) or {}
    new_name = (data.get("new_username") or "").strip()
    if not new_name:
        return jsonify({"status": "error", "message": "名稱不可為空"}), 400

    old_name = user.username
    if new_name == old_name:
        return jsonify({"status": "ok", "new_username": new_name})

    # 唯一性檢查
    dup = User.query.filter(User.username == new_name, User.id != user.id).first()
    if dup is not None:
        return jsonify({"status": "error", "message": "名稱已被使用"}), 409

    try:
        user.username = new_name
        TrustedDevice.query.filter_by(user_id=user.id).update(
            {"device_name": new_name}, synchronize_session=False
        )
        _log_user_action("rename", user, detail=f"{old_name} → {new_name}")
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("rename_user failed")
        return jsonify({"status": "error", "message": "內部錯誤"}), 500

    return jsonify({"status": "ok", "new_username": new_name})
```

- [ ] **Step 3: `list_user_logs` 加 rename label**

找到 `action_labels` dict（約 line 291）：

```python
    action_labels = {
        "create": "新增",
        "delete": "刪除",
        "activate": "啟用",
        "deactivate": "停用",
        "set_role": "改角色",
        "set_store": "改店別",
        "approve_device": "核准裝置",
        "set_features": "改模組權限",
    }
```

加一行 `"rename": "改名",`：

```python
    action_labels = {
        "create": "新增",
        "delete": "刪除",
        "activate": "啟用",
        "deactivate": "停用",
        "set_role": "改角色",
        "set_store": "改店別",
        "approve_device": "核准裝置",
        "set_features": "改模組權限",
        "rename": "改名",
    }
```

- [ ] **Step 4: 跑測試，確認全部 PASS**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_admin_rename.py -v`

Expected: 8 passed（或全綠）。若有失敗，看錯誤訊息逐一修。

- [ ] **Step 5: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/admin/routes.py
git commit -m "feat(admin): add rename user route with device_name cascade"
```

---

### Task 3：前端 — dashboard 加 ✏️ 按鈕與 inline edit

**Files:**
- Modify: `app_unified/templates/admin/dashboard.html`

- [ ] **Step 1: 改「名稱」欄 cell**

找到 line 52：

```html
      <td>{{ user.username }}</td>
```

改成：

```html
      <td>
        <span class="uname" data-uid="{{ user.id }}">{{ user.username }}</span>
        {% if user.id != current_user.id and (current_user.is_super_admin() or user.store == current_user.store) %}
        <button type="button" class="rename-btn"
                style="background:none;border:none;cursor:pointer;padding:.15rem .3rem;font-size:.95rem;"
                onclick="renameUser({{ user.id }}, '{{ user.username|e }}')"
                title="改名">✏️</button>
        {% endif %}
      </td>
```

- [ ] **Step 2: 找到現有 JS 區塊並加 `renameUser` 函式**

在檔案尾端的 `<script>` 區塊（跟 `setRole`、`setStore`、`deleteUser` 等函式一起的地方）新增：

```javascript
async function renameUser(uid, oldName) {
  const newName = prompt('新的名稱（會同步更新綁定手機上的名字）：', oldName);
  if (newName === null) return;
  const trimmed = newName.trim();
  if (!trimmed) { alert('名稱不可為空'); return; }
  if (trimmed === oldName) return;

  try {
    const res = await fetch(`/admin/users/${uid}/rename`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({new_username: trimmed}),
    });
    const data = await res.json();
    if (res.ok && data.status === 'ok') {
      const span = document.querySelector(`.uname[data-uid="${uid}"]`);
      if (span) span.textContent = data.new_username;
      const btn = document.querySelector(`.rename-btn[onclick*="renameUser(${uid},"]`);
      if (btn) btn.setAttribute('onclick', `renameUser(${uid}, '${data.new_username.replace(/'/g, "\\'")}')`);
    } else {
      alert(data.message || '改名失敗');
    }
  } catch (e) {
    alert('網路錯誤：' + e.message);
  }
}
```

（找一個現有函式旁邊插入即可，例如 `function deleteUser(...)` 上方）

- [ ] **Step 3: 手動驗證**

啟動本地 server（或在 Zeabur dev 環境），以 admin 身分登入 → admin dashboard → 點某個使用者名稱旁 ✏️ → 輸入新名 → 確認：
- 名稱就地更新（不 reload）
- 該使用者 → 設備列表（admin 頁面的 device section）→ device_name 跟著變
- 操作記錄頁面出現「改名：A → B」

若無法本地跑，至少確認 template 語法不爆（`grep "renameUser" templates/admin/dashboard.html` 有兩處）。

- [ ] **Step 4: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/admin/dashboard.html
git commit -m "feat(admin): add rename button + inline edit to user list"
```

---

## Feature 2：全站字體吃 OS 系統字體

### Task 4：移除 viewport 的 `maximum-scale=1.0`

**Files:**
- Modify: `app_unified/templates/base.html`

- [ ] **Step 1: 改 viewport**

找到 line 5：

```html
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
```

改成：

```html
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
```

- [ ] **Step 2: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/base.html
git commit -m "feat(ui): allow pinch-zoom and OS font scaling by removing maximum-scale"
```

---

### Task 5：base.html 字體 px → rem

**Files:**
- Modify: `app_unified/templates/base.html`

- [ ] **Step 1: 掃描 base.html 所有 `font-size: *px`**

Run: `grep -n "font-size" app_unified/templates/base.html`

記下每一處，逐一判斷：
- 若是 `<input>`/`<textarea>`/`<select>` 相關（line 64 附近 `input, textarea, select { font-size: 16px }`）→ **保留 px**（iOS 防 focus 放大）
- 其他文字（body、標題、導覽、錯誤訊息、狀態提示）→ 改 rem（px ÷ 16 = rem）

- [ ] **Step 2: 改字體 px → rem**

換算對照：
- 12px → 0.75rem
- 13px → 0.8125rem
- 14px → 0.875rem
- 15px → 0.9375rem
- 16px → 1rem（可直接省略）
- 18px → 1.125rem
- 20px → 1.25rem
- 24px → 1.5rem

**重要：** 僅改「字體」相關 px（`font-size`、line-height 中的 px 若存在也改；其他 `width`、`padding`、`margin`、`border-radius` 的 px **不動**）。

- [ ] **Step 3: 目視檢查 diff，確認沒動到 input font-size**

Run: `cd /home/hirain0126/projects/webapp && git diff app_unified/templates/base.html | grep -E "input|textarea|select" | grep "font-size"`

Expected: 無輸出（沒改 input 字體）。

- [ ] **Step 4: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/base.html
git commit -m "style(base): font-size px → rem for OS font scaling"
```

---

### Task 6：notes editor 與 index 字體 px → rem

**Files:**
- Modify: `app_unified/templates/notes/editor.html`
- Modify: `app_unified/templates/notes/index.html`

- [ ] **Step 1: 掃描並改 notes/editor.html**

Run: `grep -n "font-size" app_unified/templates/notes/editor.html`

規則同 Task 5：字體改 rem，input/textarea/select 的 16px 保留，其他非字體 px 不動。

注意：`.content { font-size: 16px }`（內文編輯區）→ `1rem`，但若是 `<textarea>` 就保留 16px。判斷方式：看 selector 是否作用在 input 元件。

- [ ] **Step 2: 掃描並改 notes/index.html**

Run: `grep -n "font-size" app_unified/templates/notes/index.html`

同規則處理。

- [ ] **Step 3: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/notes/editor.html app_unified/templates/notes/index.html
git commit -m "style(notes): font-size px → rem"
```

---

### Task 7：mynotes editor 與 index 字體 px → rem

**Files:**
- Modify: `app_unified/templates/mynotes/editor.html`
- Modify: `app_unified/templates/mynotes/index.html`

- [ ] **Step 1: 掃描並改 mynotes/editor.html**

Run: `grep -n "font-size" app_unified/templates/mynotes/editor.html`

規則同 Task 5。

- [ ] **Step 2: 掃描並改 mynotes/index.html**

Run: `grep -n "font-size" app_unified/templates/mynotes/index.html`

規則同 Task 5。

- [ ] **Step 3: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/mynotes/editor.html app_unified/templates/mynotes/index.html
git commit -m "style(mynotes): font-size px → rem"
```

---

### Task 8：weather、admin、auth 模板字體 px → rem

**Files:**
- Modify: `app_unified/templates/weather/index.html`
- Modify: `app_unified/templates/admin/dashboard.html`
- Modify: `app_unified/templates/auth/*.html`（掃描所有檔案）

- [ ] **Step 1: 列出所有要掃的檔案**

Run:

```bash
ls app_unified/templates/auth/
grep -l "font-size:.*px" app_unified/templates/weather/*.html app_unified/templates/admin/*.html app_unified/templates/auth/*.html
```

- [ ] **Step 2: 逐檔掃描並改**

對上一步列出的每個檔案：

```bash
grep -n "font-size" <file>
```

字體 px → rem；input 保留。

- [ ] **Step 3: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/weather/ app_unified/templates/admin/ app_unified/templates/auth/
git commit -m "style: font-size px → rem across weather/admin/auth templates"
```

---

### Task 9：手動驗證 + 回報

- [ ] **Step 1: 啟動本地 server（或部署到 dev）**

- [ ] **Step 2: 桌面瀏覽器檢查每個主要頁面不破版**

頁面清單：
- 登入頁 `/auth/login`
- 天氣頁 `/weather/`（首頁）
- 筆記列表 `/notes/`
- 筆記編輯 `/notes/<id>`
- 私人筆記列表 `/mynotes/`
- 私人筆記編輯 `/mynotes/<id>`
- Admin dashboard `/admin/dashboard`

逐頁目視：文字大小合理、layout 沒擠壓、button 沒重疊。

- [ ] **Step 3: 手機真機驗證（iOS 或 Android 擇一以上）**

- **Android Chrome**：設定 > 顯示 > 字體大小 調到最大 → 開 app → 文字變大 ✓
- **iOS Safari**：Settings > Display & Text Size > Larger Text 調大 → 開 app → 文字變大 ✓
- 編輯筆記、開相片 fullscreen pinch overlay：不受影響 ✓

若無真機可測，回報「程式碼改動完成，待使用者真機驗證」。

- [ ] **Step 4: 若有破版，列清單回報**

不要自己修，先回報讓使用者判斷優先度（例如「admin dashboard 表格在字體 200% 下會橫向捲動」）。

---

## Self-Review 結果

- [x] Spec coverage：Feature 1（rename 路由、cascade、log、UI 按鈕、權限）全在 Task 1-3；Feature 2（viewport、px→rem、驗證）全在 Task 4-9。
- [x] No placeholders：每個步驟都有實際指令或完整程式碼。
- [x] Type consistency：`rename_user`、`renameUser`、`action="rename"` 一致。
- [x] Test fixtures：使用 conftest.py 既有 `app` / `client` / `db_session`，不靠 `admin_user` fixture（自行在測試內建 admin 以設特定 store）。
