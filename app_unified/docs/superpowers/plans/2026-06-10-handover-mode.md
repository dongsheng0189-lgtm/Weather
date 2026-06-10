# 交接模式 + 快速鎖 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** admin 以上可一鍵開「交接模式」，把 session idle 上限從 5 分鐘延長到 15 分鐘（純滑動），並提供「關閉模式並登出」與「結束交接（保持登入）」兩顆按鈕，兼顧交接便利與資料安全。

**Architecture:** 沿用既有 `_enforce_session_idle` 的 session-grace 架構，新增一個 `_handover` session flag（admin-gated endpoint 設定）→ idle 取 `max(現有, 15min)`；context processor 把 `handover_active` 傳給 template；`base.html` 依此顯示頂部 banner、nav 啟用鈕、並把前端 `window.IDLE_LIMIT_MS` 連動到 15 分鐘。

**Tech Stack:** Flask（session / before_request / context_processor）+ Flask-Login + Jinja2 + vanilla JS + pytest。

**Spec:** `docs/superpowers/specs/2026-06-10-handover-mode-design.md`

---

## File Structure

| 檔案 | 動作 | 責任 |
|------|------|------|
| `app.py` | Modify | `_enforce_session_idle` 加 `_handover` → 15min；新增 `inject_handover` context processor |
| `auth/routes.py` | Modify | 新增 `POST /auth/handover/start`（admin）+ `POST /auth/handover/end` |
| `templates/base.html` | Modify | 頂部 banner（含 alert-circle SVG）+ nav 啟用鈕（admin+，未開時）+ startHandover/endHandover JS + IDLE_LIMIT 連動 |
| `tests/test_handover_mode.py` | Create | 後端 endpoint 權限 + idle 延長行為 + base.html wiring |

---

## Task 1: 後端 — handover flag + endpoints + idle 延長

**Files:**
- Modify: `app.py`（`_enforce_session_idle` ~第 489-490 行；context processor ~第 113 行後）
- Modify: `auth/routes.py`（`change_pin` route 之後、`def _verify_face` 之前，~第 313 行）
- Test: `tests/test_handover_mode.py`（新檔）

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_handover_mode.py`：

```python
"""Tests for handover mode（admin-gated 15min sliding idle extension）。"""
import time
import uuid

from extensions import db
from models import User, Store


def _make_user(role="user", pin="1357"):
    if not Store.query.filter_by(name="A").first():
        db.session.add(Store(name="A"))
        db.session.commit()
    u = User(username=f"u-{uuid.uuid4().hex[:8]}", store="A", role=role)
    u.set_password(pin)
    u.set_features(["notes"])
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, uid):
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["_last_request_at"] = int(time.time())


def test_admin_can_start_handover(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(role="admin").id
    _login(client, uid)
    r = client.post("/auth/handover/start")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"
    with client.session_transaction() as s:
        assert s.get("_handover") is True


def test_regular_user_cannot_start_handover(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(role="user").id
    _login(client, uid)
    r = client.post("/auth/handover/start")
    assert r.status_code == 403
    with client.session_transaction() as s:
        assert not s.get("_handover")


def test_start_requires_login(app):
    client = app.test_client()
    r = client.post("/auth/handover/start")
    assert r.status_code in (401, 302)


def test_end_clears_handover(app):
    client = app.test_client()
    with app.app_context():
        uid = _make_user(role="admin").id
    _login(client, uid)
    client.post("/auth/handover/start")
    r = client.post("/auth/handover/end")
    assert r.status_code == 200
    with client.session_transaction() as s:
        assert not s.get("_handover")


def test_handover_extends_idle_to_15min(app):
    """handover ON + 距上次活動 6 分鐘（>5、<15）→ 不踢。"""
    client = app.test_client()
    with app.app_context():
        uid = _make_user(role="admin").id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["_handover"] = True
        s["_last_request_at"] = int(time.time()) - 6 * 60
    r = client.get("/auth/ping")
    assert r.status_code == 200  # 仍登入


def test_handover_still_locks_after_15min(app):
    """handover ON + 距上次活動 16 分鐘（>15）→ 踢。"""
    client = app.test_client()
    with app.app_context():
        uid = _make_user(role="admin").id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["_handover"] = True
        s["_last_request_at"] = int(time.time()) - 16 * 60
    r = client.get("/auth/ping")
    assert r.status_code == 401  # 被踢


def test_without_handover_locks_at_5min(app):
    """對照組：無 handover + 6 分鐘（>5）→ 踢。"""
    client = app.test_client()
    with app.app_context():
        uid = _make_user(role="admin").id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["_last_request_at"] = int(time.time()) - 6 * 60
    r = client.get("/auth/ping")
    assert r.status_code == 401
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_handover_mode.py -q`
Expected: 多數 fail（endpoint 404 / handover idle 未延長 → 6 分鐘被踢）。

- [ ] **Step 3: `_enforce_session_idle` 加 handover 延長**

在 `app.py` 的 `_enforce_session_idle` 裡，找到：

```python
        else:
            max_idle = 5 * 60
        last = session.get("_last_request_at")
```

改成（插入 handover 判斷）：

```python
        else:
            max_idle = 5 * 60
        if session.get("_handover"):
            max_idle = max(max_idle, 15 * 60)   # 交接模式：15 分鐘純滑動
        last = session.get("_last_request_at")
```

- [ ] **Step 4: 新增 context processor**

在 `app.py` 的 `inject_features` context processor（`return {...}` 結束的 `}` 那行）之後，加一個新的 context processor：

```python
    @app.context_processor
    def inject_handover():
        from flask import session
        return {"handover_active": bool(session.get("_handover"))}
```

- [ ] **Step 5: 新增 handover endpoints**

在 `auth/routes.py` 的 `change_pin` 函式之後、`def _verify_face` 之前，加入：

```python
@auth_bp.route("/handover/start", methods=["POST"])
@login_required
def handover_start():
    """admin 以上開啟交接模式：session idle 延長為 15 分鐘（純滑動）。"""
    if not current_user.is_admin():
        return jsonify({"status": "error", "message": "需要管理員權限"}), 403
    import time
    session["_handover"] = True
    session["_last_request_at"] = int(time.time())
    session.permanent = True
    return jsonify({"status": "ok"})


@auth_bp.route("/handover/end", methods=["POST"])
@login_required
def handover_end():
    """結束交接模式（保持登入）：idle 回到預設規則。"""
    import time
    session.pop("_handover", None)
    session["_last_request_at"] = int(time.time())
    return jsonify({"status": "ok"})
```

- [ ] **Step 6: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_handover_mode.py -q`
Expected: 7 passed。

- [ ] **Step 7: 防回歸——跑既有 auth/session 相關測試**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_auth.py tests/test_auth_change_pin.py tests/test_announcements_idle_keepalive.py -q`
Expected: 全 pass（確認沒動壞既有 idle / auth）。

- [ ] **Step 8: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/app.py app_unified/auth/routes.py app_unified/tests/test_handover_mode.py
git commit -m "feat(auth): handover mode backend — admin-gated 15min idle extension + endpoints"
```

---

## Task 2: 前端 — banner + nav 鈕 + JS + idle 連動

**Files:**
- Modify: `templates/base.html`（nav admin 區塊 ~第 87-90 + 106-109 行；`<div class="container">` ~第 118 行前；`{% block scripts %}{% endblock %}` ~第 127 行後）
- Test: `tests/test_handover_mode.py`（加 base.html wiring 測試）

- [ ] **Step 1: 加 base.html wiring 測試**

在 `tests/test_handover_mode.py` 末尾加：

```python
from pathlib import Path

_BASE = Path(__file__).parent.parent / 'templates' / 'base.html'


def test_base_has_handover_banner():
    src = _BASE.read_text(encoding='utf-8')
    assert 'id="handover-banner"' in src
    assert 'handover_active' in src               # banner 受 handover_active 控制


def test_base_nav_has_handover_toggle_and_js():
    src = _BASE.read_text(encoding='utf-8')
    assert 'startHandover()' in src
    assert 'function startHandover()' in src
    assert 'function endHandover()' in src


def test_base_wires_endpoints_and_idle_extension():
    src = _BASE.read_text(encoding='utf-8')
    assert '/auth/handover/start' in src
    assert '/auth/handover/end' in src
    assert "window.location.href='/auth/logout'" in src   # 關閉模式並登出
    assert 'IDLE_LIMIT_MS = 15 * 60 * 1000' in src         # 前端 idle 連動延長
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_handover_mode.py -q`
Expected: 新加的 3 條 fail。

- [ ] **Step 3: nav 加「交接模式」啟用鈕（admin+，未開時顯示）**

`base.html` 有兩處**完全相同**的 admin 區塊（桌機 `.nav-links` + 手機 `.nav-dropdown`）。用 replace-all 把：

```html
        {% if current_user.is_admin() %}
          <a href="{{ url_for('admin.dashboard') }}">管理</a>
          <a href="{{ url_for('face.settings') }}">人臉設定</a>
        {% endif %}
```

兩處都改成：

```html
        {% if current_user.is_admin() %}
          <a href="{{ url_for('admin.dashboard') }}">管理</a>
          <a href="{{ url_for('face.settings') }}">人臉設定</a>
          {% if not handover_active %}<a href="#" onclick="startHandover();return false;">🤝 交接模式</a>{% endif %}
        {% endif %}
```

- [ ] **Step 4: 加頂部 banner（`<div class="container">` 之前）**

把 `base.html` 的：

```html
  <div class="container">
```

改成（在前面插入 banner 區塊）：

```html
  {% if handover_active %}
  <div id="handover-banner" style="background:#fff3cd;border-bottom:2px solid #ffc107;padding:.5rem 1rem;display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;font-size:.9rem;color:#856404;">
    <span>🤝 <strong>交接模式中</strong>（閒置 15 分鐘才鎖）</span>
    <button type="button" onclick="window.location.href='/auth/logout'"
            style="margin-left:auto;background:#e74c3c;color:#fff;border:none;border-radius:8px;padding:.4rem .8rem;font-size:.85rem;cursor:pointer;display:inline-flex;align-items:center;gap:.3rem;min-height:36px;">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>關閉模式並登出</button>
    <button type="button" onclick="endHandover()"
            style="background:#fff;border:1px solid #856404;color:#856404;border-radius:8px;padding:.4rem .8rem;font-size:.85rem;cursor:pointer;min-height:36px;">結束交接（保持登入）</button>
  </div>
  {% endif %}
  <div class="container">
```

- [ ] **Step 5: 加 handover JS + idle 連動（在 `{% block scripts %}{% endblock %}` 之後、主 `<script>` 之前）**

把 `base.html` 的：

```html
  {% block scripts %}{% endblock %}
```

改成（在後面插入一個新的 `<script>`，它會在主 idle 腳本之前執行 → 先設好 IDLE_LIMIT_MS）：

```html
  {% block scripts %}{% endblock %}
  <script>
    {% if handover_active %}window.IDLE_LIMIT_MS = 15 * 60 * 1000;{% endif %}
    function startHandover() {
      if (!confirm('開啟交接模式？閒置上限延長為 15 分鐘，期間請留意手機，可隨時「關閉模式並登出」。')) return;
      fetch('/auth/handover/start', {method:'POST', headers:{'Content-Type':'application/json'}})
        .then(function(r){ if (r.ok) { location.reload(); } else { alert('開啟失敗（需管理員權限）'); } })
        .catch(function(){ alert('網路錯誤，請再試一次'); });
    }
    function endHandover() {
      fetch('/auth/handover/end', {method:'POST', headers:{'Content-Type':'application/json'}})
        .then(function(){ location.reload(); })
        .catch(function(){ location.reload(); });
    }
  </script>
```

- [ ] **Step 6: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_handover_mode.py -q`
Expected: 10 passed（7 後端 + 3 wiring）。

- [ ] **Step 7: 防回歸——跑全套件**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/ -q`
Expected: 全 pass（原 573 + 本案新測試；無 TemplateNotFound / 例外）。

- [ ] **Step 8: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/base.html app_unified/tests/test_handover_mode.py
git commit -m "feat(ui): handover mode banner + nav toggle + quick logout/end (base.html)"
```

---

## Task 3: 本機實測 + 收尾

**Files:** 無（驗證）

- [ ] **Step 1: 起本機**

Run: `cd app_unified && bash scripts/run_local.sh`（背景）。

- [ ] **Step 2: 手動驗證**（`/dev` 登入）
  - **admin（如 admin_north）**：右上 nav 看到「🤝 交接模式」→ 點 → 確認 → 頂部出現黃色 banner「交接模式中」、nav 的「交接模式」消失。
  - **延長生效**：開著筆記頁放 ~6 分鐘不動（或改 `_last_request_at` 測）→ 不被踢；超過 15 分鐘沒動 → 自動蓋黑幕登出。
  - **關閉模式並登出**：點 banner 紅鈕（圈圈驚嘆號）→ 立即登出、回登入頁。
  - **結束交接（保持登入）**：點 banner 灰鈕 → banner 消失、留在登入、idle 回 5 分鐘、nav「交接模式」又出現。
  - **一般 user（如 alice）**：nav **看不到**「交接模式」；直接 POST `/auth/handover/start` → 403（可用 devtools 驗，選測）。

- [ ] **Step 3: 關 server**

Run: `cd app_unified && fuser -k 5000/tcp`

> 部署備註：純 session flag、**無新 DB 欄位**（不需 inline schema repair）、**無新 static**（banner/JS inline 在 base.html）→ **不用 bump SW**。Push / merge 依使用者指示，遵守尖峰時段禁推規則。

---

## Self-Review

**Spec coverage：**
- idle 延長 15min 純滑動（`_handover` → `max(...,15min)`）→ Task 1 Step 3 ✓
- admin-gated 啟用 + endpoints（start 403 非 admin / end）→ Task 1 Step 5 + 測試 ✓
- context processor `handover_active` → Task 1 Step 4 ✓
- nav 啟用鈕（admin+、未開時）兩處 → Task 2 Step 3 ✓
- banner +「關閉模式並登出」(alert-circle SVG, →logout) +「結束交接（保持登入）」(→end) → Task 2 Step 4 ✓
- 前端 IDLE_LIMIT_MS 連動 15min → Task 2 Step 5 ✓
- 15min 沒互動仍鎖（前端黑幕 + 後端 idle）→ 既有機制 + Task1 Step3 max_idle 上限 ✓
- 無新 DB / 無新 static → 範圍正確 ✓

**Placeholder scan：** 無 TBD/TODO；每個 code step 都有完整程式碼或完整 old→new 替換。

**Type consistency：** session key `_handover`、context 變數 `handover_active`、endpoint 路徑 `/auth/handover/start` `/auth/handover/end`、JS `startHandover`/`endHandover`、`window.IDLE_LIMIT_MS = 15 * 60 * 1000` 在 backend / context / template / JS / 測試斷言全一致；`is_admin()`（admin+super_admin）與 spec「admin 以上」一致。
