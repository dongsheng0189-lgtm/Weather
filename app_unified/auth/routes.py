"""
Unified auth blueprint.

Two login flows:
  1. /auth/login  — username + PIN + optional face (App1 style, for web)
  2. /auth/verify — face + PIN only, no username (App2 hidden modal style)
     After verify: server writes session directly → returns {status: ok}
     Frontend navigates to /s/r (opaque endpoint) → redirects to notes
     This hides the token and target URL from DevTools.
"""
import io
import base64
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
from flask import Blueprint, request, jsonify, session, redirect, url_for, render_template, flash, current_app, abort
from flask_login import login_user, logout_user, login_required, current_user
from gevent.threadpool import ThreadPool
from extensions import db, limiter
from models import User, Store

try:
    import face_recognition_models
    import face_recognition
    FACE_RECOGNITION_AVAILABLE = True
except BaseException as _e:
    import logging as _log
    _log.getLogger(__name__).warning("face_recognition import failed: %s", _e)
    FACE_RECOGNITION_AVAILABLE = False

# gevent.threadpool.ThreadPool runs work on real OS threads even when gevent
# monkey-patches stdlib threading. dlib in face_recognition does not yield to
# the gevent hub; if dispatched onto greenlets (via stdlib ThreadPoolExecutor)
# 3 concurrent verifies block the hub for the full duration of the dlib calls
# → all other Flask requests stall (incident 2026-05-09 20:00, 10+ min outage).
_face_executor = ThreadPool(maxsize=3)


def _run_in_threadpool(fn, *args, timeout=15):
    """Wrap _face_executor.spawn — 在 prod gevent 環境下走 threadpool（avoid
    blocking hub），dev Werkzeug 環境 gevent hub 不存在會 raise
    InvalidThreadUseError、自動 fallback 直接 inline 呼叫。
    """
    from gevent.exceptions import InvalidThreadUseError
    try:
        async_result = _face_executor.spawn(fn, *args)
        return async_result.get(timeout=timeout)
    except InvalidThreadUseError:
        # Dev fallback：沒 gevent hub 直接跑
        return fn(*args)


def _do_face_compare(known_encoding, img_data_bytes):
    """Run in thread — CPU-intensive face_recognition calls."""
    img = face_recognition.load_image_file(io.BytesIO(img_data_bytes))
    locations = face_recognition.face_locations(img, number_of_times_to_upsample=1)
    encodings = face_recognition.face_encodings(img, locations)
    if not encodings:
        return False, 0.0
    distances = face_recognition.face_distance([known_encoding], encodings[0])
    match = bool(face_recognition.compare_faces([known_encoding], encodings[0], tolerance=0.45)[0])
    confidence = float(1 - distances[0])
    return match, confidence


def _do_detect_any_face(img_data_bytes):
    """Run in thread — check if any face exists in image."""
    img = face_recognition.load_image_file(io.BytesIO(img_data_bytes))
    return len(face_recognition.face_locations(img, number_of_times_to_upsample=1)) > 0


def _do_face_pose(img_data_bytes):
    """Run in thread — landmarks → classify pose；順便回 face_encoding。

    Returns: (pose_label, encoding_or_None)
        pose_label: 'L' / 'C' / 'R' / 'unclear'
        encoding: 128-d ndarray or None（沒偵測到臉時 None）
    """
    from auth.liveness import classify_pose
    img = face_recognition.load_image_file(io.BytesIO(img_data_bytes))
    locations = face_recognition.face_locations(img, number_of_times_to_upsample=1)
    if not locations:
        return "unclear", None
    landmarks_list = face_recognition.face_landmarks(img, locations)
    pose = classify_pose(landmarks_list[0]) if landmarks_list else "unclear"
    encodings = face_recognition.face_encodings(img, locations)
    return pose, (encodings[0] if encodings else None)


def _decode_face_image(b64_str: str) -> bytes:
    """Data URL (`data:image/jpeg;base64,...`) 或純 b64 → bytes。"""
    return base64.b64decode(b64_str.split(",")[-1])


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/register", methods=["GET", "POST"])
@login_required
def register():
    if not current_user.is_admin():
        abort(403)
    if request.method == "GET":
        return render_template("auth/register.html")

    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    pin = str(data.get("pin") or "").strip()

    if not username or not pin:
        return jsonify({"status": "error", "message": "請填寫帳號和 PIN"}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({"status": "error", "message": "帳號已存在"}), 409

    user = User(username=username)
    user.set_password(pin)
    db.session.add(user)
    db.session.commit()
    return jsonify({"status": "ok", "user_id": user.id})


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    return redirect(url_for("weather.index"))


@auth_bp.route("/face-pose", methods=["POST"])
@limiter.limit("60 per minute", exempt_when=lambda: current_app.config.get("TESTING"))
def face_pose():
    """Frontend 連拍輪詢用：吃單張 frame、回偵測到的 head pose。

    不寫 session、不做帳號驗證；純 vision classification 輔助 UI。
    frontend 用回傳的 pose 控制 modal 內 3 個位置點是否亮起、user 看哪個
    沒亮就知道要轉哪邊。集滿 3 種 pose 後一次性 POST /auth/verify。
    """
    data = request.get_json(silent=True) or {}
    face_image = data.get("face_image")
    if not face_image:
        return jsonify({"status": "error", "message": "missing face_image"}), 400
    try:
        img_bytes = _decode_face_image(face_image)
    except Exception:
        return jsonify({"status": "error", "message": "invalid base64"}), 400
    try:
        pose, _enc = _run_in_threadpool(_do_face_pose, img_bytes, timeout=10)
    except Exception as e:
        logger.warning("face_pose: exception type=%s msg=%r", type(e).__name__, e, exc_info=True)
        return jsonify({"status": "error", "message": "processing failed"}), 500
    return jsonify({"status": "ok", "pose": pose})


@auth_bp.route("/verify", methods=["POST"])
@limiter.limit("20 per minute", exempt_when=lambda: current_app.config.get("TESTING"))
def verify():
    """
    Face + PIN verification (hidden modal flow).

    新版接 `frames: [b64, b64, b64]`（多幀 liveness + best-match across all
    PIN-matching candidates）；舊版 single `face_image` 保留 backward compat
    供既有 test fixtures / 內部工具使用。

    On success: logs in user server-side, returns {status: ok}.
    Frontend navigates to /s/r which redirects to notes — no token in any response.
    """
    data = request.get_json(silent=True) or {}
    pin = str(data.get("pin") or "").strip()
    frames = data.get("frames")
    face_image = data.get("face_image")
    logger.warning("verify: called, frames=%s, face_image=%s, pin_len=%d",
                   len(frames) if isinstance(frames, list) else "NO",
                   "yes" if face_image else "NO",
                   len(pin))

    # Step 1: PIN 篩選候選帳號
    all_users = User.query.filter_by(is_active=True).all()
    pin_users = [u for u in all_users if u.check_password(pin)]
    logger.warning("verify: pin_users=%s", [u.username for u in pin_users])
    if not pin_users:
        return jsonify({"status": "wrong_password"})

    # Step 2: 分為「已登錄人臉」和「未登錄人臉」
    face_enrolled    = [u for u in pin_users if u.face_encoding is not None]
    no_face_enrolled = [u for u in pin_users if u.face_encoding is None]

    # Step 3: 此 PIN 下沒有任何人登錄人臉 → 拒絕（管理員協助登錄）
    if not face_enrolled:
        logger.warning("verify: PIN matched but no face enrolled, users=%s",
                       [u.username for u in no_face_enrolled])
        return jsonify({"status": "need_face_enroll"})

    # ── Path A：多幀 liveness + best-match（新版、強化）──
    if isinstance(frames, list) and len(frames) >= 3:
        matched = _verify_multi_frame(face_enrolled, frames[:3])
        if matched is None:
            return jsonify({"status": "face_mismatch"})
        if matched == "_liveness_failed":
            return jsonify({"status": "face_mismatch", "reason": "liveness"})
        return _login_success(matched)

    # ── Path B：單張舊路徑（backward compat、不擋照片）──
    if not face_image:
        return jsonify({"status": "face_mismatch"})

    matched = None
    for u in face_enrolled:
        match, _ = _verify_face(u, face_image)
        if match:
            matched = u
            break

    if matched:
        return _login_success(matched)

    # Step 5: 人臉不符 → 區分「圖中無人臉」vs「有人臉但不符」
    try:
        img_data = base64.b64decode(face_image.split(",")[-1])
        any_face_found = _run_in_threadpool(_do_detect_any_face, img_data, timeout=15)
    except Exception:
        any_face_found = False

    if not any_face_found:
        logger.warning("verify: no face detected in submitted image")
        return jsonify({"status": "face_not_found"})

    logger.warning("verify: face found but no match among pin_users=%s", [u.username for u in face_enrolled])
    return jsonify({"status": "face_mismatch"})


def _login_success(user):
    """共用：寫 session + 回 ok。"""
    if not user.is_super_admin() and user.store:
        s = Store.query.filter_by(name=user.store).first()
        if s and not s.login_enabled:
            return jsonify({"status": "store_disabled"})
    login_user(user)
    session.permanent = True
    import time as _time
    session["_last_request_at"] = int(_time.time())
    logger.warning("verify: PIN matched, face matched user=%s", user.username)
    return jsonify({"status": "ok"})


def _verify_multi_frame(face_enrolled, frames_b64):
    """多幀路徑：每幀算 encoding + texture variance → liveness check → best-match。

    Returns:
        User instance（驗證成功）
        None（face_mismatch — 沒臉 / 不是 enrolled user / ambiguous）
        '_liveness_failed' string sentinel（liveness 直接失敗）
    """
    from auth.liveness import (
        check_multi_frame_liveness, best_match_user, texture_variance,
    )
    encodings = []
    texture_vars = []
    for b64 in frames_b64:
        try:
            img_bytes = _decode_face_image(b64)
        except Exception:
            return None
        # 取 encoding（順便驗有臉）
        try:
            _pose, enc = _run_in_threadpool(_do_face_pose, img_bytes, timeout=15)
        except Exception:
            return None
        if enc is None:
            return None
        encodings.append(enc)
        texture_vars.append(texture_variance(img_bytes))

    ok, reason = check_multi_frame_liveness(encodings, texture_vars)
    if not ok:
        logger.warning("verify: liveness failed: %s", reason)
        return "_liveness_failed"

    matched, info = best_match_user(face_enrolled, encodings)
    if matched is None:
        logger.warning("verify: best-match no winner, info=%s", info)
        return None
    logger.warning("verify: best-match selected user=%s, dist=%.3f, info=%s",
                   matched.username, info.get("best_dist", -1), info)
    return matched


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("weather.index"))


@auth_bp.route("/ping", methods=["GET"])
def ping():
    # 編輯頁面 keepalive 用。永遠回 JSON，不走 Flask-Login 的 302 redirect。
    # _enforce_session_idle 已在 before_request 跑過；若 session 還活著會自動刷新 _last_request_at。
    if not current_user.is_authenticated:
        return jsonify({"status": "unauthorized"}), 401
    return jsonify({"status": "ok"})


# Opaque redirect endpoint — hides destination from JS/DevTools
@auth_bp.route("/s/r")
@login_required
def silent_redirect():
    from features import user_visible_features
    from flask_login import current_user
    feats = user_visible_features(current_user)
    if len(feats) == 0:
        # 沒有任何模組權限 → 依舊導向天氣頁（退場）
        return redirect(url_for("weather.index"))
    if len(feats) == 1:
        return redirect(feats[0]["url"])
    # 多個模組 → 進選單頁
    return redirect(url_for("auth.module_picker"))


@auth_bp.route("/modules")
@login_required
def module_picker():
    from features import user_visible_features
    from flask_login import current_user
    feats = user_visible_features(current_user)
    if len(feats) == 0:
        return redirect(url_for("weather.index"))
    if len(feats) == 1:
        return redirect(feats[0]["url"])
    return render_template("auth/module_picker.html", features=feats)


@auth_bp.route("/font-size")
@login_required
def font_size():
    return render_template("auth/font_size.html")


def _verify_face(user: User, image_b64: str):
    """Returns (match: bool, confidence: float). Face ops run in thread pool."""
    try:
        img_data = base64.b64decode(image_b64.split(",")[-1])
        known = user.get_face_encoding()
        if known is None:
            return False, 0.0
        logger.warning("_verify_face: submitting to thread pool for user=%s", user.username)
        match, confidence = _run_in_threadpool(_do_face_compare, known, img_data, timeout=15)
        logger.warning("_verify_face: user=%s match=%s confidence=%.3f", user.username, match, confidence)
        return match, confidence
    except Exception as e:
        logger.warning("_verify_face exception: user=%s error=%s", user.username, e)
        return False, 0.0
