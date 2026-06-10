# 使用者自助設定頁（改 PIN + 字體）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `/auth/settings` 設定頁讓使用者自助修改 4 位 PIN 並調整字體，右上角入口換成「⚙️ 設定」，移除舊的獨立 `/auth/font-size`。

**Architecture:** `auth` blueprint 加 `GET /auth/settings`（render 新 template）與 `POST /auth/change-pin`（JSON 驗證 + `set_password` + UserLog，rate-limited）。設定頁 PIN 區走後端 API，字體區沿用既有 localStorage 機制（全域套用邏輯已在 `base.html`，不動）。`base.html` nav 兩處連結改指向設定頁，舊 font-size 路由與 template 刪除。

**Tech Stack:** Flask + Flask-Login + Flask-Limiter + SQLAlchemy + Jinja2 + vanilla JS + pytest。

**Spec:** `docs/superpowers/specs/2026-06-10-user-settings-pin-font-design.md`

---

## File Structure

| 檔案 | 動作 | 責任 |
|------|------|------|
| `auth/routes.py` | Modify | 加 `_is_weak_pin` helper、`settings` route、`change_pin` route；import 加 `UserLog`；Task 4 刪 `font_size` route |
| `templates/auth/settings.html` | Create | 設定頁：🔑 修改 PIN 表單 + 🔤 字體 picker（含 inline CSS/JS） |
| `templates/base.html` | Modify | nav 兩處 `調整字體` → `⚙️ 設定`（L91、L110） |
| `templates/auth/font_size.html` | Delete | 字體功能併入設定頁後移除 |
| `tests/test_auth_change_pin.py` | Create | change-pin 後端驗證 unit/integration tests |

---

## Task 1: 後端 `_is_weak_pin` + `settings` + `change-pin` 路由（TDD）

**Files:**
- Modify: `auth/routes.py`（import 第 21 行；新增 helper + 2 routes，接在 `module_picker` route 之後，約第 264 行）
- Test: `tests/test_auth_change_pin.py`（新檔）

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_auth_change_pin.py`：

```python
"""Tests for auth/routes.py self-service PIN change (/auth/change-pin)."""
import uuid
import pytest

from extensions import db
from models import User, Store, UserLog


def _make_user(pin="1357"):
    """Create a 北店 user with a known 4-digit PIN. Ensures Store 'A' exists."""
    if not Store.query.filter_by(name="A").first():
        db.session.add(Store(name="A"))
        db.session.commit()
    u = User(username=f"u-{uuid.uuid4().hex[:8]}", store="A", role="user")
    u.set_password(pin)
    u.set_features(["notes"])
    db.session.add(u)
    db.session.commit()
    return u


def _login_as(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def test_change_pin_success(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(pin="1357").id
    _login_as(client, uid)
    r = client.post("/auth/change-pin",
                    json={"current_pin": "1357", "new_pin": "2580", "confirm_pin": "2580"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"
    with app.app_context():
        u = User.query.get(uid)
        assert u.check_password("2580")
        assert not u.check_password("1357")
        assert UserLog.query.filter_by(target_id=uid, action="change_pin").count() == 1


def test_change_pin_wrong_current(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(pin="1357").id
    _login_as(client, uid)
    r = client.post("/auth/change-pin",
                    json={"current_pin": "0000", "new_pin": "2580", "confirm_pin": "2580"})
    assert r.status_code == 400
    assert "目前 PIN" in r.get_json()["message"]
    with app.app_context():
        assert User.query.get(uid).check_password("1357")  # unchanged


def test_change_pin_confirm_mismatch(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(pin="1357").id
    _login_as(client, uid)
    r = client.post("/auth/change-pin",
                    json={"current_pin": "1357", "new_pin": "2580", "confirm_pin": "2581"})
    assert r.status_code == 400
    assert "不一致" in r.get_json()["message"]


def test_change_pin_same_as_current(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(pin="1357").id
    _login_as(client, uid)
    r = client.post("/auth/change-pin",
                    json={"current_pin": "1357", "new_pin": "1357", "confirm_pin": "1357"})
    assert r.status_code == 400
    assert "不能與目前相同" in r.get_json()["message"]


@pytest.mark.parametrize("weak", ["0000", "1111", "1234", "9876", "3210"])
def test_change_pin_weak_rejected(app, weak):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(pin="1357").id
    _login_as(client, uid)
    r = client.post("/auth/change-pin",
                    json={"current_pin": "1357", "new_pin": weak, "confirm_pin": weak})
    assert r.status_code == 400
    assert "過於簡單" in r.get_json()["message"]


@pytest.mark.parametrize("bad", ["123", "12345", "12a4", "abcd", ""])
def test_change_pin_not_4_digits(app, bad):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(pin="1357").id
    _login_as(client, uid)
    r = client.post("/auth/change-pin",
                    json={"current_pin": "1357", "new_pin": bad, "confirm_pin": bad})
    assert r.status_code == 400
    assert "4 位數字" in r.get_json()["message"]


def test_change_pin_requires_login(app):
    client = app.test_client()
    r = client.post("/auth/change-pin",
                    json={"current_pin": "1357", "new_pin": "2580", "confirm_pin": "2580"})
    assert r.status_code in (401, 302)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_auth_change_pin.py -q`
Expected: FAIL — `werkzeug.routing.exceptions.BuildError` / 404（`/auth/change-pin` 尚未存在）。

- [ ] **Step 3: 改 import 加 `UserLog`**

在 `auth/routes.py` 第 21 行：

```python
from models import User, Store
```

改成：

```python
from models import User, Store, UserLog
```

- [ ] **Step 4: 加 helper + 2 routes**

在 `auth/routes.py` 的 `module_picker` route 之後（約第 264 行，原 `font_size` route 位置附近）加入：

```python
def _is_weak_pin(pin: str) -> bool:
    """太弱的 4 位 PIN：四碼全同 / 連號遞增 / 連號遞減。"""
    if len(set(pin)) == 1:                 # 0000, 1111, ...
        return True
    digits = [int(c) for c in pin]
    if all(digits[i + 1] - digits[i] == 1 for i in range(len(digits) - 1)):   # 1234
        return True
    if all(digits[i + 1] - digits[i] == -1 for i in range(len(digits) - 1)):  # 9876
        return True
    return False


@auth_bp.route("/settings")
@login_required
def settings():
    return render_template("auth/settings.html")


@auth_bp.route("/change-pin", methods=["POST"])
@limiter.limit("10 per minute", exempt_when=lambda: current_app.config.get("TESTING"))
@login_required
def change_pin():
    data = request.get_json(silent=True) or {}
    current = str(data.get("current_pin") or "").strip()
    new = str(data.get("new_pin") or "").strip()
    confirm = str(data.get("confirm_pin") or "").strip()

    if not current_user.check_password(current):
        return jsonify({"status": "error", "message": "目前 PIN 不正確"}), 400
    if len(new) != 4 or not new.isdigit():
        return jsonify({"status": "error", "message": "新 PIN 需為 4 位數字"}), 400
    if new != confirm:
        return jsonify({"status": "error", "message": "兩次新 PIN 不一致"}), 400
    if new == current:
        return jsonify({"status": "error", "message": "新 PIN 不能與目前相同"}), 400
    if _is_weak_pin(new):
        return jsonify({"status": "error", "message": "PIN 過於簡單，請換一組"}), 400

    current_user.set_password(new)
    db.session.add(UserLog(
        operator_id=current_user.id,
        operator_name=current_user.username,
        target_id=current_user.id,
        target_name=current_user.username,
        action="change_pin",
        detail=None,
    ))
    db.session.commit()
    return jsonify({"status": "ok"})
```

> 註：`@limiter.limit` 放在 `@login_required` 之上，與 `verify` route 的裝飾器順序一致。`exempt_when` 在 TESTING 下豁免，測試不會觸發 429。

- [ ] **Step 5: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_auth_change_pin.py -q`
Expected: PASS（全部，含 parametrize 展開的弱 PIN / 非 4 位案例）。

- [ ] **Step 6: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/auth/routes.py app_unified/tests/test_auth_change_pin.py
git commit -m "feat(auth): self-service change-pin endpoint + settings route"
```

---

## Task 2: 設定頁 template `settings.html`

**Files:**
- Create: `templates/auth/settings.html`
- Test: `tests/test_auth_change_pin.py`（加 1 個 render smoke test）

- [ ] **Step 1: 加設定頁 render smoke test**

在 `tests/test_auth_change_pin.py` 末尾加：

```python
def test_settings_page_renders(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(pin="1357").id
    _login_as(client, uid)
    r = client.get("/auth/settings")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "修改 PIN" in body
    assert "字體大小" in body
    assert 'id="cp-current"' in body          # PIN 表單存在
    assert 'data-size="small"' in body          # 字體 picker 存在


def test_settings_requires_login(app):
    client = app.test_client()
    r = client.get("/auth/settings")
    assert r.status_code in (401, 302)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_auth_change_pin.py::test_settings_page_renders -q`
Expected: FAIL — `jinja2.exceptions.TemplateNotFound: auth/settings.html`。

- [ ] **Step 3: 建立 `templates/auth/settings.html`**

```html
{% extends "base.html" %}
{% block title %}設定{% endblock %}
{% block content %}
<style>
  .set-wrap { max-width: 480px; margin: 2rem auto; padding: 0 1rem; }
  .set-section { background: #fff; border: 1px solid #e0e0e0; border-radius: 12px;
                 padding: 1.25rem; margin-bottom: 1.5rem; }
  .set-section h2 { font-size: 1.05rem; color: #2c3e50; margin: 0 0 1rem; }
  .set-field { margin-bottom: .85rem; }
  .set-field label { display: block; font-size: .85rem; color: #555; margin-bottom: .3rem; }
  .set-field input { width: 100%; padding: .6rem .7rem; border: 1px solid #ddd;
                     border-radius: 8px; font-size: 16px; letter-spacing: .3em; text-align: center; }
  .set-btn { width: 100%; padding: .7rem; border: none; border-radius: 8px;
             background: #3498db; color: #fff; font-size: 1rem; cursor: pointer; min-height: 44px; }
  .set-btn:hover { background: #2980b9; }
  .set-msg { min-height: 1.4em; font-size: .9rem; margin-top: .6rem; text-align: center; }
  .set-msg.ok { color: #27ae60; }
  .set-msg.err { color: #e74c3c; }
  /* 字體 picker（沿用 font_size 樣式） */
  .fs-options { display: flex; flex-direction: column; gap: .75rem; }
  .fs-option { display: flex; align-items: center; justify-content: space-between;
               background: #fff; border: 2px solid #e0e0e0; border-radius: 12px;
               padding: 1rem 1.25rem; cursor: pointer; transition: border-color .15s, background .15s;
               font-family: inherit; text-align: left; width: 100%; }
  .fs-option:hover { border-color: #3498db; }
  .fs-option.active { border-color: #3498db; background: #eaf5fc; }
  .fs-option-label { font-weight: 600; color: #2c3e50; }
  .fs-option-preview { color: #555; }
  .fs-option[data-size="small"] .fs-option-preview { font-size: 16px; }
  .fs-option[data-size="medium"] .fs-option-preview { font-size: 19px; }
  .fs-option[data-size="large"] .fs-option-preview { font-size: 22px; }
  .fs-check { color: #3498db; font-size: 1.25rem; opacity: 0; transition: opacity .15s; }
  .fs-option.active .fs-check { opacity: 1; }
</style>
<div class="set-wrap">

  <div class="set-section">
    <h2>🔑 修改 PIN</h2>
    <div class="set-field">
      <label for="cp-current">目前 PIN</label>
      <input type="password" id="cp-current" inputmode="numeric" maxlength="4" autocomplete="current-password">
    </div>
    <div class="set-field">
      <label for="cp-new">新 PIN（4 位數字）</label>
      <input type="password" id="cp-new" inputmode="numeric" maxlength="4" autocomplete="new-password">
    </div>
    <div class="set-field">
      <label for="cp-confirm">再輸入一次新 PIN</label>
      <input type="password" id="cp-confirm" inputmode="numeric" maxlength="4" autocomplete="new-password">
    </div>
    <button type="button" class="set-btn" id="cp-save">儲存新 PIN</button>
    <div class="set-msg" id="cp-msg"></div>
  </div>

  <div class="set-section">
    <h2>🔤 字體大小</h2>
    <div class="fs-options">
      <button type="button" class="fs-option" data-size="small">
        <span class="fs-option-label">小（預設）</span>
        <span class="fs-option-preview">範例文字 Aa 示範</span>
        <span class="fs-check">✓</span>
      </button>
      <button type="button" class="fs-option" data-size="medium">
        <span class="fs-option-label">中</span>
        <span class="fs-option-preview">範例文字 Aa 示範</span>
        <span class="fs-check">✓</span>
      </button>
      <button type="button" class="fs-option" data-size="large">
        <span class="fs-option-label">大</span>
        <span class="fs-option-preview">範例文字 Aa 示範</span>
        <span class="fs-check">✓</span>
      </button>
    </div>
  </div>

</div>
<script>
  // ── 修改 PIN ──
  (function() {
    var btn = document.getElementById('cp-save');
    var msg = document.getElementById('cp-msg');
    var f = {
      current: document.getElementById('cp-current'),
      new: document.getElementById('cp-new'),
      confirm: document.getElementById('cp-confirm'),
    };
    btn.addEventListener('click', function() {
      msg.className = 'set-msg';
      msg.textContent = '';
      btn.disabled = true;
      fetch('/auth/change-pin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          current_pin: f.current.value,
          new_pin: f.new.value,
          confirm_pin: f.confirm.value,
        }),
      })
        .then(function(r) { return r.json().then(function(j) { return [r, j]; }); })
        .then(function(pair) {
          var r = pair[0], j = pair[1];
          if (r.ok && j.status === 'ok') {
            msg.className = 'set-msg ok';
            msg.textContent = '✓ PIN 已更新';
            f.current.value = f.new.value = f.confirm.value = '';
          } else {
            msg.className = 'set-msg err';
            msg.textContent = j.message || '更新失敗';
          }
        })
        .catch(function() {
          msg.className = 'set-msg err';
          msg.textContent = '網路錯誤，請再試一次';
        })
        .finally(function() { btn.disabled = false; });
    });
  })();

  // ── 字體大小（沿用 localStorage 機制；全域套用在 base.html）──
  (function() {
    var sizes = { small: '16px', medium: '19px', large: '22px' };
    function apply(pref) {
      document.documentElement.style.fontSize = sizes[pref] || sizes.small;
      localStorage.setItem('font_size_pref', pref);
      document.querySelectorAll('.fs-option').forEach(function(b) {
        b.classList.toggle('active', b.getAttribute('data-size') === pref);
      });
    }
    var current = localStorage.getItem('font_size_pref') || 'small';
    document.querySelectorAll('.fs-option').forEach(function(b) {
      b.addEventListener('click', function() { apply(b.getAttribute('data-size')); });
    });
    apply(current);
  })();
</script>
{% endblock %}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_auth_change_pin.py -q`
Expected: PASS（含新加的 render smoke tests）。

- [ ] **Step 5: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/auth/settings.html app_unified/tests/test_auth_change_pin.py
git commit -m "feat(auth): settings page template (change PIN + font picker)"
```

---

## Task 3: nav 入口 `調整字體` → `⚙️ 設定`（base.html）

**Files:**
- Modify: `templates/base.html`（桌機 `.nav-links` 第 91 行、手機 `.nav-dropdown` 第 110 行）

- [ ] **Step 1: 改桌機 nav（第 91 行）**

```html
        <a href="{{ url_for('auth.font_size') }}">調整字體</a>
```

改成：

```html
        <a href="{{ url_for('auth.settings') }}">⚙️ 設定</a>
```

- [ ] **Step 2: 改手機 nav（第 110 行）**

同樣把 `.nav-dropdown` 裡那行：

```html
        <a href="{{ url_for('auth.font_size') }}">調整字體</a>
```

改成：

```html
        <a href="{{ url_for('auth.settings') }}">⚙️ 設定</a>
```

- [ ] **Step 3: 確認沒有殘留舊連結**

Run: `cd app_unified && grep -rn "auth.font_size\|調整字體" templates/`
Expected: 只剩 `templates/auth/font_size.html` 本身（Task 4 會刪），其他 template 都沒有了。

- [ ] **Step 4: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/base.html
git commit -m "feat(auth): nav 調整字體 -> 設定 entry"
```

---

## Task 4: 移除舊 `/auth/font-size` 路由 + template

**Files:**
- Modify: `auth/routes.py`（刪 `font_size` route，約第 265–268 行）
- Delete: `templates/auth/font_size.html`

- [ ] **Step 1: 刪 `font_size` route**

在 `auth/routes.py` 刪除這段：

```python
@auth_bp.route("/font-size")
@login_required
def font_size():
    return render_template("auth/font_size.html")
```

- [ ] **Step 2: 刪 template 檔**

Run: `cd app_unified && git rm templates/auth/font_size.html`

- [ ] **Step 3: 確認全庫無殘留 `font_size` 參照**

Run: `cd app_unified && grep -rn "font_size\b\|font-size\"" templates/ auth/ | grep -v "font_size_pref\|font-size:" `
Expected: 空（除了 `base.html` 的 `font_size_pref` localStorage key 與 CSS `font-size:` 屬性，這些是字體套用本身、保留）。

- [ ] **Step 4: 跑全測試套件**

Run: `cd app_unified && ../venv/bin/python -m pytest -q`
Expected: PASS（原 548 + 1 skipped，加本案新測試；無 BuildError／TemplateNotFound）。

- [ ] **Step 5: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add -A app_unified/auth/routes.py
git commit -m "refactor(auth): remove standalone /auth/font-size (merged into settings)"
```

---

## Task 5: 本機實測 + 收尾

**Files:** 無（驗證）

- [ ] **Step 1: 起本機**

Run: `cd app_unified && bash scripts/run_local.sh`（背景）。

- [ ] **Step 2: 手動驗證清單**（登入 alice 後）
  - 右上角（桌機 + 手機漢堡）看到「⚙️ 設定」，點進 `/auth/settings`
  - 修改 PIN：目前 PIN 錯 → 紅字「目前 PIN 不正確」；新≠確認 → 「不一致」；弱 PIN（1234）→「過於簡單」；正確 → 綠字「✓ PIN 已更新」，欄位清空
  - 用新 PIN 重新登入成功（PIN + 人臉）
  - 字體：點小/中/大即時變、reload 後保持、跨頁保持
  - 直接開舊網址 `/auth/font-size` → 404（已移除）

- [ ] **Step 3: 關 server**

Run: `cd app_unified && fuser -k 5000/tcp`

> 部署備註：**無新增 DB 欄位**（不需 inline schema repair）、**無新增 static js/css**（settings 樣式/腳本 inline，不需 bump SW）。Push / merge 依使用者指示，遵守尖峰時段禁推規則。

---

## Self-Review

**Spec coverage：**
- 路由 `GET /auth/settings`、`POST /auth/change-pin`、刪 `/auth/font-size` → Task 1 / Task 4 ✓
- change-pin 6 步驗證（current / 4 位 / 一致 / 不等舊 / 弱 PIN / 存+log）→ Task 1 Step 4 + 測試涵蓋 ✓
- rate limit 10/min + 不記 PIN 值 → Task 1 Step 4 ✓
- 設定頁兩區（PIN 表單 + 字體 picker，inline CSS/JS）→ Task 2 ✓
- nav 兩處改「⚙️ 設定」→ Task 3 ✓
- 不新增 DB 欄位 / 不新增 static → Task 5 備註 ✓
- 測試 7 案例（成功/current 錯/不一致/同舊/弱/非4位/未登入）+ render smoke → Task 1 + Task 2 ✓

**Placeholder scan：** 無 TBD/TODO；每個 code step 都有完整程式碼。

**Type consistency：** `_is_weak_pin` / `settings` / `change_pin` 名稱前後一致；template id（`cp-current`/`cp-new`/`cp-confirm`/`cp-save`/`cp-msg`）與測試斷言（`id="cp-current"`）、JS 取用一致；`data-size` 與字體 JS 一致；`UserLog` 欄位（operator_id/name、target_id/name、action、detail）與 models 定義一致。
