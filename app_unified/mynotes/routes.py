"""私人筆記（mynotes）— 與店務筆記完全隔離，只有 owner 看得到。

支援 3 種類型：
- meeting        會議紀錄：可 AI 摘要、截圖分享
- todo_note      代辦-純文字：可提醒日期、可附件
- todo_checklist 代辦-清單：逐項勾選、可提醒日期、項目可附件
"""
import uuid
import json
import logging
from datetime import datetime, date, timedelta, timezone
from flask import Blueprint, request, jsonify, render_template, abort, current_app
from flask_login import login_required, current_user
from extensions import db
from models import (
    PrivateNote, PrivateChecklistItem, PrivateAttachment, PRIVATE_NOTE_TYPES
)
from features import user_has_feature

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
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "updated_at": n.updated_at.isoformat() if n.updated_at else None,
        "item_count": len(n.items) if n.note_type == "todo_checklist" else 0,
        "done_count": sum(1 for i in n.items if i.is_checked) if n.note_type == "todo_checklist" else 0,
    }


def _item_json(i: PrivateChecklistItem):
    return {
        "id": i.id,
        "text": i.text,
        "is_checked": i.is_checked,
        "checked_at": i.checked_at.isoformat() if i.checked_at else None,
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
    return render_template("mynotes/index.html")


@mynotes_bp.route("/new")
@login_required
def new_note():
    note_type = request.args.get("type", "todo_note")
    if note_type not in PRIVATE_NOTE_TYPES:
        note_type = "todo_note"
    type_label = {"meeting": "會議", "todo_note": "筆記", "todo_checklist": "清單"}.get(note_type, "筆記")
    today_tw = datetime.now(_TW)
    default_title = f"{today_tw.month}/{today_tw.day} {type_label}"
    return render_template("mynotes/editor.html", note=None, note_type=note_type, default_title=default_title)


@mynotes_bp.route("/<int:note_id>")
@login_required
def view_note(note_id):
    note = _get_my_note(note_id)
    return render_template("mynotes/editor.html", note=note, note_type=note.note_type, default_title="")


# ═══ 筆記 CRUD ═════════════════════════════════════════

@mynotes_bp.route("/api", methods=["GET"])
@login_required
def list_notes():
    q = (request.args.get("q") or "").strip()
    query = PrivateNote.query.filter_by(user_id=current_user.id)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(PrivateNote.title.ilike(like), PrivateNote.content.ilike(like)))
    notes = query.order_by(PrivateNote.updated_at.desc()).limit(300).all()
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
    data["items"] = [_item_json(i) for i in sorted(n.items, key=lambda x: x.order_index)] if n.note_type == "todo_checklist" else []
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
    items = sorted(n.items, key=lambda i: i.order_index)
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
    item = PrivateChecklistItem(
        note_id=n.id,
        order_index=max_order + 1,
        text=text,
        reminder_date=_parse_date(data.get("reminder_date")),
    )
    db.session.add(item)
    n.updated_at = datetime.utcnow()
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
    if "text" in data:
        item.text = (data.get("text") or "").strip()
    if "is_checked" in data:
        is_checked = bool(data.get("is_checked"))
        if is_checked and not item.is_checked:
            item.is_checked = True
            item.checked_at = datetime.utcnow()
        elif not is_checked and item.is_checked:
            item.is_checked = False
            item.checked_at = None
    if "reminder_date" in data:
        item.reminder_date = _parse_date(data.get("reminder_date"))
    n.updated_at = datetime.utcnow()
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
    from storage import upload_attachment, ALLOWED_CONTENT_TYPES, MAX_FILE_SIZE
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
    n.updated_at = datetime.utcnow()
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
    db.session.delete(att)
    n.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"status": "ok"})


# ═══ AI 摘要（僅 meeting）══════════════════════════════

_ai_tasks = {}


@mynotes_bp.route("/api/<int:note_id>/summarize", methods=["POST"])
@login_required
def summarize(note_id):
    from admin.routes import call_llm
    n = _get_my_note(note_id)
    if n.note_type != "meeting":
        return jsonify({"status": "error", "message": "僅會議紀錄可摘要"}), 400

    prompt = f"""# Role
你是一位專業的會議紀錄整理助理。

# Task
請根據下方會議原文整理出完整的結論摘要，要求：
1. **提煉關鍵決議**：每個決議一條列點，含負責人與期限（若原文提及）。
2. **行動項目（Action Items）**：條列誰要在什麼時間前完成什麼。
3. **討論重點**：無結論的重要討論也列出，避免遺漏。
4. **極度精簡**：刪除冗詞、重複內容。

# Output Style
使用 Markdown，結構如下：

## 📌 會議主題
（一行摘要）

## ✅ 決議
1. ...
2. ...

## 🎯 行動項目
| 負責人 | 事項 | 期限 |
|---|---|---|
| ... | ... | ... |

## 💬 討論重點
1. ...

# 會議原文
標題：{n.title}

{n.content or "（無內文）"}
"""
    task_id = uuid.uuid4().hex[:12]
    _ai_tasks[task_id] = {"status": "processing"}
    import gevent
    gevent.spawn(_run_summary, task_id, current_app._get_current_object(), prompt, n.id)
    return jsonify({"status": "accepted", "task_id": task_id})


def _run_summary(task_id, app, prompt, note_id):
    from admin.routes import call_llm
    with app.app_context():
        try:
            result = call_llm(prompt, max_tokens=8192)
            n = PrivateNote.query.get(note_id)
            if n:
                n.ai_summary = result
                n.updated_at = datetime.utcnow()
                db.session.commit()
            _ai_tasks[task_id] = {"status": "done", "result": result}
        except Exception as e:
            _ai_tasks[task_id] = {"status": "error", "message": str(e)}


@mynotes_bp.route("/api/ai/task/<task_id>", methods=["GET"])
@login_required
def get_ai_task(task_id):
    task = _ai_tasks.get(task_id)
    if not task:
        return jsonify({"status": "error", "message": "任務不存在"}), 404
    if task["status"] == "done":
        result = task["result"]
        del _ai_tasks[task_id]
        return jsonify({"status": "done", "result": result})
    if task["status"] == "error":
        msg = task["message"]
        del _ai_tasks[task_id]
        return jsonify({"status": "error", "message": msg})
    return jsonify({"status": "processing"})
