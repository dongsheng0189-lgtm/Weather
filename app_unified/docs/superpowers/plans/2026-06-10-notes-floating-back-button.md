# 浮動返回鈕 + 修正分頁返回 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在店務筆記 / 確認表單編輯頁加一顆右下角浮動返回鈕（向下捲動後出現、整併掉舊的 review-back-widget），並修正「從列表第 2 頁點進筆記、返回卻跳回第 1 頁」的分頁 bug。

**Architecture:** 一顆通用 `#float-back-widget`，點擊鏡像頂部 `#btn-back`（單一真相來源）；用 IntersectionObserver 監看 `#btn-back` 決定一般模式的顯示，檢視模式一律顯示，search-hit-widget 顯示時互斥隱藏。分頁 bug 透過在 index 卡片連結與 editor 返回連結各補一個 `page` 參數修正。

**Tech Stack:** Flask + Jinja2 + vanilla JS + pytest（template 字串 wiring 測試）。

**Spec:** `docs/superpowers/specs/2026-06-10-notes-floating-back-button-design.md`

---

## File Structure

| 檔案 | 動作 | 責任 |
|------|------|------|
| `templates/notes/index.html` | Modify | note-card 連結補 `page=pagination.page` |
| `templates/notes/editor.html` | Modify | `#btn-back` 補 `page`；移除 `#review-back-widget`（CSS+markup）；新增 `#float-back-widget` + 顯示 JS；review-mode JS 改設 float-back label；buildSearchHits 顯示時隱藏 float-back |
| `tests/test_notes_back_pagination.py` | Create | wiring：兩處連結含 `page` |
| `tests/test_notes_floating_back.py` | Create | wiring：float-back 存在/鏡像/observer、舊 widget 已移除、review label 還在 |

---

## Task 1: 修正分頁返回（`page` 保留）

**Files:**
- Modify: `templates/notes/index.html`（note-card onclick，第 266 行）
- Modify: `templates/notes/editor.html`（`#btn-back` 的 `url_for`，第 151-159 行）
- Test: `tests/test_notes_back_pagination.py`（新檔）

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_notes_back_pagination.py`：

```python
"""Wiring: notes editor 返回連結 + index 卡片連結保留 `page` 參數（分頁返回）。"""
from pathlib import Path

ROOT = Path(__file__).parent.parent
EDITOR = ROOT / 'templates' / 'notes' / 'editor.html'
INDEX = ROOT / 'templates' / 'notes' / 'index.html'


def test_index_card_link_preserves_page():
    src = INDEX.read_text(encoding='utf-8')
    # 點筆記進編輯頁時帶上當前列表頁碼
    assert 'page=pagination.page' in src


def test_editor_back_link_preserves_page():
    src = EDITOR.read_text(encoding='utf-8')
    # 頂部「← 返回」回列表時帶回原本的頁碼
    assert "page=request.args.get('page')" in src
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_notes_back_pagination.py -q`
Expected: 2 failed（兩個字串都還不存在）。

- [ ] **Step 3: index.html 卡片連結補 page**

在 `templates/notes/index.html` 第 266 行，把：

```html
  <div class="note-card" onclick="location.href='{{ url_for('notes.edit_note', note_id=note.id, sort=current_sort, q=current_keyword, tag=current_tag, category=current_category, shift=current_shift, range=current_range, status=current_status, store=current_store) }}'" {% if note.note_type == 'checklist' %}style="border-left-color:#27ae60;"{% endif %}>
```

改成（在 `store=current_store` 後加 `, page=pagination.page`）：

```html
  <div class="note-card" onclick="location.href='{{ url_for('notes.edit_note', note_id=note.id, sort=current_sort, q=current_keyword, tag=current_tag, category=current_category, shift=current_shift, range=current_range, status=current_status, store=current_store, page=pagination.page) }}'" {% if note.note_type == 'checklist' %}style="border-left-color:#27ae60;"{% endif %}>
```

- [ ] **Step 4: editor.html 返回連結補 page**

在 `templates/notes/editor.html` 第 151-159 行，把：

```html
  <a href="{{ url_for('notes.index',
                       q=request.args.get('q'),
                       tag=request.args.get('tag'),
                       category=request.args.get('category'),
                       shift=request.args.get('shift'),
                       range=request.args.get('range'),
                       status=request.args.get('status'),
                       sort=request.args.get('sort'),
                       store=request.args.get('store')) }}" class="btn btn-secondary" id="btn-back">← 返回</a>
```

改成（加一行 `page=request.args.get('page'),`）：

```html
  <a href="{{ url_for('notes.index',
                       q=request.args.get('q'),
                       tag=request.args.get('tag'),
                       category=request.args.get('category'),
                       shift=request.args.get('shift'),
                       range=request.args.get('range'),
                       status=request.args.get('status'),
                       sort=request.args.get('sort'),
                       page=request.args.get('page'),
                       store=request.args.get('store')) }}" class="btn btn-secondary" id="btn-back">← 返回</a>
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_notes_back_pagination.py -q`
Expected: 2 passed。

- [ ] **Step 6: 防回歸——跑既有 notes 相關測試**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_notes_editor_gallery_wiring.py tests/test_attachment_download.py -q`
Expected: 全 pass（確認沒動壞 editor 既有 wiring）。

- [ ] **Step 7: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/notes/index.html app_unified/templates/notes/editor.html app_unified/tests/test_notes_back_pagination.py
git commit -m "fix(notes): preserve list page on note open + back (paginated return)"
```

---

## Task 2: 通用浮動返回鈕（整併掉 review-back-widget）

**Files:**
- Modify: `templates/notes/editor.html`（CSS 第 54-55 行；markup 第 185-192 行；review-mode JS 第 798-799 行；buildSearchHits 顯示處；新增顯示 JS）
- Test: `tests/test_notes_floating_back.py`（新檔）

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_notes_floating_back.py`：

```python
"""Wiring: 通用浮動返回鈕（取代 review-back-widget）。"""
from pathlib import Path

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def test_float_back_widget_present():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'id="float-back-widget"' in src


def test_float_back_mirrors_btn_back_and_uses_observer():
    src = EDITOR.read_text(encoding='utf-8')
    # 點擊鏡像頂部 #btn-back；用 IntersectionObserver 決定捲動顯示
    assert "getElementById('btn-back')" in src
    assert 'IntersectionObserver' in src


def test_old_review_back_widget_removed():
    src = EDITOR.read_text(encoding='utf-8')
    # 整併後舊 widget（含 CSS rule / markup / 註解）完全移除
    assert 'review-back-widget' not in src


def test_review_mode_label_preserved():
    src = EDITOR.read_text(encoding='utf-8')
    # 檢視模式仍把浮動鈕標籤改成「← 返回摘要」
    assert '← 返回摘要' in src
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_notes_floating_back.py -q`
Expected: 多數 fail（float-back 尚未存在、review-back-widget 還在）。

- [ ] **Step 3: 移除舊 review-back-widget 的 CSS（第 54-55 行）**

在 `templates/notes/editor.html` 刪除這兩行：

```css
#review-back-widget { display: none; }
body.review-mode #review-back-widget { display: inline-flex; }
```

（直接刪掉，不留空 CSS rule。）

- [ ] **Step 4: 用 float-back 取代 review-back-widget markup（第 185-192 行）**

把這段（含上方註解）：

```html
{# review-back floating widget — 跟 search-hit-widget 同款 fixed position，
   讓 user 滑到頁面底端時也能一鍵返回摘要、不用 scroll 回最上面 #}
<a id="review-back-widget" href="#"
   style="position:fixed;bottom:20px;right:20px;z-index:1000;
          align-items:center;gap:.4rem;padding:.65rem 1.1rem;
          background:#1f4e79;color:#fff;border-radius:24px;
          font-size:.95rem;font-weight:600;text-decoration:none;
          box-shadow:0 4px 12px rgba(0,0,0,.25);">← 返回摘要</a>
```

整段換成：

```html
{# 通用浮動返回鈕（取代舊 review-back-widget）— 右下角、向下捲動後出現，
   點擊鏡像頂部 #btn-back（回列表+filter+page；檢視模式回 AI 摘要）。
   檢視模式一律顯示；search-hit-widget 顯示時隱藏（避免兩顆返回）。 #}
<a id="float-back-widget" href="#" title="返回"
   style="display:none;position:fixed;bottom:20px;right:20px;z-index:1000;
          align-items:center;gap:.4rem;padding:.65rem 1.1rem;cursor:pointer;
          background:#1f4e79;color:#fff;border-radius:24px;
          font-size:.95rem;font-weight:600;text-decoration:none;
          box-shadow:0 4px 12px rgba(0,0,0,.25);">← 返回</a>
```

- [ ] **Step 5: review-mode JS 改設 float-back 的 label（第 798-799 行）**

把：

```javascript
  // 浮動「返回摘要」widget — 跟頂上 btn-back 同 href，user 不用滑回頁首
  var floatBack = document.getElementById('review-back-widget');
  if (floatBack) floatBack.setAttribute('href', from);
```

換成：

```javascript
  // 檢視模式：把通用浮動鈕標籤改成「← 返回摘要」（href 鏡像 btn-back，已被改成 from）
  var floatBack = document.getElementById('float-back-widget');
  if (floatBack) floatBack.textContent = '← 返回摘要';
```

- [ ] **Step 6: search-hit-widget 顯示時隱藏 float-back**

在 `templates/notes/editor.html` 的 `buildSearchHits` 函式裡，找到顯示 widget 的那行：

```javascript
  widget.style.display = 'flex';
  jumpToHit(0);
```

改成（中間插一行）：

```javascript
  widget.style.display = 'flex';
  if (window._refreshFloatBack) window._refreshFloatBack();   // search-hit 出現 → 收起通用返回鈕
  jumpToHit(0);
```

- [ ] **Step 7: 新增 float-back 顯示/點擊 JS**

在 `templates/notes/editor.html` 的 review-mode `DOMContentLoaded` handler **結束之後**（即 `document.body.classList.add('review-mode'); });` 那行的下一行），插入這個獨立 `<script>` 區塊內容（沿用同一個 `<script>` 區、直接接在後面即可）：

```javascript

// 通用浮動返回鈕：一般模式捲動出現（IntersectionObserver 監看 #btn-back）、
// 檢視模式一律顯示、search-hit-widget 顯示時隱藏；點擊鏡像 #btn-back。
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

- [ ] **Step 8: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_notes_floating_back.py -q`
Expected: 4 passed。

- [ ] **Step 9: 防回歸——跑既有 editor wiring 測試**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_notes_editor_gallery_wiring.py tests/test_attachment_download.py tests/test_notes_back_pagination.py -q`
Expected: 全 pass。

- [ ] **Step 10: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/notes/editor.html app_unified/tests/test_notes_floating_back.py
git commit -m "feat(notes): unified floating back button (replaces review-back-widget)"
```

---

## Task 3: 本機實測 + 收尾

**Files:** 無（驗證）

- [ ] **Step 1: 起本機**

Run: `cd app_unified && bash scripts/run_local.sh`（背景；dev 帳號 /dev 登入 alice）。

- [ ] **Step 2: 手動驗證**
  - **長筆記**：開一篇內容很長的店務筆記 → 向下捲 → 右下角出現「← 返回」浮動鈕 → 點它回到列表（filter 保留）；捲回頂端浮動鈕消失。
  - **分頁**：列表若有多頁，到第 2 頁 → 點一篇筆記 → 按返回（頂部或浮動）→ **回到第 2 頁**。
  - **確認表單 + 搜尋**：列表搜尋關鍵字 → 點命中的確認表單 → 右下角是 search-hit widget（🔍 計數 / ▶ 下一個 / ← 返回），**沒有第二顆通用返回鈕**；▶ 可跳下一個命中。
  - **AI 摘要直連**：從 AI 摘要點進某表單項目 → 捲到該項目 + 高亮、進檢視模式 → 右下角浮動鈕顯示「← 返回摘要」→ 點它回摘要。

- [ ] **Step 3: 關 server**

Run: `cd app_unified && fuser -k 5000/tcp`

> 部署備註：無新增 DB 欄位（不需 inline schema repair）、無新增 static js/css（CSS/JS inline 在 editor.html）→ **不用 bump SW**。Push / merge 依使用者指示，遵守尖峰時段禁推規則。

---

## Self-Review

**Spec coverage：**
- 通用浮動返回鈕（右下角、捲動出現、鏡像 btn-back）→ Task 2 Step 4+7 ✓
- 整併移除 review-back-widget（CSS+markup+JS）→ Task 2 Step 3/4/5 ✓
- 檢視模式一律顯示 + label「← 返回摘要」→ Task 2 Step 5（label）+ Step 7（review-mode 一律顯示分支）✓
- search-hit 互斥（不顯示第二顆、不碰 search-hit 功能）→ Task 2 Step 6 + Step 7 `searchHitVisible()` ✓
- AI 摘要深連結 scroll-to-item 不受影響 → 本案不碰 `_maybeScrollToHashItem`（無對應修改 task = 正確）✓
- 修分頁 `page`（index 卡片 + editor 返回）→ Task 1 ✓
- 不碰 mynotes / 無新 DB / 無新 static → 範圍正確 ✓

**Placeholder scan：** 無 TBD/TODO；每個 code step 都有完整程式碼或完整 old→new 替換。

**Type consistency：** 元素 id `float-back-widget` 在 markup（Step 4）、review-mode JS（Step 5）、顯示 JS（Step 7）、測試（Step 1）一致；`window._refreshFloatBack` 在 Step 6 呼叫、Step 7 定義一致；`#btn-back` / `#search-hit-widget` id 與既有 template 一致；`page=request.args.get('page')`（editor）、`page=pagination.page`（index）與 Task 1 測試斷言字串一致。
