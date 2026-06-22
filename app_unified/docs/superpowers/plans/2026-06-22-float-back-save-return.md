# 浮動返回鈕「儲存並返回」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 確認表單/筆記編輯器 dirty 時，右下浮動返回鈕變「💾 儲存並返回」，點擊自動存檔成功後才導頁，使用者不必捲回頂部按儲存、也不會丟資料。

**Architecture:** 純前端，全部在 `templates/notes/editor.html` 內嵌 script。`doSave()` 改回傳 Promise；浮動鈕標籤跟 `_dirty` 連動；浮動鈕 click handler dirty 時走「存後導頁」。上方 `#btn-back` 維持原 confirm 不變。

**Tech Stack:** Vanilla JS（內嵌於 Jinja2 template）。測試＝pytest wiring 斷言（讀 template 內容比對字串），瀏覽器互動另行手動驗證。

## Global Constraints

- 只動 `templates/notes/editor.html` 內嵌 script；**不碰任何 `/static/` 檔 → 不 bump SW**（editor 頁是 navigation，SW network-first）。
- 不動後端 / DB / models / 上方 `#btn-back` 行為。
- 適用所有編輯器型別（note 與 checklist 一致）。
- review/唯讀模式浮動鈕維持 `← 返回摘要`、行為不變（review 時 `_dirty` 恆 false）。
- 本機測試一律 `pytest tests/`（非裸 pytest，見專案踩雷 #11）。
- commit message 結尾加 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

---

### Task 1: `doSave()` 回傳 Promise

**Files:**
- Modify: `templates/notes/editor.html`（`function doSave()`，約 952–1008 行）
- Test: `tests/test_notes_editor_inline_save.py`

**Interfaces:**
- Produces: `doSave()` 回傳 `Promise<boolean>` — 完整成功（含 pending 附件全部上傳完）resolve `true`；任一失敗路徑（`handleMutationResponse` false / save 失敗 / 附件上傳失敗 / fetch catch）resolve `false`。永不 reject。
- 頂部 `<button id="btn-save" onclick="doSave()">` 不受影響（忽略回傳值）。

- [ ] **Step 1: 加 failing wiring test**

在 `tests/test_notes_editor_inline_save.py` 末尾新增：

```python
def test_dosave_returns_promise():
    """doSave 改回傳 Promise<boolean>，供浮動鈕「存後導頁」串接。"""
    src = EDITOR.read_text(encoding='utf-8')
    body = _dosave_body(src)
    assert 'return new Promise(' in body
    # 成功 / 失敗都要有明確 resolve
    assert 'resolve(true)' in body
    assert 'resolve(false)' in body
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `cd app_unified && python -m pytest tests/test_notes_editor_inline_save.py::test_dosave_returns_promise -v`
Expected: FAIL（`assert 'return new Promise(' in body` → AssertionError）

- [ ] **Step 3: 改寫 `doSave()` 包成 Promise**

把整個 `function doSave() { ... }`（約 952–1008 行）替換為：

```javascript
function doSave() {
  var title = document.getElementById('title-input').value.trim() || '未命名筆記';
  var contentEl = document.getElementById('content-area');
  var content = contentEl ? contentEl.value : '';
  var store = currentStore || null;
  var statusEl = document.getElementById('save-status');
  var saveBtn = document.getElementById('btn-save');
  statusEl.textContent = '儲存中…';
  saveBtn.disabled = true;

  var url = NOTE_ID ? ('/notes/api/' + NOTE_ID) : '/notes/api';
  var method = NOTE_ID ? 'PUT' : 'POST';
  var body = { title: title, content: content, store: store, status: currentStatus, category: currentCategory, shift: currentShift };
  // F18: note 級到期日（只有 note 型 editor 有此 input；checklist 走項目級）
  var rdEl = document.getElementById('reminder-date');
  if (rdEl) body.reminder_date = rdEl.value || null;
  if (!NOTE_ID) {
    body.note_type = EDITOR_TYPE;
    // 新筆記：把 pre-save 標籤一起帶進 POST、後端會 inline 建 m2m
    if (window.__PRESAVE_TAG_IDS && window.__PRESAVE_TAG_IDS.length > 0) {
      body.tag_ids = window.__PRESAVE_TAG_IDS;
    }
  }
  return new Promise(function(resolve) {
    fetch(url, {
      method: method,
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    }).then(function(res) {
      return res.json().catch(function() { return {}; }).then(function(j) { return [res, j]; });
    }).then(function(pair) {
      var res = pair[0], data = pair[1];
      if (!handleMutationResponse(res, data)) { saveBtn.disabled = false; resolve(false); return; }
      if (data.status === 'ok' || data.id) {
        if (!NOTE_ID && data.id) {
          NOTE_ID = data.id;
          // 新筆記首存：就地把網址換成編輯 URL（保留 filter query string），
          // 重整不掉內容、之後存檔走 PUT。不 redirect。
          history.replaceState(null, '', '/notes/' + NOTE_ID + window.location.search);
        }
        // 如果有暫存的檔案，先全部上傳完才標記已儲存
        if (pendingFiles.length > 0) {
          statusEl.textContent = '上傳附件中…';
          uploadPendingFiles().then(function() {
            setSaved();
            statusEl.textContent = '✓ 已儲存';
            resolve(true);
          }).catch(function(err) {
            statusEl.textContent = '附件上傳失敗：' + err.message;
            saveBtn.disabled = false;
            resolve(false);
          });
        } else {
          setSaved();
          statusEl.textContent = '✓ 已儲存';
          resolve(true);
        }
      }
      else { statusEl.textContent = '儲存失敗：' + (data.message || '未知錯誤'); saveBtn.disabled = false; resolve(false); }
    }).catch(function(err) { statusEl.textContent = '儲存失敗：' + err.message; saveBtn.disabled = false; resolve(false); });
  });
}
```

- [ ] **Step 4: 跑測試確認 pass（含既有 inline-save 測試不破）**

Run: `cd app_unified && python -m pytest tests/test_notes_editor_inline_save.py -v`
Expected: 全 PASS（含原本 `test_editor_save_no_longer_redirects_to_list`：body 仍無 `NOTES_INDEX_URL`、仍有 `setSaved();`）

- [ ] **Step 5: Commit**

```bash
git add app_unified/templates/notes/editor.html app_unified/tests/test_notes_editor_inline_save.py
git commit -m "refactor(notes): doSave 回傳 Promise<boolean> 供存後導頁

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 浮動鈕 dirty 時「儲存並返回」

**Files:**
- Modify: `templates/notes/editor.html`（`var _dirty` 區塊約 936–950、`markDirty`/`setSaved`、float widget IIFE 約 827–855 的 `fb` click handler 與 `refresh()`、`_guardLeave` IIFE 約 2054–2067）
- Test: `tests/test_notes_floating_back.py`

**Interfaces:**
- Consumes: `doSave()`（Task 1，回傳 `Promise<boolean>`）、既有 `_dirty` / `_leavingIntentionally` / `IS_READONLY` / `window._refreshFloatBack`。
- Produces: 浮動鈕標籤三態（review→`← 返回摘要`、clean→`← 返回`、dirty→`💾 儲存並返回`）；dirty 點擊走 `doSave().then` 存後導頁。

- [ ] **Step 1: 加 failing wiring tests**

在 `tests/test_notes_floating_back.py` 末尾新增：

```python
def test_float_back_dirty_label_present():
    src = EDITOR.read_text(encoding='utf-8')
    assert '💾 儲存並返回' in src


def test_float_back_saves_then_navigates_when_dirty():
    """dirty 點浮動鈕 → doSave() 成功才導頁（取代放棄變更 confirm）。"""
    src = EDITOR.read_text(encoding='utf-8')
    assert 'doSave().then(' in src


def test_unsaved_guard_only_on_btn_back_not_float():
    """_guardLeave 的 confirm 只掛在 #btn-back；浮動鈕改走存後導頁、不再掛 guard。"""
    src = EDITOR.read_text(encoding='utf-8')
    # 浮動鈕不再以 capture 監聽 _guardLeave
    assert "floatBack.addEventListener('click', _guardLeave, true)" not in src
    # btn-back 仍掛 guard
    assert "backBtn.addEventListener('click', _guardLeave)" in src
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `cd app_unified && python -m pytest tests/test_notes_floating_back.py -v`
Expected: 新增三個 FAIL（標籤字串不存在 / `doSave().then(` 不存在 / `_guardLeave, true)` 仍存在）

- [ ] **Step 3a: 加 `_fbSaving` 旗標**

把（約 937–938 行）：

```javascript
var _dirty = false;
var _leavingIntentionally = false;   // 點返回鈕「確定離開」時設 → 抑制 beforeunload 雙重彈窗
```

改為：

```javascript
var _dirty = false;
var _leavingIntentionally = false;   // 點返回鈕「確定離開」時設 → 抑制 beforeunload 雙重彈窗
var _fbSaving = false;               // 浮動鈕「儲存並返回」進行中 → 擋重複點擊、勿被 refresh 覆蓋標籤
```

- [ ] **Step 3b: `markDirty` / `setSaved` 末尾連動浮動鈕標籤**

把（約 939–949 行）：

```javascript
function markDirty() {
  if (IS_READONLY) return;           // 被鎖/唯讀不標 dirty
  _dirty = true;
  var b = document.getElementById('btn-save');
  if (b) { b.textContent = '💾 儲存'; b.disabled = false; b.classList.remove('is-saved'); }
}
function setSaved() {
  _dirty = false;
  var b = document.getElementById('btn-save');
  if (b) { b.textContent = '✓ 已儲存'; b.disabled = true; b.classList.add('is-saved'); }
}
```

改為（各加一行 `_refreshFloatBack`）：

```javascript
function markDirty() {
  if (IS_READONLY) return;           // 被鎖/唯讀不標 dirty
  _dirty = true;
  var b = document.getElementById('btn-save');
  if (b) { b.textContent = '💾 儲存'; b.disabled = false; b.classList.remove('is-saved'); }
  if (window._refreshFloatBack) window._refreshFloatBack();
}
function setSaved() {
  _dirty = false;
  var b = document.getElementById('btn-save');
  if (b) { b.textContent = '✓ 已儲存'; b.disabled = true; b.classList.add('is-saved'); }
  if (window._refreshFloatBack) window._refreshFloatBack();
}
```

- [ ] **Step 3c: `refresh()` 設定標籤、click handler 改存後導頁**

把 float widget IIFE（約 827–855 行）整段：

```javascript
(function() {
  var fb = document.getElementById('float-back-widget');
  var bb = document.getElementById('btn-back');
  if (!fb || !bb) return;
  fb.addEventListener('click', function(e) {
    e.preventDefault();
    if (bb.href) window.location.href = bb.href;
  });
  function searchHitVisible() {
    var sh = document.getElementById('search-hit-widget');
    return !!sh && sh.style.display && sh.style.display !== 'none';
  }
  var _btnBackInView = true;
  function refresh() {
    if (searchHitVisible()) { fb.style.display = 'none'; return; }
    if (document.body.classList.contains('review-mode')) { fb.style.display = 'inline-flex'; return; }
    fb.style.display = _btnBackInView ? 'none' : 'inline-flex';
  }
  window._refreshFloatBack = refresh;
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function(entries) {
      _btnBackInView = entries[0].isIntersecting;
      refresh();
    }).observe(bb);
  } else {
    _btnBackInView = false;  // 舊瀏覽器 fallback：直接顯示
    refresh();
  }
})();
```

替換為：

```javascript
(function() {
  var fb = document.getElementById('float-back-widget');
  var bb = document.getElementById('btn-back');
  if (!fb || !bb) return;
  // dirty 時「儲存並返回」：doSave() 成功才導頁；失敗留在頁面、不導頁。
  // 非 dirty（或 review/唯讀）→ 直接導頁，行為同舊版。
  fb.addEventListener('click', function(e) {
    e.preventDefault();
    if (!bb.href) return;
    var dest = bb.href;
    var canSave = _dirty && !IS_READONLY && !document.body.classList.contains('review-mode');
    if (!canSave) { window.location.href = dest; return; }
    if (_fbSaving) return;             // 存檔進行中、擋重複點擊
    _fbSaving = true;
    fb.textContent = '💾 儲存中…';
    doSave().then(function(ok) {
      _fbSaving = false;
      if (ok) { _leavingIntentionally = true; window.location.href = dest; }
      else if (window._refreshFloatBack) window._refreshFloatBack();   // 還原「💾 儲存並返回」
    });
  });
  function searchHitVisible() {
    var sh = document.getElementById('search-hit-widget');
    return !!sh && sh.style.display && sh.style.display !== 'none';
  }
  var _btnBackInView = true;
  function refresh() {
    if (searchHitVisible()) { fb.style.display = 'none'; return; }
    if (document.body.classList.contains('review-mode')) { fb.style.display = 'inline-flex'; return; }
    fb.style.display = _btnBackInView ? 'none' : 'inline-flex';
    // 標籤跟 dirty 狀態連動（存檔進行中不覆蓋「💾 儲存中…」）
    if (!_fbSaving) fb.textContent = _dirty ? '💾 儲存並返回' : '← 返回';
  }
  window._refreshFloatBack = refresh;
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function(entries) {
      _btnBackInView = entries[0].isIntersecting;
      refresh();
    }).observe(bb);
  } else {
    _btnBackInView = false;  // 舊瀏覽器 fallback：直接顯示
    refresh();
  }
})();
```

備註：`_dirty` / `IS_READONLY` / `_fbSaving` / `_leavingIntentionally` / `doSave` 皆為同一 `<script>` scope 的 `var`/`function`（hoisted），click 時點才求值，順序安全。

- [ ] **Step 3d: `_guardLeave` 只留在 `#btn-back`**

把 `_guardLeave` IIFE 末段（約 2061–2066 行）：

```javascript
  var backBtn = document.getElementById('btn-back');
  if (backBtn) backBtn.addEventListener('click', _guardLeave);
  // float-back 既有 click handler 也會 preventDefault + 導頁；guard 用 capture
  // 先跑，dirty 取消時擋掉後續導頁，確定離開時設旗標再讓既有 handler 導頁。
  var floatBack = document.getElementById('float-back-widget');
  if (floatBack) floatBack.addEventListener('click', _guardLeave, true);
})();
```

改為（移除浮動鈕的 capture 監聽；浮動鈕已改走存後導頁，不再需要放棄-confirm）：

```javascript
  var backBtn = document.getElementById('btn-back');
  if (backBtn) backBtn.addEventListener('click', _guardLeave);
  // 浮動返回鈕 dirty 時改走「儲存並返回」（doSave 成功才導頁），
  // 不再掛 _guardLeave 放棄-confirm。
})();
```

- [ ] **Step 4: 跑測試確認 pass（含 floating_back 與 inline_save 既有測試）**

Run: `cd app_unified && python -m pytest tests/test_notes_floating_back.py tests/test_notes_editor_inline_save.py -v`
Expected: 全 PASS（含原 `test_review_mode_label_preserved`：`← 返回摘要` 仍在）

- [ ] **Step 5: 跑全套確認沒打到別的 wiring 測試**

Run: `cd app_unified && python -m pytest tests/ -q`
Expected: 全 PASS（對齊現況 633 passed + 1 skipped 之數量級，無新 fail）

- [ ] **Step 6: Commit**

```bash
git add app_unified/templates/notes/editor.html app_unified/tests/test_notes_floating_back.py
git commit -m "feat(notes): 浮動返回鈕 dirty 時「儲存並返回」自動存檔

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 手動驗證（本機，實作完成後）

依 spec 驗證步驟（`bash scripts/run_local.sh` → `/dev` 切帳號）：

1. checklist editor 連續改資料、往下拉 → 浮動鈕顯示 `💾 儲存並返回`。
2. 點浮動鈕 → 暫顯 `💾 儲存中…` → 存好回原列表（filter/page 保留），變更已落地。
3. 不改任何資料往下拉 → 浮動鈕仍 `← 返回`，點擊直接返回、不存檔。
4. note 型 editor 同樣驗一遍。
5. review 模式（AI 摘要 `?from=` 進入）→ 浮動鈕仍 `← 返回摘要`、行為不變。
6. 存檔失敗情境（可開兩分頁鎖同一筆製造 lock）→ 點浮動鈕應留在頁面、標籤還原、不導頁。

## Self-Review 紀錄

- **Spec 覆蓋**：三態（Task 2 Step 3c）、存後導頁（Task 2）、doSave Promise（Task 1）、移除浮動鈕 confirm（Task 2 Step 3d）、不 bump SW / 不動後端（Global Constraints）、review 模式不變（Step 3c + 既有 `test_review_mode_label_preserved`）皆有對應。
- **Placeholder**：無 TBD/TODO，所有 code step 附完整 code。
- **型別一致**：`doSave()` Task 1 產出 `Promise<boolean>`、Task 2 以 `doSave().then(function(ok){...})` 消費；`_fbSaving`/`_refreshFloatBack` 命名前後一致。
