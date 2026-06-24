# Notes 返回時還原捲動位置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 notes 列表點進筆記/確認表單後按返回，自動捲回剛點的那張卡片位置並短暫 highlight，取代現在跳回最上面。

**Architecture:** 純前端。點卡片時把 note id 存進 `sessionStorage`（`notes_return_to`）；回到列表頁載入時（`DOMContentLoaded` + `pageshow`）讀回 id，若該頁有對應 `#note-<id>` 卡片就 `scrollIntoView` + 加 highlight class（~2s 淡出），用過即清。返回鈕與滑動/瀏覽器上一頁都回到同一個列表 URL/同一頁，故同一段還原邏輯都涵蓋。

**Tech Stack:** Jinja2 template + 內嵌 vanilla JS + sessionStorage + CSS keyframes。pytest wiring 測試（讀 template 字串斷言）。

## Global Constraints

- 只動 `templates/notes/index.html`：不動編輯器、不動後端、無 DB schema 變動、無 `/static/` 變動 → **不 bump Service Worker**。
- 還原 script 內嵌在 `index.html`（不放進 static JS，避免動到 SW 快取）。
- 範圍只做 notes 列表；不碰 mynotes。
- 測試與 CI 一致一律跑 `pytest tests/`（不跑裸 `pytest`，避免收集 untracked seed 腳本）。
- highlight 不可重用 `.tag-highlight`（綁 `.cl-item`）；另開 `.note-card.note-return-highlight`。

---

### Task 1: 點卡片時記住 note id（remember 半）

**Files:**
- Modify: `templates/notes/index.html`（`.note-card` div，約 line 282）
- Test: `tests/test_notes_scroll_restore.py`（新建）

**Interfaces:**
- Produces:
  - 每張卡片 DOM 有 `id="note-<note.id>"`。
  - 卡片 `onclick` 在導航前執行 `sessionStorage.setItem('notes_return_to', '<note.id>')`（key 名 `notes_return_to`，Task 2 會讀同一個 key）。

- [ ] **Step 1: 寫 failing wiring 測試**

新建 `tests/test_notes_scroll_restore.py`：

```python
"""Wiring: notes 列表返回時還原捲動位置（scroll restore）。"""
from pathlib import Path

INDEX = Path(__file__).parent.parent / 'templates' / 'notes' / 'index.html'


def test_card_has_stable_id():
    """每張卡片要有 id="note-<id>" 才能被還原邏輯定位。"""
    src = INDEX.read_text(encoding='utf-8')
    assert 'id="note-{{ note.id }}"' in src


def test_card_click_remembers_note_id():
    """點卡片時把 note id 存進 sessionStorage('notes_return_to')。"""
    src = INDEX.read_text(encoding='utf-8')
    assert "sessionStorage.setItem('notes_return_to', '{{ note.id }}')" in src
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `pytest tests/test_notes_scroll_restore.py -v`
Expected: 2 個 FAIL（字串還不在 template）。

- [ ] **Step 3: 改 index.html 卡片 div**

找到（約 line 282）：

```html
  <div class="note-card" onclick="location.href='{{ url_for('notes.edit_note', note_id=note.id, sort=current_sort, q=current_keyword, tag=current_tag, category=current_category, shift=current_shift, range=current_range, status=current_status, store=current_store, page=pagination.page) }}'" {% if note.note_type == 'checklist' %}style="border-left-color:#27ae60;"{% endif %}>
```

改成（加 `id`、onclick 前綴 sessionStorage 寫入）：

```html
  <div class="note-card" id="note-{{ note.id }}" onclick="try{sessionStorage.setItem('notes_return_to', '{{ note.id }}')}catch(e){}; location.href='{{ url_for('notes.edit_note', note_id=note.id, sort=current_sort, q=current_keyword, tag=current_tag, category=current_category, shift=current_shift, range=current_range, status=current_status, store=current_store, page=pagination.page) }}'" {% if note.note_type == 'checklist' %}style="border-left-color:#27ae60;"{% endif %}>
```

- [ ] **Step 4: 跑測試確認 pass**

Run: `pytest tests/test_notes_scroll_restore.py -v`
Expected: 2 PASS。

- [ ] **Step 5: Commit**

```bash
git add templates/notes/index.html tests/test_notes_scroll_restore.py
git commit -m "feat(notes): 點卡片時記住 note id 供返回還原捲動位置

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 返回列表時還原捲動 + highlight（restore 半）

**Files:**
- Modify: `templates/notes/index.html`（`<style>` 區塊約 line 40 區段加 CSS；body 末加 `<script>`）
- Test: `tests/test_notes_scroll_restore.py`（沿用 Task 1 檔案，追加 case）

**Interfaces:**
- Consumes: Task 1 的 `sessionStorage('notes_return_to')` 與卡片 `id="note-<id>"`。
- Produces:
  - 還原邏輯函式（內嵌 IIFE）綁 `DOMContentLoaded` + `pageshow`。
  - highlight class `.note-card.note-return-highlight`（~2s 淡出）。

- [ ] **Step 1: 追加 failing wiring 測試**

在 `tests/test_notes_scroll_restore.py` 末尾追加：

```python
def test_restore_reads_and_clears_key():
    """還原邏輯讀 notes_return_to，並用過即清（避免之後正常進列表亂捲）。"""
    src = INDEX.read_text(encoding='utf-8')
    assert "sessionStorage.getItem('notes_return_to')" in src
    assert "sessionStorage.removeItem('notes_return_to')" in src


def test_restore_scrolls_to_card():
    """讀回 id → getElementById('note-<id>') → scrollIntoView 定位。"""
    src = INDEX.read_text(encoding='utf-8')
    assert "getElementById('note-' + " in src
    assert 'scrollIntoView' in src


def test_restore_highlights_card():
    """套用卡片專用 highlight class（非 tag-highlight）。"""
    src = INDEX.read_text(encoding='utf-8')
    assert 'note-return-highlight' in src
    assert '.note-card.note-return-highlight' in src  # 卡片專用 CSS，非 .cl-item


def test_restore_covers_bfcache_and_dom_ready():
    """DOMContentLoaded（一般載入）+ pageshow（bfcache 還原）兩條路徑都觸發。"""
    src = INDEX.read_text(encoding='utf-8')
    assert "addEventListener('DOMContentLoaded'" in src
    assert "addEventListener('pageshow'" in src
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `pytest tests/test_notes_scroll_restore.py -v`
Expected: 新增 4 個 FAIL（Task 1 的 2 個仍 PASS）。

- [ ] **Step 3: 加 highlight CSS**

在 `index.html` 的 `<style>` 區塊內（`.note-card { ... }` 規則附近）加入：

```css
.note-card.note-return-highlight { animation: note-return-flash 2s ease-out; }
@keyframes note-return-flash {
  0%   { background:#fff3b0; box-shadow:0 0 0 3px #ffd54f; }
  100% { background:#fff;     box-shadow:0 2px 8px rgba(0,0,0,.07); }
}
```

- [ ] **Step 4: 加還原 script**

在 `index.html` body 末（其他 `<script>` 之後、`{% endblock %}` 之前）加入：

```html
<script>
// 返回列表時還原捲動位置：讀 Task 1 存的 notes_return_to → 捲到該卡片 + 短暫黃底。
(function(){
  function restoreScrollToNote(){
    var id;
    try { id = sessionStorage.getItem('notes_return_to'); } catch(e){ return; }
    if (!id) return;
    try { sessionStorage.removeItem('notes_return_to'); } catch(e){}  // 用過即清
    var el = document.getElementById('note-' + id);
    if (!el) return;  // 卡片不在這頁（被刪/濾掉/換頁）→ 安靜不捲
    el.scrollIntoView({block:'center'});
    el.classList.add('note-return-highlight');
    setTimeout(function(){ el.classList.remove('note-return-highlight'); }, 2000);
  }
  document.addEventListener('DOMContentLoaded', restoreScrollToNote);
  window.addEventListener('pageshow', restoreScrollToNote);  // 涵蓋 bfcache 還原
})();
</script>
```

- [ ] **Step 5: 跑測試確認 pass**

Run: `pytest tests/test_notes_scroll_restore.py -v`
Expected: 6 PASS（Task 1 的 2 + Task 2 的 4）。

- [ ] **Step 6: 跑全套確認無回歸**

Run: `pytest tests/`
Expected: 全綠（原有 + 新增 6 個 wiring 測試），無 fail。

- [ ] **Step 7: Commit**

```bash
git add templates/notes/index.html tests/test_notes_scroll_restore.py
git commit -m "feat(notes): 返回列表時還原捲動位置並 highlight 剛點的卡片

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 本機驗證（實作完成後，user 親測）

`bash scripts/run_local.sh` → `/dev` 切帳號 → 在 notes 列表：

1. 往下滑、點一篇下面的筆記 → 按「← 返回」→ 回到該卡片位置 + 黃底閃一下。
2. 重複，改用手機螢幕邊緣滑動 / 瀏覽器上一頁 → 同樣定位到該卡片。
3. 點一篇 → 換濾鏡 / 換頁讓該卡片不在當頁 → 返回 → 安靜停在最上面、不報錯。
4. 從導覽列正常進列表（未經返回）→ 不應因舊記號亂捲（用過即清）。

user OK 才 push（依推送政策、本機測試流程）。

## Self-Review 紀錄

- **Spec coverage**：痛點/目標/流程/改動範圍/邊界/範圍界線/驗證全有對應 task。涵蓋兩種返回路徑（DOMContentLoaded+pageshow）、degrade（`if (!el) return`）、用過即清（removeItem）、卡片專用 highlight。✅
- **Placeholder scan**：無 TBD/TODO，所有 step 含完整 code。✅
- **Type/naming consistency**：key 名 `notes_return_to`、card id `note-<id>`、class `note-return-highlight` 三處 task 一致。✅
