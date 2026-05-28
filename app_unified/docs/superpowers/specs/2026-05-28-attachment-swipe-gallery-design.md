# Spec — Attachment Swipe Gallery (F10)

**Date**: 2026-05-28
**Scope**: 店務筆記 / 確認表單 (NoteAttachment) + 公告 (AnnouncementAttachment) + mynotes 私人筆記 (PrivateAttachment) 四個 surface 的圖/影 fullscreen 改成可左右滑切換相鄰附件（LINE / IG / WhatsApp 標準體感）。
**Base**: main HEAD `759802c` (2026-05-27 F9 attachment download + 2026-05-28 CI workflow_dispatch)

---

## 1. Problem

目前一條筆記 / 一則公告插超過一張圖+影片時，user 看完一張要關掉 viewer、回 grid、找下一張再點開。多附件查閱很卡。

目標：點任一張附件後，可像 LINE / IG / WhatsApp 一樣**左右滑切相鄰附件**（圖跨影 interleaved），手機手勢、桌面按鈕 + 鍵盤。

## 2. Scope

In-scope (四個 surface)：

| Surface | Template / JS | Model | F9 download? |
|---|---|---|---|
| 店務筆記 + 確認表單 editor | `templates/notes/editor.html` line ~1038, ~1197, ~1481 | NoteAttachment | ✓ (保留) |
| 公告 | `static/js/announcement_attachments.js` line ~31 | AnnouncementAttachment | ✓ (保留) |
| mynotes 私人筆記 editor | `templates/mynotes/editor.html` line 323, 401, 496 | PrivateAttachment | ✗ (維持私人不開下載、`downloadHref: null`) |

Out-of-scope：
- 公告卡片列表縮圖（`_card_list.html`、目前用 carousel 看的就那一條公告自己的縮圖，OK 走同 viewer）
- 上下滑 dismiss (IG Story 風)
- 自動切下一張的影片 autoplay 連播
- Share / 收藏按鈕
- 替 video 上傳獨立 poster file（用 browser `preload="metadata"` 抓第一幀）

## 3. UX Decisions (User-confirmed)

| 決策 | 選項 | User pick |
|---|---|---|
| 範圍 | 4 surface 都加 | ✓ |
| 圖+影 mix | interleaved (圖A → 影B → 圖C) | ✓ |
| 影片在 viewer | poster + 中央 ▶ 大圓鈕、user 點才播 | ✓ |
| 桌面 nav | 左右 overlay arrow 鈕 + keyboard ← → | ✓ |
| 位置提示 | 右上 / 上中 「3 / 12」 counter | ✓ |
| Impl | 原生 CSS scroll-snap + 手寫 pointer handlers | ✓ |
| 邊界 | bounce/stop (不 loop)、arrow disabled state | (我決定，LINE/IG 標準) |
| 順序 | attachment id asc (= upload order、跟現有 grid 一致) | (我決定) |

## 4. Architecture

### 4.1 New public API

```js
// fullscreen_viewer.js 新 export
window.openFullscreenGallery(items, startIdx)
// items: [{ kind: 'image'|'video', url, content_type, downloadHref? }, ...]
// startIdx: number, 點的那張在 items 陣列裡的 index

// 既有 API 保留 backward compat
window.openFullscreen(url, opts)
//  -> openFullscreenGallery([{kind:'image', url, downloadHref: opts.downloadHref}], 0)
```

`window.closeFullscreen` + `window.downloadAttachment` + `window.DOWNLOAD_ICON_SVG` 不變。

### 4.2 Markup

```html
<div class="fullscreen-overlay" id="fullscreen-overlay">
  <div class="fs-track" id="fs-track">
    <section class="fs-slide" data-idx="0" data-kind="image">
      <div class="fs-stage"><img class="fs-img" src="..."></div>
    </section>
    <section class="fs-slide" data-idx="1" data-kind="video">
      <div class="fs-video-wrap">
        <video preload="metadata" playsinline src="..."></video>
        <button class="fs-video-play" aria-label="播放">▶</button>
      </div>
    </section>
    <!-- N slides -->
  </div>

  <!-- 浮在 track 上方的 chrome -->
  <button class="fs-prev"  aria-label="上一張">‹</button>
  <button class="fs-next"  aria-label="下一張">›</button>
  <div    class="fs-counter">3 / 12</div>
  <button class="fs-close" aria-label="關閉">✕</button>
  <button class="fs-download" id="fs-download" style="display:none;">⬇</button>
  <div    class="fs-hint">←/→ 切換 · 雙指縮放 · 雙擊放大 · 拖曳移動</div>
</div>
```

Track 內 slide 數量動態 (`items.length`)。每次 `openFullscreenGallery` 重建 track innerHTML。`fs-stage` 是 image slide 的 zoom container（沿用既有 `_fsOnPointer*` handlers、scope 在這層）。

### 4.3 CSS scroll-snap

```css
.fs-track {
  display: flex;
  width: 100vw; height: 100vh;
  overflow-x: auto;
  scroll-snap-type: x mandatory;
  scroll-behavior: smooth;
  scrollbar-width: none;
  overscroll-behavior-x: contain;     /* iOS 不要連帶 swipe-back */
}
.fs-track::-webkit-scrollbar { display: none; }

.fs-slide {
  flex: 0 0 100vw; height: 100vh;
  scroll-snap-align: center;
  scroll-snap-stop: always;            /* 一次只跳一張、防快速 swipe 過頭 */
  display: flex; align-items: center; justify-content: center;
}

.fs-prev, .fs-next {
  position: fixed; top: 50%; transform: translateY(-50%);
  width: 48px; height: 80px;
  background: rgba(0,0,0,.4); color: #fff; border: none;
  font-size: 32px; cursor: pointer; z-index: 10;
}
.fs-prev { left: 0; border-radius: 0 8px 8px 0; }
.fs-next { right: 0; border-radius: 8px 0 0 8px; }
.fs-prev[disabled], .fs-next[disabled] { opacity: .3; cursor: default; }
@media (hover: none) { .fs-prev, .fs-next { display: none !important; } }

.fs-counter {
  position: fixed; top: 16px; left: 50%; transform: translateX(-50%);
  background: rgba(0,0,0,.5); color: #fff; padding: 4px 12px; border-radius: 12px;
  font: 14px/1 system-ui; z-index: 10;
}

.fs-video-wrap { position: relative; max-width: 100vw; max-height: 100vh; }
.fs-video-wrap video { max-width: 100vw; max-height: 100vh; display: block; }
.fs-video-play {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  width: 80px; height: 80px; border-radius: 50%;
  background: rgba(0,0,0,.6); color: #fff; font-size: 36px;
  border: 2px solid #fff; cursor: pointer;
}
.fs-video-wrap.playing .fs-video-play { display: none; }
```

## 5. Behavior

### 5.1 Page tracking

```js
let _items = [], _currentIdx = 0;

track.addEventListener('scrollend', () => {
  const idx = Math.round(track.scrollLeft / window.innerWidth);
  if (idx !== _currentIdx) _onPageChange(idx);
});

function _onPageChange(newIdx) {
  _pauseAndResetVideo(_currentIdx);
  _currentIdx = newIdx;
  const item = _items[newIdx];
  counter.textContent = `${newIdx + 1} / ${_items.length}`;
  dlBtn.style.display = item.downloadHref ? '' : 'none';
  dlBtn.dataset.href = item.downloadHref || '';
  prevBtn.disabled = (newIdx === 0);
  nextBtn.disabled = (newIdx === _items.length - 1);
  _fsReset();  /* 切頁順手 reset zoom/pan */
}
```

`scrollend` 在 Chrome 109+ / Firefox 109+ / Safari 18.2+ 可用 — webapp 用戶 99% 是 Chrome on Android / Safari on iOS、夠新。Fallback：若 `'onscrollend' in window === false`、改用 `scroll` event + 200ms debounce（兼容舊瀏覽器）。

### 5.2 Arrow / keyboard

```js
prevBtn.onclick = () => _goTo(_currentIdx - 1);
nextBtn.onclick = () => _goTo(_currentIdx + 1);

document.addEventListener('keydown', e => {
  if (!overlay.classList.contains('open')) return;
  if (e.key === 'ArrowLeft')  { _goTo(_currentIdx - 1); e.preventDefault(); }
  if (e.key === 'ArrowRight') { _goTo(_currentIdx + 1); e.preventDefault(); }
  if (e.key === 'Escape')     { closeFullscreen(); }
});

function _goTo(idx) {
  if (idx < 0 || idx >= _items.length) return;   /* 邊界 stop、不 loop */
  track.scrollTo({ left: idx * window.innerWidth, behavior: 'smooth' });
  /* scrollend 會自動觸發 _onPageChange */
}
```

### 5.3 Pinch zoom + swipe 共存（technical核心）

既有 `_fsOnPointerDown / Move / Up` 邏輯保留，但 logic 重寫：

| 狀態 | pointer 數 | scale | 行為 |
|---|---|---|---|
| Native scroll-snap swipe (拿來換頁) | 1 | 1.0 | **不 preventDefault**、讓 browser 處理 |
| Pan zoomed image | 1 | > 1.0 | preventDefault + 自己 translate |
| Pinch zoom | 2 | any | preventDefault + 自己 scale + translate |

Zoom 進去後（`scale > 1.01`）：

```js
function _fsApply() {
  /* 既有 transform 設定 ... */
  if (_fsState.scale > 1.01) {
    track.style.overflowX = 'hidden';    /* 鎖換頁、防止 zoom 中誤滑 */
  } else {
    track.style.overflowX = 'auto';
  }
}
```

`_fsReset` 也設 `overflowX = 'auto'`。雙擊放大、雙擊縮回 = 既有 `_fsOnDblClick` 不變、走相同 lock 路徑。

Pointer handlers 只 bind 在當前頁的 `.fs-stage`（換頁時 rebind、其他頁 stage 不 listen）。

### 5.4 影片頁

```js
function _renderVideoSlide(item, idx) {
  const wrap = document.createElement('div');
  wrap.className = 'fs-video-wrap';
  wrap.innerHTML = `
    <video preload="metadata" playsinline data-src="${escapeHtml(item.url)}"></video>
    <button class="fs-video-play" aria-label="播放">▶</button>
  `;
  /* video 不設 src、靠 _ensureLoaded(idx) lazy 注入、跟 image 一致 */
  const video = wrap.querySelector('video');
  const playBtn = wrap.querySelector('.fs-video-play');
  playBtn.onclick = (e) => {
    e.stopPropagation();
    video.controls = true;
    wrap.classList.add('playing');
    video.play();
  };
  return wrap;
}

function _pauseAndResetVideo(idx) {
  const slide = track.querySelector(`.fs-slide[data-idx="${idx}"][data-kind="video"]`);
  if (!slide) return;
  const video = slide.querySelector('video');
  if (!video) return;
  video.pause();
  video.currentTime = 0;
  video.controls = false;
  slide.querySelector('.fs-video-wrap')?.classList.remove('playing');
}
```

切離 video 頁 → `scrollend → _onPageChange → _pauseAndResetVideo(舊 idx)`、自動停 + reset。

### 5.5 Lazy load (省流量)

開 viewer 時只把 startIdx + 鄰居 (±1) 的 `<img src>` / `<video src>` set 真實 URL，其他 slide 先放 placeholder。Page change 後 set 新進入 ±1 的 src。

```js
function _ensureLoaded(idx) {
  if (idx < 0 || idx >= _items.length) return;
  const slide = track.querySelector(`.fs-slide[data-idx="${idx}"]`);
  const media = slide.querySelector('img, video');
  if (media && !media.src && media.dataset.src) media.src = media.dataset.src;
}
function _onPageChange(newIdx) {
  /* ... */
  _ensureLoaded(newIdx);
  _ensureLoaded(newIdx - 1);
  _ensureLoaded(newIdx + 1);
}
```

User 不左右滑、就只載 1 張、跟現在一樣輕。

### 5.6 Open / close

```js
function openFullscreenGallery(items, startIdx) {
  _fsInit();    /* idempotent、確保 overlay markup + base handlers 存在 */
  _items = items.slice();
  _currentIdx = startIdx || 0;

  /* rebuild track */
  track.innerHTML = '';
  items.forEach((item, i) => {
    const slide = document.createElement('section');
    slide.className = 'fs-slide';
    slide.dataset.idx = i;
    slide.dataset.kind = item.kind;
    if (item.kind === 'image') {
      slide.innerHTML = `<div class="fs-stage"><img class="fs-img" data-src="${escapeHtml(item.url)}"></div>`;
    } else {
      slide.appendChild(_renderVideoSlide(item, i));
      /* video data-src 已在 _renderVideoSlide 設好、_ensureLoaded 會 lazy 注入 */
    }
    track.appendChild(slide);
  });

  /* hide chrome if single */
  const multi = items.length > 1;
  prevBtn.style.display = nextBtn.style.display = multi ? '' : 'none';
  counter.style.display = multi ? '' : 'none';

  /* set initial position WITHOUT smooth */
  overlay.classList.add('open');
  requestAnimationFrame(() => {
    track.scrollLeft = _currentIdx * window.innerWidth;
    _onPageChange(_currentIdx);   /* 直接 call、不靠 scrollend、確保初始 chrome state 正確 */
    _bindPinchHandlersToCurrentSlide();
  });
}

function closeFullscreen() {
  if (!overlay) return;
  overlay.classList.remove('open');
  _items.forEach((_, i) => _pauseAndResetVideo(i));   /* 全部暫停 */
  track.innerHTML = '';
  _items = [];
  _fsReset();
}
```

## 6. Caller 接線

### 6.1 Notes editor (`templates/notes/editor.html`)

3 處改：

| Line | 現在 | 改成 |
|---|---|---|
| ~1038 | `openFullscreen(att.url, { downloadHref: endpoint });` (saved attachment 縮圖) | Build `_items = note.attachments.filter(可顯示).map(toGalleryItem)`、`openFullscreenGallery(_items, idxOf(att))` |
| ~1197 | `img.onclick = () => openFullscreen(url, { downloadHref: null })` (pending upload preview) | 同上、但 items = 當前未送出的 pending uploads 陣列 |
| ~1481 | `openFullscreen(a.url, { ... })` (checklist item 內 attachment) | 同上、items = 該 item 的 attachments |

helper：

```js
function toGalleryItem(att) {
  return {
    kind: att.content_type?.startsWith('video/') ? 'video' : 'image',
    url: att.url,
    content_type: att.content_type,
    downloadHref: '/notes/api/attachments/' + att.id + '/download'   /* note vs item endpoint 不同、各 caller 自己組 */
  };
}
```

### 6.2 公告 (`static/js/announcement_attachments.js`)

`_renderMedia(att, ...)` 改 signature 多吃 `siblings` 陣列 + `idx`：

```js
function _renderMedia(att, siblings, idx) {
  /* ... 現有 img / video element ... */
  img.onclick = () => {
    const items = siblings.map(s => ({
      kind: s.content_type?.startsWith('video/') ? 'video' : 'image',
      url: s.url,
      content_type: s.content_type,
      downloadHref: '/notes/announcement/api/attachments/' + s.id + '/download'
    }));
    openFullscreenGallery(items, idx);
  };
}
```

Caller 是 `renderAnnouncementCard(card)`、loop 內帶 `idx` 進來。

### 6.3 mynotes (`templates/mynotes/editor.html`)

3 處 `img.onclick = function(){ openFs(a.url); }` (line 323 / 401 / 496) 改：

```js
function openMynotesGallery(attachments, currentAtt) {
  const items = attachments.map(a => ({
    kind: a.content_type?.startsWith('video/') ? 'video' : 'image',
    url: a.url,
    content_type: a.content_type,
    downloadHref: null   /* mynotes 私人不開下載 */
  }));
  const idx = attachments.findIndex(a => a.id === currentAtt.id);
  openFullscreenGallery(items, Math.max(0, idx));
}

/* 替換 3 處 */
img.onclick = function(){ openMynotesGallery(_savedAtts, a); };
```

舊 `openFs / closeFs` + inline `#fs-overlay` markup (line 132-134) 移除 — 改靠 shared viewer。

### 6.4 Backward compat shim

```js
window.openFullscreen = function(url, opts) {
  opts = opts || {};
  openFullscreenGallery([{
    kind: 'image', url: url, downloadHref: opts.downloadHref
  }], 0);
};
```

F9 single-image fullscreen path 全保留、不破。

## 7. Tests (pytest)

| Test 檔 | 內容 |
|---|---|
| `tests/test_fullscreen_viewer_gallery_smoke.py` | `static/js/fullscreen_viewer.js` source 含 `openFullscreenGallery` / `fs-track` / `fs-slide` / `fs-counter` / `scroll-snap-type: x mandatory` / `scroll-snap-stop: always` / `scrollend` / `_pauseAndResetVideo` |
| `tests/test_notes_editor_gallery_wiring.py` | `templates/notes/editor.html` 含 `openFullscreenGallery(` 至少 3 處、`toGalleryItem` helper 存在 |
| `tests/test_announcement_attachments_gallery.py` | `static/js/announcement_attachments.js` 含 `openFullscreenGallery(` + `_renderMedia` signature 帶 siblings + idx |
| `tests/test_mynotes_editor_gallery_wiring.py` | `templates/mynotes/editor.html` 含 `openMynotesGallery` + 不再含 `function openFs(` 也不再含 `id="fs-overlay"` (避免雙 viewer 共存衝突) |
| `tests/test_attachment_order.py` | NoteAttachment / AnnouncementAttachment / PrivateAttachment 從 DB 撈回時 order_by id asc (upload order) — caller build items[] 依賴此順序 |
| `tests/test_attachment_download_backward_compat.py` | `openFullscreen('url', {downloadHref: 'x'})` 仍可正常 invoke、不破 F9 |

不寫：jsdom 的 PointerEvent / scroll-snap integration test (徒勞)、visual snapshot (沒設施)。手感驗 = local server 跑、user 實機 swipe。

## 8. Rollout

- 開 branch `feat/attachment-swipe-gallery`
- 7-9 個 commit (對應 plan task)
- 全做完 → `bash scripts/run_local.sh` → user `/dev` 切帳號實機驗 → ff-merge main + push (按 [[feedback_local_test_before_push]] + [[feedback_push_policy]])
- `static/sw.js` `CACHE_NAME` v34 → v35

## 9. Risk / Open

| Risk | Mitigation |
|---|---|
| iOS Safari scroll-snap + pinch zoom 互動可能不穩 | 先試 native scroll-snap、若實機驗證有問題 fallback transform-based track (留 follow-up) |
| `scrollend` event 在很舊 browser 沒支援 | feature-detect、fallback `scroll` + 200ms debounce |
| Video 切走 pause + reset 時 controls UI flash | 切走的 slide 不在 viewport、user 看不到 |
| 一條筆記 100 張附件 (極端) → 100 個 slide DOM | items.length 上限 200 (現實 webapp 沒見過超過 10)、lazy src 已省流量、DOM 100 node OK |
| Backward compat 漏網 | tests/test_attachment_download_backward_compat.py 守 + grep 既有 `openFullscreen(` callsite 全 audit |

## 10. Not in this spec

- 公告卡片列表 `_card_list.html` 縮圖直接走 gallery (現在點開單張公告 → 卡片內縮圖 grid → 點縮圖開 viewer、流程不變)
- 上下滑 dismiss
- 影片連播 autoplay
- Pinch zoom 在 video 上 (video 用 browser native controls + native fullscreen)
- mynotes 開 download (維持 F9 私人不開下載決策)
- Share / 收藏 / 標記 / 收藏
- Lightbox 進場 / 退場動畫
