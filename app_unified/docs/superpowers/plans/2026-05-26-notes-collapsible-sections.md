# Notes 公告 + AI 摘要可摺疊 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/notes/` 首頁的公告區跟 AI 摘要區改成可摺疊、公告自動展開 = 「有新公告 OR 今天首次」、AI 摘要永遠摺疊。

**Architecture:** 純 frontend localStorage、不動 backend / route / schema。新 CSS + JS 各一檔、改兩個 template、加 pytest markup smoke。SW cache bump 強制 client 重抓。

**Tech Stack:** Jinja2 templates, vanilla JS (ES5+), CSS3 transitions, pytest, Flask Blueprint (notes), localStorage API.

**Spec:** `docs/superpowers/specs/2026-05-26-notes-collapsible-sections-design.md`

**Branch:** `feat/notes-collapsible-sections`（已開、HEAD = `c62d574` spec commit）

---

## File Structure

| 動作 | 路徑 | 職責 |
|---|---|---|
| Create | `static/css/collapsible_sections.css` | `.collapsible` / `.collapsible-bar` / `.collapsible-body` / chevron 動畫 / hover state / 公告 bullet 列表 / 個別 ann-item toggle |
| Create | `static/js/collapsible_sections.js` | `initCollapsibleSections()` + auto-expand 邏輯（讀寫 localStorage）+ section bar toggle handler + individual bullet toggle handler |
| Create | `tests/test_notes_collapsible_sections.py` | Markup smoke：兩 section 有 `.collapsible` class、announcement 有 `data-max-ann-id`、每個 ann 有 bullet + card wrapper、CSS/JS 有 link |
| Modify | `templates/announcements/_card_list.html` | 包 `.collapsible` wrapper + 標題 bar + `.collapsible-preview` bullet 列表 + `.collapsible-body` 內含每個 `.ann-item` |
| Modify | `templates/notes/index.html` | AI 摘要 card 改成 `.collapsible`；`<head>` 加 link、底部加 script |
| Modify | `static/sw.js` | `CACHE_NAME` 'note-weather-v22' → 'note-weather-v23' |

---

## Task 1: 寫 failing markup smoke tests

**Files:**
- Create: `tests/test_notes_collapsible_sections.py`

- [ ] **Step 1: 建立 test 檔（4 條 smoke test、全部會 fail）**

```python
"""Notes index：公告 + AI 摘要可摺疊 markup smoke tests."""
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _make_user(db, role, store=None, username=None):
    from models import User
    import json as _json
    u = User(
        username=username or f"{role}_{store or 'na'}",
        role=role,
        store=store,
        features=_json.dumps(["notes"]),
    )
    u.set_password("0000")
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_ai_summary_section_has_collapsible_wrapper(client, db):
    u = _make_user(db, "user", store="B店", username="u_collap_ai")
    _login(client, u)
    r = client.get("/notes/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'class="collapsible' in html
    assert 'data-section="ai-summary"' in html


def test_announcement_section_has_collapsible_wrapper_and_max_id(client, db):
    sa = _make_user(db, "super_admin", username="sa_collap_seed")
    u = _make_user(db, "user", store="B店", username="u_collap_ann")
    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "ANN_FOR_COLLAP", "content": "body", "store": None})
    _login(client, u)
    r = client.get("/notes/")
    html = r.get_data(as_text=True)
    assert 'data-section="announcements"' in html
    assert 'data-max-ann-id="' in html
    import re
    m = re.search(r'data-max-ann-id="(\d+)"', html)
    assert m and int(m.group(1)) > 0


def test_each_announcement_has_bullet_and_card_wrappers(client, db):
    sa = _make_user(db, "super_admin", username="sa_collap_each_seed")
    u = _make_user(db, "user", store="B店", username="u_collap_each")
    _login(client, sa)
    client.post("/notes/announcement/api",
                json={"title": "FIRST", "content": "x", "store": None})
    client.post("/notes/announcement/api",
                json={"title": "SECOND", "content": "y", "store": None})
    _login(client, u)
    r = client.get("/notes/")
    html = r.get_data(as_text=True)
    assert html.count('class="ann-item"') >= 2
    assert html.count('class="ann-bullet"') >= 2
    assert 'FIRST' in html
    assert 'SECOND' in html


def test_collapsible_css_and_js_linked(client, db):
    u = _make_user(db, "user", store="B店", username="u_collap_assets")
    _login(client, u)
    r = client.get("/notes/")
    html = r.get_data(as_text=True)
    assert 'collapsible_sections.css' in html
    assert 'collapsible_sections.js' in html
```

- [ ] **Step 2: 跑 test 確認全 fail**

```bash
cd /home/hirain0126/projects/webapp/app_unified
source ../venv/bin/activate
pytest tests/test_notes_collapsible_sections.py -v
```

Expected: 4 條 FAIL（assertion errors、缺 class / attribute）

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/test_notes_collapsible_sections.py
git commit -m "test(notes): markup smoke for collapsible sections (failing baseline)"
```

---

## Task 2: 新 CSS 檔

**Files:**
- Create: `static/css/collapsible_sections.css`

- [ ] **Step 1: 寫 CSS**

```css
/* ===== Collapsible sections (公告 + AI 摘要) ===== */

.collapsible {
  margin-bottom: 1rem;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0,0,0,.06);
  border: 1px solid #e0e0e0;
}

.collapsible-bar {
  display: flex;
  align-items: center;
  gap: .5rem;
  padding: .65rem .9rem;
  cursor: pointer;
  user-select: none;
  background: #f8f9fa;
  transition: background .15s ease;
}
.collapsible-bar:hover { background: #f0f2f5; }
.collapsible-bar:active { background: #e8ebef; }

.collapsible-bar .bar-title {
  flex: 1;
  font-size: .95rem;
  font-weight: 600;
  color: #333;
}
.collapsible-bar .bar-chevron {
  font-size: .85rem;
  color: #888;
  transition: transform .2s ease;
  display: inline-block;
}
.collapsible.is-expanded > .collapsible-bar .bar-chevron {
  transform: rotate(90deg);
}

/* preview = bullet list、永遠出現在 collapsed 狀態 */
.collapsible-preview {
  padding: .4rem .9rem .65rem;
  border-top: 1px dashed #e0e0e0;
}
.collapsible.is-expanded > .collapsible-preview {
  display: none;
}

.collapsible-body {
  display: none;
  padding: .75rem .9rem;
}
.collapsible.is-expanded > .collapsible-body {
  display: block;
}

/* 公告 bullet 列表 */
.ann-bullet-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.ann-bullet {
  display: block;
  padding: .35rem .25rem;
  cursor: pointer;
  font-size: .88rem;
  color: #555;
  border-radius: 4px;
  transition: background .12s ease;
}
.ann-bullet::before {
  content: "• ";
  color: #f39c12;
  font-weight: bold;
  margin-right: .3rem;
}
.ann-bullet:hover { background: #fff5e0; color: #333; }

/* 個別公告 ─ ann-item 預設 collapsed (show bullet、hide card) */
.ann-item > .ann-bullet { display: block; }
.ann-item > .announcement-card { display: none; }
.ann-item.is-expanded > .ann-bullet { display: none; }
.ann-item.is-expanded > .announcement-card { display: block; }
```

- [ ] **Step 2: Commit**

```bash
git add static/css/collapsible_sections.css
git commit -m "feat(notes): add collapsible_sections.css for 公告 + AI 摘要 bar"
```

---

## Task 3: 改 `_card_list.html`（公告 section）

**Files:**
- Modify: `templates/announcements/_card_list.html`

- [ ] **Step 1: 重寫 template**

完整新內容（覆蓋舊檔）：

```jinja
{% if announcements %}
{% set max_ann_id = announcements | map(attribute='id') | max %}
<section class="collapsible" data-section="announcements" data-max-ann-id="{{ max_ann_id }}">
  <div class="collapsible-bar" data-role="section-bar">
    <span class="bar-title">📢 公告 ({{ announcements|length }})</span>
    <span class="bar-chevron">▶</span>
  </div>
  <div class="collapsible-preview">
    <ul class="ann-bullet-list">
      {% for a in announcements %}
      <li>
        <a class="ann-bullet" data-ann-id="{{ a.id }}" role="button" tabindex="0">{{ a.title }}</a>
      </li>
      {% endfor %}
    </ul>
  </div>
  <div class="collapsible-body">
    {% for a in announcements %}
    <div class="ann-item is-expanded" data-ann-id="{{ a.id }}">
      <a class="ann-bullet" data-ann-id="{{ a.id }}" role="button" tabindex="0">{{ a.title }}</a>
      <article class="announcement-card {% if a.is_global %}is-global{% endif %}"
               data-ann-id="{{ a.id }}">
        <header class="ann-header">
          <span class="scope-chip">{{ '全部' if a.is_global else a.store }}</span>
          <h3 class="ann-title">{{ a.title }}</h3>
        </header>
        <div class="ann-content">{{ a.content }}</div>
        <div class="ann-attachments" data-ann-id="{{ a.id }}"></div>
        <footer class="ann-footer">
          <span class="ann-author">{{ a.display_author }}</span>
          <time class="ann-time">{{ ann_times[a.id] }}</time>
          {% if a.id in ann_edited_ids %}
          <span class="ann-edited-chip">已編輯</span>
          {% endif %}
          {% if a.id in editable_ann_ids %}
          <a class="btn btn-secondary btn-sm"
             href="{{ url_for('notes.announcement_edit_page', announcement_id=a.id) }}">編輯</a>
          {% endif %}
        </footer>
      </article>
    </div>
    {% endfor %}
  </div>
</section>

<script src="{{ url_for('static', filename='js/announcement_attachments.js') }}"></script>
<script>
document.querySelectorAll('.ann-attachments[data-ann-id]').forEach(function(holder) {
  var id = holder.getAttribute('data-ann-id');
  fetch('/notes/announcement/api/' + id + '/attachments')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      (d.attachments || []).forEach(function(att) {
        holder.appendChild(renderAnnAttView(att));
      });
    });
});
</script>
{% endif %}
```

注意：
- 同個公告**會出現兩次** `.ann-bullet`（一在 preview、一在 body 內 ann-item）— CSS 控顯示
- `.ann-item` 預設 `is-expanded` class（全展開 mode 下 body 內所有 ann-item card 都展開）；個別 toggle 由 JS 加 / 移 `is-expanded` class
- attachment fetch 仍依現有 pattern（每個 holder 個別 fetch）

- [ ] **Step 2: 跑 Task 1 第 2 條 + 第 3 條 test 確認 announcement 部分 pass**

```bash
pytest tests/test_notes_collapsible_sections.py::test_announcement_section_has_collapsible_wrapper_and_max_id tests/test_notes_collapsible_sections.py::test_each_announcement_has_bullet_and_card_wrappers -v
```

Expected: 兩條 PASS

- [ ] **Step 3: 跑既有 announcement integration test 確認沒打到別人**

```bash
pytest tests/test_announcements_index_integration.py tests/test_announcements_api.py tests/test_announcements_permission.py -v
```

Expected: 全 PASS（行為沒改、只是 wrapper 多 div）

- [ ] **Step 4: Commit**

```bash
git add templates/announcements/_card_list.html
git commit -m "feat(notes): wrap announcements in collapsible section with bullet preview"
```

---

## Task 4: 改 `notes/index.html`（AI 摘要 section + 載入 CSS/JS）

**Files:**
- Modify: `templates/notes/index.html`

- [ ] **Step 1: 加 CSS link**

在 line 30 後面（`fullscreen_viewer.css` 之後）加：

```jinja
<link rel="stylesheet" href="{{ url_for('static', filename='css/collapsible_sections.css') }}">
```

- [ ] **Step 2: 改 AI 摘要 card（line 103-132）**

把這段：

```jinja
{% if not current_keyword %}
<div class="card" style="margin-bottom:1rem;">
  <div style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;">
    <strong style="font-size:.95rem;">🤖 AI 摘要</strong>
    ...（原 AI 摘要全部內容）
  </div>
  ...
</div>
{% endif %}
```

整段替換成：

```jinja
{% if not current_keyword %}
<section class="collapsible" data-section="ai-summary">
  <div class="collapsible-bar" data-role="section-bar">
    <span class="bar-title">🤖 AI 摘要</span>
    <span class="bar-chevron">▶</span>
  </div>
  <div class="collapsible-body">
    <div style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;">
      <strong style="font-size:.95rem;">🤖 AI 摘要</strong>
      {% if current_user.is_super_admin() %}
      <select id="ai-store-select" style="font-size:.85rem;padding:.3rem .5rem;border-radius:6px;border:1px solid #ddd;min-height:36px;">
        {% if not current_user.store %}
        <option value="" selected disabled>-- 請選擇店別 --</option>
        {% endif %}
        {% for s in stores %}
        <option value="{{ s }}" {% if current_user.store == s %}selected{% endif %}>{{ s }} 店</option>
        {% endfor %}
      </select>
      {% else %}
      <input type="hidden" id="ai-store-select" value="{{ current_user.store }}">
      <span style="font-size:.85rem;color:#666;">{{ current_user.store }} 店</span>
      {% endif %}
      <button type="button" id="ai-bd-today" data-bd="today" onclick="selectBusinessDay('today')" style="font-size:.82rem;padding:.35rem .65rem;border-radius:6px;border:2px solid #3498db;background:#3498db;color:#fff;cursor:pointer;font-weight:600;">今天 {{ ai_today_label }}</button>
      <button type="button" id="ai-bd-yesterday" data-bd="yesterday" onclick="selectBusinessDay('yesterday')" style="font-size:.82rem;padding:.35rem .65rem;border-radius:6px;border:2px solid #ddd;background:#fff;color:#555;cursor:pointer;font-weight:600;">昨天 {{ ai_yesterday_label }}</button>
      <button class="btn btn-primary" id="btn-ai-summary" onclick="generateSummary()" style="font-size:.85rem;padding:.4rem .8rem;">產生摘要</button>
      <span id="ai-status" style="font-size:.8rem;color:#666;"></span>
    </div>
    <div style="font-size:.75rem;color:#888;margin-top:.35rem;">ⓘ 以早上 8 點為營業日分界。8 點後要看 8 點前的事項請選「昨天」。</div>
    <div id="ai-summary-wrap" style="display:none;position:relative;margin-top:.75rem;">
      <button type="button" class="summary-fullscreen-toggle" onclick="toggleSummaryFullscreen('ai-summary-wrap')" title="全螢幕">⛶</button>
      <div id="ai-summary-output" style="background:#f8f9fa;border:1px solid #e0e0e0;border-radius:8px;padding:1rem;padding-right:3rem;white-space:pre-wrap;font-size:.88rem;line-height:1.6;"></div>
    </div>
    <button id="btn-save-summary" class="btn btn-success" style="display:none;margin-top:.5rem;font-size:.85rem;padding:.4rem .8rem;" onclick="saveSummaryAsNote()">💾 儲存為筆記</button>
  </div>
</section>
{% endif %}
```

注意：
- 移除 bar 內第二個重複的 `<strong>🤖 AI 摘要</strong>` — 為了 minimal diff 暫保留（在 body 內、展開時看到）。如要拿掉就刪 body 第一行那行 `<strong>`
- `<section>` 取代外層 `<div class="card">`
- 整段控制鈕跟原本內容**完全不改**（保證 generateSummary / selectBusinessDay 等 onclick 全部還能跑）

- [ ] **Step 3: 在檔案最末加載入 JS**

找 `{% block content %}` 對應的 `{% endblock %}` 區塊結束處（檔案結尾、最後一個 `</script>` 之後），加：

```jinja
<script src="{{ url_for('static', filename='js/collapsible_sections.js') }}" defer></script>
```

（具體位置：`</script>` + `{% endblock %}` 之前；如不確定位置，就放在 `{% endblock %}` 前一行）

- [ ] **Step 4: 跑全 Task 1 test 確認都 PASS**

```bash
pytest tests/test_notes_collapsible_sections.py -v
```

Expected: 4 條全 PASS

- [ ] **Step 5: 跑既有 notes index test 確認沒打到別人**

```bash
pytest tests/test_notes_misc.py tests/test_announcements_index_integration.py -v
```

Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add templates/notes/index.html
git commit -m "feat(notes): wrap AI 摘要 in collapsible section + load assets"
```

---

## Task 5: JS 實作

**Files:**
- Create: `static/js/collapsible_sections.js`

- [ ] **Step 1: 寫 JS**

```javascript
/* collapsible_sections.js — 公告 + AI 摘要可摺疊 toggle 邏輯 */
(function() {
  'use strict';

  var ANN_KEY = 'notes_ann_last_seen';

  function todayStr() {
    var d = new Date();
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var dd = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + dd;
  }

  function readLastSeen() {
    try {
      var raw = localStorage.getItem(ANN_KEY);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return null;
      return {
        date: String(parsed.date || ''),
        last_ann_id: parseInt(parsed.last_ann_id || 0, 10) || 0
      };
    } catch (e) { return null; }
  }

  function writeLastSeen(date, lastAnnId) {
    try {
      localStorage.setItem(ANN_KEY, JSON.stringify({date: date, last_ann_id: lastAnnId}));
    } catch (e) {}
  }

  function shouldAutoExpandAnn(maxAnnId) {
    var stored = readLastSeen();
    if (!stored) return true;
    if (stored.date < todayStr()) return true;
    if (stored.last_ann_id < maxAnnId) return true;
    return false;
  }

  function setSectionExpanded(section, expanded) {
    if (expanded) section.classList.add('is-expanded');
    else section.classList.remove('is-expanded');
  }

  function toggleSection(section) {
    setSectionExpanded(section, !section.classList.contains('is-expanded'));
  }

  function bindSectionBar(section) {
    var bar = section.querySelector('.collapsible-bar[data-role="section-bar"]');
    if (!bar) return;
    bar.addEventListener('click', function() { toggleSection(section); });
  }

  function bindAnnBullets(section) {
    // bullets in preview (collapsed mode) and inside body (per-item) both work
    section.querySelectorAll('.ann-bullet[data-ann-id]').forEach(function(bullet) {
      bullet.addEventListener('click', function(e) {
        e.stopPropagation();
        var annId = bullet.getAttribute('data-ann-id');
        // Find the .ann-item in body matching this id
        var item = section.querySelector('.ann-item[data-ann-id="' + annId + '"]');
        if (!item) return;
        var isInPreview = !item.contains(bullet);
        if (isInPreview) {
          // Section currently collapsed (bullet in preview) → open section + expand this item only
          if (!section.classList.contains('is-expanded')) {
            // Mark all other items as collapsed-individual so only clicked one opens
            section.querySelectorAll('.ann-item').forEach(function(it) {
              if (it !== item) it.classList.remove('is-expanded');
              else it.classList.add('is-expanded');
            });
            setSectionExpanded(section, true);
          } else {
            // Section already expanded → just ensure this one is expanded
            item.classList.add('is-expanded');
          }
        } else {
          // Bullet in body (item currently collapsed) → toggle that item
          item.classList.toggle('is-expanded');
        }
      });
    });
  }

  function initAnnouncementSection() {
    var section = document.querySelector('.collapsible[data-section="announcements"]');
    if (!section) return;
    var maxAnnId = parseInt(section.getAttribute('data-max-ann-id') || '0', 10);
    var auto = shouldAutoExpandAnn(maxAnnId);
    if (auto) {
      setSectionExpanded(section, true);
      // ensure every item starts expanded (default class in template = is-expanded)
      section.querySelectorAll('.ann-item').forEach(function(it) { it.classList.add('is-expanded'); });
      writeLastSeen(todayStr(), maxAnnId);
    } else {
      setSectionExpanded(section, false);
      // strip is-expanded so individual click logic starts clean
      section.querySelectorAll('.ann-item').forEach(function(it) { it.classList.remove('is-expanded'); });
    }
    bindSectionBar(section);
    bindAnnBullets(section);
  }

  function initAiSummarySection() {
    var section = document.querySelector('.collapsible[data-section="ai-summary"]');
    if (!section) return;
    setSectionExpanded(section, false); // always collapsed
    bindSectionBar(section);
  }

  function init() {
    initAnnouncementSection();
    initAiSummarySection();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
```

- [ ] **Step 2: 跑全 test 確認沒 regression**

```bash
pytest tests/test_notes_collapsible_sections.py -v
```

Expected: 4 條全 PASS（JS 不影響 server-side markup）

- [ ] **Step 3: Commit**

```bash
git add static/js/collapsible_sections.js
git commit -m "feat(notes): collapsible_sections.js — auto-expand + toggle handlers"
```

---

## Task 6: SW cache bump

**Files:**
- Modify: `static/sw.js`

- [ ] **Step 1: bump CACHE_NAME**

`static/sw.js` line 8：

```javascript
// 舊
const CACHE_NAME = 'note-weather-v22';
// 新
const CACHE_NAME = 'note-weather-v23';
```

- [ ] **Step 2: Commit**

```bash
git add static/sw.js
git commit -m "chore(sw): bump CACHE_NAME v22 -> v23 for collapsible sections deploy"
```

---

## Task 7: 全測試套件 + verification-before-completion

**Files:** N/A（驗收）

- [ ] **Step 1: 跑全 pytest 套件**

```bash
cd /home/hirain0126/projects/webapp/app_unified
source ../venv/bin/activate
pytest -x -q 2>&1 | tail -30
```

Expected: passed 數 = 既有 430 + 4 新 = 434（或 ±1 if pre-existing flaky `test_range_all_no_date_filter` 又中）、0 failed/error

如果有失敗：
- 新加的 test fail → 回 Task 3/4 補 template
- 既有 test fail → 看是不是我們改的 template 影響（_card_list.html 結構變了、attachment fetch、announcement_attachments.js 路徑 etc）

- [ ] **Step 2: 本機跑 webapp（按 [[feedback_local_test_before_push]]）**

```bash
cd /home/hirain0126/projects/webapp/app_unified
bash scripts/run_local.sh
```

開 Chrome 進 `http://localhost:5000/dev` → 切 admin → 進 `/notes/`

- [ ] **Step 3: User 實機驗證 checklist**

User 自己跑這幾個 case（plan executor 不做、交付 user）：

1. **第一次進**：公告區 auto-expand（全展開）、AI 摘要區摺疊
2. **F5 reload 同一天**：公告區摺疊（顯示標題列表）、AI 摘要區摺疊
3. **DevTools 改本地時間+1 天 / 改 localStorage `notes_ann_last_seen` 的 date 往前一天**：reload → 公告 auto-expand
4. **同一天 admin 新增公告 → user reload**：auto-expand（max_ann_id 變大）
5. **點公告 section bar**：toggle 全展開 ↔ 全摺疊
6. **摺疊狀態下點個別標題 bullet**：該篇展開、其他維持摺疊
7. **AI 摘要點 bar**：展開 → 看到所有控制鈕；產生摘要 OK；點 bar 收起 → 再展開 output 還在
8. **關鍵字搜尋**：兩 section 都不出現
9. **chevron 視覺**：摺疊 `▶`、展開 `▼`，切換有 200ms 旋轉動畫
10. **Mobile 模擬**：bar tap 區域夠大、不誤觸

- [ ] **Step 4: User 說 OK → ff-merge main + push（**等 user 明確說「可以 push」才做、按 [[feedback_push_policy]]**）**

```bash
git checkout main
git merge --ff-only feat/notes-collapsible-sections
git push origin main
git branch -d feat/notes-collapsible-sections
```

- [ ] **Step 5: Zeabur 自動 deploy 後 prod 實機驗收**

GHA Docker build 綠 → user 在 Zeabur Redeploy（或自動）→ 進 prod `/notes/` 跑同 9 條驗證 case。

---

## Risks 待 Implementer 注意

| Risk | Mitigation |
|---|---|
| `_card_list.html` 改完每篇公告 `.ann-bullet` 出現兩次（preview + body）導致 dup ID 警告 | 用 `data-ann-id` 不用 `id`、避免 dup `id` HTML 警告 |
| `announcement_attachments.js` 既有 fetch logic 依賴 `.ann-attachments[data-ann-id]` selector 仍可運作 | 不動 attachment fetch script、selector 仍合（每個 ann card 內仍有 `.ann-attachments[data-ann-id]`） |
| AI 摘要 body 內有兩個 `🤖 AI 摘要` 字樣（bar 一個、body 第一行 strong 一個） | Cosmetic、收尾如要清就刪 body 內的 strong；不影響功能 |
| SW v22 cache 強制 client 重抓、user 第一次進可能 lag | 已 well-known pattern、user 看過、不另處理 |
| localStorage 跨 device 不一致 | 設計接受、spec 已交代 |
| 跑 `pytest -x` 中遇到 pre-existing flaky `test_range_all_no_date_filter` | 已知、單獨跑就 pass、不阻塞 |

---

## Self-Review

**Spec 覆蓋率**:
- [x] 公告 Mode 1/2/3 → Task 3 (template 結構) + Task 5 (JS toggle)
- [x] auto-expand 觸發邏輯 (date + max_ann_id) → Task 5 step 1 `shouldAutoExpandAnn`
- [x] AI 摘要永遠摺疊 → Task 5 `initAiSummarySection` `setSectionExpanded(false)`
- [x] chevron 動畫 → Task 2 `.collapsible.is-expanded > .collapsible-bar .bar-chevron { transform: rotate(90deg); }`
- [x] 整條 bar 可點 → Task 5 `bindSectionBar` 綁在整個 `.collapsible-bar`
- [x] 個別 card 點開 → Task 5 `bindAnnBullets` `e.stopPropagation`
- [x] 不持久化手動 toggle → Task 5 沒寫狀態、auto-expand 才寫
- [x] 關鍵字搜尋兩 section 不出現 → Task 3/4 都包在現有 `{% if not current_keyword %}` 之內
- [x] 邊界條件 (localStorage 禁用 / JSON parse error / max_ann_id=0 / 沒公告) → Task 5 都有 try/catch + 預設值
- [x] 測試策略 4 條 markup smoke → Task 1
- [x] 9 條手動 verification case → Task 7 step 3

**Placeholder scan**: 無 TBD / TODO / 「依需求加錯誤處理」/「實作細節後補」之類。

**Type consistency**: `setSectionExpanded` / `shouldAutoExpandAnn` / `bindSectionBar` / `initAnnouncementSection` 等 function name 跨 task 一致。

**Bite-size**: 每個 step 2-5 分鐘可完成。最大 step（Task 3 step 1 重寫 template）較長但是純 copy-paste，無 logic。
