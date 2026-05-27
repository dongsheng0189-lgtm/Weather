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
      '<button type="button" class="fs-download" id="fs-download" aria-label="下載原檔" style="display:none;">' + DOWNLOAD_ICON_SVG(32) + '</button>' +
      '<div class="fs-hint">雙指縮放 · 雙擊放大 · 拖曳移動</div>';
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

  function openFullscreen(url, opts) {
    opts = opts || {};
    _fsInit();
    _fsReset();
    document.getElementById('fullscreen-img').src = url;
    document.getElementById('fullscreen-overlay').classList.add('open');
    var dlBtn = document.getElementById('fs-download');
    if (dlBtn) {
      if (opts.downloadHref) {
        dlBtn.style.display = '';
        dlBtn.dataset.href = opts.downloadHref;
      } else {
        dlBtn.style.display = 'none';
        dlBtn.dataset.href = '';
      }
    }
  }

  function closeFullscreen() {
    var overlay = document.getElementById('fullscreen-overlay');
    if (!overlay) return;
    overlay.classList.remove('open');
    var img = document.getElementById('fullscreen-img');
    if (img) img.src = '';
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
  window.closeFullscreen = closeFullscreen;
  window.downloadAttachment = downloadAttachment;
})();
