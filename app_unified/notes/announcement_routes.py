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

from models import Announcement, AnnouncementAttachment, Store
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
            }
            for att in (a.attachments or [])
        ]
    return out


def _visibility_query():
    """回傳已套用 visibility filter 的 query（給 list / 詳情用）。"""
    if current_user.is_super_admin():
        return Announcement.query
    return Announcement.query.filter(
        or_(
            Announcement.store.is_(None),
            Announcement.store == current_user.store,
        )
    )


def _validate_store_payload(store):
    """store 必須是 None 或非空字串。回傳 (ok, err_msg)。

    註：不強制要 Store table 有對應 row（與 Note.store 欄位一致；
    Store 表只用在登入流程，公告對 store 是 free-text snapshot）。
    """
    if store is None:
        return True, None
    if not isinstance(store, str) or not store.strip():
        return False, "store 必須是非空字串或 null"
    return True, None


# ── POST /notes/announcement/api ─────────────────────────────

@notes_bp.route("/announcement/api", methods=["POST"])
@login_required
def announcement_create():
    if not _can_create():
        return jsonify({"status": "error", "message": "無建立公告權限"}), 403

    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    content = payload.get("content") or ""
    store = payload.get("store")  # None or str

    if not title:
        return jsonify({"status": "error", "message": "title 不可為空"}), 400

    ok, err = _validate_store_payload(store)
    if not ok:
        return jsonify({"status": "error", "message": err}), 400

    if not _can_target_store(store):
        return jsonify({"status": "error", "message": "無此 scope 的權限"}), 403

    a = Announcement(
        user_id=current_user.id,
        author_name=current_user.username,  # snapshot
        title=title,
        content=content,
        store=store,
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
