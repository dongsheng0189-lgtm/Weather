/* 編輯頁面保活 + 草稿 fallback（共用）
 *
 * 用途：
 *  - B 保活：使用者在頁面有動作（input/touchstart/pointerdown）時，每分鐘 ping
 *            /auth/ping 以刷新伺服器 _last_request_at，避免編輯中被閒置登出。
 *  - C 草稿：debounce 把 serialize() 結果寫進 localStorage；save 呼叫在失敗或
 *            session 逾時（非 JSON response / 401）時，保留 draft 並顯示全螢幕
 *            黑幕提示「登入逾時，內容已暫存」，使用者重新登入回到同一頁會看到
 *            還原 banner。
 *
 * API:
 *    EditKeepalive.init({
 *      storageKey: 'mn_draft:<uid>:<id>',
 *      serialize: () => ({...})  返回 null 表示沒內容不要存
 *      restore:   (data) => { ... }
 *    });
 *    EditKeepalive.markSaved();              // 儲存成功時呼叫 → 清掉 draft
 *    EditKeepalive.fetchJson(url, options);  // 包裝過的 fetch（自動偵測 auth lost）
 */
(function (global) {
  'use strict';

  var HEARTBEAT_MS = 60 * 1000;     // 每 60s 檢查
  var ACTIVE_WINDOW_MS = 90 * 1000; // 過去 90s 有動作才算活躍
  var DRAFT_DEBOUNCE_MS = 1000;
  var DRAFT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000; // 7 天過期
  var PING_URL = '/auth/ping';

  var _opts = null;
  var _lastActivity = 0;
  var _hbTimer = null;
  var _draftTimer = null;
  var _authLost = false;
  var _dirty = false; // 使用者真的改過內容才設 true

  function _now() { return Date.now(); }

  function _markActivity() { _lastActivity = _now(); }

  function _getStorageKey() {
    if (!_opts) return null;
    return (typeof _opts.storageKey === 'function') ? _opts.storageKey() : _opts.storageKey;
  }

  function _writeDraft() {
    if (!_opts || _authLost) return;
    if (!_dirty) return; // 沒改過就不寫
    var key = _getStorageKey();
    if (!key) return;
    try {
      var data = _opts.serialize();
      if (data == null) {
        localStorage.removeItem(key);
        return;
      }
      localStorage.setItem(key, JSON.stringify({
        data: data,
        savedAt: _now(),
      }));
    } catch (e) { /* quota / private mode — ignore */ }
  }

  function _scheduleDraft() {
    clearTimeout(_draftTimer);
    _draftTimer = setTimeout(_writeDraft, DRAFT_DEBOUNCE_MS);
  }

  function _clearDraft() {
    var key = _getStorageKey();
    if (!key) return;
    try { localStorage.removeItem(key); } catch (e) {}
  }

  function _readDraft() {
    var key = _getStorageKey();
    if (!key) return null;
    try {
      var raw = localStorage.getItem(key);
      if (!raw) return null;
      var obj = JSON.parse(raw);
      if (!obj || !obj.savedAt || (_now() - obj.savedAt) > DRAFT_MAX_AGE_MS) {
        localStorage.removeItem(key);
        return null;
      }
      return obj;
    } catch (e) { return null; }
  }

  function _showRestoreBanner(draft) {
    var bar = document.createElement('div');
    bar.id = 'ek-restore-banner';
    bar.style.cssText =
      'position:sticky;top:0;z-index:9999;background:#fff3cd;border-bottom:2px solid #f0ad4e;' +
      'padding:.6rem .8rem;font-size:.9rem;color:#5a4300;display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;';
    var ago = Math.round((_now() - draft.savedAt) / 60000);
    var ts = new Date(draft.savedAt).toLocaleString();
    var txt = document.createElement('span');
    txt.style.flex = '1';
    txt.textContent = '偵測到未儲存的內容（' + ts + '，約 ' + ago + ' 分鐘前）';
    var btnRestore = document.createElement('button');
    btnRestore.textContent = '還原';
    btnRestore.style.cssText = 'padding:.3rem .8rem;border:0;border-radius:4px;background:#27ae60;color:#fff;font-weight:600;cursor:pointer;';
    btnRestore.onclick = function () {
      try { _opts.restore(draft.data); } catch (e) { console.error(e); }
      bar.remove();
    };
    var btnDrop = document.createElement('button');
    btnDrop.textContent = '丟棄';
    btnDrop.style.cssText = 'padding:.3rem .8rem;border:1px solid #aaa;border-radius:4px;background:#fff;cursor:pointer;';
    btnDrop.onclick = function () { _clearDraft(); bar.remove(); };
    bar.appendChild(txt);
    bar.appendChild(btnRestore);
    bar.appendChild(btnDrop);
    document.body.insertBefore(bar, document.body.firstChild);
  }

  function _showAuthLostOverlay() {
    if (_authLost) return;
    _authLost = true;
    // 在標黑幕之前，先強制把當下最新狀態 flush 到 localStorage
    _writeDraft();
    var cover = document.createElement('div');
    cover.style.cssText =
      'position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:2147483647;' +
      'display:flex;align-items:center;justify-content:center;color:#fff;padding:2rem;';
    var box = document.createElement('div');
    box.style.cssText = 'max-width:420px;text-align:center;font-size:1rem;line-height:1.6;';
    box.innerHTML =
      '<div style="font-size:1.2rem;font-weight:700;margin-bottom:.8rem;">登入逾時</div>' +
      '<div style="margin-bottom:1.2rem;">你的編輯內容已暫存在本機。<br>重新登入並回到這個頁面後，可選擇還原。</div>';
    var btn = document.createElement('button');
    btn.textContent = '重新登入';
    btn.style.cssText = 'padding:.6rem 1.4rem;border:0;border-radius:6px;background:#3498db;color:#fff;font-weight:700;font-size:1rem;cursor:pointer;';
    btn.onclick = function () { window.location.href = '/auth/logout'; };
    box.appendChild(btn);
    cover.appendChild(box);
    document.documentElement.appendChild(cover);
  }

  function _heartbeat() {
    if (_authLost) return;
    if ((_now() - _lastActivity) > ACTIVE_WINDOW_MS) return; // 使用者真的閒著 → 放著讓它自然逾時
    fetch(PING_URL, { method: 'GET', credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) {
        if (r.status === 401) { _showAuthLostOverlay(); return; }
        if (!r.ok) return;
      })
      .catch(function () { /* 網路斷線 — 不觸發 overlay，等使用者自己重送 */ });
  }

  // 包裝 fetch：只處理 application/json response；遇到 401 / HTML → auth lost
  function fetchJson(url, options) {
    if (_authLost) return Promise.reject(new Error('auth lost'));
    return fetch(url, Object.assign({ credentials: 'same-origin' }, options || {}))
      .then(function (r) {
        if (r.status === 401) {
          _showAuthLostOverlay();
          return Promise.reject(new Error('unauthorized'));
        }
        var ct = (r.headers.get('content-type') || '').toLowerCase();
        if (ct.indexOf('application/json') === -1) {
          // session 過期後 Flask-Login 多半回 302 → follow 後是 HTML。
          // 也可能是後端炸了回 HTML error page。保守都當 auth lost 較安全。
          _showAuthLostOverlay();
          return Promise.reject(new Error('non-json response'));
        }
        return r.json().then(function (data) {
          if (!r.ok) {
            var err = new Error((data && data.message) || ('HTTP ' + r.status));
            err.data = data;
            err.status = r.status;
            throw err;
          }
          return data;
        });
      });
  }

  function init(opts) {
    if (_opts) return; // idempotent
    _opts = Object.assign({}, opts);

    if (typeof _opts.serialize !== 'function' || typeof _opts.restore !== 'function' || !_opts.storageKey) {
      console.warn('[EditKeepalive] init missing required opts');
      return;
    }

    // 活動偵測：任何互動都算（給 heartbeat 用）。不代表有內容變動。
    ['touchstart', 'pointerdown', 'keydown', 'scroll'].forEach(function (ev) {
      document.addEventListener(ev, _markActivity, { passive: true, capture: true });
    });
    // 只有「真的有內容變動」才排程 draft 寫入
    ['input', 'change'].forEach(function (ev) {
      document.addEventListener(ev, function () {
        _markActivity();
        _dirty = true;
        _scheduleDraft();
      }, { passive: true, capture: true });
    });
    _markActivity();

    // 起 heartbeat
    _hbTimer = setInterval(_heartbeat, HEARTBEAT_MS);

    // 頁面從背景回來立即 check
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) _heartbeat();
    });

    // 載入時若有 draft，提示還原
    var draft = _readDraft();
    if (draft) {
      // 稍延遲到 DOM 準備好
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { _showRestoreBanner(draft); });
      } else {
        _showRestoreBanner(draft);
      }
    }
  }

  function markSaved() {
    clearTimeout(_draftTimer);
    _dirty = false;
    _clearDraft();
  }

  global.EditKeepalive = {
    init: init,
    markSaved: markSaved,
    fetchJson: fetchJson,
  };
})(window);
