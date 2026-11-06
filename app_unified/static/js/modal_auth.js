/**
 * 隱藏登入視窗的送出邏輯。POST /auth/verify。
 *
 * 2026-11-06 由 app2_weather 版本移植，並對齊 app_unified 的回應格式：
 *   - app_unified 成功時只回 {"status":"ok"}，**沒有 redirect_url**
 *     （auth/routes.py 的 docstring：前端自己導去 /auth/s/r，回應裡不帶 token）
 *   - 多了 need_face_enroll / face_not_found / store_disabled 三種狀態
 *   - 沒有 legacy 的 demo 狀態
 */
(function () {
  const modal = document.getElementById('auth-modal');
  const closeBtn = document.getElementById('modal-close');
  const submitBtn = document.getElementById('m-submit');
  const msgEl = document.getElementById('modal-msg');

  function showMsg(text, cls) {
    if (!msgEl) return;
    msgEl.textContent = text;
    msgEl.className = cls || '';
  }

  function closeModal() {
    modal?.classList.remove('open');
    showMsg('');
    const pin = document.getElementById('m-pin');
    if (pin) pin.value = '';
    if (window.stopModalCamera) window.stopModalCamera();
  }

  closeBtn?.addEventListener('click', closeModal);

  // 點背景關閉
  modal?.addEventListener('click', (e) => {
    if (e.target === modal) closeModal();
  });

  submitBtn?.addEventListener('click', async () => {
    const pin = document.getElementById('m-pin')?.value.trim() || '';
    const faceImage = window.captureModalFace ? window.captureModalFace() : null;

    submitBtn.disabled = true;
    showMsg('辨識中…');

    try {
      const res = await fetch('/auth/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin, face_image: faceImage }),
      });
      const data = await res.json();

      switch (data.status) {
        case 'ok':
          showMsg('驗證成功！正在跳轉…', 'ok');
          if (window.stopModalCamera) window.stopModalCamera();
          // /auth/s/r 依使用者可見模組數決定去哪：0 個回天氣頁、1 個直接進該模組、
          // 多個進選單頁。前端不需要知道規則。
          setTimeout(() => { location.href = '/auth/s/r'; }, 800);
          break;

        case 'wrong_password':
          showMsg('密碼錯誤', 'err');
          break;

        case 'face_mismatch':
          // 刻意誤導：不告訴對方「PIN 對了但臉不對」，免得被拿來逐一試 PIN。
          showMsg('密碼錯了喔', 'err');
          break;

        case 'face_not_found':
          showMsg('沒有偵測到人臉，請正對鏡頭再試一次', 'err');
          break;

        case 'need_face_enroll':
          showMsg('此帳號尚未登錄人臉，請聯絡管理員', 'err');
          break;

        case 'store_disabled':
          showMsg('此店家目前停用登入', 'err');
          break;

        default:
          showMsg('驗證失敗，請重試', 'err');
      }
    } catch (err) {
      showMsg('連線錯誤，請重試', 'err');
    } finally {
      submitBtn.disabled = false;
    }
  });

  // Enter 送出
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && modal?.classList.contains('open')) {
      submitBtn?.click();
    }
  });
})();
