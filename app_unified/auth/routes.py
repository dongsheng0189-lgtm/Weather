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
from concurrent.futures import ThreadPoolExecutor
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

_face_executor = ThreadPoolExecutor(max_workers=3)


def _do_face_compare(known_encoding, img_data_bytes):
    """Run in thread — CPU-intensive face_recognition calls."""
    img = face_recognition.load_image_file(io.BytesIO(img_data_bytes))
    locations = face_recognition.face_locations(img, number_of_times_to_upsample=2)
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
    return len(face_recognition.face_locations(img, number_of_times_to_upsample=2)) > 0

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


@auth_bp.route("/verify", methods=["POST"])
@limiter.limit("20 per minute", exempt_when=lambda: current_app.config.get("TESTING"))
def verify():
    """
    Face + PIN verification (hidden modal flow).
    On success: logs in user server-side, returns {status: ok}.
    Frontend navigates to /s/r which redirects to notes — no token in any response.
    """
    data = request.get_json(silent=True) or {}
    pin = str(data.get("pin") or "").strip()
    face_image = data.get("face_image")
    logger.warning("verify: called, face_image=%s, pin_len=%d",
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

    # Step 4: 有人臉 → 需要比對
    if not face_image:
        return jsonify({"status": "face_mismatch"})

    matched = None
    for u in face_enrolled:
        match, _ = _verify_face(u, face_image)
        if match:
            matched = u
            break

    if matched:
        if not matched.is_super_admin() and matched.store:
            s = Store.query.filter_by(name=matched.store).first()
            if s and not s.login_enabled:
                return jsonify({"status": "store_disabled"})
        login_user(matched)
        session.permanent = True
        # 重置 idle 計時器：避免 session 裡殘留的舊 _last_request_at
        # 讓 _enforce_session_idle 在下個 request 直接判定 idle 超時而登出
        import time as _time
        session["_last_request_at"] = int(_time.time())
        logger.warning("verify: PIN matched, face matched user=%s", matched.username)
        return jsonify({"status": "ok"})

    # Step 5: 人臉不符 → 區分「圖中無人臉」vs「有人臉但不符」
    try:
        img_data = base64.b64decode(face_image.split(",")[-1])
        future = _face_executor.submit(_do_detect_any_face, img_data)
        any_face_found = future.result(timeout=15)
    except Exception:
        any_face_found = False

    if not any_face_found:
        logger.warning("verify: no face detected in submitted image")
        return jsonify({"status": "face_not_found"})

    logger.warning("verify: face found but no match among pin_users=%s", [u.username for u in face_enrolled])
    return jsonify({"status": "face_mismatch"})


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


def _verify_face(user: User, image_b64: str):
    """Returns (match: bool, confidence: float). Face ops run in thread pool."""
    try:
        img_data = base64.b64decode(image_b64.split(",")[-1])
        known = user.get_face_encoding()
        if known is None:
            return False, 0.0
        logger.warning("_verify_face: submitting to thread pool for user=%s", user.username)
        future = _face_executor.submit(_do_face_compare, known, img_data)
        match, confidence = future.result(timeout=15)
        logger.warning("_verify_face: user=%s match=%s confidence=%.3f", user.username, match, confidence)
        return match, confidence
    except Exception as e:
        logger.warning("_verify_face exception: user=%s error=%s", user.username, e)
        return False, 0.0
