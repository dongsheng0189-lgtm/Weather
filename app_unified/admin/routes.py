import io
import base64
from datetime import datetime, timedelta, timezone
TW_TZ = timezone(timedelta(hours=8))
from flask import Blueprint, render_template, jsonify, request, abort, current_app
from flask_login import login_required, current_user
from extensions import db
from models import User, Note, Store, NoteLog, UserLog, STATUS_CHOICES


def _log_user_action(action, target_user, detail=None):
    """記錄使用者管理動作"""
    log = UserLog(
        operator_id=current_user.id,
        operator_name=current_user.username,
        target_id=target_user.id if target_user else None,
        target_name=target_user.username if target_user else "",
        action=action,
        detail=detail,
    )
    db.session.add(log)

try:
    import face_recognition_models
    import face_recognition
    FACE_RECOGNITION_AVAILABLE = True
except BaseException as _e:
    import logging as _log
    _log.getLogger(__name__).warning("face_recognition import failed: %s", _e)
    FACE_RECOGNITION_AVAILABLE = False

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _call_gemini(prompt: str, max_tokens: int) -> str:
    """Gemini 2.5 Flash（免費方案），含 429 retry"""
    api_key = current_app.config.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")
    import time
    import logging
    import requests as _req
    model = current_app.config.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    for attempt in range(5):
        resp = _req.post(url, json=payload, timeout=120)
        if resp.status_code == 429:
            wait = (attempt + 1) * 15
            logging.getLogger("call_llm").warning("=== Gemini 429, retry %d/5 in %ds ===", attempt + 1, wait)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    raise Exception("Gemini API 持續 429，請稍後再試")


def _is_output_looping(text: str) -> bool:
    """偵測 LLM repetition loop：同一行連續出現 ≥ 8 次 或 同句佔輸出 > 40%"""
    if not text:
        return False
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 10:
        return False
    # 連續重複
    run = 1
    for i in range(1, len(lines)):
        if lines[i] == lines[i - 1]:
            run += 1
            if run >= 8:
                return True
        else:
            run = 1
    # 單一行佔比過高（去掉行首數字/符號後比對）
    import re
    norm = [re.sub(r"^[\d\.\-\*\s]+", "", ln) for ln in lines]
    from collections import Counter
    c = Counter(norm)
    most, cnt = c.most_common(1)[0]
    if most and cnt >= max(8, int(len(lines) * 0.4)):
        return True
    return False


def call_llm(prompt: str, max_tokens: int = 8192) -> str:
    """使用 Gemini 2.5 Flash。偵測到 repetition loop 會自動重試一次。"""
    import time
    import logging
    logger = logging.getLogger("call_llm")
    logger.warning("=== LLM: calling Gemini ===")
    t0 = time.time()
    result = _call_gemini(prompt, max_tokens)
    elapsed = time.time() - t0
    logger.warning("=== LLM: Gemini OK, %.1f sec, %d chars ===", elapsed, len(result))

    if _is_output_looping(result):
        logger.warning("=== LLM: output repetition loop detected, retrying once ===")
        # 在 prompt 加反重複提示再試一次
        retry_prompt = prompt + (
            "\n\n# 再次提醒\n"
            "請絕對避免在輸出中連續重複同一句話。每個事項只能列出一次。"
            "若多筆輸入資料描述相同事件，請合併為單一條目。"
        )
        t0 = time.time()
        result2 = _call_gemini(retry_prompt, max_tokens)
        elapsed = time.time() - t0
        logger.warning("=== LLM: retry OK, %.1f sec, %d chars ===", elapsed, len(result2))
        if not _is_output_looping(result2):
            return result2
        # 仍 loop：回傳 retry 結果（至少不是第一次的亂碼）並加警示
        return "⚠️ AI 產生結果異常（偵測到重複迴圈），以下為重試結果，建議再按一次：\n\n" + result2
    return result


def require_admin():
    if not current_user.is_authenticated or not current_user.is_admin():
        abort(403)


@admin_bp.route("/dashboard")
@login_required
def dashboard():
    require_admin()
    user_page = request.args.get("user_page", 1, type=int)
    user_store_filter = request.args.get("user_store", "").strip()
    per_page = 20
    if current_user.is_super_admin():
        user_query = User.query
    else:
        user_query = User.query.filter_by(store=current_user.store)
    # 店別篩選（只對 super_admin 有效，admin 已限定本店）
    if current_user.is_super_admin() and user_store_filter:
        user_query = user_query.filter_by(store=user_store_filter)
    user_pagination = user_query.order_by(User.created_at.desc()).paginate(
        page=user_page, per_page=per_page, error_out=False
    )
    stores = [s.name for s in Store.query.order_by(Store.name).all()]
    return render_template("admin/dashboard.html", users=user_pagination.items,
                           user_pagination=user_pagination,
                           user_store_filter=user_store_filter,
                           stores=stores, status_choices=STATUS_CHOICES)


@admin_bp.route("/users/create", methods=["POST"])
@login_required
def create_user():
    require_admin()
    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    pin = str(data.get("pin") or "").strip()
    face_image = data.get("face_image")
    store = (data.get("store") or "").strip()
    role = data.get("role", "user")

    if not username or not pin:
        return jsonify({"status": "error", "message": "請填寫帳號和 PIN"}), 400

    if role not in ("super_admin", "admin", "user"):
        role = "user"

    # admin/user 必須選擇店別
    valid_stores = [s.name for s in Store.query.all()]
    if role in ("admin", "user") and store not in valid_stores:
        return jsonify({"status": "error", "message": "admin 和 user 必須選擇店別"}), 400

    if role == "super_admin" and not current_user.is_super_admin():
        return jsonify({"status": "error", "message": "僅 super_admin 可建立此角色"}), 403

    if User.query.filter_by(username=username).first():
        return jsonify({"status": "error", "message": "帳號已存在"}), 409

    user = User(
        username=username,
        role=role,
        store=store if store in valid_stores else None,
    )
    user.set_password(pin)

    if face_image and FACE_RECOGNITION_AVAILABLE:
        try:
            img_data = base64.b64decode(face_image.split(",")[-1])
            img = face_recognition.load_image_file(io.BytesIO(img_data))
            encodings = face_recognition.face_encodings(img)
            if encodings:
                user.set_face_encoding(encodings[0])
        except Exception:
            pass

    db.session.add(user)
    db.session.flush()

    if face_image:
        try:
            from storage import upload_face_photo
            img_bytes = base64.b64decode(face_image.split(",")[-1])
            key = upload_face_photo(img_bytes, user.id)
            if key:
                user.face_photo_url = key
        except Exception:
            pass

    _log_user_action("create", user, detail=f"role={role}, store={user.store or '—'}")
    db.session.commit()
    return jsonify({"status": "ok", "user_id": user.id, "username": user.username})


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required
def toggle_user(user_id):
    require_admin()
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        return jsonify({"status": "error", "message": "不可停用自己"}), 400
    user.is_active = not user.is_active
    _log_user_action("activate" if user.is_active else "deactivate", user)
    db.session.commit()
    return jsonify({"status": "ok", "is_active": user.is_active})


@admin_bp.route("/users/<int:user_id>", methods=["DELETE"])
@login_required
def delete_user(user_id):
    require_admin()
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        return jsonify({"status": "error", "message": "不可刪除自己"}), 400
    # 筆記保留：確保 author_name 快照已存（顯示為「帳號 (帳號已刪除)」）
    Note.query.filter_by(user_id=user.id, author_name=None).update({"author_name": user.username})
    Note.query.filter_by(user_id=user.id).update({"user_id": None})
    Note.query.filter_by(updated_by=user.id).update({"updated_by": None})
    _log_user_action("delete", user, detail=f"role={user.role}, store={user.store or '—'}")
    db.session.delete(user)
    db.session.commit()
    return jsonify({"status": "ok"})


@admin_bp.route("/users/<int:user_id>/set-role", methods=["POST"])
@login_required
def set_role(user_id):
    require_admin()
    user = User.query.get_or_404(user_id)
    data = request.get_json(silent=True) or {}
    role = data.get("role", "user")
    if role not in ("super_admin", "admin", "user"):
        return jsonify({"status": "error", "message": "角色無效"}), 400
    if role == "super_admin" and not current_user.is_super_admin():
        return jsonify({"status": "error", "message": "僅 super_admin 可指派此角色"}), 403
    old_role = user.role
    user.role = role
    if old_role != role:
        _log_user_action("set_role", user, detail=f"{old_role}→{role}")
    db.session.commit()
    return jsonify({"status": "ok"})


@admin_bp.route("/users/<int:user_id>/set-store", methods=["POST"])
@login_required
def set_store(user_id):
    require_admin()
    user = User.query.get_or_404(user_id)
    data = request.get_json(silent=True) or {}
    store = data.get("store", "")
    valid_stores = [s.name for s in Store.query.all()]
    old_store = user.store
    user.store = store if store in valid_stores else None
    if old_store != user.store:
        _log_user_action("set_store", user, detail=f"{old_store or '—'}→{user.store or '—'}")
    db.session.commit()
    return jsonify({"status": "ok", "store": user.store})


# ── 使用者操作 Log ────────────────────────────────────────

@admin_bp.route("/user-logs", methods=["GET"])
@login_required
def list_user_logs():
    require_admin()
    logs = UserLog.query.order_by(UserLog.created_at.desc()).limit(200).all()
    action_labels = {
        "create": "新增",
        "delete": "刪除",
        "activate": "啟用",
        "deactivate": "停用",
        "set_role": "改角色",
        "set_store": "改店別",
        "approve_device": "核准裝置",
    }
    return jsonify([{
        "id": log.id,
        "operator": log.operator_name,
        "target": log.target_name,
        "action": action_labels.get(log.action, log.action),
        "detail": log.detail or "",
        "created_at": (log.created_at.replace(tzinfo=timezone.utc).astimezone(TW_TZ).strftime("%Y/%m/%d %H:%M")) if log.created_at else "",
    } for log in logs])


# ── 設備管理 ──────────────────────────────────────────────

@admin_bp.route("/devices", methods=["GET"])
@login_required
def list_devices():
    require_admin()
    from models import TrustedDevice
    from device.routes import _cleanup_pending_devices
    try:
        _cleanup_pending_devices()
    except BaseException:
        db.session.rollback()
    store_filter = request.args.get("store", "").strip()
    query = TrustedDevice.query

    # admin（非 super_admin）：強制限制在本店帳號 + 未綁定裝置範圍
    if not current_user.is_super_admin():
        if store_filter == "__none__":
            query = query.filter(TrustedDevice.user_id.is_(None))
        elif store_filter:
            # 只允許篩選本店
            if store_filter != current_user.store:
                return jsonify([])
            query = query.join(User, TrustedDevice.user_id == User.id).filter(User.store == current_user.store)
        else:
            # 預設「全部」= 本店 + 未綁定
            query = query.outerjoin(User, TrustedDevice.user_id == User.id).filter(
                db.or_(TrustedDevice.user_id.is_(None), User.store == current_user.store)
            )
    else:
        if store_filter == "__none__":
            query = query.filter(TrustedDevice.user_id.is_(None))
        elif store_filter:
            query = query.join(User, TrustedDevice.user_id == User.id).filter(User.store == store_filter)

    devices = query.order_by(TrustedDevice.created_at.desc()).all()
    return jsonify([{
        "id": d.id,
        "fingerprint": d.fingerprint[:12] + "...",
        "device_name": d.device_name,
        "is_approved": d.is_approved,
        "is_revoked": d.is_revoked,
        "user_id": d.user_id,
        "username": d.user.username if d.user else None,
        "store": d.user.store if d.user else None,
        "role": d.user.role if d.user else None,
        "created_at": (d.created_at.replace(tzinfo=timezone.utc).astimezone(TW_TZ).strftime("%Y/%m/%d %H:%M")) if d.created_at else "",
        "last_seen_at": (d.last_seen_at.replace(tzinfo=timezone.utc).astimezone(TW_TZ).strftime("%Y/%m/%d %H:%M")) if d.last_seen_at else "",
    } for d in devices])


@admin_bp.route("/devices/<int:device_id>/approve", methods=["POST"])
@login_required
def approve_device(device_id):
    require_admin()
    from models import TrustedDevice
    import io, base64
    device = TrustedDevice.query.get_or_404(device_id)
    data = request.get_json(silent=True) or {}

    username = (data.get("username") or "").strip()
    pin = str(data.get("pin") or "").strip()
    store = (data.get("store") or "").strip()
    role = data.get("role", "user")
    face_image = data.get("face_image")

    if not username or not pin:
        return jsonify({"status": "error", "message": "請填寫帳號和 PIN"}), 400

    if not face_image:
        return jsonify({"status": "error", "message": "請先拍攝人臉"}), 400

    if role in ("admin", "user"):
        valid_stores = [s.name for s in Store.query.all()]
        if store not in valid_stores:
            return jsonify({"status": "error", "message": "admin 和 user 必須選擇店別"}), 400

    if role == "super_admin" and not current_user.is_super_admin():
        return jsonify({"status": "error", "message": "僅 super_admin 可指派此角色"}), 403

    valid_stores = [s.name for s in Store.query.all()]
    existing_user = User.query.filter_by(username=username).first()

    if existing_user:
        # 既有帳號 → 驗證 PIN 後綁定裝置（處理 PWA 重裝 / 換裝置 情境）
        if not existing_user.check_password(pin):
            return jsonify({"status": "error", "message": "此帳號已存在，但 PIN 碼錯誤"}), 401
        # 權限檢查：admin 不能綁定到別店的既有帳號
        if not current_user.is_super_admin():
            if existing_user.store and existing_user.store != current_user.store:
                return jsonify({"status": "error", "message": "無權限綁定到其他店別的帳號"}), 403
        # 可選：更新人臉（如果這次有拍新的）
        if face_image:
            try:
                import face_recognition
                img_data = base64.b64decode(face_image.split(",")[-1])
                img = face_recognition.load_image_file(io.BytesIO(img_data))
                encodings = face_recognition.face_encodings(img)
                if encodings:
                    existing_user.set_face_encoding(encodings[0])
            except BaseException:
                pass
        device.user_id = existing_user.id
        device.is_approved = True
        device.is_revoked = False
        _log_user_action(
            "approve_device", existing_user,
            detail=f"rebind existing account, device={device.device_name}"
        )
        db.session.commit()
        return jsonify({"status": "ok", "user_id": existing_user.id, "rebind": True})

    # 帳號不存在 → 建立新帳號
    user = User(
        username=username,
        role=role if role in ("super_admin", "admin", "user") else "user",
        store=store if store in valid_stores else None,
    )
    user.set_password(pin)

    if face_image:
        try:
            import face_recognition
            img_data = base64.b64decode(face_image.split(",")[-1])
            img = face_recognition.load_image_file(io.BytesIO(img_data))
            encodings = face_recognition.face_encodings(img)
            if encodings:
                user.set_face_encoding(encodings[0])
        except BaseException:
            pass

    db.session.add(user)
    db.session.flush()

    device.user_id = user.id
    device.is_approved = True
    device.is_revoked = False
    _log_user_action("approve_device", user, detail=f"role={role}, store={user.store or '—'}, device={device.device_name}")
    db.session.commit()

    return jsonify({"status": "ok", "user_id": user.id})


@admin_bp.route("/devices/<int:device_id>/revoke", methods=["POST"])
@login_required
def revoke_device(device_id):
    require_admin()
    from models import TrustedDevice
    device = TrustedDevice.query.get_or_404(device_id)
    device.is_revoked = True
    db.session.commit()
    return jsonify({"status": "ok"})


@admin_bp.route("/devices/<int:device_id>/unrevoke", methods=["POST"])
@login_required
def unrevoke_device(device_id):
    require_admin()
    from models import TrustedDevice
    device = TrustedDevice.query.get_or_404(device_id)
    device.is_revoked = False
    db.session.commit()
    return jsonify({"status": "ok"})


@admin_bp.route("/devices/<int:device_id>", methods=["DELETE"])
@login_required
def delete_device(device_id):
    require_admin()
    from models import TrustedDevice
    device = TrustedDevice.query.get_or_404(device_id)
    db.session.delete(device)
    db.session.commit()
    return jsonify({"status": "ok"})


@admin_bp.route("/devices/batch-delete", methods=["POST"])
@login_required
def batch_delete_devices():
    require_admin()
    from models import TrustedDevice
    data = request.get_json(silent=True) or {}
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"status": "error", "message": "未選擇設備"}), 400
    TrustedDevice.query.filter(TrustedDevice.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"status": "ok", "deleted": len(ids)})


# ── 店家管理 ──────────────────────────────────────────────

@admin_bp.route("/stores", methods=["GET"])
@login_required
def list_stores():
    require_admin()
    stores = Store.query.order_by(Store.name).all()
    return jsonify([{"name": s.name, "login_enabled": s.login_enabled} for s in stores])


@admin_bp.route("/stores", methods=["POST"])
@login_required
def create_store():
    require_admin()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip().upper()
    if not name:
        return jsonify({"status": "error", "message": "請填寫店名"}), 400
    if Store.query.filter_by(name=name).first():
        return jsonify({"status": "error", "message": "店家已存在"}), 409
    db.session.add(Store(name=name))
    db.session.commit()
    return jsonify({"status": "ok", "name": name}), 201


@admin_bp.route("/stores/<name>", methods=["DELETE"])
@login_required
def delete_store(name):
    require_admin()
    store = Store.query.filter_by(name=name).first_or_404()
    db.session.delete(store)
    db.session.commit()
    return jsonify({"status": "ok"})


@admin_bp.route("/stores/<name>/toggle-login", methods=["POST"])
@login_required
def toggle_store_login(name):
    require_admin()
    store = Store.query.filter_by(name=name).first_or_404()
    store.login_enabled = not store.login_enabled
    db.session.commit()
    return jsonify({"status": "ok", "login_enabled": store.login_enabled})


# ── 操作 Log ──────────────────────────────────────────────

@admin_bp.route("/logs", methods=["GET"])
@login_required
def get_logs():
    require_admin()
    # 自動清理 30 天前的記錄
    cutoff = datetime.utcnow() - timedelta(days=30)
    NoteLog.query.filter(NoteLog.created_at < cutoff).delete()
    db.session.commit()

    logs = NoteLog.query.order_by(NoteLog.created_at.desc()).limit(200).all()
    return jsonify([{
        "id": l.id,
        "note_id": l.note_id,
        "note_title": l.note_title,
        "operator": l.operator.username if l.operator else "?",
        "action": l.action,
        "diff": l.diff,
        "created_at": l.created_at.strftime("%Y/%m/%d %H:%M") if l.created_at else "",
    } for l in logs])
