/**
 * 私人筆記隱私黑幕（筆電版）
 * 啟動條件：window.MN_PRIVACY_AT_MS > 0 (例：5 分鐘)
 *
 * 行為：
 *  - 連續閒置達 MN_PRIVACY_AT_MS → 顯示全螢幕黑幕
 *  - 任何使用者操作（mousemove / click / keydown / scroll / touchstart）→ 立刻喚醒（隱藏黑幕）
 *  - 黑幕只是視覺遮蔽，不會踢人；硬登出由 base.html 的 IDLE_LIMIT_MS 計時器負責
 */
(function () {
  var PRIVACY_MS = window.MN_PRIVACY_AT_MS || 0;
  if (PRIVACY_MS <= 0) return;  // 手機 / 不適用

  var screenEl = document.createElement('div');
  screenEl.id = 'mn-privacy-screen';
  screenEl.style.cssText =
    'position:fixed;inset:0;background:#000;z-index:99998;display:none;opacity:0;' +
    'transition:opacity .4s ease;cursor:pointer;';
  document.addEventListener('DOMContentLoaded', function () {
    document.body.appendChild(screenEl);
  });

  var lastActivity = Date.now();
  var visible = false;
  var timer = null;

  function show() {
    if (visible) return;
    visible = true;
    screenEl.style.display = 'block';
    requestAnimationFrame(function () { screenEl.style.opacity = '1'; });
  }

  function hide() {
    if (!visible) return;
    visible = false;
    screenEl.style.opacity = '0';
    setTimeout(function () { if (!visible) screenEl.style.display = 'none'; }, 400);
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(show, PRIVACY_MS);
  }

  function onActivity() {
    lastActivity = Date.now();
    if (visible) hide();
    schedule();
  }

  ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll'].forEach(function (evt) {
    document.addEventListener(evt, onActivity, { passive: true });
  });

  // 螢幕鎖定 / tab 切回時：若已超過 PRIVACY_MS，立即無動畫蓋上
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && (Date.now() - lastActivity >= PRIVACY_MS)) {
      screenEl.style.transition = 'none';
      visible = true;
      screenEl.style.display = 'block';
      screenEl.style.opacity = '1';
      requestAnimationFrame(function () { screenEl.style.transition = ''; });
    }
  });

  schedule();
})();
