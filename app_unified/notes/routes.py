import uuid
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify, render_template, current_app
from flask_login import login_required, current_user
from extensions import db
from models import Note, Store, NoteLog, ChecklistItem, STATUS_CHOICES, PRIORITY_CHOICES
from admin.routes import call_llm

notes_bp = Blueprint("notes", __name__, url_prefix="/notes")


@notes_bp.before_request
def _gate_notes_feature():
    """模組開關：未啟用 notes 的使用者存取此 blueprint 任何路由皆回 404。
    super_admin 會在 user_has_feature 內 bypass。未登入者由 @login_required 另行處理。"""
    from features import user_has_feature
    if current_user.is_authenticated and not user_has_feature(current_user, "notes"):
        from flask import abort
        abort(404)


RANGE_DAYS = {"today": 0, "3d": 3, "7d": 7, "30d": 30}

_TW = timezone(timedelta(hours=8))

LOCK_TIMEOUT_MIN = 5


class _PyPagination:
    """給 keyword 搜尋用的簡易分頁 shim（加密後無法 SQL ilike）。"""
    def __init__(self, items, page, per_page):
        self.total = len(items)
        self.page = max(1, page)
        self.per_page = per_page
        self.pages = max(1, (self.total + per_page - 1) // per_page)
        start = (self.page - 1) * per_page
        self.items = items[start:start + per_page]
        self.has_prev = self.page > 1
        self.has_next = self.page < self.pages
        self.prev_num = self.page - 1 if self.has_prev else None
        self.next_num = self.page + 1 if self.has_next else None

    def iter_pages(self, left_edge=2, left_current=2, right_current=3, right_edge=2):
        last = 0
        for num in range(1, self.pages + 1):
            if (num <= left_edge or
                (self.page - left_current - 1 < num < self.page + right_current) or
                num > self.pages - right_edge):
                if last + 1 != num:
                    yield None
                yield num
                last = num


def _lock_status(note):
    """回傳 (is_locked_by_other, locker_user_or_None)"""
    if not note.locked_by or not note.locked_at:
        return False, None
    age_sec = (datetime.utcnow() - note.locked_at).total_seconds()
    if age_sec > LOCK_TIMEOUT_MIN * 60:
        return False, None  # 過期鎖視為無效
    if note.locked_by == current_user.id:
        return False, None  # 自己的鎖
    from models import User
    locker = User.query.get(note.locked_by)
    return True, locker


def _acquire_lock(note):
    note.locked_by = current_user.id
    note.locked_at = datetime.utcnow()


def _get_business_day_range():
    """回傳目前營業日的 (start, end)，以 UTC 表示（無 tzinfo）。"""
    now_tw = datetime.now(_TW)
    if now_tw.hour >= 8:
        start_tw = now_tw.replace(hour=8, minute=0, second=0, microsecond=0)
    else:
        start_tw = (now_tw - timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    end_tw = start_tw + timedelta(days=1)
    start_utc = start_tw.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_tw.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def _get_business_day_label():
    """回傳營業日的日期字串（YYYY-MM-DD），供顯示用。"""
    now_tw = datetime.now(_TW)
    if now_tw.hour >= 8:
        return now_tw.strftime("%Y-%m-%d")
    else:
        return (now_tw - timedelta(days=1)).strftime("%Y-%m-%d")


# 異步 AI 任務存儲（in-memory）
_ai_tasks = {}


def _get_stores():
    return [s.name for s in Store.query.order_by(Store.name).all()]


def _date_filter(query, range_param):
    days = RANGE_DAYS.get(range_param, 3)
    if days == 0:
        start_utc, end_utc = _get_business_day_range()
        return query.filter(Note.updated_at >= start_utc, Note.updated_at < end_utc)
    else:
        since = datetime.utcnow() - timedelta(days=days)
        return query.filter(Note.updated_at >= since)


@notes_bp.route("/")
@login_required
def index():
    try:
        from notes.cleanup import cleanup_resolved_notes
        cleanup_resolved_notes()
    except Exception:
        current_app.logger.exception("cleanup_resolved_notes failed")
    store_filter = request.args.get("store", "")
    status_filter = request.args.get("status", "")
    range_param = request.args.get("range", "")
    priority_filter = request.args.get("priority", "")
    keyword = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 20
    stores = _get_stores()

    query = Note.query

    # 店別範圍
    if current_user.is_super_admin():
        if store_filter in stores:
            query = query.filter_by(store=store_filter)
    elif current_user.is_admin():
        query = query.filter_by(store=current_user.store)
    else:
        query = query.filter_by(store=current_user.store)

    # 互斥篩選：關鍵字 > 優先權 > 狀態 > 日期範圍
    active_filter = None
    keyword_mode = False
    if keyword:
        active_filter = 'keyword'
        keyword_mode = True   # title/content 已加密，無法 SQL ilike → Python 端 filter
    elif priority_filter and priority_filter in PRIORITY_CHOICES:
        active_filter = 'priority'
        query = query.filter_by(priority=priority_filter)
    elif status_filter is not None and 'status' in request.args:
        active_filter = 'status'
        if status_filter in STATUS_CHOICES:
            query = query.filter_by(status=status_filter)
        # status='' (全部狀態) → 不加 filter，顯示全部
    else:
        active_filter = 'range'
        if not range_param:
            range_param = 'today'
        query = _date_filter(query, range_param)

    if keyword_mode:
        all_rows = query.order_by(Note.updated_at.desc()).all()
        kl = keyword.lower()
        filtered = [n for n in all_rows if kl in (n.title or "").lower() or kl in (n.content or "").lower()]
        pagination = _PyPagination(filtered, page=page, per_page=per_page)
    else:
        pagination = query.order_by(Note.updated_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

    return render_template("notes/index.html", notes=pagination.items, pagination=pagination,
                           stores=stores,
                           current_store=store_filter,
                           current_status=status_filter,
                           current_range=range_param,
                           current_priority=priority_filter,
                           current_keyword=keyword,
                           active_filter=active_filter,
                           status_choices=STATUS_CHOICES,
                           priority_choices=PRIORITY_CHOICES)


@notes_bp.route("/new", methods=["GET"])
@login_required
def new_note():
    stores = _get_stores()
    note_type = request.args.get("type", "note")
    if note_type not in ("note", "checklist"):
        note_type = "note"
    today = datetime.now(_TW).strftime("%-m/%-d")
    type_label = "確認表單" if note_type == "checklist" else "筆記"
    default_title = f"{today} {current_user.username}{type_label}"
    return render_template("notes/editor.html", note=None, stores=stores,
                           status_choices=STATUS_CHOICES, priority_choices=PRIORITY_CHOICES,
                           default_title=default_title, note_type=note_type)


@notes_bp.route("/api", methods=["GET"])
@login_required
def list_notes():
    store_filter = request.args.get("store", "")
    status_filter = request.args.get("status", "")
    range_param = request.args.get("range", "")
    priority_filter = request.args.get("priority", "")
    keyword = request.args.get("q", "").strip()
    stores = _get_stores()

    query = Note.query
    if current_user.is_super_admin():
        if store_filter in stores:
            query = query.filter_by(store=store_filter)
    elif current_user.is_admin():
        query = query.filter_by(store=current_user.store)
    else:
        query = query.filter_by(store=current_user.store)

    keyword_mode = False
    if keyword:
        keyword_mode = True   # title/content 加密 → Python 端 filter
    elif priority_filter and priority_filter in PRIORITY_CHOICES:
        query = query.filter_by(priority=priority_filter)
    elif 'status' in request.args:
        if status_filter in STATUS_CHOICES:
            query = query.filter_by(status=status_filter)
        # status='' (全部狀態) → 不加 filter
    else:
        if not range_param:
            range_param = 'today'
        query = _date_filter(query, range_param)

    notes = query.order_by(Note.updated_at.desc()).all()
    if keyword_mode:
        kl = keyword.lower()
        notes = [n for n in notes if kl in (n.title or "").lower() or kl in (n.content or "").lower()]

    return jsonify([{
        "id": n.id, "title": n.title, "content": n.content,
        "note_type": n.note_type or "note",
        "store": n.store, "status": n.status or "pending",
        "priority": n.priority or "medium",
        "author": n.display_author,
        "created_at": n.created_at.isoformat() if n.created_at else "",
        "updated_at": n.updated_at.isoformat() if n.updated_at else "",
    } for n in notes])


@notes_bp.route("/api", methods=["POST"])
@login_required
def create_note():
    data = request.get_json(silent=True) or {}
    stores = _get_stores()
    now = datetime.utcnow()
    if current_user.is_super_admin():
        store = data.get("store") if data.get("store") in stores else None
    elif current_user.is_admin():
        store = current_user.store
    else:
        store = current_user.store if current_user.store in stores else None
    status = data.get("status") if data.get("status") in STATUS_CHOICES else "pending"
    priority = data.get("priority") if data.get("priority") in PRIORITY_CHOICES else "medium"
    note_type = data.get("note_type") if data.get("note_type") in ("note", "checklist") else "note"
    note = Note(
        user_id=current_user.id,
        author_name=current_user.username,
        note_type=note_type,
        title=data.get("title", "未命名筆記"),
        content=data.get("content", ""),
        store=store,
        status=status,
        priority=priority,
        created_at=now,
        updated_at=now,
    )
    if status == "resolved":
        note.resolved_at = datetime.utcnow()
    db.session.add(note)
    db.session.commit()
    return jsonify({"status": "ok", "id": note.id}), 201


@notes_bp.route("/api/<int:note_id>", methods=["GET"])
@login_required
def get_note(note_id):
    if current_user.is_super_admin():
        note = Note.query.get_or_404(note_id)
    elif current_user.is_admin():
        note = Note.query.filter_by(id=note_id, store=current_user.store).first_or_404()
    else:
        note = Note.query.filter_by(id=note_id, store=current_user.store).first_or_404()
    updater = None
    if note.updated_by:
        from models import User
        u = User.query.get(note.updated_by)
        updater = u.username if u else None
    return jsonify({
        "id": note.id, "title": note.title, "content": note.content,
        "note_type": note.note_type or "note",
        "store": note.store, "status": note.status or "pending",
        "priority": note.priority or "medium",
        "ai_summary": note.ai_summary, "ai_outline": note.ai_outline,
        "updated_by": updater,
        "created_at": note.created_at.isoformat() if note.created_at else "",
        "updated_at": note.updated_at.isoformat() if note.updated_at else "",
    })


@notes_bp.route("/api/<int:note_id>", methods=["PUT"])
@login_required
def update_note(note_id):
    if current_user.is_super_admin():
        note = Note.query.get_or_404(note_id)
    elif current_user.is_admin():
        note = Note.query.filter_by(id=note_id, store=current_user.store).first_or_404()
    else:
        note = Note.query.filter_by(id=note_id, store=current_user.store).first_or_404()
    data = request.get_json(silent=True) or {}
    stores = _get_stores()

    diff_parts = []
    if "title" in data and data["title"] != note.title:
        diff_parts.append(f"標題: {note.title} → {data['title']}")
        note.title = data["title"]
    if "content" in data and data["content"] != note.content:
        from notes.diff_utils import compute_paragraph_diff, PARAGRAPH_DIFF_PREFIX
        pdiff = compute_paragraph_diff(note.content, data["content"])
        if pdiff:
            diff_parts.append(f"{PARAGRAPH_DIFF_PREFIX}{pdiff}")
        else:
            diff_parts.append(f"內容變更（全文）")
        note.content = data["content"]
    if "store" in data and current_user.is_super_admin():
        note.store = data["store"] if data["store"] in stores else None
    if "status" in data and data["status"] in STATUS_CHOICES:
        new_status = data["status"]
        if new_status != note.status:
            diff_parts.append(f"狀態: {note.status} → {new_status}")
            if new_status == "resolved" and note.status != "resolved":
                note.resolved_at = datetime.utcnow()
            elif note.status == "resolved" and new_status != "resolved":
                note.resolved_at = None
        note.status = new_status
    if "priority" in data and data["priority"] in PRIORITY_CHOICES:
        if data["priority"] != note.priority:
            diff_parts.append(f"優先度: {note.priority} → {data['priority']}")
        note.priority = data["priority"]

    # 編輯鎖檢查：如果被別人鎖定（非過期），拒絕
    is_locked, locker = _lock_status(note)
    if is_locked:
        return jsonify({"status": "error", "message": f"目前由 {locker.username if locker else '其他人'} 編輯中"}), 409

    note.updated_by = current_user.id
    note.updated_at = datetime.utcnow()
    # 儲存後釋放鎖
    note.locked_by = None
    note.locked_at = None
    db.session.flush()

    if diff_parts:
        log = NoteLog(
            note_id=note.id,
            note_title=note.title,
            user_id=current_user.id,
            action="edit",
            diff="; ".join(diff_parts),
        )
        db.session.add(log)

    db.session.commit()
    return jsonify({"status": "ok"})


@notes_bp.route("/api/<int:note_id>/lock/heartbeat", methods=["POST"])
@login_required
def heartbeat_lock(note_id):
    note = Note.query.get(note_id)
    if not note:
        return jsonify({"status": "error", "message": "筆記不存在"}), 404
    # 店別權限
    if not current_user.is_super_admin():
        if note.store and note.store != current_user.store:
            return jsonify({"status": "error", "message": "無權限"}), 403
    if note.locked_by == current_user.id:
        note.locked_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"status": "ok"})
    # 鎖已失效或被別人搶走
    is_locked, locker = _lock_status(note)
    if not is_locked:
        # 沒人持有，重新取得
        _acquire_lock(note)
        db.session.commit()
        return jsonify({"status": "ok", "reacquired": True})
    return jsonify({"status": "error", "message": f"鎖已由 {locker.username if locker else '其他人'} 持有"}), 409


@notes_bp.route("/api/<int:note_id>/lock/release", methods=["POST"])
@login_required
def release_lock_endpoint(note_id):
    note = Note.query.get(note_id)
    if not note:
        return jsonify({"status": "error", "message": "筆記不存在"}), 404
    # 店別權限檢查
    if not current_user.is_super_admin():
        if note.store and note.store != current_user.store:
            return jsonify({"status": "error", "message": "無權限"}), 403
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force"))
    if force:
        # 強制釋放需要 admin/super_admin
        if not (current_user.is_admin() or current_user.is_super_admin()):
            return jsonify({"status": "error", "message": "僅 admin 可強制釋放"}), 403
        note.locked_by = None
        note.locked_at = None
        db.session.commit()
        return jsonify({"status": "ok", "forced": True})
    # 一般釋放：只能釋放自己的鎖
    if note.locked_by == current_user.id:
        note.locked_by = None
        note.locked_at = None
        db.session.commit()
        return jsonify({"status": "ok"})
    return jsonify({"status": "ok", "skipped": True})


@notes_bp.route("/api/<int:note_id>", methods=["DELETE"])
@login_required
def delete_note(note_id):
    from storage import delete_attachment as r2_delete
    if current_user.is_super_admin():
        note = Note.query.get_or_404(note_id)
    elif current_user.is_admin():
        note = Note.query.filter_by(id=note_id, store=current_user.store).first_or_404()
    else:
        note = Note.query.filter_by(id=note_id, store=current_user.store).first_or_404()

    for att in note.attachments:
        r2_delete(att.object_key)

    log = NoteLog(
        note_id=note.id,
        note_title=note.title,
        user_id=current_user.id,
        action="delete",
    )
    db.session.add(log)
    db.session.delete(note)
    db.session.commit()
    return jsonify({"status": "ok"})


MANAGER_PROMPT = """# Role
你是一位專業的店務連鎖店總管，負責將各分店或各部門的雜亂筆記，快速整理成給老闆看的「每日彙整表」。

# Task
請將輸入的所有原始文字進行分類摘要。要求：
1. **不准遺漏**：每個提到的事件都必須轉換成一條列點。
2. **極度精簡**：刪除所有口語、形容詞、抱怨與重複資訊，僅保留「主語 + 動作 + 結果」。
3. **模糊分類**：若某事項不確定分類，優先放入【店務】，若完全無關則放入【雜項】。

# Categories
- 【人事】：請假、遲到、排班、人力短缺、獎懲。
- 【修繕】：設備故障、漏水、燈泡更換、報修進度。
- 【店務】：進貨、庫存、營收、活動、顧客反應、環境衛生。
- 【雜項】：不屬於上述三類的所有事項。

# Output Style
依「類別」分組輸出，每個類別用 `## 類別名` 作為標題，
該類別下方所有事項用數字列表（1. 2. 3. ...）依序排列。
沒有事項的類別請直接省略，不要輸出空標題。

# 去重規則（極度重要）
- 若多篇筆記提到同一事件（如 A 寫「冰箱壞了」、B 寫「冰箱還沒修」），
  請合併成**一條**列點，不要每篇各列一次。
- 內容高度重疊的事項請合併；互補資訊併入同一條後面。
- **絕對禁止**在輸出中連續重複同一句話或同一條目。
- **絕對禁止**同一事件在同一類別下列出兩次以上。每個事項只能出現一次。

# 禁止事項
- 禁止憑空生成輸入資料中不存在的事項。
- 若輸入沒有對應類別的事項，該類別省略，不得虛構。
- 禁止為了填滿輸出而重複貼相同列點。

範例：
## 人事
1. 小明午班請假：已協調小華代班。
2. 阿華遲到 30 分鐘，已口頭告誡。

## 修繕
1. 廚房冰箱漏水：已請廠商週三來修。
"""


def _run_ai_task(task_id, app, prompt, max_tokens, note_id=None, field=None):
    """在 gevent greenlet 中執行 LLM 呼叫"""
    with app.app_context():
        try:
            result = call_llm(prompt, max_tokens=max_tokens)
            if note_id and field:
                note = Note.query.get(note_id)
                if note:
                    setattr(note, field, result)
                    note.updated_at = datetime.utcnow()
                    db.session.commit()
            _ai_tasks[task_id] = {"status": "done", "result": result}
        except Exception as e:
            _ai_tasks[task_id] = {"status": "error", "message": str(e)}


@notes_bp.route("/ai/task/<task_id>", methods=["GET"])
@login_required
def get_ai_task(task_id):
    """輪詢 AI 任務狀態"""
    if not current_user.is_admin():
        return jsonify({"status": "error", "message": "僅限管理員"}), 403
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


def _note_source_label(n):
    return f"【檔名：{n.store or '未分店'}店_{n.author_name or (n.author.username if n.author else '?')}_{n.updated_at.strftime('%m%d') if n.updated_at else ''}_{n.id}】"


def _format_note_for_ai(n):
    """一般筆記的文字區塊（不含 checklist 項目）"""
    return f"{_note_source_label(n)}\n{n.title}\n{n.content or ''}"


def _format_checklist_for_ai(n, since=None):
    """確認表單區塊：標題 + 已完成 / 未完成項目。

    since: datetime（UTC, naive）— 若給定，已完成項目只列 updated_at >= since
           的（避免把好幾天前完成的也帶進來）；未完成項目永遠全部列出。
    """
    items = sorted(n.checklist_items or [], key=lambda i: (i.updated_at or i.created_at), reverse=True)
    total = len(items)
    done_all = [i for i in items if i.is_checked]
    if since is not None:
        done = [i for i in done_all if (i.updated_at or i.created_at) >= since]
    else:
        done = done_all
    undone = [i for i in items if not i.is_checked]
    progress_line = f"完成進度：{len(done_all)}/{total}"
    if since is not None:
        progress_line += f"（區間內完成 {len(done)} 項）"
    lines = [_note_source_label(n), f"表單標題：{n.title}", progress_line]
    if n.content:
        lines.append(f"備註：{n.content}")
    if done:
        label = "✅ 已完成（區間內）：" if since is not None else "✅ 已完成："
        lines.append(label)
        for i in done:
            who = i.checked_by_name or "?"
            lines.append(f"- [{who}] {i.text}")
    if undone:
        author = n.author_name or (n.author.username if n.author else "?")
        lines.append(f"❌ 未完成（表單建立者：{author}）：")
        for i in undone:
            lines.append(f"- [{author}] {i.text}")
    return "\n".join(lines)


def _split_notes(notes):
    """分成一般筆記 / 確認表單兩組"""
    regular = [n for n in notes if getattr(n, "note_type", "note") != "checklist"]
    checklists = [n for n in notes if getattr(n, "note_type", "note") == "checklist"]
    return regular, checklists


def _build_ai_sections(notes, header_prefix="", since=None):
    """組出傳給 AI 的 prompt 後段（# 待整理... 以下），回傳 (data_block, has_checklist)。

    since: datetime — 傳給 _format_checklist_for_ai 過濾「已完成項目」。
    """
    regular, checklists = _split_notes(notes)
    blocks = []
    if regular:
        body = "\n\n---\n\n".join(_format_note_for_ai(n) for n in regular)
        blocks.append(f"# 一般筆記（{header_prefix}共 {len(regular)} 筆）\n\n{body}")
    if checklists:
        body = "\n\n---\n\n".join(_format_checklist_for_ai(n, since=since) for n in checklists)
        blocks.append(f"# 確認表單（{header_prefix}共 {len(checklists)} 筆）\n\n{body}")
    return "\n\n".join(blocks), bool(checklists), len(regular), len(checklists)


def _build_output_structure_prompt(has_regular, has_checklist):
    """根據實際資料動態產生輸出結構指示，沒資料的 section 不出現"""
    if not has_checklist:
        # 純筆記：維持原本 4 分類輸出（Output Style 已定義）
        return ""

    parts = ["\n# 輸出結構"]
    if has_regular:
        parts.append(
            "請將輸出分為兩大區塊（用 Markdown `#` 標題）：\n\n"
            "# 筆記彙整\n"
            "針對上方「一般筆記」依 Output Style 的規則（## 類別 + 數字列表）輸出。\n\n"
            "# 確認表單彙整"
        )
    else:
        # 只有表單：禁止出現「筆記彙整」段
        parts.append(
            "**本次沒有一般筆記資料，禁止輸出「筆記彙整」段，也禁止將表單內容當作筆記分類。**\n\n"
            "請只輸出以下區塊：\n\n"
            "# 確認表單彙整"
        )

    parts.append(
        "針對上方「確認表單」做整理，分為以下兩小節：\n\n"
        "## ✅ 已完成項目\n"
        "依「完成者」分組：同一個人的已完成項目合併到同一組。\n"
        "每組以 `### 完成者名字` 作為小標題，下方用 1. 2. 3. 列出該人完成的事項。\n"
        "**不要輸出任何時間資訊**，只需呈現完成者與事項。\n"
        "若沒有任何已完成項目，此小節輸出「（無）」。\n\n"
        "範例：\n"
        "### 小明\n"
        "1. 更換廚房燈泡\n"
        "2. 清潔冷凍庫\n\n"
        "## ❌ 未完成項目\n"
        "依「表單建立者」分組：同一建立者名下的未完成項目合併到同一組。\n"
        "每組以 `### 建立者名字` 作為小標題，下方用 1. 2. 3. 列出項目。\n"
        "同一事項若重複出現請合併。若沒有任何未完成項目，此小節輸出「（無）」。\n\n"
        "範例：\n"
        "### 小明\n"
        "1. 盤點後場\n"
        "2. 對帳\n\n"
        "### 小華\n"
        "1. 收單"
    )
    return "\n".join(parts) + "\n"


def _build_all_content(store_name=None):
    """收集同店所有筆記，合併成 all_content（舊介面，供 summarize 使用）"""
    query = Note.query
    if store_name:
        query = query.filter_by(store=store_name)
    notes = query.order_by(Note.updated_at.desc()).all()
    data_block, has_cl, nreg, ncl = _build_ai_sections(notes)
    return data_block, len(notes), has_cl


@notes_bp.route("/api/<int:note_id>/summarize", methods=["POST"])
@login_required
def summarize(note_id):
    if not current_user.is_admin():
        return jsonify({"status": "error", "message": "僅限管理員"}), 403
    note = Note.query.get_or_404(note_id)

    # 收集同店所有筆記合併，一次呼叫 API 做全體分類摘要
    data_block, count, has_cl = _build_all_content(note.store)
    notes_for_count = Note.query.filter_by(store=note.store).all() if note.store else Note.query.all()
    has_reg = any(getattr(n, "note_type", "note") != "checklist" for n in notes_for_count)
    prompt = (
        MANAGER_PROMPT
        + _build_output_structure_prompt(has_reg, has_cl)
        + f"\n# 待整理資料（{note.store or '未分店'}店，共 {count} 筆）\n\n{data_block}"
    )
    task_id = uuid.uuid4().hex[:12]
    _ai_tasks[task_id] = {"status": "processing"}
    import gevent
    gevent.spawn(_run_ai_task, task_id, current_app._get_current_object(),
                 prompt, 8192, note_id, "ai_summary")
    return jsonify({"status": "accepted", "task_id": task_id})


@notes_bp.route("/api/<int:note_id>/outline", methods=["POST"])
@login_required
def outline(note_id):
    if not current_user.is_admin():
        return jsonify({"status": "error", "message": "僅限管理員"}), 403
    note = Note.query.get_or_404(note_id)

    # 單筆大綱：只處理這一篇（checklist 也用 outline 結構直接列項目）
    is_cl = getattr(note, "note_type", "note") == "checklist"
    body = _format_checklist_for_ai(note) if is_cl else _format_note_for_ai(note)
    prompt = (
        MANAGER_PROMPT
        + "\n# 額外要求\n請將此筆記整理成條列式大綱，保留所有細節。"
        + ("若為確認表單，請分「已完成」「未完成」兩段整理。\n" if is_cl else "\n")
        + f"\n# 待整理筆記\n\n{body}"
    )
    task_id = uuid.uuid4().hex[:12]
    _ai_tasks[task_id] = {"status": "processing"}
    import gevent
    gevent.spawn(_run_ai_task, task_id, current_app._get_current_object(),
                 prompt, 8192, note_id, "ai_outline")
    return jsonify({"status": "accepted", "task_id": task_id})


@notes_bp.route("/ai/summary", methods=["POST"])
@login_required
def notes_ai_summary():
    if not current_user.is_admin():
        return jsonify({"status": "error", "message": "僅限管理員"}), 403
    data = request.get_json(silent=True) or {}
    store = data.get("store", "all")
    # Admin 只能摘要自己的店
    if current_user.is_admin() and not current_user.is_super_admin():
        store = current_user.store
    days = int(data.get("days", 7))
    if days == 1:
        start_utc, end_utc = _get_business_day_range()
    else:
        start_utc = datetime.utcnow() - timedelta(days=days)
        end_utc = None

    valid_stores = [s.name for s in Store.query.all()]
    query = Note.query.filter(Note.updated_at >= start_utc)
    if end_utc:
        query = query.filter(Note.updated_at < end_utc)
    if store != "all" and store in valid_stores:
        query = query.filter_by(store=store)
    notes = query.order_by(Note.store, Note.updated_at.desc()).all()

    if not notes:
        return jsonify({"status": "ok", "summary": "（近期無筆記）"})

    # 拆成一般筆記 / 確認表單兩段（已完成項目只列區間內 updated 的）
    data_block, has_cl, nreg, ncl = _build_ai_sections(notes, since=start_utc)

    store_label = f"「{store}店」" if store != "all" else "全店"
    extra = ""
    if store == "all":
        extra = "\n# 額外要求\n請在輸出最前面依「店別」分組，每組內再依下述結構排列。\n"
    else:
        extra = f"\n# 額外要求\n請在輸出最前面標明這是「{store}店」的彙整。\n"

    prompt = (
        MANAGER_PROMPT + extra
        + _build_output_structure_prompt(nreg > 0, has_cl)
        + f"\n# 待整理資料（{store_label}近 {days} 天，一般筆記 {nreg} 筆 / 確認表單 {ncl} 筆）\n\n{data_block}"
    )

    task_id = uuid.uuid4().hex[:12]
    _ai_tasks[task_id] = {"status": "processing"}
    import gevent
    gevent.spawn(_run_ai_task, task_id, current_app._get_current_object(),
                 prompt, 8192)
    return jsonify({"status": "accepted", "task_id": task_id})


# ── Checklist Item CRUD ────────────────────────────────

def _get_note_for_edit(note_id):
    """取得筆記（檢查權限）"""
    if current_user.is_super_admin():
        return Note.query.get(note_id)
    else:
        return Note.query.filter_by(id=note_id, store=current_user.store).first()


def _checklist_item_json(item):
    from storage import get_signed_url
    return {
        "id": item.id,
        "note_id": item.note_id,
        "order_index": item.order_index,
        "text": item.text,
        "is_checked": item.is_checked,
        "checked_at": item.checked_at.isoformat() if item.checked_at else None,
        "checked_by_name": item.checked_by_name or (item.checker.username if item.checker else None),
        "created_at": item.created_at.isoformat() if item.created_at else "",
        "attachments": [{
            "id": a.id,
            "filename": a.filename,
            "content_type": a.content_type,
            "file_size": a.file_size,
            "url": get_signed_url(a.object_key),
        } for a in item.attachments],
    }


@notes_bp.route("/api/<int:note_id>/items", methods=["GET"])
@login_required
def list_checklist_items(note_id):
    note = _get_note_for_edit(note_id)
    if not note:
        return jsonify({"status": "error", "message": "筆記不存在或無權限"}), 404
    # 排序：updated_at 新→舊，相同時 id 大→小
    items = ChecklistItem.query.filter_by(note_id=note_id).order_by(
        ChecklistItem.updated_at.desc(), ChecklistItem.id.desc()
    ).all()
    return jsonify({"status": "ok", "items": [_checklist_item_json(i) for i in items]})


@notes_bp.route("/api/<int:note_id>/items", methods=["POST"])
@login_required
def create_checklist_item(note_id):
    note = _get_note_for_edit(note_id)
    if not note:
        return jsonify({"status": "error", "message": "筆記不存在或無權限"}), 404
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    # order_index 仍保留（向下相容），但實際排序用 updated_at
    max_order = db.session.query(db.func.max(ChecklistItem.order_index)).filter_by(note_id=note_id).scalar() or 0
    now = datetime.utcnow()
    item = ChecklistItem(
        note_id=note_id,
        order_index=max_order + 1,
        text=text,
        is_checked=False,
        updated_at=now,
    )
    db.session.add(item)
    note.updated_at = now
    note.updated_by = current_user.id
    db.session.commit()
    return jsonify({"status": "ok", "item": _checklist_item_json(item)}), 201


@notes_bp.route("/api/items/<int:item_id>", methods=["PATCH"])
@login_required
def update_checklist_item(item_id):
    item = ChecklistItem.query.get(item_id)
    if not item:
        return jsonify({"status": "error", "message": "項目不存在"}), 404
    note = _get_note_for_edit(item.note_id)
    if not note:
        return jsonify({"status": "error", "message": "無權限"}), 403
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
            item.checked_by = current_user.id
            item.checked_by_name = current_user.username
            changed = True
        elif not is_checked and item.is_checked:
            item.is_checked = False
            item.checked_at = None
            item.checked_by = None
            item.checked_by_name = None
            changed = True
    # 任何修改（含勾選/取消勾選）都 bump updated_at
    # → 但前端勾選後不重新 fetch 整個 list，所以當下不會跳；
    #   下次重進頁面時才會看到該項目排到最上面
    if changed:
        item.updated_at = datetime.utcnow()
    note.updated_at = datetime.utcnow()
    note.updated_by = current_user.id
    db.session.commit()
    return jsonify({"status": "ok", "item": _checklist_item_json(item)})


@notes_bp.route("/api/items/<int:item_id>", methods=["DELETE"])
@login_required
def delete_checklist_item(item_id):
    from storage import delete_attachment
    item = ChecklistItem.query.get(item_id)
    if not item:
        return jsonify({"status": "error", "message": "項目不存在"}), 404
    note = _get_note_for_edit(item.note_id)
    if not note:
        return jsonify({"status": "error", "message": "無權限"}), 403
    # 刪 R2 上的附件
    for a in list(item.attachments):
        try:
            delete_attachment(a.object_key)
        except Exception:
            pass
    db.session.delete(item)
    note.updated_at = datetime.utcnow()
    note.updated_by = current_user.id
    db.session.commit()
    return jsonify({"status": "ok"})


@notes_bp.route("/api/attachments/upload", methods=["POST"])
@login_required
def upload_attachment_api():
    from models import NoteAttachment
    from storage import upload_attachment, get_signed_url, ALLOWED_CONTENT_TYPES, MAX_FILE_SIZE

    note_id = request.form.get("note_id", type=int)
    item_id = request.form.get("item_id", type=int)
    if not note_id:
        return jsonify({"status": "error", "message": "缺少 note_id"}), 400

    if current_user.is_super_admin():
        note = Note.query.get(note_id)
    elif current_user.is_admin():
        note = Note.query.filter_by(id=note_id, store=current_user.store).first()
    else:
        note = Note.query.filter_by(id=note_id, store=current_user.store).first()
    if not note:
        return jsonify({"status": "error", "message": "筆記不存在或無權限"}), 404

    # 驗證 item 屬於這個 note
    if item_id:
        item = ChecklistItem.query.filter_by(id=item_id, note_id=note_id).first()
        if not item:
            return jsonify({"status": "error", "message": "項目不存在"}), 404

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"status": "error", "message": "未選擇檔案"}), 400

    content_type = file.content_type or ''
    if content_type not in ALLOWED_CONTENT_TYPES:
        return jsonify({"status": "error", "message": "不支援的檔案格式"}), 400

    file_bytes = file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        return jsonify({"status": "error", "message": "檔案超過 50MB 上限"}), 400

    object_key = upload_attachment(file_bytes, note_id, file.filename, content_type)
    if not object_key:
        return jsonify({"status": "error", "message": "儲存服務未設定"}), 503

    attachment = NoteAttachment(
        note_id=note_id,
        checklist_item_id=item_id,
        user_id=current_user.id,
        object_key=object_key,
        filename=file.filename,
        content_type=content_type,
        file_size=len(file_bytes),
    )
    db.session.add(attachment)
    # 若附件綁到 checklist 項目，bump 該項目 updated_at（讓它跳到最上面）
    if item_id:
        item = ChecklistItem.query.get(item_id)
        if item:
            item.updated_at = datetime.utcnow()
    db.session.commit()

    url = get_signed_url(object_key)
    return jsonify({
        "status": "ok",
        "attachment": {
            "id": attachment.id,
            "filename": attachment.filename,
            "content_type": attachment.content_type,
            "file_size": attachment.file_size,
            "url": url,
        }
    }), 201


@notes_bp.route("/api/attachments", methods=["GET"])
@login_required
def list_attachments():
    from models import NoteAttachment
    from storage import get_signed_url

    note_id = request.args.get("note_id", type=int)
    if not note_id:
        return jsonify({"status": "error", "message": "缺少 note_id"}), 400

    if current_user.is_super_admin():
        note = Note.query.get(note_id)
    elif current_user.is_admin():
        note = Note.query.filter_by(id=note_id, store=current_user.store).first()
    else:
        note = Note.query.filter_by(id=note_id, store=current_user.store).first()
    if not note:
        return jsonify({"status": "error", "message": "筆記不存在或無權限"}), 404

    # 只列出直接屬於筆記的附件（排除屬於 checklist 項目的）
    attachments = NoteAttachment.query.filter_by(note_id=note_id, checklist_item_id=None).order_by(NoteAttachment.created_at).all()
    result = []
    for a in attachments:
        url = get_signed_url(a.object_key)
        result.append({
            "id": a.id,
            "filename": a.filename,
            "content_type": a.content_type,
            "file_size": a.file_size,
            "url": url,
            "uploader": a.uploader.username if a.uploader else "",
            "created_at": a.created_at.isoformat() if a.created_at else "",
        })
    return jsonify({"status": "ok", "attachments": result})


@notes_bp.route("/api/attachments/<int:attachment_id>", methods=["DELETE"])
@login_required
def delete_attachment_api(attachment_id):
    from models import NoteAttachment
    from storage import delete_attachment

    attachment = NoteAttachment.query.get(attachment_id)
    if not attachment:
        return jsonify({"status": "error", "message": "附件不存在"}), 404

    note = Note.query.get(attachment.note_id)
    if not note:
        return jsonify({"status": "error", "message": "筆記不存在"}), 404

    can_delete = False
    if current_user.is_super_admin():
        can_delete = True
    elif note.store == current_user.store:
        can_delete = True

    if not can_delete:
        return jsonify({"status": "error", "message": "無權限刪除"}), 403

    delete_attachment(attachment.object_key)
    item_id = attachment.checklist_item_id
    db.session.delete(attachment)
    # 若是 checklist 項目附件，bump 項目 updated_at
    if item_id:
        item = ChecklistItem.query.get(item_id)
        if item:
            item.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"status": "ok"})


@notes_bp.route("/<int:note_id>")
@login_required
def edit_note(note_id):
    if current_user.is_super_admin():
        note = Note.query.get_or_404(note_id)
    elif current_user.is_admin():
        note = Note.query.filter_by(id=note_id, store=current_user.store).first_or_404()
    else:
        note = Note.query.filter_by(id=note_id, store=current_user.store).first_or_404()
    stores = _get_stores()
    # 編輯鎖
    is_locked, locker = _lock_status(note)
    if not is_locked:
        _acquire_lock(note)
        db.session.commit()
    return render_template("notes/editor.html", note=note, stores=stores,
                           status_choices=STATUS_CHOICES, priority_choices=PRIORITY_CHOICES,
                           is_readonly=is_locked,
                           locker_name=locker.username if locker else None)
