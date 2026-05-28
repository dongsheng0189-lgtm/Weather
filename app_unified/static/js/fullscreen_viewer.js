/* Shared fullscreen viewer — F6 spec: docs/superpowers/specs/2026-05-25-announcement-media-viewer.md
 *
 * Public API:
 *   window.openFullscreen(url, opts)  — 顯示 image fullscreen + ✕ + pinch zoom + dblclick + drag
 *                                       opts.downloadHref: optional endpoint URL for ⬇️ button
 *   window.closeFullscreen()          — 關閉
 *   window.downloadAttachment(endpointUrl) — 下載附件（fetch presigned URL → hidden <a> click）
 *   window.DOWNLOAD_ICON_SVG(size)    — 共用 download SVG icon、size 預設 20、viewer 用 32
 *
 * 第一次 openFullscreen 自動 dynamic inject overlay markup 到 document.body (idempotent)。
 * 抽自 notes/editor.html line 914-1067、原本 inline 在 editor template + 用 inline HTML overlay。
 */
(function() {
  // Feather Icons "download" — 線條清楚、跟 webapp 視覺風格一致
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

  var _fsState = { scale: 1, tx: 0, ty: 0, minScale: 1, maxScale: 6 };
  var _fsPointers = {};
  var _fsLastDist = 0;
  var _fsLastCenter = { x: 0, y: 0 };
  var _fsLastTap = 0;
  var _items = [];
  var _currentIdx = 0;

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

  function _fsInit() {
    _ensureMarkup();
    var track = document.getElementById('fs-track');
    if (track && !track._fsBound) {
      track._fsBound = true;

      /* page tracking — prefer scrollend, fallback to scroll + 200ms debounce */
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

  function openFullscreenGallery(items, startIdx) {
    if (!items || items.length === 0) return;
    _fsInit();
    _items = items.slice();
    _currentIdx = Math.max(0, Math.min(startIdx || 0, _items.length - 1));

    var track = document.getElementById('fs-track');
    var html = '';
    _items.forEach(function(item, i) {
      var inner;
      if (item.kind === 'image') {
        inner = '<div class="fs-stage"><img class="fs-img" data-src="' + _escapeHtml(item.url) + '"></div>';
      } else {
        inner = '<div class="fs-video-wrap"><video preload="metadata" playsinline data-src="' + _escapeHtml(item.url) + '"></video><button type="button" class="fs-video-play" aria-label="播放">&#9654;</button></div>';
      }
      html += '<section class="fs-slide" data-idx="' + i + '" data-kind="' + _escapeHtml(item.kind) + '">' + inner + '</section>';
    });
    track.innerHTML = html;

    var multi = _items.length > 1;
    document.getElementById('fs-prev').style.display = multi ? '' : 'none';
    document.getElementById('fs-next').style.display = multi ? '' : 'none';
    document.getElementById('fs-counter').style.display = multi ? '' : 'none';

    var overlay = document.getElementById('fullscreen-overlay');
    overlay.classList.add('open');

    requestAnimationFrame(function() {
      track.scrollLeft = _currentIdx * window.innerWidth;
      _onPageChange(_currentIdx);
    });
  }

  function _onPageChange(newIdx) {
    if (newIdx < 0 || newIdx >= _items.length) return;
    _pauseAndResetVideo(_currentIdx);
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
    _ensureLoaded(newIdx);
    _ensureLoaded(newIdx - 1);
    _ensureLoaded(newIdx + 1);
  }

  function _goTo(idx) {
    if (idx < 0 || idx >= _items.length) return;
    var track = document.getElementById('fs-track');
    if (!track) return;
    track.scrollTo({ left: idx * window.innerWidth, behavior: 'smooth' });
    /* scrollend handler will fire _onPageChange */
  }

  /* Placeholders — _ensureLoaded baseline here, T6 guards. */
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

  function _ensureLoaded(idx) {
    if (idx < 0 || idx >= _items.length) return;
    var slide = document.querySelector('.fs-slide[data-idx="' + idx + '"]');
    if (!slide) return;
    var media = slide.querySelector('img, video');
    if (media && media.dataset.src && !media.src) media.src = media.dataset.src;
  }

  /* Backward compat — F9 single-image callers still work. */
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
    if (track) track.innerHTML = '';
    _items = [];
    _currentIdx = 0;
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
