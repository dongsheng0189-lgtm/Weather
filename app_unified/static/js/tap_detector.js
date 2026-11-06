/**
 * #tap-target 上 2 秒內連點 6 下 → 開啟隱藏登入視窗。
 *
 * 2026-11-06 由 app2_weather/static/js/tap_detector.js 移植回 app_unified。
 * app_unified 的 face_capture.js 一直都是為這個視窗寫的（startModalCamera /
 * captureModalFace / 綁 #modal-close），但 HTML 與本檔、modal_auth.js 不在 repo 裡，
 * 正式站只靠舊的 PWA 快取在運作 —— 任何全新瀏覽器都登不進去。
 */
(function () {
  const TARGET_TAPS = 6;
  // 6 秒，不是 app2_weather 那份的 2000。移植時照抄 legacy 的 2 秒是錯的 ——
  // 原開發者說遺失的 app_unified 版本是 6 秒，而且 2 秒內在手機上點 6 下太趕。
  const RESET_MS = 6000;
  let count = 0;
  let timer = null;
  const indicator = document.getElementById('tap-indicator');

  function reset() {
    count = 0;
    if (indicator) { indicator.style.display = 'none'; indicator.textContent = ''; }
  }

  document.getElementById('tap-target')?.addEventListener('click', () => {
    clearTimeout(timer);
    count++;

    if (indicator) {
      indicator.style.display = 'flex';
      indicator.textContent = count;
    }

    if (count >= TARGET_TAPS) {
      reset();
      document.getElementById('auth-modal')?.classList.add('open');
      // 相機必須在使用者手勢內啟動，行動瀏覽器才會給權限 —— 所以開窗當下就啟動，
      // 不能等到按「驗證登入」才開。
      if (window.startModalCamera) window.startModalCamera();
      return;
    }

    timer = setTimeout(reset, RESET_MS);
  });
})();
