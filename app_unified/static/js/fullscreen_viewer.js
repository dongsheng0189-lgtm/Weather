/* Shared fullscreen viewer — F10 swipe gallery (seamless circular wrap)
 * Spec: docs/superpowers/specs/2026-05-28-attachment-swipe-gallery-design.md
 *
 * Public API:
 *   window.openFullscreen(url, opts)             — backward-compat shim, single image
 *   window.openFullscreenGallery(items, startIdx) — multi-attachment gallery with left/right swipe
 *   window.closeFullscreen()                      — close
 *   window.downloadAttachment(endpointUrl)        — download via presigned URL
 *   window.DOWNLOAD_ICON_SVG(size)                — shared download SVG icon
 *
 * Architecture:
 *   - Track is a flex row of slides positioned by CSS `transform: translateX(...)`.
 *   - For N > 1 we render items × 3 (left copy / middle = canonical / right copy)
 *     so swipes past either end keep flowing visually; after each swipe transition
 *     we silently re-center to the canonical (middle) copy of the same real idx.
 *     User never sees a "jump" — the motion is a continuous loop.
 *   - Swipe handled by our own pointer events on the track (not native scroll).
 *   - Pinch zoom + double-tap zoom + wheel zoom on per-slide `.fs-stage`; while
 *     scale > 1 the swipe handler bails (pan handled by stage).
 */
(function() {
  function DOWNLOAD_ICON_SVG(size) {
    var s = size || 20;
    return '<svg width="' + s + '" height="' + s + '" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
      'aria-hidden="true">' +
      '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>' +
      '<polyline points="7 10 12 15 17 10"/>' +
      '<line x1="12" y1="15" x2="12" y2="3"/>' +
      '</svg>';
  }
  window.DOWNLOAD_ICON_SVG = DOWNLOAD_ICON_SVG;

  function _escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, function(c) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
    });
  }

  /* ──────── Zoom / pan state (per slide) ──────── */
  var _fsState = { scale: 1, tx: 0, ty: 0, minScale: 1, maxScale: 6 };
  var _fsPointers = {};
  var _fsLastDist = 0;
  var _fsLastCenter = { x: 0, y: 0 };

  /* ──────── Gallery state ──────── */
  var _items = [];        /* real items */
  var _currentIdx = 0;    /* real idx 0..N-1 */
  var _translateX = 0;    /* current track translate px (negative) */
  var _drag = null;       /* swipe drag state */

  /* ──────── DOM index math ──────── */
  function _N() { return _items.length; }
  function _W() { return window.innerWidth; }
  /* When N > 1 we render 3 copies (left/middle/right). Middle starts at slot N. */
  function _multi() { return _N() > 1; }
  function _canonicalSlot(realIdx) { return _multi() ? _N() + realIdx : realIdx; }

  /* ──────── Pinch zoom helpers ──────── */
  function _fsApply() {
    var slide = document.querySelector('.fs-slide[data-idx="' + _currentIdx + '"][data-kind="image"][data-copy="m"]');
    if (!slide) slide = document.querySelector('.fs-slide[data-idx="' + _currentIdx + '"][data-kind="image"]');
    if (!slide) return;
    var img = slide.querySelector('.fs-img');
    if (img) img.style.transform = 'translate(' + _fsState.tx + 'px,' + _fsState.ty + 'px) scale(' + _fsState.scale + ')';
  }

  function _fsReset() {
    _fsState.scale = 1; _fsState.tx = 0; _fsState.ty = 0;
    _fsApply();
  }

  function _fsClampPan() {
    var slide = document.querySelector('.fs-slide[data-idx="' + _currentIdx + '"][data-kind="image"][data-copy="m"]');
    if (!slide) slide = document.querySelector('.fs-slide[data-idx="' + _currentIdx + '"][data-kind="image"]');
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

  function _fsOnPointerDown(e) {
    _fsPointers[e.pointerId] = { x: e.clientX, y: e.clientY };
    var ids = Object.keys(_fsPointers);
    if (ids.length === 2) {
      var p1 = _fsPointers[ids[0]], p2 = _fsPointers[ids[1]];
      _fsLastDist = Math.hypot(p2.x - p1.x, p2.y - p1.y);
      _fsLastCenter = { x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 };
      e.preventDefault();
      e.stopPropagation();   /* block swipe handler from grabbing this gesture */
    }
  }

  function _fsOnPointerMove(e) {
    if (!_fsPointers[e.pointerId]) return;
    var prev = _fsPointers[e.pointerId];
    _fsPointers[e.pointerId] = { x: e.clientX, y: e.clientY };
    var ids = Object.keys(_fsPointers);
    if (ids.length === 2) {
      /* pinch */
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
      e.stopPropagation();
    } else if (ids.length === 1 && _fsState.scale > 1.01) {
      /* pan zoomed image */
      _fsState.tx += e.clientX - prev.x;
      _fsState.ty += e.clientY - prev.y;
      _fsClampPan();
      _fsApply();
      e.preventDefault();
      e.stopPropagation();
    }
    /* else: 1 pointer + scale === 1 — let event bubble to track swipe handler */
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

  /* ──────── Chrome markup (injected once) ──────── */
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

  /* ──────── Bind pinch/zoom handlers on every slide's stage (image only) ──────── */
  function _bindPinchHandlersToSlide(realIdx) {
    /* Bind to all copies (left/middle/right) of this real idx so pinch works no matter which copy is visible */
    var slides = document.querySelectorAll('.fs-slide[data-idx="' + realIdx + '"][data-kind="image"]');
    slides.forEach(function(slide) {
      var stage = slide.querySelector('.fs-stage');
      if (!stage || stage._fsBound) return;
      stage._fsBound = true;
      stage.addEventListener('pointerdown', _fsOnPointerDown);
      stage.addEventListener('pointermove', _fsOnPointerMove);
      stage.addEventListener('pointerup', _fsOnPointerUp);
      stage.addEventListener('pointercancel', _fsOnPointerUp);
      stage.addEventListener('dblclick', _fsOnDblClick);
      stage.addEventListener('wheel', _fsOnWheel, { passive: false });
    });
  }

  /* ──────── Track transform helper ──────── */
  function _setTrack(translateX, animated) {
    var track = document.getElementById('fs-track');
    if (!track) return;
    _translateX = translateX;
    if (animated) track.classList.add('fs-track-snap');
    else track.classList.remove('fs-track-snap');
    track.style.transform = 'translate3d(' + translateX + 'px,0,0)';
  }

  /* After transition: if current slot is in left/right copy, instant-jump back to canonical (middle) copy.
   * Same real idx → identical visible image → user sees no change. */
  function _recenter() {
    if (!_multi()) return;
    var target = -_canonicalSlot(_currentIdx) * _W();
    if (Math.round(_translateX) !== Math.round(target)) _setTrack(target, false);
  }

  /* ──────── Swipe handlers (bound on track) ──────── */
  function _onTrackPointerDown(e) {
    if (!_multi()) return;
    if (_fsState.scale > 1.01) return;            /* zoomed — pinch/pan owns the gesture */
    if (_drag) return;                            /* one drag at a time */
    /* Skip if pointer started on chrome button (close / arrow / download / play) */
    if (e.target && e.target.closest && e.target.closest('.fs-close,.fs-prev,.fs-next,.fs-download,.fs-video-play')) return;
    _drag = {
      id: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      startTx: _translateX,
      didMove: false,
      abandoned: false,
      t0: Date.now(),
    };
    /* Disable transition during drag so finger tracks 1:1 */
    var track = document.getElementById('fs-track');
    if (track) track.classList.remove('fs-track-snap');
  }

  function _onTrackPointerMove(e) {
    if (!_drag || _drag.id !== e.pointerId || _drag.abandoned) return;
    if (_fsState.scale > 1.01) { _drag = null; return; }
    var dx = e.clientX - _drag.startX;
    var dy = e.clientY - _drag.startY;
    if (!_drag.didMove) {
      /* Lock in axis: horizontal → swipe, vertical → abandon */
      if (Math.abs(dx) > 6 && Math.abs(dx) > Math.abs(dy) * 1.2) {
        _drag.didMove = true;
      } else if (Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)) {
        _drag.abandoned = true;
        return;
      } else {
        return;
      }
    }
    e.preventDefault();
    _setTrack(_drag.startTx + dx, false);
  }

  function _onTrackPointerUp(e) {
    if (!_drag || _drag.id !== e.pointerId) return;
    var dx = e.clientX - _drag.startX;
    var dt = Math.max(1, Date.now() - _drag.t0);
    var velocity = Math.abs(dx) / dt;             /* px/ms */
    var didMove = _drag.didMove && !_drag.abandoned;
    _drag = null;
    if (!didMove) return;
    var threshold = Math.min(60, _W() * 0.15);
    var advance = 0;
    if (Math.abs(dx) > threshold || velocity > 0.5) {
      advance = dx < 0 ? 1 : -1;                  /* drag left → next; drag right → prev */
    }
    _goTo(_currentIdx + advance);                 /* advance=0 → snap back to current canonical */
  }

  /* ──────── Init: attach handlers once ──────── */
  function _fsInit() {
    _ensureMarkup();
    var track = document.getElementById('fs-track');
    if (track && !track._fsBound) {
      track._fsBound = true;
      track.addEventListener('pointerdown', _onTrackPointerDown);
      track.addEventListener('pointermove', _onTrackPointerMove);
      track.addEventListener('pointerup', _onTrackPointerUp);
      track.addEventListener('pointercancel', _onTrackPointerUp);
      /* re-center to canonical (middle) copy after swipe animation completes */
      track.addEventListener('transitionend', function(e) {
        if (e.propertyName !== 'transform') return;
        _recenter();
      });
    }

    /* delegated play button handler for video slides */
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
        video.play().catch(function() { /* iOS autoplay quirks — already inside click, should be OK */ });
      });
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
        if (e.key === 'ArrowLeft')       { _goTo(_currentIdx - 1); e.preventDefault(); }
        else if (e.key === 'ArrowRight') { _goTo(_currentIdx + 1); e.preventDefault(); }
        else if (e.key === 'Escape')     { closeFullscreen(); }
      });
    }
  }

  /* ──────── Slide markup ──────── */
  function _slideHtml(item, realIdx, copy) {
    /* copy: 'l' (left), 'm' (middle/canonical), 'r' (right), or null (single-item N=1) */
    var inner;
    if (item.kind === 'image') {
      inner = '<div class="fs-stage"><img class="fs-img" data-src="' + _escapeHtml(item.url) + '"></div>';
    } else {
      inner = '<div class="fs-video-wrap"><video preload="metadata" playsinline data-src="' + _escapeHtml(item.url) + '"></video><button type="button" class="fs-video-play" aria-label="播放">&#9654;</button></div>';
    }
    return '<section class="fs-slide" data-idx="' + realIdx + '" data-kind="' + _escapeHtml(item.kind) +
      '"' + (copy ? ' data-copy="' + copy + '"' : '') + '>' + inner + '</section>';
  }

  /* ──────── Open / chrome update / close ──────── */
  function openFullscreenGallery(items, startIdx) {
    if (!items || items.length === 0) return;
    _fsInit();
    _items = items.slice();
    _currentIdx = Math.max(0, Math.min(startIdx || 0, _items.length - 1));

    var track = document.getElementById('fs-track');
    var html = '';
    if (_multi()) {
      /* 3 copies: left | middle (canonical) | right */
      _items.forEach(function(item, i) { html += _slideHtml(item, i, 'l'); });
      _items.forEach(function(item, i) { html += _slideHtml(item, i, 'm'); });
      _items.forEach(function(item, i) { html += _slideHtml(item, i, 'r'); });
    } else {
      html += _slideHtml(_items[0], 0, null);
    }
    track.innerHTML = html;
    /* Set track width explicitly so flex children layout correctly */
    track.style.width = (_multi() ? _N() * 3 : 1) * 100 + 'vw';

    var multi = _multi();
    document.getElementById('fs-prev').style.display = multi ? '' : 'none';
    document.getElementById('fs-next').style.display = multi ? '' : 'none';
    document.getElementById('fs-counter').style.display = multi ? '' : 'none';

    var overlay = document.getElementById('fullscreen-overlay');
    overlay.classList.add('open');

    requestAnimationFrame(function() {
      _setTrack(-_canonicalSlot(_currentIdx) * _W(), false);
      _updateChrome();
      _ensureLoaded(_currentIdx);
      if (_N() > 1) {
        _ensureLoaded((_currentIdx - 1 + _N()) % _N());
        _ensureLoaded((_currentIdx + 1) % _N());
      }
      _bindPinchHandlersToSlide(_currentIdx);
    });
  }

  /* Update counter / download / arrows based on _currentIdx. Always-enabled arrows for circular wrap. */
  function _updateChrome() {
    var N = _N();
    if (N === 0) return;
    var item = _items[_currentIdx];
    var counter = document.getElementById('fs-counter');
    if (counter) counter.textContent = (_currentIdx + 1) + ' / ' + N;
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
    if (prevBtn) prevBtn.disabled = false;
    if (nextBtn) nextBtn.disabled = false;
  }

  /* Advance to target real idx (with wrap). Computes slot contiguous to current visual position
   * so transition animates smoothly even when wrapping; _recenter() snaps back after settling. */
  function _goTo(targetRealIdx) {
    var N = _N();
    if (N === 0) return;
    var realIdx = ((targetRealIdx % N) + N) % N;
    var oldIdx = _currentIdx;

    if (realIdx !== oldIdx) _pauseAndResetVideo(oldIdx);

    /* Compute slot to animate to: current slot + raw delta (preserves visual direction across wrap) */
    var currentSlot = Math.round(-_translateX / _W());
    var targetSlot;
    if (realIdx === oldIdx) {
      targetSlot = _multi() ? _canonicalSlot(oldIdx) : 0;   /* snap back to canonical for current */
    } else {
      var deltaIdx = targetRealIdx - oldIdx;                /* raw delta from caller (±1 typically) */
      targetSlot = currentSlot + deltaIdx;
      /* Clamp into renderable range [0, 3N-1] when multi-copy */
      if (_multi()) {
        var total = N * 3;
        targetSlot = ((targetSlot % total) + total) % total;
      } else {
        targetSlot = 0;
      }
    }

    _currentIdx = realIdx;
    _updateChrome();
    _fsReset();
    _ensureLoaded(realIdx);
    if (_multi()) {
      _ensureLoaded((realIdx - 1 + N) % N);
      _ensureLoaded((realIdx + 1) % N);
    }
    _setTrack(-targetSlot * _W(), true);
    _bindPinchHandlersToSlide(realIdx);
  }

  function _pauseAndResetVideo(realIdx) {
    var slides = document.querySelectorAll('.fs-slide[data-idx="' + realIdx + '"][data-kind="video"]');
    slides.forEach(function(slide) {
      var video = slide.querySelector('video');
      if (!video) return;
      try { video.pause(); } catch (e) {}
      try { video.currentTime = 0; } catch (e) {}
      video.controls = false;
      var wrap = slide.querySelector('.fs-video-wrap');
      if (wrap) wrap.classList.remove('playing');
    });
  }

  function _ensureLoaded(realIdx) {
    if (realIdx < 0 || realIdx >= _N()) return;
    /* lazy-load every copy (left/middle/right) of this real idx */
    var slides = document.querySelectorAll('.fs-slide[data-idx="' + realIdx + '"]');
    slides.forEach(function(slide) {
      var media = slide.querySelector('img, video');
      if (media && media.dataset.src && !media.src) media.src = media.dataset.src;
    });
  }

  /* Backward compat — F9 single-image callers still work */
  var openFullscreen = function(url, opts) {
    opts = opts || {};
    openFullscreenGallery([{ kind: 'image', url: url, downloadHref: opts.downloadHref || null }], 0);
  };

  function closeFullscreen() {
    var overlay = document.getElementById('fullscreen-overlay');
    if (!overlay) return;
    overlay.classList.remove('open');
    _items.forEach(function(_, i) { _pauseAndResetVideo(i); });
    var track = document.getElementById('fs-track');
    if (track) {
      track.innerHTML = '';
      track.classList.remove('fs-track-snap');
      track.style.transform = '';
      track.style.width = '';
    }
    _items = [];
    _currentIdx = 0;
    _translateX = 0;
    _drag = null;
    _fsReset();
  }

  function downloadAttachment(endpointUrl) {
    return fetch(endpointUrl, { credentials: 'same-origin' })
      .then(function(res) {
        if (!res.ok) {
          return res.json().then(function(j) {
            throw new Error(j.message ? j.message : 'HTTP ' + res.status);
          }, function() {
            throw new Error('HTTP ' + res.status);
          });
        }
        return res.json();
      })
      .then(function(j) {
        if (!j.url) throw new Error('missing url');
        var a = document.createElement('a');
        a.href = j.url;
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      })
      .catch(function(err) {
        var msg = err && err.message ? err.message : String(err);
        if (typeof window.showToast === 'function') {
          window.showToast('下載失敗：' + msg);
        } else {
          alert('下載失敗：' + msg);
        }
      });
  }

  window.openFullscreen = openFullscreen;
  window.openFullscreenGallery = openFullscreenGallery;
  window.closeFullscreen = closeFullscreen;
  window.downloadAttachment = downloadAttachment;
})();
