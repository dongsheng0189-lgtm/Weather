"""私人筆記（mynotes）— 與店務筆記完全隔離，只有 owner 看得到。

支援 3 種類型：
- meeting        會議紀錄：可 AI 摘要、截圖分享
- todo_note      筆記-純文字：可提醒日期、可附件
- todo_checklist 清單：逐項勾選、可提醒日期、項目可附件
"""
import json
import time
import uuid
import logging
from datetime import datetime, date, timedelta, timezone

import gevent
from gevent.lock import BoundedSemaphore
from flask import Blueprint, request, jsonify, render_template, abort, current_app
from flask_login import login_required, current_user
from extensions import db
from models import (
    AiTask, PrivateNote, PrivateChecklistItem, PrivateAttachment, PRIVATE_NOTE_TYPES
)
from features import user_has_feature
from tw_time import tw_iso

logger = logging.getLogger(__name__)
mynotes_bp = Blueprint("mynotes", __name__, url_prefix="/mynotes")

_TW = timezone(timedelta(hours=8))


@mynotes_bp.before_request
def _gate_mynotes_feature():
    if current_user.is_authenticated and not user_has_feature(current_user, "mynotes"):
        abort(404)


# ═══ helper ═══════════════════════════════════════════════

def _today_tw() -> date:
    return datetime.now(_TW).date()


def _idle_limit_ms() -> int:
    """依當前 User-Agent 判斷閒置時間：手機 5 分鐘、電腦 30 分鐘。"""
    ua = (request.headers.get("User-Agent") or "").lower()
    is_mobile = any(k in ua for k in ("iphone", "ipad", "ipod", "android"))
    minutes = 5 if is_mobile else 30
    return minutes * 60 * 1000


def _get_my_note(note_id):
    """取屬於 current_user 的筆記，找不到或不屬於自己都 404（看不見即不存在）"""
    n = PrivateNote.query.get(note_id)
    if not n or n.user_id != current_user.id:
        abort(404)
    return n


def _note_json(n: PrivateNote):
    return {
        "id": n.id,
        "note_type": n.note_type,
        "title": n.title,
        "content": n.content,
        "ai_summary": n.ai_summary,
        "reminder_date": n.reminder_date.isoformat() if n.reminder_date else None,
        "created_at": tw_iso(n.created_at, fallback=None),
        "updated_at": tw_iso(n.updated_at, fallback=None),
        "item_count": len(n.items) if n.note_type == "todo_checklist" else 0,
        "done_count": sum(1 for i in n.items if i.is_checked) if n.note_type == "todo_checklist" else 0,
    }


def _item_json(i: PrivateChecklistItem):
    return {
        "id": i.id,
        "text": i.text,
        "is_checked": i.is_checked,
        "checked_at": tw_iso(i.checked_at, fallback=None),
        "reminder_date": i.reminder_date.isoformat() if i.reminder_date else None,
        "order_index": i.order_index,
        "attachments": [_attachment_json(a) for a in i.attachments],
    }


def _attachment_json(a: PrivateAttachment):
    from storage import get_signed_url
    return {
        "id": a.id,
        "filename": a.filename,
        "content_type": a.content_type,
        "file_size": a.file_size,
        "url": get_signed_url(a.object_key, expires_in=3600),
        "item_id": a.item_id,
    }


# ═══ 頁面 ═══════════════════════════════════════════════

@mynotes_bp.route("/")
@login_required
def index():
    return render_template("mynotes/index.html", idle_limit_ms=_idle_limit_ms())


@mynotes_bp.route("/new")
@login_required
def new_note():
    note_type = request.args.get("type", "todo_note")
    if note_type not in PRIVATE_NOTE_TYPES:
        note_type = "todo_note"
    type_label = {"meeting": "會議", "todo_note": "筆記", "todo_checklist": "清單"}.get(note_type, "筆記")
    today_tw = datetime.now(_TW)
    default_title = f"{today_tw.month}/{today_tw.day} {type_label}"
    return render_template("mynotes/editor.html", note=None, note_type=note_type,
                           default_title=default_title, idle_limit_ms=_idle_limit_ms())


@mynotes_bp.route("/<int:note_id>")
@login_required
def view_note(note_id):
    note = _get_my_note(note_id)
    return render_template("mynotes/editor.html", note=note, note_type=note.note_type,
                           default_title="", idle_limit_ms=_idle_limit_ms())


# ═══ 筆記 CRUD ═════════════════════════════════════════

@mynotes_bp.route("/api", methods=["GET"])
@login_required
def list_notes():
    q = (request.args.get("q") or "").strip()
    query = PrivateNote.query.filter_by(user_id=current_user.id)
    notes = query.order_by(PrivateNote.updated_at.desc()).limit(300).all()
    if q:
        ql = q.lower()
        notes = [n for n in notes if ql in (n.title or "").lower() or ql in (n.content or "").lower()]
    return jsonify([_note_json(n) for n in notes])


@mynotes_bp.route("/api/reminders/today", methods=["GET"])
@login_required
def reminders_today():
    """今天（及之前）到期的提醒。
    - todo_note：note 級別
    - todo_checklist：每個項目獨立（只列尚未完成的）
    """
    today = _today_tw()
    out = []

    # note 級別（僅 todo_note）
    notes = PrivateNote.query.filter(
        PrivateNote.user_id == current_user.id,
        PrivateNote.note_type == "todo_note",
        PrivateNote.reminder_date.isnot(None),
        PrivateNote.reminder_date <= today,
    ).order_by(PrivateNote.reminder_date.asc()).all()
    for n in notes:
        out.append({
            "kind": "note",
            "id": n.id,
            "note_id": n.id,
            "note_type": n.note_type,
            "title": n.title,
            "text": "",
            "reminder_date": n.reminder_date.isoformat() if n.reminder_date else None,
            "overdue": n.reminder_date < today,
        })

    # item 級別（todo_checklist 的未完成項目）
    items = (PrivateChecklistItem.query
             .join(PrivateNote, PrivateChecklistItem.note_id == PrivateNote.id)
             .filter(
                 PrivateNote.user_id == current_user.id,
                 PrivateNote.note_type == "todo_checklist",
                 PrivateChecklistItem.is_checked.is_(False),
                 PrivateChecklistItem.reminder_date.isnot(None),
                 PrivateChecklistItem.reminder_date <= today,
             ).order_by(PrivateChecklistItem.reminder_date.asc()).all())
    for i in items:
        out.append({
            "kind": "item",
            "id": i.id,
            "note_id": i.note_id,
            "note_type": "todo_checklist",
            "title": i.note.title,
            "text": i.text,
            "reminder_date": i.reminder_date.isoformat() if i.reminder_date else None,
            "overdue": i.reminder_date < today,
        })

    # 依逾期優先 + 日期升冪
    out.sort(key=lambda x: (not x["overdue"], x["reminder_date"] or ""))
    return jsonify(out)


@mynotes_bp.route("/api", methods=["POST"])
@login_required
def create_note():
    data = request.get_json(silent=True) or {}
    note_type = data.get("note_type", "todo_note")
    if note_type not in PRIVATE_NOTE_TYPES:
        return jsonify({"status": "error", "message": "note_type 無效"}), 400
    n = PrivateNote(
        user_id=current_user.id,
        note_type=note_type,
        title=(data.get("title") or "").strip()[:200] or "未命名",
        content=data.get("content") or "",
    )
    if note_type in ("todo_note", "todo_checklist"):
        rd = data.get("reminder_date")
        n.reminder_date = _parse_date(rd)
    db.session.add(n)
    db.session.commit()
    return jsonify({"status": "ok", "id": n.id, "note": _note_json(n)}), 201


@mynotes_bp.route("/api/<int:note_id>", methods=["GET"])
@login_required
def get_note(note_id):
    n = _get_my_note(note_id)
    data = _note_json(n)
    data["items"] = [_item_json(i) for i in sorted(n.items, key=lambda x: (x.updated_at or x.created_at, x.id), reverse=True)] if n.note_type == "todo_checklist" else []
    data["attachments"] = [_attachment_json(a) for a in n.attachments if a.item_id is None]
    return jsonify(data)


@mynotes_bp.route("/api/<int:note_id>", methods=["PUT"])
@login_required
def update_note(note_id):
    n = _get_my_note(note_id)
    data = request.get_json(silent=True) or {}
    if "title" in data:
        n.title = (data.get("title") or "").strip()[:200] or "未命名"
    if "content" in data:
        n.content = data.get("content") or ""
    if "reminder_date" in data:
        n.reminder_date = _parse_date(data.get("reminder_date"))
    if "ai_summary" in data:
        # 僅 meeting 有意義；其他類型也允許寫入但 UI 不顯示
        n.ai_summary = data.get("ai_summary") or None
    n.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"status": "ok", "note": _note_json(n)})


@mynotes_bp.route("/api/<int:note_id>", methods=["DELETE"])
@login_required
def delete_note(note_id):
    from storage import delete_attachment
    n = _get_my_note(note_id)
    # 清 R2 檔案
    for a in list(n.attachments):
        try:
            delete_attachment(a.object_key)
        except Exception:
            pass
    db.session.delete(n)
    db.session.commit()
    return jsonify({"status": "ok"})


def _parse_date(val):
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y-%m-%d").date()
    except Exception:
        return None


# ═══ Checklist items ═══════════════════════════════════

@mynotes_bp.route("/api/<int:note_id>/items", methods=["GET"])
@login_required
def list_items(note_id):
    n = _get_my_note(note_id)
    items = sorted(n.items, key=lambda i: (i.updated_at or i.created_at, i.id), reverse=True)
    return jsonify([_item_json(i) for i in items])


@mynotes_bp.route("/api/<int:note_id>/items", methods=["POST"])
@login_required
def create_item(note_id):
    n = _get_my_note(note_id)
    if n.note_type != "todo_checklist":
        return jsonify({"status": "error", "message": "此筆記不是清單類型"}), 400
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    max_order = max([i.order_index for i in n.items], default=-1)
    now = datetime.utcnow()
    item = PrivateChecklistItem(
        note_id=n.id,
        order_index=max_order + 1,
        text=text,
        reminder_date=_parse_date(data.get("reminder_date")),
        updated_at=now,
    )
    db.session.add(item)
    n.updated_at = now
    db.session.commit()
    return jsonify({"status": "ok", "item": _item_json(item)}), 201


@mynotes_bp.route("/api/items/<int:item_id>", methods=["PATCH"])
@login_required
def update_item(item_id):
    item = PrivateChecklistItem.query.get(item_id)
    if not item:
        abort(404)
    n = _get_my_note(item.note_id)  # 附帶 ownership 檢查
    data = request.get_json(silent=True) or {}
    changed = False
    if "text" in data:
        new_text = (data.get("text") or "").strip()
        if new_text != item.text:
            item.text = new_text
            changed = True
    if "is_checked" in data:
        is_checked = bool(data.get("is_checked"))
        if is_checked and not item.is_checked:
            item.is_checked = True
            item.checked_at = datetime.utcnow()
            changed = True
        elif not is_checked and item.is_checked:
            item.is_checked = False
            item.checked_at = None
            changed = True
    if "reminder_date" in data:
        new_rem = _parse_date(data.get("reminder_date"))
        if new_rem != item.reminder_date:
            item.reminder_date = new_rem
            changed = True
    now = datetime.utcnow()
    if changed:
        item.updated_at = now
    n.updated_at = now
    db.session.commit()
    return jsonify({"status": "ok", "item": _item_json(item)})


@mynotes_bp.route("/api/items/<int:item_id>", methods=["DELETE"])
@login_required
def delete_item(item_id):
    from storage import delete_attachment
    item = PrivateChecklistItem.query.get(item_id)
    if not item:
        abort(404)
    n = _get_my_note(item.note_id)
    for a in list(item.attachments):
        try:
            delete_attachment(a.object_key)
        except Exception:
            pass
    db.session.delete(item)
    n.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"status": "ok"})


# ═══ Attachments ══════════════════════════════════════

@mynotes_bp.route("/api/attachments/upload", methods=["POST"])
@login_required
def upload_attachment_api():
    from storage import upload_attachment, ALLOWED_CONTENT_TYPES_MYNOTES as ALLOWED_CONTENT_TYPES, MAX_FILE_SIZE
    file = request.files.get("file")
    note_id = request.form.get("note_id", type=int)
    item_id = request.form.get("item_id", type=int)
    if not file or not note_id:
        return jsonify({"status": "error", "message": "缺少 file / note_id"}), 400
    n = _get_my_note(note_id)
    if item_id:
        item = PrivateChecklistItem.query.get(item_id)
        if not item or item.note_id != n.id:
            return jsonify({"status": "error", "message": "item 不屬於此筆記"}), 400
    ct = (file.content_type or "").lower()
    if ct not in ALLOWED_CONTENT_TYPES:
        return jsonify({"status": "error", "message": f"不支援的檔案類型：{ct}"}), 400
    data = file.read()
    if len(data) > MAX_FILE_SIZE:
        return jsonify({"status": "error", "message": "檔案過大（最大 50MB）"}), 400
    prefix = f"private/{current_user.id}"
    key = upload_attachment(data, n.id, file.filename or "file", ct, prefix=prefix)
    if not key:
        return jsonify({"status": "error", "message": "儲存失敗"}), 500
    att = PrivateAttachment(
        note_id=n.id,
        item_id=item_id,
        user_id=current_user.id,
        object_key=key,
        filename=file.filename or "file",
        content_type=ct,
        file_size=len(data),
    )
    db.session.add(att)
    now = datetime.utcnow()
    n.updated_at = now
    if item_id:
        item_obj = PrivateChecklistItem.query.get(item_id)
        if item_obj:
            item_obj.updated_at = now
    db.session.commit()
    return jsonify({"status": "ok", "attachment": _attachment_json(att)}), 201


@mynotes_bp.route("/api/attachments/<int:attachment_id>", methods=["DELETE"])
@login_required
def delete_attachment_api(attachment_id):
    from storage import delete_attachment
    att = PrivateAttachment.query.get(attachment_id)
    if not att:
        abort(404)
    n = _get_my_note(att.note_id)
    try:
        delete_attachment(att.object_key)
    except Exception:
        pass
    item_id = att.item_id
    db.session.delete(att)
    now = datetime.utcnow()
    n.updated_at = now
    if item_id:
        item_obj = PrivateChecklistItem.query.get(item_id)
        if item_obj:
            item_obj.updated_at = now
    db.session.commit()
    return jsonify({"status": "ok"})


# ═══ AI 摘要（僅 meeting，local Python pipeline）═══════════
# Task state 走 DB (AiTask, kind='mynotes') 跨 worker 都活；獨立 sem 限制每
# worker 並發到 2 個（防 gunicorn timeout=60s 把 worker SIGKILL 整批死光）。
# 細節同 notes/routes.py 的 fix（2026-05-10 commit ef2ca06）。

_mynotes_ai_sem = BoundedSemaphore(2)
_AI_TASK_TTL_SEC = 30
_AI_TASK_STUCK_SEC = 60
_AI_TASK_HARD_TIMEOUT_SEC = 45


def _create_ai_task(user_id):
    task_id = uuid.uuid4().hex[:12]
    db.session.add(AiTask(id=task_id, kind="mynotes", user_id=user_id, status="processing"))
    db.session.commit()
    return task_id


def _set_ai_task_done(task_id, result):
    now = datetime.utcnow()
    row = db.session.get(AiTask, task_id)
    if row is None:
        return
    row.status = "done"
    row.result_json = json.dumps(result, ensure_ascii=False)
    row.error_msg = None
    row.updated_at = now
    row.expires_at = now + timedelta(seconds=_AI_TASK_TTL_SEC)
    db.session.commit()


def _set_ai_task_error(task_id, message):
    now = datetime.utcnow()
    row = db.session.get(AiTask, task_id)
    if row is None:
        return
    row.status = "error"
    row.error_msg = message
    row.updated_at = now
    row.expires_at = now + timedelta(seconds=_AI_TASK_TTL_SEC)
    db.session.commit()


def _sweep_ai_tasks():
    now = datetime.utcnow()
    AiTask.query.filter(
        AiTask.kind == "mynotes",
        AiTask.status == "processing",
        AiTask.updated_at < now - timedelta(seconds=_AI_TASK_STUCK_SEC),
    ).update(
        {
            AiTask.status: "error",
            AiTask.error_msg: "任務逾時",
            AiTask.updated_at: now,
            AiTask.expires_at: now + timedelta(seconds=_AI_TASK_TTL_SEC),
        },
        synchronize_session=False,
    )
    AiTask.query.filter(
        AiTask.kind == "mynotes",
        AiTask.expires_at.isnot(None),
        AiTask.expires_at < now,
    ).delete(synchronize_session=False)
    db.session.commit()


@mynotes_bp.route("/api/<int:note_id>/summarize", methods=["POST"])
@login_required
def summarize(note_id):
    n = _get_my_note(note_id)
    if n.note_type != "meeting":
        return jsonify({"status": "error", "message": "僅會議紀錄可摘要"}), 400

    if not _mynotes_ai_sem.acquire(blocking=False):
        return jsonify({"status": "busy", "message": "AI 摘要忙線中，請稍後再試"}), 429
    task_id = _create_ai_task(current_user.id)
    gevent.spawn(_run_summary, task_id, current_app._get_current_object(), n.id)
    return jsonify({"status": "accepted", "task_id": task_id})


def _run_summary(task_id, app, note_id):
    """Local Python parser task — threadpool 不阻塞 reactor、with_timeout 防 SIGKILL。"""
    with app.app_context():
        try:
            try:
                n = PrivateNote.query.get(note_id)
                if not n:
                    _set_ai_task_error(task_id, "筆記不存在")
                    return
                from mynotes.ai_local import summarize_meeting
                result = gevent.with_timeout(
                    _AI_TASK_HARD_TIMEOUT_SEC,
                    gevent.get_hub().threadpool.apply,
                    summarize_meeting,
                    args=(n.title or "", n.content or ""),
                )
                n.ai_summary = result
                n.updated_at = datetime.utcnow()
                db.session.commit()
                _set_ai_task_done(task_id, result)
            except gevent.Timeout:
                db.session.rollback()
                _set_ai_task_error(task_id, "摘要逾時，請稍後再試")
            except Exception as e:
                # commit() raise 時 session 會留 dirty state；rollback 才不會
                # 讓後續 request 在同 scoped session 看到 partial write。
                db.session.rollback()
                logger.exception("[mynotes] local summary pipeline failed")
                _set_ai_task_error(task_id, str(e))
        finally:
            _mynotes_ai_sem.release()


@mynotes_bp.route("/api/ai/task/<task_id>", methods=["GET"])
@login_required
def get_ai_task(task_id):
    """輪詢 AI 任務狀態（DB-backed，跨 worker / pod restart 都活）。"""
    _sweep_ai_tasks()

    row = db.session.get(AiTask, task_id)
    if row is None or row.kind != "mynotes":
        return jsonify({"status": "error", "message": "任務不存在"}), 404
    if row.status == "done":
        return jsonify({"status": "done", "result": json.loads(row.result_json)})
    if row.status == "error":
        return jsonify({"status": "error", "message": row.error_msg or ""})
    return jsonify({"status": "processing"})
