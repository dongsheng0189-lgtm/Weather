# F10 Attachment Swipe Gallery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 fullscreen viewer 上加左右滑切換相鄰附件（LINE / IG 風格），擴展到 4 個 surface：店務筆記 + 確認表單 + 公告 + mynotes。

**Architecture:** 擴展既有 `app_unified/static/js/fullscreen_viewer.js`，新加 `openFullscreenGallery(items, startIdx)` API。底層用 CSS `scroll-snap-type: x mandatory` + `scroll-snap-stop: always` 處理手機 swipe，桌面加 overlay arrow button + keyboard ← →，counter 右上顯示「N / total」。圖+影 interleaved，影片頁顯示 poster + 中央 ▶ 大圓鈕（user 點才播），切離自動 pause + reset。舊 `openFullscreen(url, opts)` 改成 thin backward-compat shim、F9 single-image path 不破。

**Tech Stack:** Vanilla JS（無 dep）、CSS scroll-snap、Pointer Events API、Flask + SQLAlchemy + Jinja2、pytest。

**Spec:** `app_unified/docs/superpowers/specs/2026-05-28-attachment-swipe-gallery-design.md`

**Branch:** `feat/attachment-swipe-gallery` (已開、HEAD `e68e06b` = spec commit)

---

## Task 1: Attachment ordering guard

**Files:**
- Create: `app_unified/tests/test_attachment_ordering.py`

新增 3 條 test 確保 NoteAttachment / AnnouncementAttachment / PrivateAttachment 從 DB 撈出來的順序固定 = id asc（upload order）。Caller 在 build `items[]` 時依賴此順序。

- [ ] **Step 1: Write the failing test**

```python
"""Guard: attachment lists are returned in id asc (upload order).

Caller build items[] for fullscreen swipe gallery depends on this ordering.
If anyone adds default order_by elsewhere, this test will catch the regression.
"""
import pytest
from app import create_app
from models import db, User, Store, Note, NoteAttachment, ChecklistItem
from announcements.models import Announcement, AnnouncementAttachment
from mynotes.models import PrivateNote, PrivateAttachment


@pytest.fixture
def app():
    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _make_store_user(app):
    store = Store(name='A店')
    db.session.add(store)
    db.session.commit()
    user = User(username='u1', store_id=store.id)
    user.set_password('x')
    db.session.add(user)
    db.session.commit()
    return store, user


def test_note_attachment_order_is_id_asc(app):
    store, user = _make_store_user(app)
    note = Note(store_id=store.id, author_id=user.id, content='c')
    db.session.add(note)
    db.session.commit()
    a = NoteAttachment(note_id=note.id, object_key='k1', content_type='image/png', filename='a.png')
    b = NoteAttachment(note_id=note.id, object_key='k2', content_type='video/mp4', filename='b.mp4')
    c = NoteAttachment(note_id=note.id, object_key='k3', content_type='image/png', filename='c.png')
    db.session.add_all([a, b, c])
    db.session.commit()
    db.session.refresh(note)
    ids = [att.id for att in note.attachments]
    assert ids == sorted(ids), f'NoteAttachment order broken: {ids}'


def test_announcement_attachment_order_is_id_asc(app):
    store, user = _make_store_user(app)
    ann = Announcement(store_id=store.id, author_id=user.id, title='t', content='c')
    db.session.add(ann)
    db.session.commit()
    a = AnnouncementAttachment(announcement_id=ann.id, object_key='k1', content_type='image/png', filename='a.png')
    b = AnnouncementAttachment(announcement_id=ann.id, object_key='k2', content_type='video/mp4', filename='b.mp4')
    db.session.add_all([a, b])
    db.session.commit()
    db.session.refresh(ann)
    ids = [att.id for att in ann.attachments]
    assert ids == sorted(ids), f'AnnouncementAttachment order broken: {ids}'


def test_private_attachment_order_is_id_asc(app):
    store, user = _make_store_user(app)
    pn = PrivateNote(user_id=user.id, content='c')
    db.session.add(pn)
    db.session.commit()
    a = PrivateAttachment(private_note_id=pn.id, object_key='k1', content_type='image/png', filename='a.png')
    b = PrivateAttachment(private_note_id=pn.id, object_key='k2', content_type='image/png', filename='b.png')
    db.session.add_all([a, b])
    db.session.commit()
    db.session.refresh(pn)
    ids = [att.id for att in pn.attachments]
    assert ids == sorted(ids), f'PrivateAttachment order broken: {ids}'
```

- [ ] **Step 2: Run test to verify it passes (確認既有 order 已正確)**

Run: `cd app_unified && pytest tests/test_attachment_ordering.py -v`

Expected: 3 passed (這是 guard test、若 fail 表示既有 model 沒設 cascade order 需要先 fix)。若 fail 看實際 import path / model field name 差異，調整 fixture 後再跑。

若某 model 真的沒按 id asc，加 `order_by` 到 relationship、e.g. `attachments = relationship('NoteAttachment', order_by='NoteAttachment.id', cascade=...)`。

- [ ] **Step 3: Commit**

```bash
git add app_unified/tests/test_attachment_ordering.py
git commit -m "test: guard attachment list order is id asc"
```

---

## Task 2: fullscreen_viewer.js — gallery skeleton + CSS + backward compat

**Files:**
- Modify: `app_unified/static/js/fullscreen_viewer.js` (248 lines → ~330)
- Modify: `app_unified/static/css/fullscreen_viewer.css` (10 lines → ~25)
- Create: `app_unified/tests/test_fullscreen_viewer_gallery_skeleton.py`

新加 `openFullscreenGallery(items, startIdx)` 把單張 `<img>` 換成 `<div class="fs-track">` + N 個 `.fs-slide`。舊 `openFullscreen(url, opts)` 改 thin wrapper。chrome（counter / arrow / close / download / hint）動態 show/hide 依 `items.length`。

- [ ] **Step 1: Write the failing tests**

```python
"""Smoke: fullscreen_viewer.js gallery skeleton + backward compat shim."""
from pathlib import Path

VIEWER_JS = Path(__file__).parent.parent / 'static' / 'js' / 'fullscreen_viewer.js'
VIEWER_CSS = Path(__file__).parent.parent / 'static' / 'css' / 'fullscreen_viewer.css'


def test_gallery_api_export():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'window.openFullscreenGallery' in src
    assert 'function openFullscreenGallery(' in src


def test_backward_compat_shim_for_openfullscreen():
    """openFullscreen(url, opts) must call gallery with single-item array."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    # shim definition
    assert 'openFullscreen = function' in src or 'window.openFullscreen = function' in src
    # shim body should construct a single-item array and delegate
    assert 'openFullscreenGallery([{' in src or 'openFullscreenGallery([' in src


def test_track_and_slide_markup_in_ensuremarkup():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert "id=\"fs-track\"" in src or "id='fs-track'" in src
    assert 'class="fs-slide"' in src or "class='fs-slide'" in src


def test_chrome_elements_present():
    """counter + prev + next + download + close + hint all in markup."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    for cls in ['fs-counter', 'fs-prev', 'fs-next', 'fs-close', 'fs-download', 'fs-hint']:
        assert cls in src, f'missing chrome element: {cls}'


def test_css_scroll_snap():
    css = VIEWER_CSS.read_text(encoding='utf-8')
    assert 'scroll-snap-type: x mandatory' in css
    assert 'scroll-snap-align: center' in css
    assert 'scroll-snap-stop: always' in css
    assert 'overscroll-behavior-x: contain' in css


def test_css_arrow_hidden_on_touch():
    css = VIEWER_CSS.read_text(encoding='utf-8')
    assert '@media (hover: none)' in css
    # arrows hidden under hover:none
    assert '.fs-prev' in css and '.fs-next' in css
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && pytest tests/test_fullscreen_viewer_gallery_skeleton.py -v`

Expected: 6 failed (skeleton 還沒 implement)

- [ ] **Step 3: Update CSS (`app_unified/static/css/fullscreen_viewer.css`)**

把 fullscreen-overlay 內 `img` 那條規則保留（既有 single-image fallback 視覺仍要對）。在檔尾加新規則：

```css
/* F10 swipe gallery additions */
.fs-track { display: flex; width: 100vw; height: 100vh; overflow-x: auto; scroll-snap-type: x mandatory; scroll-behavior: smooth; scrollbar-width: none; overscroll-behavior-x: contain; }
.fs-track::-webkit-scrollbar { display: none; }
.fs-slide { flex: 0 0 100vw; height: 100vh; scroll-snap-align: center; scroll-snap-stop: always; display: flex; align-items: center; justify-content: center; position: relative; }
.fs-slide .fs-img { max-width: 100vw; max-height: 100vh; object-fit: contain; transform-origin: center center; will-change: transform; pointer-events: none; -webkit-user-drag: none; user-drag: none; }
.fs-prev, .fs-next { position: fixed; top: 50%; transform: translateY(-50%); width: 48px; height: 80px; background: rgba(0,0,0,.4); color: #fff; border: none; font-size: 32px; cursor: pointer; z-index: 10; }
.fs-prev { left: 0; border-radius: 0 8px 8px 0; }
.fs-next { right: 0; border-radius: 8px 0 0 8px; }
.fs-prev[disabled], .fs-next[disabled] { opacity: .3; cursor: default; }
@media (hover: none) { .fs-prev, .fs-next { display: none !important; } }
.fs-counter { position: fixed; top: 16px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,.5); color: #fff; padding: 4px 12px; border-radius: 12px; font: 14px/1 system-ui; z-index: 10; }
.fs-video-wrap { position: relative; max-width: 100vw; max-height: 100vh; }
.fs-video-wrap video { max-width: 100vw; max-height: 100vh; display: block; }
.fs-video-play { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 80px; height: 80px; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff; font-size: 36px; border: 2px solid #fff; cursor: pointer; z-index: 5; }
.fs-video-wrap.playing .fs-video-play { display: none; }
```

- [ ] **Step 4: Rewrite `_ensureMarkup` in fullscreen_viewer.js**

替換既有 `_ensureMarkup` function (line ~143):

```js
function _ensureMarkup() {
  if (document.getElementById('fullscreen-overlay')) return;
  var overlay = document.createElement('div');
  overlay.className = 'fullscreen-overlay';
  overlay.id = 'fullscreen-overlay';
  overlay.innerHTML =
    '<div class="fs-track" id="fs-track"></div>' +
    '<button type="button" class="fs-prev" id="fs-prev" aria-label="上一張">&#8249;</button>' +
    '<button type="button" class="fs-next" id="fs-next" aria-label="下一張">&#8250;</button>' +
    '<div class="fs-counter" id="fs-counter"></div>' +
    '<button type="button" class="fs-close" aria-label="關閉">&#10005;</button>' +
    '<button type="button" class="fs-download" id="fs-download" aria-label="下載原檔" style="display:none;">' + DOWNLOAD_ICON_SVG(32) + '</button>' +
    '<div class="fs-hint">&#8592;/&#8594; 切換 · 雙指縮放 · 雙擊放大 · 拖曳移動</div>';
  document.body.appendChild(overlay);
  overlay.querySelector('.fs-close').addEventListener('click', closeFullscreen);
  overlay.querySelector('.fs-download').addEventListener('click', function(e) {
    e.stopPropagation();
    var href = this.dataset.href;
    if (!href || this.disabled) return;
    var btn = this;
    btn.disabled = true;
    downloadAttachment(href).finally(function() { btn.disabled = false; });
  });
}
```

注意：舊版裡的 `<div class="fs-stage" id="fs-stage">` + `<img id="fullscreen-img">` **拿掉**（會由 `openFullscreenGallery` 動態 build 進 fs-track）。

- [ ] **Step 5: Add `_escapeHtml` helper inside IIFE (放在 DOWNLOAD_ICON_SVG 下方)**

```js
function _escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, function(c) {
    return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
  });
}
```

- [ ] **Step 6: Add `_items` + `_currentIdx` module state (放在 `_fsState` 下方)**

```js
var _items = [];
var _currentIdx = 0;
```

- [ ] **Step 7: Implement `openFullscreenGallery` (取代既有 `openFullscreen` body、且讓 `openFullscreen` 變 backward compat shim)**

刪除既有 `function openFullscreen(url, opts)` body (line ~187-203)，換成：

```js
function openFullscreenGallery(items, startIdx) {
  if (!items || items.length === 0) return;
  _fsInit();
  _items = items.slice();
  _currentIdx = Math.max(0, Math.min(startIdx || 0, _items.length - 1));

  var track = document.getElementById('fs-track');
  track.innerHTML = '';
  _items.forEach(function(item, i) {
    var slide = document.createElement('section');
    slide.className = 'fs-slide';
    slide.dataset.idx = String(i);
    slide.dataset.kind = item.kind;
    if (item.kind === 'image') {
      slide.innerHTML = '<div class="fs-stage"><img class="fs-img" data-src="' + _escapeHtml(item.url) + '"></div>';
    } else {
      slide.innerHTML = '<div class="fs-video-wrap"><video preload="metadata" playsinline data-src="' + _escapeHtml(item.url) + '"></video><button type="button" class="fs-video-play" aria-label="播放">&#9654;</button></div>';
    }
    track.appendChild(slide);
  });

  var multi = _items.length > 1;
  document.getElementById('fs-prev').style.display = multi ? '' : 'none';
  document.getElementById('fs-next').style.display = multi ? '' : 'none';
  document.getElementById('fs-counter').style.display = multi ? '' : 'none';

  var overlay = document.getElementById('fullscreen-overlay');
  overlay.classList.add('open');

  requestAnimationFrame(function() {
    track.scrollLeft = _currentIdx * window.innerWidth;
    /* counter / arrow / download initial state — Task 3 _onPageChange takes over */
    _onPageChangeInitial(_currentIdx);
  });
}

/* Initial chrome state when opening (no scrollend event yet). Real _onPageChange in Task 3. */
function _onPageChangeInitial(idx) {
  var item = _items[idx];
  var counter = document.getElementById('fs-counter');
  if (counter) counter.textContent = (idx + 1) + ' / ' + _items.length;
  var dlBtn = document.getElementById('fs-download');
  if (dlBtn) {
    if (item && item.downloadHref) {
      dlBtn.style.display = '';
      dlBtn.dataset.href = item.downloadHref;
    } else {
      dlBtn.style.display = 'none';
      dlBtn.dataset.href = '';
    }
  }
  /* lazy load current — Task 6 will add ±1 */
  var slide = document.querySelector('.fs-slide[data-idx="' + idx + '"]');
  if (slide) {
    var media = slide.querySelector('img, video');
    if (media && media.dataset.src && !media.src) media.src = media.dataset.src;
  }
}

/* Backward compat — F9 single-image callers (notes/editor + announcement_attachments) still work */
function openFullscreen(url, opts) {
  opts = opts || {};
  openFullscreenGallery([{ kind: 'image', url: url, downloadHref: opts.downloadHref || null }], 0);
}
```

`closeFullscreen` 改寫：

```js
function closeFullscreen() {
  var overlay = document.getElementById('fullscreen-overlay');
  if (!overlay) return;
  overlay.classList.remove('open');
  var track = document.getElementById('fs-track');
  if (track) track.innerHTML = '';
  _items = [];
  _currentIdx = 0;
  _fsReset();
}
```

`_fsInit` 內 stage binding 暫時改成「等 Task 4/5 重接到當前 slide」— 此 Task 先把舊 `document.getElementById('fs-stage')` 那段拿掉避免報錯：

```js
function _fsInit() {
  _ensureMarkup();
  /* pointer + dblclick + wheel handlers bind per-slide in Task 5 — Task 2 skip */
}
```

- [ ] **Step 8: Update `window.openFullscreen` / `window.closeFullscreen` exports at bottom**

```js
window.openFullscreen = openFullscreen;
window.openFullscreenGallery = openFullscreenGallery;
window.closeFullscreen = closeFullscreen;
window.downloadAttachment = downloadAttachment;
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd app_unified && pytest tests/test_fullscreen_viewer_gallery_skeleton.py -v`

Expected: 6 passed

- [ ] **Step 10: Commit**

```bash
git add app_unified/static/js/fullscreen_viewer.js app_unified/static/css/fullscreen_viewer.css app_unified/tests/test_fullscreen_viewer_gallery_skeleton.py
git commit -m "feat(viewer): openFullscreenGallery API + scroll-snap markup/CSS + backward compat shim"
```

---

## Task 3: Page tracking + counter + arrow nav + keyboard

**Files:**
- Modify: `app_unified/static/js/fullscreen_viewer.js`
- Create: `app_unified/tests/test_fullscreen_viewer_nav.py`

加 `scrollend` listener + `_onPageChange` 更新 counter / arrow disabled / download href。Prev/Next click + keyboard ← → / Esc。Feature-detect `scrollend`、若不支援 fallback `scroll` + 200ms debounce。

- [ ] **Step 1: Write the failing tests**

```python
"""Page nav: scrollend / arrow / keyboard."""
from pathlib import Path

VIEWER_JS = Path(__file__).parent.parent / 'static' / 'js' / 'fullscreen_viewer.js'


def test_scrollend_listener():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'scrollend' in src
    assert "'onscrollend' in window" in src or 'onscrollend in window' in src
    # debounced scroll fallback
    assert 'addEventListener' in src and "'scroll'" in src


def test_onpagechange_updates_chrome():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'function _onPageChange(' in src
    # counter text format
    assert "(idx + 1) + ' / ' + _items.length" in src or '${newIdx + 1} / ${_items.length}' in src or "(newIdx + 1) + ' / ' + _items.length" in src
    # arrow disabled state
    assert 'prevBtn.disabled' in src or "fs-prev').disabled" in src
    assert 'nextBtn.disabled' in src or "fs-next').disabled" in src


def test_arrow_button_click_handlers():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert "fs-prev" in src
    assert "fs-next" in src
    assert 'function _goTo(' in src


def test_keyboard_handlers():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert "'keydown'" in src
    assert "'ArrowLeft'" in src
    assert "'ArrowRight'" in src
    assert "'Escape'" in src


def test_boundary_stop_not_loop():
    """_goTo should return early on out-of-bound, not wrap."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'function _goTo(' in src
    # the function body has early-return for boundary
    assert 'idx < 0' in src
    assert 'idx >= _items.length' in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && pytest tests/test_fullscreen_viewer_nav.py -v`

Expected: 5 failed

- [ ] **Step 3: Add `_onPageChange` + `_goTo` functions to fullscreen_viewer.js**

放在 `_onPageChangeInitial` 下方、取代它（`openFullscreenGallery` 改 call `_onPageChange`）：

```js
function _onPageChange(newIdx) {
  if (newIdx < 0 || newIdx >= _items.length) return;
  _pauseAndResetVideo(_currentIdx);   /* Task 4 implements — placeholder no-op below */
  _currentIdx = newIdx;
  var item = _items[newIdx];
  var counter = document.getElementById('fs-counter');
  if (counter) counter.textContent = (newIdx + 1) + ' / ' + _items.length;
  var dlBtn = document.getElementById('fs-download');
  if (dlBtn) {
    if (item && item.downloadHref) {
      dlBtn.style.display = '';
      dlBtn.dataset.href = item.downloadHref;
    } else {
      dlBtn.style.display = 'none';
      dlBtn.dataset.href = '';
    }
  }
  var prevBtn = document.getElementById('fs-prev');
  var nextBtn = document.getElementById('fs-next');
  if (prevBtn) prevBtn.disabled = (newIdx === 0);
  if (nextBtn) nextBtn.disabled = (newIdx === _items.length - 1);
  _fsReset();
  _ensureLoaded(newIdx);              /* Task 6 implements — placeholder below */
  _ensureLoaded(newIdx - 1);
  _ensureLoaded(newIdx + 1);
}

function _goTo(idx) {
  if (idx < 0 || idx >= _items.length) return;
  var track = document.getElementById('fs-track');
  if (!track) return;
  track.scrollTo({ left: idx * window.innerWidth, behavior: 'smooth' });
  /* scrollend will fire _onPageChange — but if user just clicks arrow rapidly, also call directly to keep chrome in sync */
  /* defer to scrollend; if browser fallback used, scroll handler fires */
}

/* Placeholders — implemented in later tasks */
function _pauseAndResetVideo(idx) { /* Task 4 */ }
function _ensureLoaded(idx) {
  if (idx < 0 || idx >= _items.length) return;
  var slide = document.querySelector('.fs-slide[data-idx="' + idx + '"]');
  if (!slide) return;
  var media = slide.querySelector('img, video');
  if (media && media.dataset.src && !media.src) media.src = media.dataset.src;
}
```

刪除 Task 2 的 `_onPageChangeInitial` function、把 `openFullscreenGallery` 內呼叫改成 `_onPageChange(_currentIdx)`。

- [ ] **Step 4: Bind arrow / keyboard / scrollend in `_fsInit`**

替換 `_fsInit` body：

```js
function _fsInit() {
  _ensureMarkup();
  var track = document.getElementById('fs-track');
  if (track && !track._fsBound) {
    track._fsBound = true;

    /* page tracking — prefer scrollend, fallback to scroll + debounce */
    function _onScrollSettled() {
      var idx = Math.round(track.scrollLeft / window.innerWidth);
      if (idx !== _currentIdx) _onPageChange(idx);
    }
    if ('onscrollend' in window) {
      track.addEventListener('scrollend', _onScrollSettled);
    } else {
      var _scrollTimer = null;
      track.addEventListener('scroll', function() {
        if (_scrollTimer) clearTimeout(_scrollTimer);
        _scrollTimer = setTimeout(_onScrollSettled, 200);
      });
    }
  }

  var prevBtn = document.getElementById('fs-prev');
  var nextBtn = document.getElementById('fs-next');
  if (prevBtn && !prevBtn._fsBound) {
    prevBtn._fsBound = true;
    prevBtn.addEventListener('click', function() { _goTo(_currentIdx - 1); });
  }
  if (nextBtn && !nextBtn._fsBound) {
    nextBtn._fsBound = true;
    nextBtn.addEventListener('click', function() { _goTo(_currentIdx + 1); });
  }

  if (!document._fsKeysBound) {
    document._fsKeysBound = true;
    document.addEventListener('keydown', function(e) {
      var overlay = document.getElementById('fullscreen-overlay');
      if (!overlay || !overlay.classList.contains('open')) return;
      if (e.key === 'ArrowLeft')  { _goTo(_currentIdx - 1); e.preventDefault(); }
      else if (e.key === 'ArrowRight') { _goTo(_currentIdx + 1); e.preventDefault(); }
      else if (e.key === 'Escape')     { closeFullscreen(); }
    });
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd app_unified && pytest tests/test_fullscreen_viewer_nav.py tests/test_fullscreen_viewer_gallery_skeleton.py -v`

Expected: 11 passed (5 new + 6 existing skeleton)

- [ ] **Step 6: Commit**

```bash
git add app_unified/static/js/fullscreen_viewer.js app_unified/tests/test_fullscreen_viewer_nav.py
git commit -m "feat(viewer): scrollend page tracking + arrow + keyboard ←/→/Esc nav"
```

---

## Task 4: Video slide rendering + pause-on-leave

**Files:**
- Modify: `app_unified/static/js/fullscreen_viewer.js`
- Create: `app_unified/tests/test_fullscreen_viewer_video.py`

Image / video slide 已分支 render（Task 2）、本 Task 補：(a) 點 ▶ 鈕後設 `video.controls = true` + `.play()` + 加 `.playing` class、(b) `_pauseAndResetVideo` 真正 pause + reset + 移除 controls + 移除 .playing。

- [ ] **Step 1: Write the failing tests**

```python
"""Video slide: poster + center play button + pause-on-leave."""
from pathlib import Path

VIEWER_JS = Path(__file__).parent.parent / 'static' / 'js' / 'fullscreen_viewer.js'


def test_video_slide_markup():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'fs-video-wrap' in src
    assert 'fs-video-play' in src
    assert 'playsinline' in src
    assert 'preload="metadata"' in src


def test_play_button_handler():
    src = VIEWER_JS.read_text(encoding='utf-8')
    # play button binding inside _fsInit or per-slide
    assert 'fs-video-play' in src
    assert '.play()' in src
    assert 'video.controls = true' in src
    assert "classList.add('playing')" in src


def test_pause_and_reset_video():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'function _pauseAndResetVideo(' in src
    assert '.pause()' in src
    assert 'currentTime = 0' in src
    assert 'video.controls = false' in src
    assert "classList.remove('playing')" in src


def test_close_pauses_all_videos():
    """closeFullscreen should pause all video slides before clearing."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    # closeFullscreen body iterates _items and calls _pauseAndResetVideo
    close_section = src.split('function closeFullscreen')[1].split('function ')[0]
    assert '_pauseAndResetVideo' in close_section
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && pytest tests/test_fullscreen_viewer_video.py -v`

Expected: 4 failed

- [ ] **Step 3: Replace `_pauseAndResetVideo` placeholder with real impl**

```js
function _pauseAndResetVideo(idx) {
  var slide = document.querySelector('.fs-slide[data-idx="' + idx + '"][data-kind="video"]');
  if (!slide) return;
  var video = slide.querySelector('video');
  if (!video) return;
  try { video.pause(); } catch (e) {}
  try { video.currentTime = 0; } catch (e) {}
  video.controls = false;
  var wrap = slide.querySelector('.fs-video-wrap');
  if (wrap) wrap.classList.remove('playing');
}
```

- [ ] **Step 4: Bind play button click via delegation in `_fsInit`**

在 Task 3 的 `_fsInit` 結尾、`document._fsKeysBound` 區塊下面加：

```js
  /* delegate clicks for per-slide play buttons */
  if (track && !track._fsPlayBound) {
    track._fsPlayBound = true;
    track.addEventListener('click', function(e) {
      var btn = e.target.closest && e.target.closest('.fs-video-play');
      if (!btn) return;
      e.stopPropagation();
      var wrap = btn.closest('.fs-video-wrap');
      if (!wrap) return;
      var video = wrap.querySelector('video');
      if (!video) return;
      if (!video.src && video.dataset.src) video.src = video.dataset.src;
      video.controls = true;
      wrap.classList.add('playing');
      video.play().catch(function() { /* iOS may require user gesture, already inside click */ });
    });
  }
```

- [ ] **Step 5: Update `closeFullscreen` to pause all videos before clearing**

替換 Task 2 的 `closeFullscreen`：

```js
function closeFullscreen() {
  var overlay = document.getElementById('fullscreen-overlay');
  if (!overlay) return;
  overlay.classList.remove('open');
  _items.forEach(function(_, i) { _pauseAndResetVideo(i); });
  var track = document.getElementById('fs-track');
  if (track) track.innerHTML = '';
  _items = [];
  _currentIdx = 0;
  _fsReset();
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd app_unified && pytest tests/test_fullscreen_viewer_video.py tests/test_fullscreen_viewer_nav.py tests/test_fullscreen_viewer_gallery_skeleton.py -v`

Expected: 15 passed

- [ ] **Step 7: Commit**

```bash
git add app_unified/static/js/fullscreen_viewer.js app_unified/tests/test_fullscreen_viewer_video.py
git commit -m "feat(viewer): video slide play handler + pause-on-leave + close pauses all"
```

---

## Task 5: Pinch zoom + swipe coexistence

**Files:**
- Modify: `app_unified/static/js/fullscreen_viewer.js`
- Create: `app_unified/tests/test_fullscreen_viewer_zoom_swipe.py`

把 `_fsOnPointerDown / Move / Up / DblClick / Wheel` 重新 bind 到「當前 slide」的 `.fs-stage`（image slide only），切頁時 rebind。Logic：1 pointer + scale === 1 → 不 preventDefault（讓 native scroll-snap 接手 swipe）；其他情況 preventDefault 自己處理 pan / pinch。Zoom > 1.01 時 `track.overflowX = 'hidden'` 鎖換頁，`_fsReset` 恢復 `auto`。

- [ ] **Step 1: Write the failing tests**

```python
"""Pinch zoom + native swipe coexistence."""
from pathlib import Path

VIEWER_JS = Path(__file__).parent.parent / 'static' / 'js' / 'fullscreen_viewer.js'


def test_pointer_handlers_per_slide():
    """Pointer handlers should bind to current slide's fs-stage, not global."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'function _bindPinchHandlersToSlide(' in src
    # called from _onPageChange + initial open
    occurrences = src.count('_bindPinchHandlersToSlide')
    assert occurrences >= 3, f'expected ≥3 references, got {occurrences}'


def test_single_pointer_scale1_does_not_preventdefault():
    """Single pointer + scale===1 must let native scroll-snap handle the swipe."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    # explicit early-return / no-preventDefault branch
    assert '_fsState.scale <= 1.01' in src or '_fsState.scale === 1' in src or 'scale <= 1.01' in src


def test_track_overflow_locked_on_zoom():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert "track.style.overflowX = 'hidden'" in src or 'overflowX = "hidden"' in src
    assert "overflowX = 'auto'" in src or 'overflowX = "auto"' in src


def test_fsreset_restores_overflow():
    src = VIEWER_JS.read_text(encoding='utf-8')
    reset_section = src.split('function _fsReset')[1].split('function ')[0]
    assert 'overflowX' in reset_section
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && pytest tests/test_fullscreen_viewer_zoom_swipe.py -v`

Expected: 4 failed

- [ ] **Step 3: Modify `_fsApply` to lock/unlock track overflow**

替換既有 `_fsApply`：

```js
function _fsApply() {
  var slide = document.querySelector('.fs-slide[data-idx="' + _currentIdx + '"][data-kind="image"]');
  if (!slide) return;
  var img = slide.querySelector('.fs-img');
  if (img) img.style.transform = 'translate(' + _fsState.tx + 'px,' + _fsState.ty + 'px) scale(' + _fsState.scale + ')';
  var track = document.getElementById('fs-track');
  if (track) {
    if (_fsState.scale > 1.01) track.style.overflowX = 'hidden';
    else track.style.overflowX = 'auto';
  }
}
```

替換 `_fsReset`：

```js
function _fsReset() {
  _fsState.scale = 1; _fsState.tx = 0; _fsState.ty = 0;
  var track = document.getElementById('fs-track');
  if (track) track.style.overflowX = 'auto';
  _fsApply();
}
```

替換 `_fsClampPan`（找正確的 img element 來 measure）：

```js
function _fsClampPan() {
  var slide = document.querySelector('.fs-slide[data-idx="' + _currentIdx + '"][data-kind="image"]');
  if (!slide) return;
  var img = slide.querySelector('.fs-img');
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
```

- [ ] **Step 4: Rewrite `_fsOnPointerMove` for swipe coexistence**

替換 `_fsOnPointerMove`：

```js
function _fsOnPointerMove(e) {
  if (!_fsPointers[e.pointerId]) return;
  var prev = _fsPointers[e.pointerId];
  _fsPointers[e.pointerId] = { x: e.clientX, y: e.clientY };
  var ids = Object.keys(_fsPointers);
  if (ids.length === 2) {
    /* 2-finger pinch — always preventDefault, do our own scale */
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
    e.preventDefault();
  } else if (ids.length === 1 && _fsState.scale > 1.01) {
    /* 1-finger pan while zoomed — preventDefault, do our own translate */
    _fsState.tx += e.clientX - prev.x;
    _fsState.ty += e.clientY - prev.y;
    _fsClampPan();
    _fsApply();
    e.preventDefault();
  }
  /* else: 1 pointer + scale === 1 — DO NOT preventDefault, let native scroll-snap handle swipe */
}
```

`_fsOnPointerDown` / `_fsOnPointerUp` 既有版本仍 OK（只記 pointer state）— 但拿掉 `_fsOnPointerDown` 結尾的 `e.preventDefault()`（不能 block 單 finger 接觸、會擋掉 swipe 起手式）：

```js
function _fsOnPointerDown(e) {
  _fsPointers[e.pointerId] = { x: e.clientX, y: e.clientY };
  var ids = Object.keys(_fsPointers);
  if (ids.length === 2) {
    var p1 = _fsPointers[ids[0]], p2 = _fsPointers[ids[1]];
    _fsLastDist = Math.hypot(p2.x - p1.x, p2.y - p1.y);
    _fsLastCenter = { x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 };
    e.preventDefault();   /* 2-finger 才 preventDefault */
  }
}
```

- [ ] **Step 5: Add `_bindPinchHandlersToSlide` + call on page change / open**

```js
function _bindPinchHandlersToSlide(idx) {
  var slide = document.querySelector('.fs-slide[data-idx="' + idx + '"][data-kind="image"]');
  if (!slide) return;
  var stage = slide.querySelector('.fs-stage');
  if (!stage || stage._fsBound) return;
  stage._fsBound = true;
  stage.addEventListener('pointerdown', _fsOnPointerDown);
  stage.addEventListener('pointermove', _fsOnPointerMove);
  stage.addEventListener('pointerup', _fsOnPointerUp);
  stage.addEventListener('pointercancel', _fsOnPointerUp);
  stage.addEventListener('dblclick', _fsOnDblClick);
  stage.addEventListener('wheel', _fsOnWheel, { passive: false });
}
```

在 `_onPageChange` 結尾加 `_bindPinchHandlersToSlide(newIdx);`，在 `openFullscreenGallery` 的 requestAnimationFrame 內、`_onPageChange(_currentIdx)` 之後加 `_bindPinchHandlersToSlide(_currentIdx);`。確保 3 處 reference（test 要求 ≥3）：1 definition + 2 callsites。

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd app_unified && pytest tests/test_fullscreen_viewer_zoom_swipe.py tests/test_fullscreen_viewer_video.py tests/test_fullscreen_viewer_nav.py tests/test_fullscreen_viewer_gallery_skeleton.py -v`

Expected: 19 passed

- [ ] **Step 7: Commit**

```bash
git add app_unified/static/js/fullscreen_viewer.js app_unified/tests/test_fullscreen_viewer_zoom_swipe.py
git commit -m "feat(viewer): pinch zoom co-exist with scroll-snap (overflow lock on zoom > 1)"
```

---

## Task 6: Lazy load (current ±1)

**Files:**
- Modify: `app_unified/static/js/fullscreen_viewer.js`
- Create: `app_unified/tests/test_fullscreen_viewer_lazy.py`

`_ensureLoaded(idx)` 已在 Task 3 placeholder。本 Task 確認 `openFullscreenGallery` 開啟時 lazy load `startIdx` + ±1、`_onPageChange` lazy load `newIdx` + ±1（Task 3 已寫入、本 Task 補 test + audit）。

- [ ] **Step 1: Write the failing tests**

```python
"""Lazy load: data-src → src for current ±1 only on open + on page change."""
from pathlib import Path

VIEWER_JS = Path(__file__).parent.parent / 'static' / 'js' / 'fullscreen_viewer.js'


def test_slides_use_data_src_not_src():
    """Initial slide markup uses data-src, real src is set lazily."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    # image slide markup
    assert 'data-src="' in src
    # img element built without src attr (only data-src)
    assert "<img class=\"fs-img\" data-src=" in src or "<img class='fs-img' data-src=" in src


def test_ensureloaded_called_for_three_indices_on_open():
    src = VIEWER_JS.read_text(encoding='utf-8')
    # _onPageChange triggers _ensureLoaded(newIdx) + _ensureLoaded(newIdx-1) + _ensureLoaded(newIdx+1)
    page_change_section = src.split('function _onPageChange')[1].split('function ')[0]
    assert page_change_section.count('_ensureLoaded') >= 3


def test_ensureloaded_swaps_dataset_src_to_src():
    src = VIEWER_JS.read_text(encoding='utf-8')
    fn_section = src.split('function _ensureLoaded')[1].split('function ')[0]
    assert 'dataset.src' in fn_section
    assert '.src = ' in fn_section or '.src=' in fn_section
```

- [ ] **Step 2: Run tests to verify result**

Run: `cd app_unified && pytest tests/test_fullscreen_viewer_lazy.py -v`

Expected: 3 passed (大部分已在 Task 3 滿足、本 Task 確認 + 守住 regression)

若 fail，audit Task 3 的 `_ensureLoaded` 實作 + `_onPageChange` 是否真的 call 3 次（newIdx / newIdx-1 / newIdx+1）。

- [ ] **Step 3: Commit (即使 0 code change、也要 commit test)**

```bash
git add app_unified/tests/test_fullscreen_viewer_lazy.py
git commit -m "test(viewer): lazy load guard (data-src + ±1 on page change)"
```

---

## Task 7: Wire notes/editor.html (3 callsites + toGalleryItem helper)

**Files:**
- Modify: `app_unified/templates/notes/editor.html` (3 callsites at line ~1038, ~1197, ~1481)
- Create: `app_unified/tests/test_notes_editor_gallery_wiring.py`

3 個 onclick 改 `openFullscreenGallery(items, idxOf(att))`。加 `toGalleryItem(att)` helper 在 editor.html 的 `<script>` 區段、3 處共用。

- [ ] **Step 1: Write the failing tests**

```python
"""Wire notes/editor.html callsites to openFullscreenGallery."""
from pathlib import Path

TEMPLATE = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def test_template_has_togallery_helper():
    src = TEMPLATE.read_text(encoding='utf-8')
    assert 'function toGalleryItem(' in src
    assert "'/notes/api/attachments/'" in src or '"/notes/api/attachments/"' in src
    assert ".id + '/download'" in src or '".id" + "/download"' in src


def test_template_calls_gallery_api_three_times():
    src = TEMPLATE.read_text(encoding='utf-8')
    # 3 callsites: note attachment thumbnail, pending upload preview, checklist item attachment
    assert src.count('openFullscreenGallery(') >= 3


def test_template_no_longer_uses_single_openfullscreen_in_attachment_context():
    """openFullscreen(...) is now the backward compat shim; new code paths should use gallery."""
    src = TEMPLATE.read_text(encoding='utf-8')
    # at least the 3 known attachment callsites use the new API
    # so total openFullscreenGallery calls + openFullscreen calls in attachment context should be ≥ 3
    # this is a smoke check, not strict
    assert 'openFullscreenGallery' in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && pytest tests/test_notes_editor_gallery_wiring.py -v`

Expected: 3 failed

- [ ] **Step 3: Add `toGalleryItem` helper in editor.html script block**

在 editor.html 找到 `<script>` block（line ~900-1500 應該都是 script），在 attachment 相關 function 之前 (e.g. 在 `function renderAttachment` 或 `function loadAttachments` 上方)、加：

```js
function toGalleryItem(att, downloadEndpointBase) {
  /* downloadEndpointBase e.g. '/notes/api/attachments/' or '/notes/api/items/<id>/attachments/' */
  return {
    kind: (att.content_type && att.content_type.indexOf('video/') === 0) ? 'video' : 'image',
    url: att.url,
    content_type: att.content_type,
    downloadHref: downloadEndpointBase + att.id + '/download'
  };
}
```

- [ ] **Step 4: Update line ~1038 callsite (note-level saved attachment thumbnail)**

找原本：
```js
openFullscreen(att.url, {
  downloadHref: endpoint
});
```

改成：
```js
var _galleryItems = (window._currentNoteAttachments || []).map(function(a) {
  return toGalleryItem(a, '/notes/api/attachments/');
});
var _idx = _galleryItems.findIndex(function(it) { return it.url === att.url; });
openFullscreenGallery(_galleryItems, Math.max(0, _idx));
```

注意：`window._currentNoteAttachments` 是 list of attachments 已經在 page state 裡（同層 loop 變數通常叫 `attachments` 或 `note.attachments`）。**先 grep 找正確 variable name**：

```bash
grep -n "attachments" app_unified/templates/notes/editor.html | grep -E "var |const |let |\.forEach|\.map" | head -10
```

若 attachments 是 closure 內 `var attachments`、直接用即可：
```js
var _galleryItems = attachments.map(function(a) { return toGalleryItem(a, '/notes/api/attachments/'); });
```

- [ ] **Step 5: Update line ~1197 callsite (pending upload preview)**

找原本：
```js
img.onclick = function() { openFullscreen(url, { downloadHref: null }); };
```

Pending uploads 還沒存 DB、沒 attachment.id、所以 `downloadHref: null`。Build items 用 pending uploads array (e.g. `_pendingUploads`)：

```js
img.onclick = function() {
  var _items = (window._pendingUploadsForFsGallery || [{ url: url, content_type: file.type }]).map(function(p) {
    return {
      kind: (p.content_type && p.content_type.indexOf('video/') === 0) ? 'video' : 'image',
      url: p.url || p.objectUrl,
      content_type: p.content_type || p.type,
      downloadHref: null
    };
  });
  var _idx = _items.findIndex(function(it) { return it.url === url; });
  openFullscreenGallery(_items, Math.max(0, _idx));
};
```

若 pending uploads 沒 array、僅有當前一張、退回 single-item gallery：
```js
img.onclick = function() {
  openFullscreenGallery([{ kind: file.type.indexOf('video/') === 0 ? 'video' : 'image', url: url, content_type: file.type, downloadHref: null }], 0);
};
```

- [ ] **Step 6: Update line ~1481 callsite (checklist item attachment)**

找原本：
```js
openFullscreen(a.url, {
  ...
});
```

改成 (假設 closure 內有 `item.attachments` 或類似)：
```js
var _itemAtts = (item.attachments || []).map(function(at) {
  return toGalleryItem(at, '/notes/api/items/' + item.id + '/attachments/');
});
var _idx = _itemAtts.findIndex(function(it) { return it.url === a.url; });
openFullscreenGallery(_itemAtts, Math.max(0, _idx));
```

**Note**: download endpoint URL pattern 必須符合 F9 spec — grep 確認：

```bash
grep -n "attachments.*download\|/download" app_unified/notes/routes.py | head -10
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd app_unified && pytest tests/test_notes_editor_gallery_wiring.py tests/test_attachment_download.py -v`

Expected: ≥3 passed (new) + existing F9 download tests still pass (backward compat)

- [ ] **Step 8: Commit**

```bash
git add app_unified/templates/notes/editor.html app_unified/tests/test_notes_editor_gallery_wiring.py
git commit -m "feat(notes/editor): wire 3 attachment callsites to openFullscreenGallery"
```

---

## Task 8: Wire announcement_attachments.js (_renderMedia signature)

**Files:**
- Modify: `app_unified/static/js/announcement_attachments.js`
- Create: `app_unified/tests/test_announcement_attachments_gallery.py`

`_renderMedia(att)` 改成 `_renderMedia(att, siblings, idx)`、image onclick 改 `openFullscreenGallery`。Caller `renderAnnouncementCard` 改 loop 帶 idx。

- [ ] **Step 1: Write the failing tests**

```python
"""Announcement attachment gallery wiring."""
from pathlib import Path

JS_FILE = Path(__file__).parent.parent / 'static' / 'js' / 'announcement_attachments.js'


def test_rendermedia_signature_has_siblings_and_idx():
    src = JS_FILE.read_text(encoding='utf-8')
    assert 'function _renderMedia(att, siblings, idx)' in src or 'function _renderMedia(att, siblings, idx,' in src


def test_calls_gallery_api():
    src = JS_FILE.read_text(encoding='utf-8')
    assert 'openFullscreenGallery(' in src
    # download endpoint per announcement attachment
    assert "/notes/announcement/api/attachments/" in src


def test_caller_passes_idx():
    src = JS_FILE.read_text(encoding='utf-8')
    # the call site that builds attachment thumbnails should pass idx
    assert '_renderMedia(' in src
    # at least one call passes 3 args
    assert '_renderMedia(att, ' in src or '_renderMedia(a, ' in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && pytest tests/test_announcement_attachments_gallery.py -v`

Expected: 3 failed

- [ ] **Step 3: Read current `announcement_attachments.js` to find caller**

Run: `cat app_unified/static/js/announcement_attachments.js | head -80`

定位 `_renderMedia` 定義 + 其 caller（通常是 `renderAnnouncementCard` 或 grid loop）。

- [ ] **Step 4: Update `_renderMedia` signature + image onclick**

```js
function _renderMedia(att, siblings, idx) {
  var wrap = document.createElement('div');
  wrap.className = 'announcement-media-wrap';
  if (att.content_type && att.content_type.indexOf('image/') === 0) {
    var img = document.createElement('img');
    img.src = att.url;
    img.alt = att.filename || '';
    img.onclick = function() {
      var items = siblings.map(function(s) {
        return {
          kind: (s.content_type && s.content_type.indexOf('video/') === 0) ? 'video' : 'image',
          url: s.url,
          content_type: s.content_type,
          downloadHref: '/notes/announcement/api/attachments/' + s.id + '/download'
        };
      });
      openFullscreenGallery(items, idx);
    };
    wrap.appendChild(img);
  } else if (att.content_type && att.content_type.indexOf('video/') === 0) {
    var video = document.createElement('video');
    video.src = att.url;
    video.controls = true;
    video.preload = 'metadata';
    video.playsInline = true;
    /* click on inline video also opens gallery at this idx (video pages get poster + ▶) */
    video.onclick = function(e) {
      /* allow native video controls but if click on video frame area open gallery */
      /* keep current behavior: video element is inline-playable; thumbnail click handled by wrapping anchor */
    };
    wrap.appendChild(video);
  }
  return wrap;
}
```

注意：video inline 行為（grid 上仍 `<video controls>` 直接播）不變、與 spec 一致（只有圖片縮圖 click 進 gallery；影片想全螢幕看用 browser native fullscreen 或下次也可以加 onclick 進 gallery）。

**簡化決定**：video onclick 也進 gallery（讓 user 從影片 inline 點開、進去看到 poster + ▶、跟 LINE 一致）。把 video.onclick 改：

```js
video.onclick = function(e) {
  /* prevent native play, open gallery instead */
  e.preventDefault();
  video.pause();
  var items = siblings.map(function(s) {
    return {
      kind: (s.content_type && s.content_type.indexOf('video/') === 0) ? 'video' : 'image',
      url: s.url,
      content_type: s.content_type,
      downloadHref: '/notes/announcement/api/attachments/' + s.id + '/download'
    };
  });
  openFullscreenGallery(items, idx);
};
```

但這樣會破壞「inline 看影片預覽」的 UX。**保留 inline play、不加 onclick 進 gallery for video**（user 要 swipe gallery 通常從圖開始）。維持上面 `_renderMedia` 第一版：image → gallery、video → inline native player。

- [ ] **Step 5: Update caller to pass `siblings` + `idx`**

找 `_renderMedia(att)` 的 call site (大概在 `renderAnnouncementCard` 或類似 function 內 loop)：

```js
/* before */
attachments.forEach(function(att) {
  card.appendChild(_renderMedia(att));
});

/* after */
attachments.forEach(function(att, idx) {
  card.appendChild(_renderMedia(att, attachments, idx));
});
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd app_unified && pytest tests/test_announcement_attachments_gallery.py -v`

Expected: 3 passed

- [ ] **Step 7: Commit**

```bash
git add app_unified/static/js/announcement_attachments.js app_unified/tests/test_announcement_attachments_gallery.py
git commit -m "feat(announcements): _renderMedia takes siblings+idx, image opens gallery"
```

---

## Task 9: Wire mynotes/editor.html (replace inline openFs)

**Files:**
- Modify: `app_unified/templates/mynotes/editor.html` (3 callsites + remove inline viewer markup + helpers)
- Create: `app_unified/tests/test_mynotes_editor_gallery_wiring.py`

mynotes 有自己的 `openFs / closeFs` + `<div class="fs-overlay">` inline viewer (line 132-134, 612-619)。改用 shared `openFullscreenGallery`、刪除 inline viewer markup + helpers。Mynotes 私人 scope：downloadHref=null（不開下載）。

確認 mynotes editor.html 已 include `static/js/fullscreen_viewer.js`。若沒、加 `<script src="{{ url_for('static', filename='js/fullscreen_viewer.js') }}"></script>` + CSS `<link>`。

- [ ] **Step 1: Write the failing tests**

```python
"""Mynotes editor: swap inline fullscreen viewer for shared gallery."""
from pathlib import Path

TEMPLATE = Path(__file__).parent.parent / 'templates' / 'mynotes' / 'editor.html'


def test_template_includes_shared_viewer_assets():
    src = TEMPLATE.read_text(encoding='utf-8')
    assert 'fullscreen_viewer.js' in src
    assert 'fullscreen_viewer.css' in src


def test_template_has_openMynotesGallery_helper():
    src = TEMPLATE.read_text(encoding='utf-8')
    assert 'function openMynotesGallery(' in src
    assert 'openFullscreenGallery(' in src
    # mynotes is private — downloadHref must be null
    assert 'downloadHref: null' in src


def test_template_no_inline_fs_overlay():
    src = TEMPLATE.read_text(encoding='utf-8')
    assert 'id="fs-overlay"' not in src
    assert "id='fs-overlay'" not in src
    assert 'function openFs(' not in src
    assert 'function closeFs(' not in src


def test_three_callsites_use_gallery():
    src = TEMPLATE.read_text(encoding='utf-8')
    # 3 original openFs callers (line 323, 401, 496) should now call openMynotesGallery
    assert src.count('openMynotesGallery(') >= 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && pytest tests/test_mynotes_editor_gallery_wiring.py -v`

Expected: 4 failed

- [ ] **Step 3: Add shared viewer asset includes (if missing)**

在 `templates/mynotes/editor.html` `<head>` 加：

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/fullscreen_viewer.css') }}">
```

在 `<body>` 結尾 / script 區段加：

```html
<script src="{{ url_for('static', filename='js/fullscreen_viewer.js') }}"></script>
```

若已經有就跳過。

- [ ] **Step 4: Remove inline `<div class="fs-overlay">` markup (line ~132)**

刪除：
```html
<div class="fs-overlay" id="fs-overlay" onclick="closeFs()">
  <img id="fs-img" src="">
  <button class="fs-close" onclick="event.stopPropagation();closeFs();" aria-label="關閉">✕</button>
</div>
```

- [ ] **Step 5: Remove inline `openFs` / `closeFs` functions (line ~612-619)**

刪除：
```js
function openFs(url) {
  document.getElementById('fs-img').src = url;
  /* ... */
}
function closeFs() {
  /* ... */
  document.getElementById('fs-img').src = '';
}
```

- [ ] **Step 6: Add `openMynotesGallery` helper**

放在 script 區段 (e.g. 取代被刪 openFs 的位置):

```js
function openMynotesGallery(attachments, currentAtt) {
  if (!attachments || attachments.length === 0) return;
  var items = attachments.map(function(a) {
    return {
      kind: (a.content_type && a.content_type.indexOf('video/') === 0) ? 'video' : 'image',
      url: a.url,
      content_type: a.content_type,
      downloadHref: null   /* mynotes 私人 scope 不開下載 */
    };
  });
  var idx = attachments.findIndex(function(a) { return a.id === currentAtt.id; });
  openFullscreenGallery(items, Math.max(0, idx));
}
```

- [ ] **Step 7: Update 3 callsites (line 323, 401, 496)**

定位每處 `img.onclick = function(){ openFs(a.url); };`、改為（attachment list variable name 視 context）：

Line 323 context (assumed `addAttachmentToList(a)` 內、用 closure list `_savedAtts` 或類似)：
```js
img.onclick = function(){ openMynotesGallery(_savedAtts, a); };
```

若 closure 沒有 list、需要從 outer state 拿 — grep 找：
```bash
grep -n "_savedAtts\|attachments\." app_unified/templates/mynotes/editor.html | head -10
```

Line 401 context: 大概在 `renderItem` 內、list 是 `item.attachments`：
```js
im.onclick = function(){ openMynotesGallery(item.attachments, a); };
```

Line 496 context: 大概在 item modal、list 是 `_itemSavedAtts`：
```js
img.onclick = function(){ openMynotesGallery(_itemSavedAtts, a); };
```

3 處 caller 都明確帶「該 attachment 所屬 list + 該 attachment 本身」。

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd app_unified && pytest tests/test_mynotes_editor_gallery_wiring.py -v`

Expected: 4 passed

- [ ] **Step 9: Commit**

```bash
git add app_unified/templates/mynotes/editor.html app_unified/tests/test_mynotes_editor_gallery_wiring.py
git commit -m "feat(mynotes): replace inline openFs with shared gallery (downloadHref=null)"
```

---

## Task 10: SW cache bump + full test sweep + local verify

**Files:**
- Modify: `app_unified/static/sw.js` line 8 (`CACHE_NAME`)
- Run: full pytest suite
- Manual: local dev server smoke test

- [ ] **Step 1: Bump SW CACHE_NAME**

Edit `app_unified/static/sw.js` line 8：
```js
const CACHE_NAME = 'note-weather-v35';
```

- [ ] **Step 2: Run full pytest suite**

```bash
cd app_unified && pytest tests/ -v --tb=short
```

Expected: all green。本 feature 加了：
- test_attachment_ordering.py (3)
- test_fullscreen_viewer_gallery_skeleton.py (6)
- test_fullscreen_viewer_nav.py (5)
- test_fullscreen_viewer_video.py (4)
- test_fullscreen_viewer_zoom_swipe.py (4)
- test_fullscreen_viewer_lazy.py (3)
- test_notes_editor_gallery_wiring.py (3)
- test_announcement_attachments_gallery.py (3)
- test_mynotes_editor_gallery_wiring.py (4)
= 35 new tests + existing 494 = 529 total

若任何既有 test 紅、回 Task 對應 surface 修。

- [ ] **Step 3: Local server smoke test**

```bash
cd /home/hirain0126/projects/webapp
bash scripts/run_local.sh
```

開 browser → http://localhost:5000 → `/dev` 切到測試帳號 → 開「店務筆記」一條有 ≥2 張圖+影 attachment 的筆記 → 點任一張圖：
- 滑左右切換 ✓
- 滑到影片 → poster + 中央 ▶ → 點 ▶ → 播放 OK
- 切走 → 影片自動停 + reset ✓
- 桌面用 ←/→ 鍵切 + arrow 鈕切 ✓
- 右上 counter `3 / N` ✓
- 第一張左滑不動、最後一張右滑不動 (bounce/stop) ✓
- 圖 pinch zoom in (or wheel zoom) → page 鎖 swipe ✓、zoom out → 解鎖
- 下載按鈕仍 OK (F9 path 不破)
- 公告 + 確認表單 + mynotes 同樣驗

Edge cases：
- 單張附件：counter / arrow 不顯示
- 全是圖 / 全是影 / 圖影 mix
- mynotes 不顯示下載鈕（downloadHref=null）

- [ ] **Step 4: Commit SW bump**

```bash
git add app_unified/static/sw.js
git commit -m "chore(sw): bump CACHE_NAME v34 -> v35 for F10 swipe gallery deploy"
```

- [ ] **Step 5: User 實機驗 → push origin/main**

按 [[feedback_local_test_before_push]] + [[feedback_push_policy]]：local 跑完、user 實機驗 OK 才能 ff-merge main + push。詢問 user 是否 push。

push 後 monitor GHA build 綠 → Zeabur 自動 deploy → user prod 實機驗。

```bash
git checkout main
git merge --ff-only feat/attachment-swipe-gallery
git push origin main
git branch -d feat/attachment-swipe-gallery
```

---

## Self-Review Summary

**Spec coverage:**
- §2 Scope (4 surface) → Task 1 / 7 / 8 / 9 ✓
- §3 UX decisions → Task 2 (CSS scroll-snap + chrome) / Task 3 (arrow + keyboard + counter) / Task 4 (video poster + ▶) / Task 5 (pinch zoom) ✓
- §4 Architecture (API + markup + CSS) → Task 2 ✓
- §5.1 Page tracking (scrollend fallback) → Task 3 ✓
- §5.2 Arrow / keyboard → Task 3 ✓
- §5.3 Pinch zoom + swipe coexist → Task 5 ✓
- §5.4 Video page → Task 4 ✓
- §5.5 Lazy load → Task 3 placeholder + Task 6 guard ✓
- §5.6 Open / close → Task 2 (open) + Task 4 (close pauses videos) ✓
- §6.1-6.4 Caller wiring + backward compat → Task 7 / 8 / 9 + shim in Task 2 ✓
- §7 Tests (6 categories) → Task 1 / 2 / 7 / 8 / 9 + 4 categories of viewer tests in Task 2-6 ✓
- §8 Rollout (SW bump + local verify + push) → Task 10 ✓

**Placeholder scan:** No "TBD" / "TODO" / "similar to" / vague handling. Each step has complete code or commands.

**Type consistency:**
- `openFullscreenGallery(items, startIdx)` consistent across Task 2 / 7 / 8 / 9.
- `toGalleryItem(att, downloadEndpointBase)` defined Task 7 step 3, used Task 7 step 4/6.
- `openMynotesGallery(attachments, currentAtt)` defined Task 9 step 6, used Task 9 step 7.
- `_pauseAndResetVideo(idx)` placeholder Task 3, real impl Task 4. `_ensureLoaded(idx)` placeholder Task 3, guarded Task 6.
- `_bindPinchHandlersToSlide(idx)` defined Task 5, called from `openFullscreenGallery` + `_onPageChange`.

**Risks (called out in spec):** scrollend feature-detect fallback (Task 3 covered)、iOS Safari pinch + scroll-snap edge cases (Task 10 manual verify)、backward compat (Task 2 shim + Task 10 full suite).
