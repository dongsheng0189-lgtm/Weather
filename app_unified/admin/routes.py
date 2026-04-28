import io
import base64
from datetime import datetime, timedelta, timezone
TW_TZ = timezone(timedelta(hours=8))
from flask import Blueprint, render_template, jsonify, request, abort, current_app
from flask_login import login_required, current_user
from extensions import db
from models import User, Note, Store, NoteLog, UserLog, TrustedDevice, STATUS_CHOICES


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
    """使用 Gemini 2.5 Flash。偵測到 repetition loop 會自動重試一次。

    送出前會把 PII（身分證/手機/email/信用卡/市話）替換成 placeholder，
    回收後再把 placeholder 還原回原值。
    """
    import time
    import logging
    from pii_redact import redact, restore
    logger = logging.getLogger("call_llm")

    safe_prompt, pii_map = redact(prompt)
    if pii_map:
        logger.warning("=== LLM: redacted %d PII (types: %s) ===",
                       len(pii_map),
                       sorted({k.strip("[]").rstrip("0123456789") for k in pii_map}))
        # 告知 LLM：這些 placeholder 是已遮罩的客戶識別資訊，必須完整保留
        # 否則會被 LLM 當 metadata 過濾掉，restore 時就還原不出原值
        safe_prompt = (
            "# 個資遮罩說明（系統自動加入，請務必遵守）\n"
            "以下內容中的 [id1] [id2] [tel1] [email1] [card1] [ph1] 等"
            "中括號代號，代表已遮罩的客戶識別資訊"
            "（身分證 / 手機 / Email / 信用卡 / 市話）。\n"
            "**請在摘要輸出中完整保留這些代號原文**（不要省略、不要改寫格式、"
            "不要加空白），系統會在輸出後自動還原成真實值。\n\n"
            + safe_prompt
        )

    logger.warning("=== LLM: calling Gemini ===")
    t0 = time.time()
    result = _call_gemini(safe_prompt, max_tokens)
    elapsed = time.time() - t0
    logger.warning("=== LLM: Gemini OK, %.1f sec, %d chars ===", elapsed, len(result))

    if _is_output_looping(result):
        logger.warning("=== LLM: output repetition loop detected, retrying once ===")
        retry_prompt = safe_prompt + (
            "\n\n# 再次提醒\n"
            "請絕對避免在輸出中連續重複同一句話。每個事項只能列出一次。"
            "若多筆輸入資料描述相同事件，請合併為單一條目。"
        )
        t0 = time.time()
        result2 = _call_gemini(retry_prompt, max_tokens)
        elapsed = time.time() - t0
        logger.warning("=== LLM: retry OK, %.1f sec, %d chars ===", elapsed, len(result2))
        if not _is_output_looping(result2):
            return restore(result2, pii_map)
        return "⚠️ AI 產生結果異常（偵測到重複迴圈），以下為重試結果，建議再按一次：\n\n" + restore(result2, pii_map)
    return restore(result, pii_map)


def require_admin():
    if not current_user.is_authenticated or not current_user.is_admin():
        abort(403)


@admin_bp.route("/dashboard")
@login_required
def dashboard():
    require_admin()
    try:
        from notes.cleanup import cleanup_resolved_notes
        cleanup_resolved_notes()
    except Exception:
        current_app.logger.exception("cleanup_resolved_notes failed")
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
    from models import NoteLog, NoteAttachment, LoginToken, ChecklistItem, TrustedDevice

    # 先查綁定裝置；有裝置且未帶 confirm=1 → 回 409 讓前端二次確認
    bound_devices = TrustedDevice.query.filter_by(user_id=user.id).all()
    confirm = request.args.get("confirm") == "1"
    if bound_devices and not confirm:
        return jsonify({
            "status": "confirm_required",
            "message": f"此使用者綁定 {len(bound_devices)} 個裝置",
            "devices": [
                {"id": d.id, "device_name": d.device_name, "is_approved": d.is_approved}
                for d in bound_devices
            ],
        }), 409

    # 筆記保留：確保 author_name 快照已存（顯示為「帳號 (帳號已刪除)」）
    Note.query.filter_by(user_id=user.id, author_name=None).update({"author_name": user.username})
    Note.query.filter_by(user_id=user.id).update({"user_id": None})
    Note.query.filter_by(updated_by=user.id).update({"updated_by": None})
    Note.query.filter_by(locked_by=user.id).update({"locked_by": None, "locked_at": None})
    # 清掉 user 相關 FK（對應舊 schema 沒 ON DELETE 的情況也能刪）
    NoteLog.query.filter_by(user_id=user.id).update({"user_id": None})
    NoteAttachment.query.filter_by(user_id=user.id).update({"user_id": None})
    ChecklistItem.query.filter_by(checked_by=user.id).update({"checked_by": None})
    # 綁定裝置：與 user 一併刪除（使用者已確認），避免產生 orphan device
    for d in bound_devices:
        db.session.delete(d)
    LoginToken.query.filter_by(user_id=user.id).delete()
    _log_user_action(
        "delete", user,
        detail=f"role={user.role}, store={user.store or '—'}, devices_deleted={len(bound_devices)}",
    )
    db.session.delete(user)
    db.session.commit()
    return jsonify({"status": "ok", "devices_deleted": len(bound_devices)})


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


@admin_bp.route("/users/<int:user_id>/rename", methods=["POST"])
@login_required
def rename_user(user_id):
    require_admin()
    user = User.query.get_or_404(user_id)

    # admin/super_admin 可以改自己；admin 跨店不可
    if not current_user.is_super_admin():
        if user.store != current_user.store:
            return jsonify({"status": "error", "message": "無權修改他店使用者"}), 403

    data = request.get_json(silent=True) or {}
    new_name = (data.get("new_username") or "").strip()
    if not new_name:
        return jsonify({"status": "error", "message": "名稱不可為空"}), 400

    old_name = user.username
    if new_name == old_name:
        return jsonify({"status": "ok", "new_username": new_name})

    dup = User.query.filter(User.username == new_name, User.id != user.id).first()
    if dup is not None:
        return jsonify({"status": "error", "message": "名稱已被使用"}), 409

    try:
        user.username = new_name
        TrustedDevice.query.filter_by(user_id=user.id).update(
            {"device_name": new_name}, synchronize_session=False
        )
        _log_user_action("rename", user, detail=f"{old_name} → {new_name}")
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("rename_user failed")
        return jsonify({"status": "error", "message": "內部錯誤"}), 500

    return jsonify({"status": "ok", "new_username": new_name})


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
        "set_features": "改模組權限",
        "rename": "改名",
    }
    return jsonify([{
        "id": log.id,
        "operator": log.operator_name,
        "target": log.target_name,
        "action": action_labels.get(log.action, log.action),
        "detail": log.detail or "",
        "created_at": (log.created_at.replace(tzinfo=timezone.utc).astimezone(TW_TZ).strftime("%Y/%m/%d %H:%M")) if log.created_at else "",
    } for log in logs])


# ── 模組權限管理（僅種子 admin username="admin" 可用） ──────

@admin_bp.route("/features/catalog", methods=["GET"])
@login_required
def features_catalog():
    from features import AVAILABLE_FEATURES, require_root_admin
    require_root_admin()
    return jsonify({"features": AVAILABLE_FEATURES})


@admin_bp.route("/features/users", methods=["GET"])
@login_required
def features_user_list():
    """供模組權限管理下拉使用的完整使用者清單（僅種子 admin 可呼叫）"""
    from features import require_root_admin
    require_root_admin()
    users = User.query.order_by(User.username).all()
    return jsonify([{
        "id": u.id, "username": u.username, "store": u.store, "role": u.role,
    } for u in users])


@admin_bp.route("/users/<int:user_id>/features", methods=["GET"])
@login_required
def get_user_features(user_id):
    from features import require_root_admin
    require_root_admin()
    user = User.query.get_or_404(user_id)
    return jsonify({"status": "ok", "features": user.get_features()})


@admin_bp.route("/users/<int:user_id>/features", methods=["POST"])
@login_required
def set_user_features(user_id):
    from features import AVAILABLE_FEATURES, require_root_admin
    require_root_admin()
    user = User.query.get_or_404(user_id)
    data = request.get_json(silent=True) or {}
    keys = data.get("features") or []
    if not isinstance(keys, list):
        return jsonify({"status": "error", "message": "features 必須為陣列"}), 400
    valid_keys = {f["key"] for f in AVAILABLE_FEATURES}
    cleaned = [k for k in keys if isinstance(k, str) and k in valid_keys]
    old = user.get_features()
    user.set_features(cleaned)
    if set(old) != set(cleaned):
        _log_user_action("set_features", user,
                         detail=f"{sorted(old)} → {sorted(cleaned)}")
    db.session.commit()
    return jsonify({"status": "ok", "features": cleaned})


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


@admin_bp.route("/devices/<int:device_id>/rebind", methods=["POST"])
@login_required
def rebind_device(device_id):
    """快速綁定舊帳號：PIN + 人臉 → 自動識別使用者並綁定 + 核准。
    用於 Safari 清完 cookie 後要重新綁定既有帳號的情境，省去填帳號/店別/角色。"""
    require_admin()
    from models import TrustedDevice
    from auth.routes import _verify_face

    device = TrustedDevice.query.get_or_404(device_id)
    if device.is_approved:
        return jsonify({"status": "error", "message": "此裝置已核准"}), 400

    data = request.get_json(silent=True) or {}
    pin = str(data.get("pin") or "").strip()
    face_image = data.get("face_image")

    if not pin:
        return jsonify({"status": "error", "message": "請輸入 PIN"}), 400
    if not face_image:
        return jsonify({"status": "error", "message": "請拍攝人臉"}), 400

    # 1. PIN 篩候選
    all_users = User.query.filter_by(is_active=True).all()
    pin_users = [u for u in all_users if u.check_password(pin)]
    if not pin_users:
        return jsonify({"status": "wrong_password", "message": "PIN 錯誤"})

    # 2. 必須已登錄人臉
    face_enrolled = [u for u in pin_users if u.face_encoding is not None]
    if not face_enrolled:
        return jsonify({"status": "need_face_enroll",
                        "message": "此 PIN 對應帳號尚未登錄人臉，請先用「設定並核准」完整流程"})

    # 3. 人臉比對
    matched = None
    for u in face_enrolled:
        match, _ = _verify_face(u, face_image)
        if match:
            matched = u
            break
    if not matched:
        return jsonify({"status": "face_mismatch", "message": "人臉不符"})

    # 4. 跨店綁定檢查（跟 approve_device 同規則）
    if not current_user.is_super_admin():
        if matched.store and matched.store != current_user.store:
            return jsonify({"status": "error", "message": "無權限綁定到其他店別的帳號"}), 403

    # 5. 綁定
    device.user_id = matched.id
    device.is_approved = True
    device.is_revoked = False
    _log_user_action("rebind_device", matched, detail=f"device={device.device_name}")
    db.session.commit()

    return jsonify({"status": "ok", "username": matched.username, "user_id": matched.id})


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
        "created_at": (l.created_at.replace(tzinfo=timezone.utc).astimezone(TW_TZ).strftime("%Y/%m/%d %H:%M")) if l.created_at else "",
    } for l in logs])
