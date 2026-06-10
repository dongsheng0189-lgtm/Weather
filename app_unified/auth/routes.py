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
from models import User, Store, UserLog

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


def _do_face_encode(img_data_bytes):
    """Run in thread — decode image + extract single face_encoding.

    回 128-d ndarray 或 None（沒偵測到臉）。給 best-match 用、一張圖只算一次
    encoding、再對候選 users 用純 numpy 算 face_distance。比舊版 first-match
    每個候選都重跑整條 face_recognition pipeline 省 N-1 倍 CPU。
    """
    img = face_recognition.load_image_file(io.BytesIO(img_data_bytes))
    locations = face_recognition.face_locations(img, number_of_times_to_upsample=1)
    encodings = face_recognition.face_encodings(img, locations)
    return encodings[0] if encodings else None


def _best_match_among(users, submitted_encoding,
                      threshold: float = 0.45,
                      ambiguous_margin: float = 0.05):
    """從候選 users 選 face_distance 最低且 < threshold 的；前兩名距離差距
    < ambiguous_margin 視為 close call 整批拒（避免同 PIN 兩個臉相近員工
    互登）。取代既有 first-match-wins。

    Returns:
        (matched_user_or_None, info_dict)
        info_dict: best_dist / second_dist / ambiguous flag
    """
    import numpy as _np
    submitted = _np.asarray(submitted_encoding, dtype=_np.float64)
    scored = []
    for u in users:
        enc = u.face_encoding
        if enc is None:
            continue
        # User.face_encoding 是 raw bytes（128 float64）；轉回 ndarray
        if isinstance(enc, (bytes, bytearray)):
            known = _np.frombuffer(enc, dtype=_np.float64)
        else:
            known = _np.asarray(enc, dtype=_np.float64)
        dist = float(_np.linalg.norm(known - submitted))
        scored.append((dist, u))
    if not scored:
        return None, {"reason": "no enrolled users"}
    scored.sort(key=lambda x: x[0])
    best_dist, best_user = scored[0]
    info = {"best_dist": best_dist}
    if best_dist > threshold:
        return None, info
    if len(scored) >= 2:
        second_dist = scored[1][0]
        info["second_dist"] = second_dist
        if second_dist - best_dist < ambiguous_margin:
            info["ambiguous"] = True
            return None, info
    return best_user, info


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

    # Step 4: best-match 跨所有候選人 — 取代既有 first-match-wins。
    # 圖像解碼 + face_encoding 只跑一次（最 CPU heavy），再對候選 users
    # 用純 numpy 算 face_distance，省 N-1 倍 dlib 計算。
    try:
        img_data = base64.b64decode(face_image.split(",")[-1])
        async_result = _face_executor.spawn(_do_face_encode, img_data)
        submitted_encoding = async_result.get(timeout=15)
    except Exception as e:
        logger.warning("verify: encode exception: %s", e)
        submitted_encoding = None

    if submitted_encoding is None:
        logger.warning("verify: no face detected in submitted image")
        return jsonify({"status": "face_not_found"})

    matched, info = _best_match_among(face_enrolled, submitted_encoding)
    logger.warning("verify: best-match info=%s", info)

    if matched is None:
        logger.warning("verify: no match among face_enrolled=%s",
                       [u.username for u in face_enrolled])
        return jsonify({"status": "face_mismatch"})

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
    logger.warning("verify: PIN matched, best-match user=%s dist=%.3f",
                   matched.username, info.get("best_dist", -1))
    return jsonify({"status": "ok"})


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


def _is_weak_pin(pin: str) -> bool:
    """太弱的 4 位 PIN：四碼全同 / 連號遞增 / 連號遞減。"""
    if len(set(pin)) == 1:                 # 0000, 1111, ...
        return True
    digits = [int(c) for c in pin]
    if all(digits[i + 1] - digits[i] == 1 for i in range(len(digits) - 1)):   # 1234
        return True
    if all(digits[i + 1] - digits[i] == -1 for i in range(len(digits) - 1)):  # 9876
        return True
    return False


@auth_bp.route("/settings")
@login_required
def settings():
    return render_template("auth/settings.html")


@auth_bp.route("/change-pin", methods=["POST"])
@limiter.limit("10 per minute", exempt_when=lambda: current_app.config.get("TESTING"))
@login_required
def change_pin():
    data = request.get_json(silent=True) or {}
    current = str(data.get("current_pin") or "").strip()
    new = str(data.get("new_pin") or "").strip()
    confirm = str(data.get("confirm_pin") or "").strip()

    if not current_user.check_password(current):
        return jsonify({"status": "error", "message": "目前 PIN 不正確"}), 400
    if len(new) != 4 or not new.isdigit():
        return jsonify({"status": "error", "message": "新 PIN 需為 4 位數字"}), 400
    if new != confirm:
        return jsonify({"status": "error", "message": "兩次新 PIN 不一致"}), 400
    if new == current:
        return jsonify({"status": "error", "message": "新 PIN 不能與目前相同"}), 400
    if _is_weak_pin(new):
        return jsonify({"status": "error", "message": "PIN 過於簡單，請換一組"}), 400

    current_user.set_password(new)
    db.session.add(UserLog(
        operator_id=current_user.id,
        operator_name=current_user.username,
        target_id=current_user.id,
        target_name=current_user.username,
        action="change_pin",
        detail=None,
    ))
    db.session.commit()
    return jsonify({"status": "ok"})


@auth_bp.route("/handover/start", methods=["POST"])
@login_required
def handover_start():
    """admin 以上開啟交接模式：session idle 延長為 15 分鐘（純滑動）。"""
    if not current_user.is_admin():
        return jsonify({"status": "error", "message": "需要管理員權限"}), 403
    import time
    session["_handover"] = True
    session["_last_request_at"] = int(time.time())
    session.permanent = True
    return jsonify({"status": "ok"})


@auth_bp.route("/handover/end", methods=["POST"])
@login_required
def handover_end():
    """結束交接模式（保持登入）：idle 回到預設規則。"""
    import time
    session.pop("_handover", None)
    session["_last_request_at"] = int(time.time())
    return jsonify({"status": "ok"})


def _verify_face(user: User, image_b64: str):
    """Returns (match: bool, confidence: float). Face ops run in thread pool."""
    try:
        img_data = base64.b64decode(image_b64.split(",")[-1])
        known = user.get_face_encoding()
        if known is None:
            return False, 0.0
        logger.warning("_verify_face: submitting to thread pool for user=%s", user.username)
        async_result = _face_executor.spawn(_do_face_compare, known, img_data)
        match, confidence = async_result.get(timeout=15)
        logger.warning("_verify_face: user=%s match=%s confidence=%.3f", user.username, match, confidence)
        return match, confidence
    except Exception as e:
        logger.warning("_verify_face exception: user=%s error=%s", user.username, e)
        return False, 0.0
