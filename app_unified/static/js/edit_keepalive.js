/* 編輯頁面保活 (heartbeat only)
 *
 * 唯一任務：使用者在編輯頁有動作時，每 60 秒 ping /auth/ping 刷新伺服器
 * session，避免 5 分鐘閒置被 _enforce_session_idle 踢出導致儲存失敗。
 *
 * 注意：本版刻意不保留「草稿 / 還原 banner」功能，該邏輯曾因滑動觸發
 * 誤寫 draft 導致每次重進頁面都跳還原提示。
 *
 * API：EditKeepalive.init();  // 放在編輯頁底部呼叫即可
 */
(function (global) {
  'use strict';

  var HEARTBEAT_MS = 60 * 1000;
  var ACTIVE_WINDOW_MS = 90 * 1000;
  var PING_URL = '/auth/ping';

  var _inited = false;
  var _lastActivity = 0;

  // 舊版（含 bug）會在 localStorage 寫入 draft。載入時主動清掉，
  // 讓升級上來的使用者不必手動清瀏覽器資料（清資料會連帶洗掉 device_uid）。
  function _cleanupLegacyDrafts() {
    try {
      var prefixes = ['mn_draft:', 'mn_draft_v2:', 'notes_draft:', 'notes_draft_v2:'];
      var toRemove = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (!k) continue;
        for (var j = 0; j < prefixes.length; j++) {
          if (k.indexOf(prefixes[j]) === 0) { toRemove.push(k); break; }
        }
      }
      toRemove.forEach(function (k) { localStorage.removeItem(k); });
    } catch (e) { /* quota / private mode — ignore */ }
  }

  function _markActivity() { _lastActivity = Date.now(); }

  function _heartbeat() {
    if ((Date.now() - _lastActivity) > ACTIVE_WINDOW_MS) return;
    fetch(PING_URL, { method: 'GET', credentials: 'same-origin', cache: 'no-store' })
      .catch(function () { /* 網路斷線就算了，不打擾使用者 */ });
  }

  function init() {
    if (_inited) return;
    _inited = true;

    _cleanupLegacyDrafts();

    ['touchstart', 'pointerdown', 'keydown', 'input', 'change', 'scroll'].forEach(function (ev) {
      document.addEventListener(ev, _markActivity, { passive: true, capture: true });
    });
    _markActivity();

    setInterval(_heartbeat, HEARTBEAT_MS);

    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) _heartbeat();
    });
  }

  global.EditKeepalive = { init: init };
})(window);
