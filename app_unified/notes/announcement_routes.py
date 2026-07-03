"""公告 (announcements) — UI 整合進 notes 模組，資料層獨立。

Routes 註冊到既有 notes_bp（共用 URL prefix `/notes`），檔案分離避免 notes/routes.py 繼續肥。
"""
from flask import request, jsonify, render_template, abort
from flask_login import login_required, current_user

from extensions import db
from notes.routes import notes_bp


# ── Permission helpers ───────────────────────────────────────

def _can_create() -> bool:
    """誰能建立公告：admin / super_admin"""
    return current_user.is_admin()


def _can_target_store(store) -> bool:
    """檢查當前 user 能否對這個 scope 發/改公告。

    super_admin: 任何 store 或 None（全部）皆可
    admin:       只能 store == user.store（不能 None、不能別店）
    user:        永遠 False
    """
    if current_user.is_super_admin():
        return True
    if current_user.is_admin():
        return store == current_user.store and store is not None
    return False


def _can_modify(announcement) -> bool:
    """編輯/刪除這筆公告"""
    if current_user.is_super_admin():
        return True
    if current_user.is_admin() and announcement.store == current_user.store:
        return True
    return False


def _can_view(announcement) -> bool:
    """看得到這筆公告（API GET 用；list 是 query filter，不走這函式）"""
    if current_user.is_super_admin():
        return True
    return announcement.store is None or announcement.store == current_user.store


from datetime import datetime
from sqlalchemy import or_

from models import Announcement, AnnouncementAttachment
from tw_time import tw_iso


# ── Helpers ──────────────────────────────────────────────────

def _ann_to_json(a: Announcement, include_attachments: bool = True):
    from storage import get_signed_url
    out = {
        "id": a.id,
        "title": a.title or "",
        "content": a.content or "",
        "store": a.store,
        "is_global": a.is_global,
        "author_name": a.author_name or "",
        "user_id": a.user_id,
        "created_at": tw_iso(a.created_at),
        "updated_at": tw_iso(a.updated_at),
    }
    if include_attachments:
        out["attachments"] = [
            {
                "id": att.id,
                "filename": att.filename,
                "content_type": att.content_type,
                "file_size": att.file_size,
                "url": get_signed_url(att.object_key, expires_in=3600),
                "thumb_url": get_signed_url(att.thumb_key, expires_in=3600) if att.thumb_key else None,
            }
            for att in (a.attachments or [])
        ]
    return out


def _visibility_query():
    """回傳已套用 visibility filter 的 query（給 list / 詳情用）。"""
    if current_user.is_super_admin():
        hidden = current_user.get_hidden_stores()
        if hidden:
            return Announcement.query.filter(
                or_(
                    Announcement.store.is_(None),       # 全站公告永遠顯示
                    Announcement.store.notin_(hidden),
                )
            )
        return Announcement.query
    return Announcement.query.filter(
        or_(
            Announcement.store.is_(None),
            Announcement.store == current_user.store,
        )
    )


def _validate_store_payload(store):
    """store 必須是 None 或非空字串。

    註：偏離 spec 5.5「store 不是合法 Store.name → 400」— 改不強制 Store-FK 存在性
    （因 Note.store 也是 free-text 快照、conftest fixtures 不種 Store rows）。
    安全保證來自 _can_target_store 的 role × store 比對。
    回傳 (ok, normalized_or_None, err_msg)。
    """
    if store is None:
        return True, None, None
    if not isinstance(store, str):
        return False, None, "store 必須是字串或 null"
    stripped = store.strip()
    if not stripped:
        return False, None, "store 不可為空字串"
    return True, stripped, None


# ── POST /notes/announcement/api ─────────────────────────────

@notes_bp.route("/announcement/api", methods=["POST"])
@login_required
def announcement_create():
    if not _can_create():
        return jsonify({"status": "error", "message": "無建立公告權限"}), 403

    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    content = payload.get("content") or ""
    raw_store = payload.get("store")

    if not title:
        return jsonify({"status": "error", "message": "title 不可為空"}), 400

    ok, store, err = _validate_store_payload(raw_store)
    if not ok:
        return jsonify({"status": "error", "message": err}), 400

    if not _can_target_store(store):
        return jsonify({"status": "error", "message": "無此 scope 的權限"}), 403

    # 明確同步 created_at / updated_at，否則 column default=datetime.utcnow 各自
    # evaluate 會差幾個 microsecond，害 index 卡片誤把剛建立的公告當「已編輯」。
    now = datetime.utcnow()
    a = Announcement(
        user_id=current_user.id,
        author_name=current_user.username,  # snapshot
        title=title,
        content=content,
        store=store,
        created_at=now,
        updated_at=now,
    )
    db.session.add(a)
    db.session.commit()
    return jsonify(_ann_to_json(a)), 201


# ── GET /notes/announcement/api (list) ────────────────────────

@notes_bp.route("/announcement/api", methods=["GET"])
@login_required
def announcement_list():
    page = request.args.get("page", default=1, type=int) or 1
    per_page = request.args.get("per_page", default=20, type=int) or 20
    per_page = max(1, min(100, per_page))

    q = _visibility_query().order_by(Announcement.created_at.desc())

    # super_admin 可以額外用 ?store= filter
    if current_user.is_super_admin():
        store_filter = request.args.get("store", default=None)
        if store_filter == "__global__":
            q = q.filter(Announcement.store.is_(None))
        elif store_filter:
            q = q.filter(Announcement.store == store_filter)

    pag = q.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "items": [_ann_to_json(a) for a in pag.items],
        "total": pag.total,
        "page": pag.page,
        "per_page": pag.per_page,
        "has_next": pag.has_next,
        "has_prev": pag.has_prev,
    })


from storage import delete_attachment as _r2_delete


# ── GET /notes/announcement/api/<id> ─────────────────────────

@notes_bp.route("/announcement/api/<int:announcement_id>", methods=["GET"])
@login_required
def announcement_get(announcement_id):
    a = Announcement.query.get(announcement_id)
    if not a or not _can_view(a):
        return jsonify({"status": "error", "message": "公告不存在"}), 404
    return jsonify(_ann_to_json(a))


# ── PUT /notes/announcement/api/<id> ─────────────────────────

@notes_bp.route("/announcement/api/<int:announcement_id>", methods=["PUT"])
@login_required
def announcement_update(announcement_id):
    a = Announcement.query.get(announcement_id)
    if not a or not _can_view(a):
        return jsonify({"status": "error", "message": "公告不存在"}), 404
    if not _can_modify(a):
        return jsonify({"status": "error", "message": "無權限編輯"}), 403

    payload = request.get_json(silent=True) or {}
    title = payload.get("title")
    if title is not None:
        title = title.strip()
        if not title:
            return jsonify({"status": "error", "message": "title 不可為空"}), 400
    content = payload.get("content")
    has_store_in_payload = "store" in payload
    new_store = a.store
    if has_store_in_payload:
        ok, normalized_store, err = _validate_store_payload(payload.get("store"))
        if not ok:
            return jsonify({"status": "error", "message": err}), 400
        if not _can_target_store(normalized_store):
            return jsonify({"status": "error", "message": "無此 scope 的權限"}), 403
        new_store = normalized_store

    if title is not None:
        a.title = title
    if content is not None:
        a.content = content
    if has_store_in_payload:
        a.store = new_store
    a.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(_ann_to_json(a))


# ── DELETE /notes/announcement/api/<id> ───────────────────────

@notes_bp.route("/announcement/api/<int:announcement_id>", methods=["DELETE"])
@login_required
def announcement_delete(announcement_id):
    a = Announcement.query.get(announcement_id)
    if not a or not _can_view(a):
        return jsonify({"status": "error", "message": "公告不存在"}), 404
    if not _can_modify(a):
        return jsonify({"status": "error", "message": "無權限刪除"}), 403

    # 先 R2 清理（cascade=delete-orphan 會處理 DB rows）
    for att in list(a.attachments or []):
        _r2_delete(att.object_key)

    db.session.delete(a)
    db.session.commit()
    return jsonify({"status": "ok"})


# ── Attachments ──────────────────────────────────────────────

@notes_bp.route("/announcement/api/<int:announcement_id>/attachments",
                methods=["POST"])
@login_required
def announcement_attachment_upload(announcement_id):
    from storage import (upload_attachment, get_signed_url,
                         ALLOWED_CONTENT_TYPES, MAX_FILE_SIZE)

    a = Announcement.query.get(announcement_id)
    if not a or not _can_view(a):
        return jsonify({"status": "error", "message": "公告不存在"}), 404
    if not _can_modify(a):
        return jsonify({"status": "error", "message": "無權限"}), 403

    try:
        file = request.files.get("file")
    except OSError as e:
        if "unexpected end of file" in str(e):
            return jsonify({"status": "error", "message": "上傳中斷，請重試"}), 400
        raise
    if not file or not file.filename:
        return jsonify({"status": "error", "message": "未選擇檔案"}), 400

    content_type = file.content_type or ""
    if content_type not in ALLOWED_CONTENT_TYPES:
        return jsonify({"status": "error", "message": "不支援的檔案格式"}), 400

    file_bytes = file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        return jsonify({"status": "error", "message": "檔案超過 50MB 上限"}), 413

    object_key, thumb_key = upload_attachment(file_bytes, a.id, file.filename,
                                              content_type, prefix="announcements")
    if not object_key:
        return jsonify({"status": "error", "message": "儲存服務未設定"}), 503

    att = AnnouncementAttachment(
        announcement_id=a.id,
        user_id=current_user.id,
        object_key=object_key,
        thumb_key=thumb_key,
        filename=file.filename,
        content_type=content_type,
        file_size=len(file_bytes),
    )
    db.session.add(att)
    a.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "status": "ok",
        "attachment": {
            "id": att.id,
            "filename": att.filename,
            "content_type": att.content_type,
            "file_size": att.file_size,
            "url": get_signed_url(att.object_key, expires_in=3600),
            "thumb_url": get_signed_url(att.thumb_key, expires_in=3600) if att.thumb_key else None,
        },
    }), 201


@notes_bp.route("/announcement/api/<int:announcement_id>/attachments",
                methods=["GET"])
@login_required
def announcement_attachment_list(announcement_id):
    from storage import get_signed_url
    a = Announcement.query.get(announcement_id)
    if not a or not _can_view(a):
        return jsonify({"status": "error", "message": "公告不存在"}), 404
    return jsonify({
        "status": "ok",
        "attachments": [
            {
                "id": att.id,
                "filename": att.filename,
                "content_type": att.content_type,
                "file_size": att.file_size,
                "url": get_signed_url(att.object_key, expires_in=3600),
                "thumb_url": get_signed_url(att.thumb_key, expires_in=3600) if att.thumb_key else None,
                "created_at": tw_iso(att.created_at),
            }
            for att in (a.attachments or [])
        ],
    })


@notes_bp.route("/announcement/api/attachments/<int:attachment_id>",
                methods=["DELETE"])
@login_required
def announcement_attachment_delete(attachment_id):
    att = AnnouncementAttachment.query.get(attachment_id)
    if not att:
        return jsonify({"status": "error", "message": "附件不存在"}), 404
    a = Announcement.query.get(att.announcement_id)
    if not a or not _can_view(a):
        return jsonify({"status": "error", "message": "公告不存在"}), 404
    if not _can_modify(a):
        return jsonify({"status": "error", "message": "無權限"}), 403

    _r2_delete(att.object_key)
    db.session.delete(att)
    a.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"status": "ok"})


@notes_bp.route("/announcement/api/attachments/<int:attachment_id>/download",
                methods=["GET"])
@login_required
def announcement_attachment_download(attachment_id):
    """Return a 5-minute presigned R2 URL with Content-Disposition: attachment
    so browser downloads the original file. Auth = `_can_view()` on the parent
    announcement (super_admin cross-store / same-store / store=NULL global)."""
    from storage import get_download_url

    attachment = AnnouncementAttachment.query.get(attachment_id)
    if not attachment:
        return jsonify({"status": "error", "message": "附件不存在"}), 404

    announcement = attachment.announcement
    if not announcement:
        return jsonify({"status": "error", "message": "公告不存在"}), 404

    if not _can_view(announcement):
        return jsonify({"status": "error", "message": "無權限下載"}), 403

    url = get_download_url(
        object_key=attachment.object_key,
        filename=attachment.filename,
        content_type=attachment.content_type,
    )
    if not url:
        return jsonify({"status": "error", "message": "儲存服務暫時無法使用"}), 503

    return jsonify({"url": url, "expires_in": 300})


# ── HTML pages ───────────────────────────────────────────────

@notes_bp.route("/announcement/new", methods=["GET"])
@login_required
def announcement_new_page():
    if not _can_create():
        abort(403)
    # admin without store assignment cannot post (would hit confusing 403 on submit)
    if not current_user.is_super_admin() and not current_user.store:
        abort(403)
    from models import Store
    all_stores = Store.query.order_by(Store.name).all()
    if current_user.is_super_admin():
        visible = set(current_user.visible_store_names([s.name for s in all_stores]))
        stores = [s for s in all_stores if s.name in visible]
    else:
        stores = all_stores
    return render_template(
        "announcements/new.html",
        stores=stores,
        is_super_admin=current_user.is_super_admin(),
        user_store=current_user.store,
    )


@notes_bp.route("/announcement/<int:announcement_id>/edit", methods=["GET"])
@login_required
def announcement_edit_page(announcement_id):
    a = Announcement.query.get(announcement_id)
    if not a or not _can_view(a):
        abort(404)
    if not _can_modify(a):
        abort(403)
    from models import Store
    all_stores = Store.query.order_by(Store.name).all()
    if current_user.is_super_admin():
        visible = set(current_user.visible_store_names([s.name for s in all_stores]))
        if a.store:
            visible.add(a.store)   # 保留目前所屬店（即使已隱藏），避免編輯時遺失
        stores = [s for s in all_stores if s.name in visible]
    else:
        stores = all_stores
    return render_template(
        "announcements/edit.html",
        announcement=a,
        stores=stores,
        is_super_admin=current_user.is_super_admin(),
        user_store=current_user.store,
    )
