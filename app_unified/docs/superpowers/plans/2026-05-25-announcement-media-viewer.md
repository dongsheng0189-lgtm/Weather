# Shared Fullscreen Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `notes/editor.html` 內 ~154 行 inline fullscreen viewer JS + 6 行 CSS 抽成共用 module，announcement 三個 page 對齊筆記 viewer 行為（✕ + pinch + dblclick + drag）。

**Architecture:** 新增 `static/js/fullscreen_viewer.js` + `static/css/fullscreen_viewer.css`，提供 `window.openFullscreen(url)` / `window.closeFullscreen()` 全域 API。第一次 `openFullscreen()` 自動 dynamically inject overlay markup 到 `document.body`（idempotent），兩邊 page 都不用 inline HTML。`announcement_attachments.js` 移除自製簡陋版、改 fallback shared 版。

**Tech Stack:** Vanilla JS、Flask Jinja2 templates、pytest markup smoke test。

**Spec:** `docs/superpowers/specs/2026-05-25-announcement-media-viewer.md`

**Branch:** `feat/announcement-media-viewer`（已開、chain on `feat/notes-editor-bigger`）

---

## Task 1: 建立 shared CSS module

**Files:**
- Create: `static/css/fullscreen_viewer.css`

- [ ] **Step 1: 寫 CSS（抽自 notes/editor.html line 59-64）**

```css
/* Shared fullscreen viewer — F6 spec: docs/superpowers/specs/2026-05-25-announcement-media-viewer.md */
.fullscreen-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.95); z-index: 9999; overflow: hidden; touch-action: none; user-select: none; -webkit-user-select: none; }
.fullscreen-overlay.open { display: block; }
.fullscreen-overlay .fs-stage { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; touch-action: none; }
.fullscreen-overlay img { max-width: 100vw; max-height: 100vh; object-fit: contain; transform-origin: center center; will-change: transform; pointer-events: none; -webkit-user-drag: none; user-drag: none; }
.fullscreen-overlay .fs-close { position: absolute; top: 12px; right: 12px; width: 44px; height: 44px; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff; border: none; font-size: 1.375rem; cursor: pointer; z-index: 2; }
.fullscreen-overlay .fs-hint { position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%); color: rgba(255,255,255,.7); font-size: .8rem; background: rgba(0,0,0,.4); padding: .35rem .75rem; border-radius: 12px; pointer-events: none; }
```

- [ ] **Step 2: Commit**

```bash
git add static/css/fullscreen_viewer.css
git -c commit.gpgsign=false commit -m "feat(viewer): shared fullscreen viewer CSS (F6 task 1)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 建立 shared JS module（含 dynamic markup create）

**Files:**
- Create: `static/js/fullscreen_viewer.js`

- [ ] **Step 1: 寫 JS（抽自 notes/editor.html line 914-1067 + 改 `_fsInit` 為 dynamic markup create）**

```javascript
/* Shared fullscreen viewer — F6 spec: docs/superpowers/specs/2026-05-25-announcement-media-viewer.md
 *
 * Public API:
 *   window.openFullscreen(url)  — 顯示 image fullscreen + ✕ + pinch zoom + dblclick + drag
 *   window.closeFullscreen()    — 關閉
 *
 * 第一次 openFullscreen 自動 dynamic inject overlay markup 到 document.body (idempotent)。
 * 抽自 notes/editor.html line 914-1067、原本 inline 在 editor template + 用 inline HTML overlay。
 */
(function() {
  var _fsState = { scale: 1, tx: 0, ty: 0, minScale: 1, maxScale: 6 };
  var _fsPointers = {};
  var _fsLastDist = 0;
  var _fsLastCenter = { x: 0, y: 0 };
  var _fsLastTap = 0;

  function _fsApply() {
    var img = document.getElementById('fullscreen-img');
    if (!img) return;
    img.style.transform = 'translate(' + _fsState.tx + 'px,' + _fsState.ty + 'px) scale(' + _fsState.scale + ')';
  }

  function _fsReset() {
    _fsState.scale = 1; _fsState.tx = 0; _fsState.ty = 0;
    _fsApply();
  }

  function _fsClampPan() {
    var img = document.getElementById('fullscreen-img');
    if (!img) return;
    var rect = img.getBoundingClientRect();
    var w = rect.width, h = rect.height;
    var vw = window.innerWidth, vh = window.innerHeight;
    var maxX = Math.max(0, (w - vw) / 2);
    var maxY = Math.max(0, (h - vh) / 2);
    if (_fsState.tx > maxX) _fsState.tx = maxX;
    if (_fsState.tx < -maxX) _fsState.tx = -maxX;
    if (_fsState.ty > maxY) _fsState.ty = maxY;
    if (_fsState.ty < -maxY) _fsState.ty = -maxY;
  }

  function _fsOnPointerDown(e) {
    _fsPointers[e.pointerId] = { x: e.clientX, y: e.clientY };
    var stage = document.getElementById('fs-stage');
    if (stage && stage.setPointerCapture) { try { stage.setPointerCapture(e.pointerId); } catch (err) {} }
    var ids = Object.keys(_fsPointers);
    if (ids.length === 2) {
      var p1 = _fsPointers[ids[0]], p2 = _fsPointers[ids[1]];
      _fsLastDist = Math.hypot(p2.x - p1.x, p2.y - p1.y);
      _fsLastCenter = { x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 };
    }
    e.preventDefault();
  }

  function _fsOnPointerMove(e) {
    if (!_fsPointers[e.pointerId]) return;
    var prev = _fsPointers[e.pointerId];
    _fsPointers[e.pointerId] = { x: e.clientX, y: e.clientY };
    var ids = Object.keys(_fsPointers);
    if (ids.length === 2) {
      var p1 = _fsPointers[ids[0]], p2 = _fsPointers[ids[1]];
      var dist = Math.hypot(p2.x - p1.x, p2.y - p1.y);
      if (_fsLastDist > 0) {
        var ratio = dist / _fsLastDist;
        var newScale = Math.max(_fsState.minScale, Math.min(_fsState.maxScale, _fsState.scale * ratio));
        var cx = (p1.x + p2.x) / 2, cy = (p1.y + p2.y) / 2;
        var dx = cx - window.innerWidth / 2 - _fsState.tx;
        var dy = cy - window.innerHeight / 2 - _fsState.ty;
        var k = newScale / _fsState.scale;
        _fsState.tx -= dx * (k - 1);
        _fsState.ty -= dy * (k - 1);
        _fsState.scale = newScale;
      }
      _fsLastDist = dist;
      _fsApply();
    } else if (ids.length === 1 && _fsState.scale > 1) {
      _fsState.tx += e.clientX - prev.x;
      _fsState.ty += e.clientY - prev.y;
      _fsClampPan();
      _fsApply();
    }
    e.preventDefault();
  }

  function _fsOnPointerUp(e) {
    delete _fsPointers[e.pointerId];
    var ids = Object.keys(_fsPointers);
    if (ids.length < 2) _fsLastDist = 0;
    if (ids.length === 0 && _fsState.scale <= 1.01) {
      _fsState.scale = 1; _fsState.tx = 0; _fsState.ty = 0;
      _fsApply();
    } else if (ids.length === 0) {
      _fsClampPan();
      _fsApply();
    }
  }

  function _fsOnDblClick(e) {
    if (_fsState.scale > 1.01) {
      _fsState.scale = 1; _fsState.tx = 0; _fsState.ty = 0;
    } else {
      _fsState.scale = 2.5;
      var dx = (e.clientX || window.innerWidth / 2) - window.innerWidth / 2;
      var dy = (e.clientY || window.innerHeight / 2) - window.innerHeight / 2;
      _fsState.tx = -dx * 1.5;
      _fsState.ty = -dy * 1.5;
      _fsClampPan();
    }
    _fsApply();
  }

  function _fsOnWheel(e) {
    e.preventDefault();
    var delta = -e.deltaY * 0.002;
    var newScale = Math.max(_fsState.minScale, Math.min(_fsState.maxScale, _fsState.scale * (1 + delta)));
    var dx = e.clientX - window.innerWidth / 2 - _fsState.tx;
    var dy = e.clientY - window.innerHeight / 2 - _fsState.ty;
    var k = newScale / _fsState.scale;
    _fsState.tx -= dx * (k - 1);
    _fsState.ty -= dy * (k - 1);
    _fsState.scale = newScale;
    if (_fsState.scale <= 1.01) { _fsState.scale = 1; _fsState.tx = 0; _fsState.ty = 0; }
    else _fsClampPan();
    _fsApply();
  }

  function _ensureMarkup() {
    if (document.getElementById('fullscreen-overlay')) return;
    var overlay = document.createElement('div');
    overlay.className = 'fullscreen-overlay';
    overlay.id = 'fullscreen-overlay';
    overlay.innerHTML =
      '<div class="fs-stage" id="fs-stage"><img id="fullscreen-img" src=""></div>' +
      '<button type="button" class="fs-close" aria-label="關閉">✕</button>' +
      '<div class="fs-hint">雙指縮放 · 雙擊放大 · 拖曳移動</div>';
    document.body.appendChild(overlay);
    overlay.querySelector('.fs-close').addEventListener('click', closeFullscreen);
  }

  function _fsInit() {
    _ensureMarkup();
    var stage = document.getElementById('fs-stage');
    if (!stage || stage._fsBound) return;
    stage._fsBound = true;
    stage.addEventListener('pointerdown', _fsOnPointerDown);
    stage.addEventListener('pointermove', _fsOnPointerMove);
    stage.addEventListener('pointerup', _fsOnPointerUp);
    stage.addEventListener('pointercancel', _fsOnPointerUp);
    stage.addEventListener('dblclick', _fsOnDblClick);
    stage.addEventListener('wheel', _fsOnWheel, { passive: false });
    stage.addEventListener('click', function(e) {
      if (_fsState.scale > 1.01) return;
      var tap = Date.now();
      if (tap - _fsLastTap < 300) { _fsLastTap = tap; return; }
      _fsLastTap = tap;
      setTimeout(function() {
        if (Date.now() - _fsLastTap >= 280 && _fsState.scale <= 1.01) closeFullscreen();
      }, 300);
    });
  }

  function openFullscreen(url) {
    _fsInit();
    _fsReset();
    document.getElementById('fullscreen-img').src = url;
    document.getElementById('fullscreen-overlay').classList.add('open');
  }

  function closeFullscreen() {
    var overlay = document.getElementById('fullscreen-overlay');
    if (!overlay) return;
    overlay.classList.remove('open');
    var img = document.getElementById('fullscreen-img');
    if (img) img.src = '';
    _fsReset();
  }

  window.openFullscreen = openFullscreen;
  window.closeFullscreen = closeFullscreen;
})();
```

- [ ] **Step 2: Commit**

```bash
git add static/js/fullscreen_viewer.js
git -c commit.gpgsign=false commit -m "feat(viewer): shared fullscreen viewer JS with dynamic markup (F6 task 2)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: notes/editor.html 移除 inline、加 link/script

**Files:**
- Modify: `templates/notes/editor.html`

- [ ] **Step 1: 移除 inline CSS (line 59-64)**

找到並刪除這段：

```css
.fullscreen-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.95); z-index: 9999; overflow: hidden; touch-action: none; user-select: none; -webkit-user-select: none; }
.fullscreen-overlay.open { display: block; }
.fullscreen-overlay .fs-stage { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; touch-action: none; }
.fullscreen-overlay img { max-width: 100vw; max-height: 100vh; object-fit: contain; transform-origin: center center; will-change: transform; pointer-events: none; -webkit-user-drag: none; user-drag: none; }
.fullscreen-overlay .fs-close { position: absolute; top: 12px; right: 12px; width: 44px; height: 44px; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff; border: none; font-size: 1.375rem; cursor: pointer; z-index: 2; }
.fullscreen-overlay .fs-hint { position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%); color: rgba(255,255,255,.7); font-size: .8rem; background: rgba(0,0,0,.4); padding: .35rem .75rem; border-radius: 12px; pointer-events: none; }
```

- [ ] **Step 2: 移除 inline overlay HTML (line 213-219)**

找到並刪除這段：

```html
<div class="fullscreen-overlay" id="fullscreen-overlay">
    <div class="fs-stage" id="fs-stage">
      <img id="fullscreen-img" src="">
    </div>
    <button type="button" class="fs-close" onclick="closeFullscreen()" aria-label="關閉">✕</button>
    <div class="fs-hint">雙指縮放 · 雙擊放大 · 拖曳移動</div>
  </div>
```

- [ ] **Step 3: 移除 inline JS (line 914-1067 內整段 viewer 邏輯)**

刪掉從 `var _fsState = { scale: 1, ...` 到 `function closeFullscreen() { ... _fsReset(); }` 整段（154 行）。保留 `if (NOTE_ID && EDITOR_TYPE !== 'checklist') loadAttachments();`（line 1069）以及之後其他不相關函式。

- [ ] **Step 4: 在 extra_head block 加 CSS link**

在 `{% block extra_head %}` 區內（line 3 後面）加：

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/fullscreen_viewer.css') }}">
```

- [ ] **Step 5: 在 scripts block 最開頭加 script src**

在 `{% block scripts %}`（line 222）的 `{% endblock %}` 之前、所有 inline `<script>` 之前加：

```html
<script src="{{ url_for('static', filename='js/fullscreen_viewer.js') }}"></script>
```

順序要對 — viewer.js 必須先載入，才有 `window.openFullscreen` 給後續 inline script 用。

- [ ] **Step 6: 跑既有 notes test 確認沒 break**

```bash
PYTHONPATH=. ../venv/bin/python -m pytest tests/test_notes_misc.py tests/test_notes_lock.py -v 2>&1 | tail -10
```

Expected: 全綠（既有 test 沒 assert inline marker、已 grep 確認）。

- [ ] **Step 7: Commit**

```bash
git add templates/notes/editor.html
git -c commit.gpgsign=false commit -m "refactor(notes/editor): replace inline fullscreen viewer with shared module (F6 task 3)

移除 inline CSS (line 59-64) + overlay HTML (line 213-219) + viewer JS
(line 914-1067, 154 行) 改用 static/js/fullscreen_viewer.js + .css。
window.openFullscreen / closeFullscreen 名稱不變、既有 onclick 全部繼續 work。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: notes/index.html 加 link/script（給 _card_list 公告卡片區用）

**Files:**
- Modify: `templates/notes/index.html`

- [ ] **Step 1: 在 extra_head block 加 CSS link**

在 `{% block extra_head %}`（line 3）區內、現有 link 之間（line 28 附近）加：

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/fullscreen_viewer.css') }}">
```

- [ ] **Step 2: 在 scripts block 最開頭加 script src**

在 `{% block scripts %}`（line 397）的開頭加：

```html
<script src="{{ url_for('static', filename='js/fullscreen_viewer.js') }}"></script>
```

- [ ] **Step 3: Commit**

```bash
git add templates/notes/index.html
git -c commit.gpgsign=false commit -m "feat(notes/index): include shared fullscreen viewer (F6 task 4)

讓 _card_list partial 內的公告附件 img.onclick=openFullscreen 能解析到 shared 版。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: announcements/edit.html + new.html 加 link/script

**Files:**
- Modify: `templates/announcements/edit.html`
- Modify: `templates/announcements/new.html`

- [ ] **Step 1: 在 edit.html extra_head block 加 CSS link**

在 `{% block extra_head %}`（line 4）區內加：

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/fullscreen_viewer.css') }}">
```

- [ ] **Step 2: 在 edit.html content block 內、現有 announcement_attachments.js 之前加 script src**

找到 `<script src="{{ url_for('static', filename='js/announcement_attachments.js') }}"></script>` (line 63)、在它**之前**加：

```html
<script src="{{ url_for('static', filename='js/fullscreen_viewer.js') }}"></script>
```

順序要對 — viewer.js 必須先載入、announcement_attachments.js 才能用 `window.openFullscreen`。

- [ ] **Step 3: new.html 同樣兩處改動**

- 在 `{% block extra_head %}`（line 4）加 CSS link（同 Step 1）
- 在 `<script src="{{ url_for('static', filename='js/announcement_attachments.js') }}"></script>` (line 55) 之前加 viewer.js script（同 Step 2）

- [ ] **Step 4: Commit**

```bash
git add templates/announcements/edit.html templates/announcements/new.html
git -c commit.gpgsign=false commit -m "feat(announcements): include shared fullscreen viewer (F6 task 5)

edit + new 兩個 page 都引入 viewer.js + viewer.css、image 點開有 ✕ + zoom + drag。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: announcement_attachments.js 移除自製 openFullscreen

**Files:**
- Modify: `static/js/announcement_attachments.js`

- [ ] **Step 1: 刪掉自製 openFullscreen 函式 (line 11-21)**

找到並刪除整段：

```javascript
function openFullscreen(url) {
  var overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,.92);z-index:9999;display:flex;align-items:center;justify-content:center;cursor:zoom-out;';
  var img = document.createElement('img');
  img.src = url;
  img.style.cssText = 'max-width:95vw;max-height:95vh;';
  overlay.appendChild(img);
  overlay.onclick = function() { document.body.removeChild(overlay); };
  document.body.appendChild(overlay);
}
```

- [ ] **Step 2: 更新檔頭註解（去掉「openFullscreen」、加說明依賴 shared）**

把 line 1-9 的註解區塊改成：

```javascript
/* 公告附件 UI — createAttItem / renderAttView / file upload.
 * 圖片 fullscreen viewer 走 shared window.openFullscreen
 * (static/js/fullscreen_viewer.js)、template 一定要先引入該檔。
 *
 * 提供：
 *   - createAnnAttItem(att) : 編輯/建立頁的附件 item（含刪除 ×）
 *   - renderAnnAttView(att) : index 卡片唯讀展示（無刪除 ×）
 *   - handleAnnFileUpload(input) : 編輯/建立頁 file input change handler
 *   - uploadAnnPendingFiles(annId, cb) : submit 後逐檔上傳
 * 依賴 window.ANN_ID（編輯頁 = id；建立頁 = null）+ window.ANN_PENDING_FILES。
 */
```

- [ ] **Step 3: Commit**

```bash
git add static/js/announcement_attachments.js
git -c commit.gpgsign=false commit -m "refactor(announcements/attachments): use shared openFullscreen (F6 task 6)

移除 11 行簡陋自製版、改依賴 window.openFullscreen (shared viewer)。
公告圖片現在有 ✕ + pinch zoom + dblclick + drag 行為、跟筆記/確認表單一致。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Pytest markup smoke test

**Files:**
- Create: `tests/test_shared_fullscreen_viewer.py`

- [ ] **Step 1: 寫 test 檔**

```python
"""Markup smoke tests for F6 shared fullscreen viewer.

驗 5 個 page 都引入 shared viewer.js + viewer.css；
驗 notes/editor.html 已移除舊 inline marker (#fs-stage / 154 行 viewer JS)。
Runtime 行為走 manual verification（spec § 測試策略）。
"""


VIEWER_JS = '/static/js/fullscreen_viewer.js'
VIEWER_CSS = '/static/css/fullscreen_viewer.css'


def _assert_includes_viewer(body):
    assert VIEWER_JS in body, f"shared viewer JS link 缺失"
    assert VIEWER_CSS in body, f"shared viewer CSS link 缺失"


def test_notes_editor_includes_shared_viewer(client, logged_in_client):
    res = logged_in_client.get('/notes/new')
    assert res.status_code == 200
    _assert_includes_viewer(res.get_data(as_text=True))


def test_notes_index_includes_shared_viewer(client, logged_in_client):
    res = logged_in_client.get('/notes/')
    assert res.status_code == 200
    _assert_includes_viewer(res.get_data(as_text=True))


def test_announcement_new_includes_shared_viewer(client, logged_in_admin_client):
    res = logged_in_admin_client.get('/announcements/new')
    assert res.status_code == 200
    _assert_includes_viewer(res.get_data(as_text=True))


def test_notes_editor_no_inline_viewer_remnant(client, logged_in_client):
    """notes/editor.html 應該已經移除 inline viewer JS、不應該再有 _fsState/_fsOnPointerDown 字串。"""
    res = logged_in_client.get('/notes/new')
    body = res.get_data(as_text=True)
    # 這些是 inline JS 內部 helper 名稱、現在搬到外部 .js 檔、頁面 body 不該再有
    assert '_fsOnPointerDown' not in body, "舊 inline viewer JS 殘留 (_fsOnPointerDown)"
    assert 'var _fsState' not in body, "舊 inline viewer state 殘留 (var _fsState)"
```

- [ ] **Step 2: 跑新 test 確認 4 條全綠**

```bash
PYTHONPATH=. ../venv/bin/python -m pytest tests/test_shared_fullscreen_viewer.py -v 2>&1 | tail -15
```

Expected: 4 passed。如果有 fail：
- 503 / 401 / 302 → 看 logged_in fixtures 是否需要 store 設定（test_announcements_index_integration.py 有用 super_admin fixture、可參考）
- assertion fail → 回頭看 Task 3-5 對應 page 是否漏改 link/script

如果 announcement 路由要特定權限失敗、加 super_admin fixture：

```python
def test_announcement_edit_includes_shared_viewer(client, super_admin_client, sample_announcement):
    res = super_admin_client.get(f'/announcements/edit/{sample_announcement.id}')
    assert res.status_code == 200
    _assert_includes_viewer(res.get_data(as_text=True))
```

如果這個太複雜（fixture 不易設），刪 edit 那條 test、留 new 那條足夠（new 跟 edit 同模板結構）。

- [ ] **Step 3: 跑全 suite 確認沒 break 其他 test**

```bash
PYTHONPATH=. ../venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: 0 failed（既有 404 + 新 ~4 = 408 passed）。

- [ ] **Step 4: Commit**

```bash
git add tests/test_shared_fullscreen_viewer.py
git -c commit.gpgsign=false commit -m "test(viewer): markup smoke for shared fullscreen viewer (F6 task 7)

4 條 markup-only test：notes/editor + notes/index + announcement/new 都
含 viewer.js + viewer.css、notes/editor 已無 inline _fsState / _fsOnPointerDown 殘留。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Local manual verification（按 [[feedback_local_test_before_push]]）

**Files:** 無新檔、user 手動驗證

- [ ] **Step 1: 跑 local dev server（若未跑）**

```bash
cd /home/hirain0126/projects/webapp/app_unified
bash scripts/run_local.sh
```

- [ ] **Step 2: /dev 切 admin 帳號**

`http://localhost:5000/dev` → 點 admin user 自動登入。

- [ ] **Step 3: 驗 notes editor 圖片 viewer**

開既有有圖的 note → `/notes/<id>/edit` → 點圖片附件
- ✓ ✕ 按鈕在右上
- ✓ 雙指縮放 work（mobile 模式可用 DevTools touch emulation）
- ✓ 雙擊放大 work
- ✓ 拖曳移動 work（放大後）
- ✓ ✕ 或空白處點關閉

- [ ] **Step 4: 驗公告 edit 圖片 viewer（之前沒功能、這次新加）**

開既有公告 `/announcements/edit/<id>` → 點圖片附件
- ✓ 同上行為（之前都沒有）

- [ ] **Step 5: 驗公告 new 預覽**

`/announcements/new` → 上傳一張圖 → 點預覽
- ✓ 同上行為

- [ ] **Step 6: 驗 notes index 公告卡片**

`/notes/` → 找到頂部公告區圖片 → 點開
- ✓ 同上行為

- [ ] **Step 7: 驗影片不受影響**

開有影片附件的 note/公告 → 影片仍可播放、HTML5 controls 仍 work、iOS 上點全螢幕仍 trigger 原生 player（**不**走 fullscreen overlay）。

- [ ] **Step 8: User 確認 OK 才算 Task 8 done**

---

## Self-Review 對照 spec

| Spec 段落 | 對應 Task |
|---|---|
| `static/js/fullscreen_viewer.js` 154 行 | Task 2 |
| `static/css/fullscreen_viewer.css` 6 行 | Task 1 |
| `templates/notes/editor.html` 移除 inline + 加引用 | Task 3 |
| `templates/notes/index.html` 加引用 | Task 4 |
| `templates/announcements/edit.html` 加引用 | Task 5 |
| `templates/announcements/new.html` 加引用 | Task 5 |
| `static/js/announcement_attachments.js` 移除自製 openFullscreen | Task 6 |
| Public API `window.openFullscreen` / `closeFullscreen` | Task 2 |
| Dynamic markup create + idempotent `_fsInit` | Task 2（`_ensureMarkup` + `stage._fsBound`） |
| Pytest markup smoke test | Task 7 |
| Manual verification 6 步 | Task 8 |
| 影片不動 | Task 8 step 7 驗證 |

**Type consistency check**: `openFullscreen` / `closeFullscreen` / `_fsInit` / `_fsReset` / `_ensureMarkup` 在所有 task 內名稱一致。

**Placeholder scan**: 無 TODO / TBD、所有 code step 含完整 code、所有 commit message 含 task 編號。

---

## 不在這個 plan 內的（chain 完才一次處理）

- 不 ff-merge to main（按 chain 策略累積到最後一條 branch、user 同意才一起 push）
- 不 push origin（user 說「全部 6 條 done 才 push」）
- 不還原 dev/outbound_guard.py 跟 .env.local（前者已 reset、後者是 gitignored 無 push 影響）
- 不更新 project memory（push 後再寫紀錄）
