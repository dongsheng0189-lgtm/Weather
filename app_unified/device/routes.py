import os
import uuid as _uuid
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, Response, make_response
from extensions import db
from models import TrustedDevice, User, Store

device_bp = Blueprint("device", __name__, url_prefix="/api/v1")

UID_COOKIE_NAME = "device_uid"
UID_COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 10  # 10 years
PENDING_DEVICE_TTL_MINUTES = 30


def _get_cookie_uid():
    """從 cookie 讀 device_uid（優先於 request body）"""
    val = (request.cookies.get(UID_COOKIE_NAME) or "").strip()
    return val or None


def _set_uid_cookie(resp, uid):
    resp.set_cookie(
        UID_COOKIE_NAME,
        uid,
        max_age=UID_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="Lax",
    )


def _cleanup_pending_devices():
    """刪除未核准且建立超過 PENDING_DEVICE_TTL_MINUTES 分鐘的裝置"""
    cutoff = datetime.utcnow() - timedelta(minutes=PENDING_DEVICE_TTL_MINUTES)
    stale = TrustedDevice.query.filter(
        TrustedDevice.is_approved.is_(False),
        TrustedDevice.created_at < cutoff,
    ).all()
    if not stale:
        return 0
    for d in stale:
        db.session.delete(d)
    db.session.commit()
    return len(stale)


def is_device_authorized(fp, uid=None):
    """檢查設備是否已授權且未掛失
    優先順序：
    1. 有 client_uid → 用 uid 查
    2. 沒有 uid → fallback 用 fingerprint 查
    """
    device = None
    if uid:
        device = TrustedDevice.query.filter_by(client_uid=uid).first()
    if not device and fp:
        device = TrustedDevice.query.filter_by(fingerprint=fp).first()
    if not device or not device.is_approved or device.is_revoked:
        return False
    # 設備綁定的使用者若被停用 → 這台手機連 6 下都不該觸發
    # （共用公務機的情境：公務機綁的是在職「代表人」，停用某共用者不受影響；
    #  只有當代表人本身被停用時才鎖住這台手機）
    if device.user_id and device.user and not device.user.is_active:
        return False
    return True


def is_seed_mode():
    """以下任一情況進入種子模式：
    1. DB 沒有 admin
    2. 有 admin 但沒有任何已核准設備
    3. 所有 admin 都沒有 face_encoding（人臉資料損壞）
    """
    admins = User.query.filter(User.role.in_(["admin", "super_admin"])).all()
    if not admins:
        return True
    if TrustedDevice.query.filter_by(is_approved=True).count() == 0:
        return True
    # 所有 admin 都沒有有效的人臉資料
    if all(a.face_encoding is None for a in admins):
        return True
    return False


@device_bp.route("/register-device", methods=["POST"])
def register_device():
    """前端自動呼叫，註冊設備指紋
    UID 來源優先順序：cookie > body.client_uid > 伺服器新發

    匹配邏輯：
    1. 有 uid 找到裝置 → SEEN
    2. 有 uid 但找不到，fingerprint 相同且 uid IS NULL → CLAIM 認領
    3. 以上皆無 → 新增
    4. 沒送 fingerprint 也沒 uid → 400
    """
    # 順便清掉超過 30 分鐘未核准的裝置
    try:
        _cleanup_pending_devices()
    except BaseException:
        db.session.rollback()

    data = request.get_json(silent=True) or {}
    fp = data.get("fingerprint", "").strip()
    body_uid = (data.get("client_uid") or "").strip() or None
    device_name = data.get("device_name", "Unknown")

    cookie_uid = _get_cookie_uid()
    uid = cookie_uid or body_uid

    if not fp and not uid:
        return jsonify({"status": "error", "message": "missing fingerprint"}), 400

    existing = None
    claimed = False
    if uid:
        existing = TrustedDevice.query.filter_by(client_uid=uid).first()
        if not existing and fp:
            orphan = TrustedDevice.query.filter_by(fingerprint=fp, client_uid=None).first()
            if orphan:
                orphan.client_uid = uid
                existing = orphan
                claimed = True
    else:
        if fp:
            existing = TrustedDevice.query.filter_by(fingerprint=fp).first()

    # 若沒有 uid（從未產生過），伺服器主動發一個
    if not uid:
        uid = _uuid.uuid4().hex

    if existing:
        existing.last_seen_at = datetime.utcnow()
        db.session.commit()
        resp = make_response(jsonify({
            "status": "ok", "registered": False, "claimed": claimed, "client_uid": uid,
        }))
        _set_uid_cookie(resp, uid)
        return resp

    device = TrustedDevice(
        fingerprint=fp or "",
        client_uid=uid,
        device_name=device_name or "Unknown",
        is_approved=False,
        is_revoked=False,
    )
    db.session.add(device)
    db.session.commit()
    resp = make_response(jsonify({"status": "ok", "registered": True, "client_uid": uid}), 201)
    _set_uid_cookie(resp, uid)
    return resp


@device_bp.route("/salt")
def get_salt():
    """動態 Salt — 連點功能啟用前必須取得"""
    fp = (request.args.get("fp", "") or request.headers.get("X-Device-FP", "")).strip()
    uid = (request.args.get("uid", "") or request.headers.get("X-Device-UID", "")).strip() or _get_cookie_uid()
    if not is_device_authorized(fp, uid):
        return jsonify({"error": "\u6c23\u8c61\u4f3a\u670d\u5668\u9023\u7dda\u7570\u5e38"}), 503
    salt = os.urandom(16).hex()
    return jsonify({"status": "ok", "salt": salt})


@device_bp.route("/secure-loader")
def secure_loader():
    """回傳動態載入的 JS（連點 + PIN modal + 人臉驗證）"""
    fp = (request.args.get("fp", "") or request.headers.get("X-Device-FP", "")).strip()
    uid = (request.args.get("uid", "") or request.headers.get("X-Device-UID", "")).strip() or _get_cookie_uid()
    if not is_device_authorized(fp, uid):
        return "", 404
    return Response(_build_secure_loader_js(), mimetype="application/javascript")


@device_bp.route("/stealth.wasm")
def serve_wasm():
    """回傳 WASM 二進位檔"""
    fp = (request.args.get("fp", "") or request.headers.get("X-Device-FP", "")).strip()
    uid = (request.args.get("uid", "") or request.headers.get("X-Device-UID", "")).strip() or _get_cookie_uid()
    if not is_device_authorized(fp, uid):
        return "", 404
    import os
    wasm_path = os.path.join(os.path.dirname(__file__), '..', 'static', 'wasm', 'stealth_bg.wasm')
    with open(wasm_path, 'rb') as f:
        wasm_bytes = f.read()
    return Response(wasm_bytes, mimetype="application/wasm")


@device_bp.route("/seed-setup", methods=["GET"])
def seed_setup_loader():
    """種子模式：回傳初始化設定的 JS"""
    if not is_seed_mode():
        return "", 404
    return Response(_build_seed_setup_js(), mimetype="application/javascript")


@device_bp.route("/seed-setup", methods=["POST"])
def seed_setup_submit():
    """種子模式：建立第一位 admin 或綁定現有 admin 的設備"""
    if not is_seed_mode():
        return jsonify({"status": "error", "message": "已有管理員"}), 403

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    pin = (data.get("pin") or "").strip()
    face_image = data.get("face_image")
    fp = data.get("fingerprint", "").strip()
    device_name = data.get("device_name", "Unknown")

    if not username or not pin:
        return jsonify({"status": "error", "message": "請填寫帳號和 PIN"}), 400

    # 如果帳號已存在（現有 admin），驗證 PIN 後綁定設備 + 更新人臉
    existing_user = User.query.filter_by(username=username).first()
    if existing_user:
        if not existing_user.check_password(pin):
            return jsonify({"status": "error", "message": "PIN 碼錯誤"}), 401
        # 更新人臉（如果之前的人臉資料損壞或不存在）
        if face_image:
            try:
                import face_recognition
                import io
                import base64
                img_data = base64.b64decode(face_image.split(",")[-1])
                img = face_recognition.load_image_file(io.BytesIO(img_data))
                encodings = face_recognition.face_encodings(img)
                if encodings:
                    existing_user.set_face_encoding(encodings[0])
            except BaseException:
                pass
        # 綁定設備
        device = TrustedDevice.query.filter_by(fingerprint=fp).first()
        if not device and fp:
            device = TrustedDevice(fingerprint=fp, device_name=device_name)
            db.session.add(device)
            db.session.flush()
        if device:
            device.user_id = existing_user.id
            device.is_approved = True
        db.session.commit()
        return jsonify({"status": "ok", "user_id": existing_user.id})

    user = User(username=username, role="super_admin")
    user.set_password(pin)

    if face_image:
        try:
            import face_recognition
            import io
            import base64
            img_data = base64.b64decode(face_image.split(",")[-1])
            img = face_recognition.load_image_file(io.BytesIO(img_data))
            encodings = face_recognition.face_encodings(img)
            if encodings:
                user.set_face_encoding(encodings[0])
        except BaseException:
            pass

    db.session.add(user)
    db.session.flush()

    # 自動綁定並核准此設備
    device = TrustedDevice.query.filter_by(fingerprint=fp).first()
    if not device and fp:
        device = TrustedDevice(fingerprint=fp, device_name=device_name)
        db.session.add(device)
        db.session.flush()
    if device:
        device.user_id = user.id
        device.is_approved = True

    db.session.commit()
    return jsonify({"status": "ok", "user_id": user.id})


def _build_secure_loader_js():
    return r"""
(function() {
  // Extract fingerprint + uid from script URL
  var _fp = '', _uid = '';
  var _ss = document.getElementsByTagName('script');
  for (var i = 0; i < _ss.length; i++) {
    var _src = _ss[i].src || '';
    var _mfp = _src.match(/[?&]fp=([^&]+)/);
    var _muid = _src.match(/[?&]uid=([^&]+)/);
    if (_mfp || _muid) {
      if (_mfp) _fp = decodeURIComponent(_mfp[1]);
      if (_muid) _uid = decodeURIComponent(_muid[1]);
      break;
    }
  }

  // Load WASM module
  var _wasmUrl = '/api/v1/stealth.wasm?fp=' + encodeURIComponent(_fp) + '&uid=' + encodeURIComponent(_uid) + '&_t=' + Date.now();

  import('/static/wasm/stealth.js').then(function(mod) {
    return mod.default().then(function() {
      var S = new mod.Stealth(Date.now());
      var _act = S.init_action();
      if (_act === 1) _s(S);
    });
  }).catch(function(e) { /* silent */ });

  // ── Bridge functions (generic names hide purpose) ──

  function _s(S) {
    // Salt verification
    var url = S.salt_path() + encodeURIComponent(_fp) + '&uid=' + encodeURIComponent(_uid);
    fetch(url).then(function(r) { return r.json(); }).then(function(d) {
      if (d.status === 'ok' && d.salt) {
        S.on_salt_result(1);
        _b(S);
      }
    }).catch(function() {});
  }

  function _b(S) {
    // Bind tap target
    var t = document.getElementById('tap-target');
    if (!t) return;

    // Periodic timeout check
    var _ti = setInterval(function() {
      var r = S.check_timeout(Date.now());
      if (r === 9) { clearInterval(_ti); _x(); }
    }, 1000);

    t.addEventListener('click', function _th() {
      var r = S.on_tap(Date.now());
      if (r === 9) { clearInterval(_ti); t.removeEventListener('click', _th); _x(); }
      if (r === 2) { clearInterval(_ti); t.removeEventListener('click', _th); _h(); _c(); }
    });
  }

  // ── DOM operations ──

  var _vid, _cvs, _str, _cs = 'idle';

  function _h() {
    // Inject modal HTML + CSS
    var st = document.createElement('style');
    st.setAttribute('data-s', '1');
    st.textContent = ''
      + '#auth-modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:1000;align-items:flex-start;justify-content:center;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:2rem 0}'
      + '#auth-modal.open{display:flex}'
      + '.modal-box{background:#fff;color:#333;border-radius:16px;padding:2rem;width:360px;max-width:95vw;position:relative}'
      + '.modal-box h2{margin-bottom:1.25rem;color:#2c3e50}'
      + '.modal-close{position:absolute;top:.75rem;right:1rem;font-size:1.5rem;background:none;border:none;cursor:pointer;color:#888;min-width:44px;min-height:44px}'
      + '.modal-form-group{margin-bottom:.9rem}'
      + '.modal-form-group label{display:block;margin-bottom:.3rem;font-size:.85rem;font-weight:600;color:#555}'
      + '.modal-form-group input{width:100%;padding:.55rem .75rem;border:1px solid #ddd;border-radius:6px;font-size:16px}'
      + '.modal-form-group input:focus{outline:none;border-color:#3498db}'
      + '.btn-modal{display:block;width:100%;padding:.75rem;border-radius:8px;border:none;cursor:pointer;font-size:.95rem;margin-top:.5rem;font-weight:600;min-height:44px}'
      + '.btn-modal-primary{background:#3498db;color:#fff}'
      + '.btn-modal-primary:hover{background:#2980b9}'
      + '#modal-msg{text-align:center;font-size:.9rem;min-height:1.3em;margin-top:.5rem}'
      + '#modal-msg.ok{color:#27ae60}'
      + '#modal-msg.err{color:#e74c3c}'
      + '@keyframes blink{0%,100%{opacity:1}50%{opacity:0}}';
    document.head.appendChild(st);

    var d = document.createElement('div');
    d.innerHTML = ''
      + '<div id="auth-modal" class="open"><div class="modal-box">'
      + '<button class="modal-close" id="modal-close">&times;</button>'
      + '<h2>\ud83d\udd10 \u9032\u5165\u7cfb\u7d71</h2>'
      + '<div class="modal-form-group"><label style="display:flex;align-items:center;gap:.5rem">PIN \u78bc'
      + '<span id="m-cam-indicator" style="display:none;width:10px;height:10px;border-radius:50%;background:#e74c3c;animation:blink 1s infinite"></span>'
      + '</label><input type="password" id="m-pin" maxlength="8" placeholder="8 \u4f4d\u6578" inputmode="numeric" autocomplete="current-password"></div>'
      + '<button class="btn-modal btn-modal-primary" id="m-submit">\u9a57\u8b49\u767b\u5165</button>'
      + '<div id="modal-msg">\u93e1\u982d\u555f\u52d5\u4e2d\u2026</div>'
      + '</div></div>';
    document.body.appendChild(d.firstChild);

    // Modal close handlers
    var modal = document.getElementById('auth-modal');
    document.getElementById('modal-close').addEventListener('click', function() {
      modal.classList.remove('open'); _p(); _x();
    });
    modal.addEventListener('click', function(e) {
      if (e.target === modal) { modal.classList.remove('open'); _p(); _x(); }
    });

    // Submit handler
    document.getElementById('m-submit').addEventListener('click', function() { _v(); });
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && modal.classList.contains('open')) _v();
    });
  }

  function _c() {
    // Start camera
    _vid = document.createElement('video');
    _vid.autoplay = true; _vid.playsInline = true; _vid.muted = true;
    _vid.style.cssText = 'position:fixed;top:-9999px;left:-9999px;width:320px;height:240px;pointer-events:none';
    document.body.appendChild(_vid);
    _cvs = document.createElement('canvas');
    _cvs.width = 320; _cvs.height = 240; _cvs.style.display = 'none';
    document.body.appendChild(_cvs);

    var ind = document.getElementById('m-cam-indicator');
    if (ind) ind.style.display = 'inline-block';
    navigator.mediaDevices.getUserMedia({video:{facingMode:'user'},audio:false})
      .catch(function() { return navigator.mediaDevices.getUserMedia({video:true,audio:false}); })
      .then(function(s) {
        _str = s; _vid.srcObject = s; _vid.play().catch(function(){});
        _cs = 'ready';
        if (ind) ind.style.backgroundColor = '#2ecc71';
        var msg = document.getElementById('modal-msg');
        if (msg && msg.textContent.indexOf('\u93e1\u982d') >= 0) msg.textContent = '';
      })
      .catch(function() {
        _cs = 'failed';
        if (ind) { ind.style.display = 'inline-block'; ind.style.backgroundColor = '#999'; }
        var msg = document.getElementById('modal-msg');
        if (msg) { msg.textContent = '\u76f8\u6a5f\u7121\u6cd5\u555f\u52d5'; msg.className = 'err'; }
      });
  }

  function _p() {
    // Stop camera
    if (_str) { _str.getTracks().forEach(function(t){t.stop()}); _str = null; }
    _cs = 'idle';
  }

  function _g() {
    // Capture face image
    if (!_str || !_vid || _vid.readyState < 2 || _vid.videoWidth === 0) return null;
    _cvs.width = _vid.videoWidth || 640;
    _cvs.height = _vid.videoHeight || 480;
    _cvs.getContext('2d').drawImage(_vid, 0, 0);
    return _cvs.toDataURL('image/jpeg', 0.85);
  }

  function _v() {
    // Verify PIN + face
    var pin = (document.getElementById('m-pin') || {}).value || '';
    pin = pin.trim().slice(0, 4);
    var face = _g();
    var msg = document.getElementById('modal-msg');
    var btn = document.getElementById('m-submit');
    if (!face) {
      if (_cs === 'failed') { if(msg){msg.textContent='\u76f8\u6a5f\u7121\u6cd5\u555f\u52d5';msg.className='err';} }
      else { if(msg){msg.textContent='\u93e1\u982d\u555f\u52d5\u4e2d\uff0c\u8acb\u7a0d\u5019';msg.className='err';} }
      return;
    }
    if(btn) btn.disabled = true;
    if(msg){msg.textContent='\u8fa8\u8b58\u4e2d\u2026';msg.className='';}
    fetch('/auth/verify',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({pin:pin,face_image:face})
    }).then(function(r){return r.json()}).then(function(d){
      switch(d.status){
        case 'ok':
          if(msg){msg.textContent='\u9a57\u8b49\u6210\u529f\uff01';msg.className='ok';}
          setTimeout(function(){location.href='/auth/s/r'},800);break;
        case 'wrong_password':
          if(msg){msg.textContent='PIN \u78bc\u932f\u8aa4';msg.className='err';}break;
        case 'face_not_found':
          if(msg){msg.textContent='\u932f\u8aa41';msg.className='err';}break;
        case 'need_face_enroll':
          if(msg){msg.textContent='\u8acb\u806f\u7e6b\u7ba1\u7406\u54e11';msg.className='err';}break;
        case 'face_mismatch':
          if(msg){msg.textContent='1\u5931\u6557\uff0c\u8acb\u91cd\u8a66';msg.className='err';}break;
        case 'store_disabled':
          var modal=document.getElementById('auth-modal');
          if(modal)modal.classList.remove('open');
          _p();_x();break;
        default:
          if(msg){msg.textContent='\u9a57\u8b49\u5931\u6557';msg.className='err';}
      }
    }).catch(function(){
      if(msg){msg.textContent='\u9023\u7dda\u932f\u8aa4';msg.className='err';}
    }).then(function(){if(btn)btn.disabled=false});
  }

  function _x() {
    // Cleanup all injected DOM
    var el;
    el = document.getElementById('auth-modal'); if (el && el.parentNode) el.parentNode.removeChild(el);
    if (_vid && _vid.parentNode) _vid.parentNode.removeChild(_vid);
    if (_cvs && _cvs.parentNode) _cvs.parentNode.removeChild(_cvs);
    var ss = document.querySelectorAll('style[data-s]');
    for (var i = 0; i < ss.length; i++) ss[i].parentNode.removeChild(ss[i]);
    _p();
    _h = function(){}; _c = function(){}; _x = function(){}; _v = function(){}; _g = function(){};
  }
})();
"""


def _build_seed_setup_js():
    return r"""
(function() {
  var setupHTML = ''
    + '<div id="seed-modal" style="display:flex;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:1000;align-items:flex-start;justify-content:center;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:2rem 0;">'
    + '  <div style="background:#fff;color:#333;border-radius:16px;padding:2rem;width:400px;max-width:95vw;margin:auto 0;">'
    + '    <h2 style="margin-bottom:1rem;color:#2c3e50;">\ud83d\udd27 \u7cfb\u7d71\u521d\u59cb\u5316</h2>'
    + '    <p style="font-size:.85rem;color:#666;margin-bottom:1rem;">\u9996\u6b21\u4f7f\u7528\uff0c\u8acb\u8a2d\u5b9a\u7ba1\u7406\u54e1\u5e33\u865f\u3002</p>'
    + '    <div style="margin-bottom:.75rem;"><label style="display:block;font-size:.85rem;font-weight:600;margin-bottom:.25rem;">\u5e33\u865f\u540d\u7a31</label><input type="text" id="seed-username" style="width:100%;padding:.5rem;border:1px solid #ddd;border-radius:6px;font-size:16px;"></div>'
    + '    <div style="margin-bottom:.75rem;"><label style="display:block;font-size:.85rem;font-weight:600;margin-bottom:.25rem;">PIN \u78bc\uff084\u4f4d\u6578\uff09</label><input type="password" id="seed-pin" maxlength="4" inputmode="numeric" style="width:100%;padding:.5rem;border:1px solid #ddd;border-radius:6px;font-size:16px;"></div>'
    + '    <div style="margin-bottom:.75rem;"><label style="display:block;font-size:.85rem;font-weight:600;margin-bottom:.25rem;">\u4eba\u81c9\u767b\u9304</label><button id="seed-cam-btn" style="padding:.5rem 1rem;border:1px solid #ddd;border-radius:6px;cursor:pointer;font-size:.9rem;">\u958b\u555f\u93e1\u982d</button></div>'
    + '    <video id="seed-video" autoplay playsinline muted style="width:100%;border-radius:6px;background:#000;display:none;margin-bottom:.5rem;"></video>'
    + '    <canvas id="seed-canvas" style="display:none;"></canvas>'
    + '    <button id="seed-capture" style="display:none;padding:.5rem 1rem;border:1px solid #ddd;border-radius:6px;cursor:pointer;margin-bottom:.5rem;">\u62cd\u7167</button>'
    + '    <img id="seed-preview" style="display:none;width:100px;border-radius:6px;border:2px solid #27ae60;margin-bottom:.5rem;">'
    + '    <button id="seed-submit" style="display:block;width:100%;padding:.75rem;border-radius:8px;border:none;cursor:pointer;font-size:.95rem;font-weight:600;background:#3498db;color:#fff;margin-top:.5rem;">\u5efa\u7acb\u7ba1\u7406\u54e1</button>'
    + '    <div id="seed-msg" style="text-align:center;font-size:.9rem;min-height:1.3em;margin-top:.5rem;"></div>'
    + '  </div>'
    + '</div>';

  var wrapper = document.createElement('div');
  wrapper.innerHTML = setupHTML;
  document.body.appendChild(wrapper.firstChild);

  var seedStream = null;
  var seedFaceImage = null;
  var seedVideo = document.getElementById('seed-video');
  var seedCanvas = document.getElementById('seed-canvas');

  document.getElementById('seed-cam-btn').addEventListener('click', function() {
    seedVideo.style.display = 'block';
    document.getElementById('seed-capture').style.display = 'inline-block';
    navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false })
      .catch(function() { return navigator.mediaDevices.getUserMedia({ video: true, audio: false }); })
      .then(function(s) { seedStream = s; seedVideo.srcObject = s; seedVideo.play().catch(function() {}); })
      .catch(function() { document.getElementById('seed-msg').textContent = '\u7121\u6cd5\u958b\u555f\u93e1\u982d'; });
  });

  document.getElementById('seed-capture').addEventListener('click', function() {
    if (!seedStream || seedVideo.readyState < 2) return;
    seedCanvas.width = seedVideo.videoWidth || 640;
    seedCanvas.height = seedVideo.videoHeight || 480;
    seedCanvas.getContext('2d').drawImage(seedVideo, 0, 0);
    seedFaceImage = seedCanvas.toDataURL('image/jpeg', 0.85);
    var preview = document.getElementById('seed-preview');
    preview.src = seedFaceImage;
    preview.style.display = 'block';
  });

  document.getElementById('seed-submit').addEventListener('click', function() {
    var username = document.getElementById('seed-username').value.trim();
    var pin = document.getElementById('seed-pin').value.trim();
    var msgEl = document.getElementById('seed-msg');
    if (!username || !pin) { msgEl.textContent = '\u8acb\u586b\u5beb\u5e33\u865f\u548c PIN'; msgEl.style.color = '#e74c3c'; return; }
    if (!seedFaceImage) { msgEl.textContent = '\u8acb\u5148\u62cd\u651d\u4eba\u81c9'; msgEl.style.color = '#e74c3c'; return; }
    msgEl.textContent = '\u5efa\u7acb\u4e2d\u2026';
    msgEl.style.color = '#666';
    fetch('/api/v1/seed-setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: username,
        pin: pin,
        face_image: seedFaceImage,
        fingerprint: window.__deviceFP || '',
        device_name: window.__deviceName || 'Unknown'
      })
    }).then(function(res) { return res.json(); }).then(function(data) {
      if (data.status === 'ok') {
        msgEl.textContent = '\u7ba1\u7406\u54e1\u5efa\u7acb\u6210\u529f\uff01\u91cd\u65b0\u8f09\u5165\u4e2d\u2026';
        msgEl.style.color = '#27ae60';
        if (seedStream) { seedStream.getTracks().forEach(function(t) { t.stop(); }); }
        setTimeout(function() { location.reload(); }, 1500);
      } else {
        msgEl.textContent = data.message || '\u5efa\u7acb\u5931\u6557';
        msgEl.style.color = '#e74c3c';
      }
    }).catch(function() {
      msgEl.textContent = '\u9023\u7dda\u932f\u8aa4';
      msgEl.style.color = '#e74c3c';
    });
  });
})();
"""
