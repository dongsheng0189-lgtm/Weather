import uuid
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify, render_template, current_app, session
from flask_login import login_required, current_user
from extensions import db
from models import Note, Store, NoteLog, ChecklistItem, STATUS_CHOICES, CATEGORY_CHOICES, CATEGORY_LABELS, CATEGORY_CREATABLE, SHIFT_CHOICES
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


def _note_matches(n, kl):
    """搜尋比對：title / content / checklist 項目文字"""
    if kl in (n.title or "").lower() or kl in (n.content or "").lower():
        return True
    for item in (n.checklist_items or []):
        if kl in (item.text or "").lower():
            return True
    return False


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


LOCK_TTL_SEC = 5 * 60  # 鎖過期：5 分鐘無心跳即可被搶


def _ensure_lock_for_mutation(note, user_id):
    """所有 mutation API 進入前呼叫此 helper。

    回傳 (ok: bool, locker: User|None)
    - ok=True, locker=None: 已 acquire（自己原本就持鎖、或剛剛搶到）
    - ok=False, locker=<User>: 別人持有未過期鎖，呼叫端應回 409
    """
    from models import User
    if not note.locked_by or not note.locked_at:
        # 沒人持鎖
        note.locked_by = user_id
        note.locked_at = datetime.utcnow()
        return True, None
    if note.locked_by == user_id:
        # 自己持鎖：refresh timestamp（等同心跳）
        note.locked_at = datetime.utcnow()
        return True, None
    age_sec = (datetime.utcnow() - note.locked_at).total_seconds()
    if age_sec > LOCK_TTL_SEC:
        # 過期鎖搶過來
        note.locked_by = user_id
        note.locked_at = datetime.utcnow()
        return True, None
    # 別人持鎖未過期
    locker = User.query.get(note.locked_by)
    return False, locker


def _get_business_day_range(offset_days=0):
    """回傳營業日的 (start, end)，以 UTC 表示（無 tzinfo）。

    offset_days=0  → 目前營業日（8 點起算）
    offset_days=-1 → 昨天的營業日（再退一天）
    """
    now_tw = datetime.now(_TW)
    if now_tw.hour >= 8:
        start_tw = now_tw.replace(hour=8, minute=0, second=0, microsecond=0)
    else:
        start_tw = (now_tw - timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    start_tw = start_tw + timedelta(days=offset_days)
    end_tw = start_tw + timedelta(days=1)
    start_utc = start_tw.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_tw.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def _get_business_day_labels():
    """AI 摘要 toggle 按鈕顯示用：日曆日的今天/昨天 M/D。
    範圍邏輯（08:00 分界）仍由 _get_business_day_range 處理，按鈕只負責直覺顯示。"""
    now_tw = datetime.now(_TW)
    yesterday = now_tw - timedelta(days=1)
    return now_tw.strftime("%-m/%-d"), yesterday.strftime("%-m/%-d")


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
    category_filter = request.args.get("category", "")
    shift_filter = request.args.get("shift", "none")  # default 'none' (= NULL)
    keyword = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 20
    stores = _get_stores()

    # 排序偏好：URL 帶就更新 session、套用；URL 沒帶 → 讀 session，否則預設 updated
    sort_param = request.args.get("sort", "")
    if sort_param in ("updated", "created"):
        session["notes_sort"] = sort_param
        current_sort = sort_param
    else:
        current_sort = session.get("notes_sort", "updated")
    sort_order = Note.created_at.desc() if current_sort == "created" else Note.updated_at.desc()

    query = Note.query

    # 店別範圍（不變）
    if current_user.is_super_admin():
        if store_filter in stores:
            query = query.filter_by(store=store_filter)
    elif current_user.is_admin():
        query = query.filter_by(store=current_user.store)
    else:
        query = query.filter_by(store=current_user.store)

    # keyword 互斥：有 keyword 時忽略其他篩選
    keyword_mode = False
    active_filter = None
    if keyword:
        active_filter = 'keyword'
        keyword_mode = True
    else:
        # AND 組合：category + status + range
        active_filter = 'combined'
        if category_filter in CATEGORY_CHOICES:
            query = query.filter_by(category=category_filter)
        # shift 篩選：'A'/'B'/'N' 才過濾；'none'/未帶/不合法 = 不過濾(顯示全部)
        if shift_filter in SHIFT_CHOICES:
            query = query.filter_by(shift=shift_filter)
        if status_filter in STATUS_CHOICES:
            query = query.filter_by(status=status_filter)
        if not range_param:
            range_param = 'today'
        query = _date_filter(query, range_param)

    if keyword_mode:
        all_rows = query.order_by(sort_order).all()
        kl = keyword.lower()
        filtered = [n for n in all_rows if _note_matches(n, kl)]
        pagination = _PyPagination(filtered, page=page, per_page=per_page)
    else:
        pagination = query.order_by(sort_order).paginate(page=page, per_page=per_page, error_out=False)

    today_label, yesterday_label = _get_business_day_labels()
    return render_template("notes/index.html", notes=pagination.items, pagination=pagination,
                           stores=stores,
                           current_store=store_filter,
                           current_status=status_filter,
                           current_range=range_param,
                           current_category=category_filter,
                           current_shift=shift_filter,
                           current_keyword=keyword,
                           current_sort=current_sort,
                           active_filter=active_filter,
                           status_choices=STATUS_CHOICES,
                           category_choices=CATEGORY_CHOICES,
                           category_labels=CATEGORY_LABELS,
                           shift_choices=SHIFT_CHOICES,
                           ai_today_label=today_label,
                           ai_yesterday_label=yesterday_label)


@notes_bp.route("/new", methods=["GET"])
@login_required
def new_note():
    stores = _get_stores()
    note_type = request.args.get("type", "note")
    if note_type not in ("note", "checklist"):
        note_type = "note"
    category = request.args.get("category", "misc")
    if category not in CATEGORY_CREATABLE:
        category = "misc"
    # shift: 'A'/'B'/'N' or 'none' (= NULL)；其他值一律回退成 None
    shift_param = request.args.get("shift", "none")
    shift = shift_param if shift_param in SHIFT_CHOICES else None

    today = datetime.now(_TW).strftime("%-m/%-d")
    # 統一格式：「{date} {shift}{label}」例如 4/30 員工、4/30 A機台、4/30 雜項
    shift_glue = f"{shift}" if shift else ""
    default_title = f"{today} {shift_glue}{CATEGORY_LABELS[category]}"

    return render_template("notes/editor.html", note=None, stores=stores,
                           status_choices=STATUS_CHOICES,
                           category_choices=CATEGORY_CHOICES,
                           category_labels=CATEGORY_LABELS,
                           default_title=default_title,
                           note_type=note_type,
                           default_category=category,
                           default_shift=shift)


@notes_bp.route("/api", methods=["GET"])
@login_required
def list_notes():
    store_filter = request.args.get("store", "")
    status_filter = request.args.get("status", "")
    range_param = request.args.get("range", "")
    category_filter = request.args.get("category", "")
    shift_filter = request.args.get("shift", "")  # API 不強制 default，未帶則不過濾
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
    elif category_filter and category_filter in CATEGORY_CHOICES:
        query = query.filter_by(category=category_filter)
    elif shift_filter and shift_filter in SHIFT_CHOICES:
        query = query.filter_by(shift=shift_filter)
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
        notes = [n for n in notes if _note_matches(n, kl)]

    return jsonify([{
        "id": n.id, "title": n.title, "content": n.content,
        "note_type": n.note_type or "note",
        "store": n.store, "status": n.status or "pending",
        "category": n.category or "misc",
        "shift": n.shift,
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
    category = data.get("category") if data.get("category") in CATEGORY_CHOICES else "misc"
    is_ai_summary = data.get("is_ai_summary") is True
    note_type = data.get("note_type") if data.get("note_type") in ("note", "checklist") else "note"
    shift = data.get("shift") if data.get("shift") in SHIFT_CHOICES else None
    note = Note(
        user_id=current_user.id,
        author_name=current_user.username,
        note_type=note_type,
        title=data.get("title", "未命名筆記"),
        content=data.get("content", ""),
        store=store,
        status=status,
        category=category,
        shift=shift,
        is_ai_summary=is_ai_summary,
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
        "category": note.category or "misc",
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
    if "category" in data and data["category"] in CATEGORY_CHOICES:
        if data["category"] != note.category:
            diff_parts.append(f"類別: {note.category} → {data['category']}")
        note.category = data["category"]

    # 編輯鎖：lazy acquire（沒鎖就 acquire；自己持鎖通過；別人持鎖 409）
    ok, locker = _ensure_lock_for_mutation(note, current_user.id)
    if not ok:
        return jsonify({
            "status": "error",
            "message": f"目前由 {locker.username if locker else '其他人'} 編輯中"
        }), 409

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
2. **保留關鍵資訊**：每條必須保留人名（員工名、顧客名），刪除口語、形容詞、抱怨，保留「誰 + 做了什麼 + 結果」。
3. **依分類輸出**：依筆記原始分類（員工/客人/機台/雜項）放入對應段落；無法判斷的一律放入【雜項】。

# Categories（4 類）
- 【員工】：員工請假、排班、人事、獎懲、員工互動、人力短缺。
- 【客人】：客訴、顧客反應、會員、客人需求、消費。
- 【機台】：機台故障、保養、操作、設備修繕、報修進度、維修紀錄。
- 【雜項】：不屬於上述三類的所有事項（含進貨、庫存、營收、活動、衛生等店務雜事）。

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
## 員工
1. 小明午班請假：已協調小華代班。
2. 阿華遲到 30 分鐘，已口頭告誡。

## 機台
1. 廚房冰箱漏水：已請廠商週三來修。

# 結尾（必須遵守）
整理完所有內容後，在最後一行輸出：「--- 摘要整理結束 ---」
"""


def _run_split_summary(task_id, app, notes, since, until, store_label, range_label, extra, inrange_label="本區間"):
    """資料量大時，拆成「筆記」「確認表單」兩批分別呼叫 LLM 再合併結果。"""
    with app.app_context():
        try:
            regular, checklists = _split_notes(notes)
            parts = []

            if regular:
                body = _build_categorized_notes(regular, since=since, until=until, inrange_label=inrange_label)
                data = (
                    f"# ===== 一般筆記 開始 =====（共 {len(regular)} 筆，已依 category 預分類 + 區間/歷史分段）\n\n"
                    f"{body}\n\n"
                    f"# ===== 一般筆記 結束 ====="
                )
                prompt = (
                    MANAGER_PROMPT + extra
                    + _build_output_structure_prompt(True, False)
                    + f"\n# 待整理資料（{store_label}{range_label}，一般筆記 {len(regular)} 筆）\n\n{data}"
                )
                parts.append("# 筆記彙整\n\n" + call_llm(prompt, max_tokens=65536))

            if checklists:
                cl_block, _, _, ncl = _build_ai_sections(
                    [n for n in notes if getattr(n, "note_type", "note") == "checklist"],
                    since=since, until=until, inrange_label=inrange_label,
                )
                prompt = (
                    MANAGER_PROMPT + extra
                    + _build_output_structure_prompt(False, True)
                    + f"\n# 待整理資料（{store_label}{range_label}，確認表單 {ncl} 筆）\n\n{cl_block}"
                )
                parts.append("# 確認表單彙整\n\n" + call_llm(prompt, max_tokens=65536))

            result = "\n\n".join(parts) + "\n\n--- 摘要整理結束 ---"
            _ai_tasks[task_id] = {"status": "done", "result": result}
        except Exception as e:
            _ai_tasks[task_id] = {"status": "error", "message": str(e)}


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


def _format_note_for_ai(n, since=None, until=None):
    """一般筆記的文字區塊（不含 checklist 項目）。

    since/until: datetime（UTC）— 若給定，嘗試從 NoteLog 取段落 diff，
           只餵區間內變動段落而非全文。找不到 diff 則 fallback 全文。
    """
    content = n.content or ""
    if since is not None:
        from notes.diff_utils import PARAGRAPH_DIFF_PREFIX
        log_q = NoteLog.query.filter(
            NoteLog.note_id == n.id,
            NoteLog.action == "edit",
            NoteLog.created_at >= since,
        )
        if until is not None:
            log_q = log_q.filter(NoteLog.created_at < until)
        logs = log_q.order_by(NoteLog.created_at.desc()).all()
        diff_parts = []
        for log in logs:
            if log.diff and log.diff.startswith(PARAGRAPH_DIFF_PREFIX):
                diff_parts.append(log.diff[len(PARAGRAPH_DIFF_PREFIX):])
        if diff_parts:
            content = "\n---\n".join(diff_parts)
    cat_label = CATEGORY_LABELS.get(getattr(n, "category", None) or "misc", "雜項")
    return f"{_note_source_label(n)}\n[分類：{cat_label}]\n{n.title}\n{content}"


def _format_checklist_for_ai(n, since=None, until=None):
    """確認表單區塊：標題 + 已完成 / 未完成項目。

    since/until: datetime（UTC, naive）— 若給定，已完成項目只列 [since, until) 內
           的（避免今天的摘要把昨天完成的也撈進來，反之亦然）；未完成項目永遠全部列出。
    """
    items = sorted(n.checklist_items or [], key=lambda i: (i.updated_at or i.created_at), reverse=True)
    total = len(items)
    done_all = [i for i in items if i.is_checked]
    if since is not None:
        def _in_range(i):
            ts = i.updated_at or i.created_at
            if ts < since:
                return False
            if until is not None and ts >= until:
                return False
            return True
        done = [i for i in done_all if _in_range(i)]
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


def _classify_by_title(title):
    """根據表單標題關鍵字分類，回傳中文類別名（員工/客人/機台/雜項）。

    僅在 `note.category` 不在 CATEGORY_CHOICES 時做為 fallback 使用。
    """
    t = (title or "").lower()
    if any(k in t for k in ("客人", "顧客", "客訴", "客戶")):
        return "客人"
    if any(k in t for k in ("同事", "員工", "人事", "排班", "請假")):
        return "員工"
    if any(k in t for k in ("機台", "保養", "故障", "設備", "修繕", "維修")):
        return "機台"
    return "雜項"


def _build_categorized_checklist(checklists, since=None, until=None, inrange_label="本區間"):
    """Python 端預分類 + 跨表單去重，回傳已分好類的文字區塊。

    分類來源：優先用 note.category（建立時使用者已選），無對應時 fallback 標題 keyword。
    去重：跨表單同字面文字只保留一次，已完成 > 未完成 > 歷史未完成。
    since/until: 已完成項目只算 [since, until) 區間內。
    inrange_label: 顯示用標籤 — 「今天」/「昨天」/「本區間」。
    未完成項目分兩段：note.updated_at 在區間內 → ❌ {inrange_label}未完成；
                       否則 → 📜 歷史未完成。
    """
    # enum → 中文桶
    CATEGORY_TO_BUCKET = {
        "employee": "員工",
        "customer": "客人",
        "machine": "機台",
        "misc": "雜項",
    }
    categories = {
        "員工": {"done": [], "undone": [], "hist": []},
        "客人": {"done": [], "undone": [], "hist": []},
        "機台": {"done": [], "undone": [], "hist": []},
        "雜項": {"done": [], "undone": [], "hist": []},
    }
    seen = set()

    for n in checklists:
        cat_enum = getattr(n, "category", None)
        cat = CATEGORY_TO_BUCKET.get(cat_enum) or _classify_by_title(n.title)
        items = sorted(n.checklist_items or [], key=lambda i: (i.updated_at or i.created_at), reverse=True)
        done_all = [i for i in items if i.is_checked]
        if since is not None:
            def _in_range(it):
                ts = it.updated_at or it.created_at
                if ts < since:
                    return False
                if until is not None and ts >= until:
                    return False
                return True
            done = [i for i in done_all if _in_range(i)]
        else:
            done = done_all
        undone = [i for i in items if not i.is_checked]

        # note 是否在區間內（沒給 since 視同全部在區間內，相容舊呼叫）
        if since is None:
            note_in_range = True
        else:
            ts = n.updated_at
            note_in_range = (ts is not None and ts >= since
                             and (until is None or ts < until))
        undone_target = "undone" if note_in_range else "hist"

        for i in done:
            key = i.text.strip()
            if key not in seen:
                seen.add(key)
                who = i.checked_by_name or "?"
                categories[cat]["done"].append(f"- [{who}] {i.text}")

        for i in undone:
            key = i.text.strip()
            if key not in seen:
                seen.add(key)
                author = n.author_name or (n.author.username if n.author else "?")
                categories[cat][undone_target].append(f"- [{author}] {i.text}")

    # 組裝輸出（順序：員工/客人/機台/雜項）
    cat_labels = {
        "員工": "## 👥 員工",
        "客人": "## 🧑‍🤝‍🧑 客人",
        "機台": "## 🔧 機台",
        "雜項": "## 📋 雜項",
    }
    blocks = []
    for cat_key in ("員工", "客人", "機台", "雜項"):
        done_lines = categories[cat_key]["done"]
        undone_lines = categories[cat_key]["undone"]
        hist_lines = categories[cat_key]["hist"]
        if not done_lines and not undone_lines and not hist_lines:
            continue
        lines = [cat_labels[cat_key]]
        if done_lines:
            lines.append("### ✅ 已完成")
            lines.extend(done_lines)
        if undone_lines:
            lines.append(f"### ❌ {inrange_label}未完成")
            lines.extend(undone_lines)
        if hist_lines:
            lines.append("### 📜 歷史未完成")
            lines.extend(hist_lines)
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks) if blocks else "（無項目）"


def _split_notes(notes):
    """分成一般筆記 / 確認表單兩組"""
    regular = [n for n in notes if getattr(n, "note_type", "note") != "checklist"]
    checklists = [n for n in notes if getattr(n, "note_type", "note") == "checklist"]
    return regular, checklists


def _build_categorized_notes(notes, since=None, until=None, inrange_label="本區間"):
    """Python 端預分類一般筆記，依 status × 時間區間分到 4 桶 × 3 段。

    分類來源：note.category（enum→中文桶映射）
    完成（區間內）：note.status == 'resolved' AND resolved_at in [since, until)
    {inrange_label}未完成：status != 'resolved' AND note.updated_at in [since, until)
    歷史未完成：status != 'resolved' AND note.updated_at NOT in [since, until)
    其他（resolved 但 resolved_at 在區間外）→ 跳過（歷史已處理事項，不顯示）
    """
    CATEGORY_TO_BUCKET = {
        "employee": "員工",
        "customer": "客人",
        "machine": "機台",
        "misc": "雜項",
    }
    categories = {
        "員工": {"done": [], "undone": [], "hist": []},
        "客人": {"done": [], "undone": [], "hist": []},
        "機台": {"done": [], "undone": [], "hist": []},
        "雜項": {"done": [], "undone": [], "hist": []},
    }

    for n in notes:
        cat_enum = getattr(n, "category", None)
        cat = CATEGORY_TO_BUCKET.get(cat_enum) or "雜項"
        is_resolved = (getattr(n, "status", None) == "resolved")

        if since is None:
            target = "done" if is_resolved else "undone"
        else:
            ra = getattr(n, "resolved_at", None)
            if ra is not None and ra.tzinfo is not None:
                ra = ra.astimezone(timezone.utc).replace(tzinfo=None)
            ua = n.updated_at
            resolved_in_range = (is_resolved and ra is not None and ra >= since
                                 and (until is None or ra < until))
            note_in_range = (ua is not None and ua >= since
                             and (until is None or ua < until))
            if resolved_in_range:
                target = "done"
            elif is_resolved:
                continue
            elif note_in_range:
                target = "undone"
            else:
                target = "hist"

        author = n.author_name or (n.author.username if n.author else "?")
        body_lines = [f"【{author}】{n.title or ''}"]
        if n.content:
            body_lines.append(n.content)
        categories[cat][target].append("\n".join(body_lines))

    cat_labels = {
        "員工": "## 👥 員工",
        "客人": "## 🧑‍🤝‍🧑 客人",
        "機台": "## 🔧 機台",
        "雜項": "## 📋 雜項",
    }
    blocks = []
    for cat_key in ("員工", "客人", "機台", "雜項"):
        d = categories[cat_key]["done"]
        u = categories[cat_key]["undone"]
        h = categories[cat_key]["hist"]
        if not d and not u and not h:
            continue
        lines = [cat_labels[cat_key]]
        if d:
            lines.append("### ✅ 已完成")
            for body in d:
                lines.append("---")
                lines.append(body)
        if u:
            lines.append(f"### ❌ {inrange_label}未完成")
            for body in u:
                lines.append("---")
                lines.append(body)
        if h:
            lines.append("### 📜 歷史未完成")
            for body in h:
                lines.append("---")
                lines.append(body)
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks) if blocks else "（無項目）"


def _build_ai_sections(notes, header_prefix="", since=None, until=None, inrange_label="本區間"):
    """組出傳給 AI 的 prompt 後段（# 待整理... 以下），回傳 (data_block, has_checklist)。

    since/until: datetime — 一般筆記與確認表單都依此區間做完成/未完成/歷史未完成分段。
    inrange_label: 顯示用標籤（「今天」/「昨天」），給未完成段標題用。
    """
    regular, checklists = _split_notes(notes)
    blocks = []
    if regular:
        body = _build_categorized_notes(regular, since=since, until=until, inrange_label=inrange_label)
        blocks.append(
            f"# ===== 一般筆記 開始 =====（{header_prefix}共 {len(regular)} 筆，已依 category 預分類 + 區間/歷史分段）\n\n"
            f"{body}\n\n"
            f"# ===== 一般筆記 結束 ====="
        )
    if checklists:
        # Python 端預分類 + 去重，AI 不需要自己分類
        body = _build_categorized_checklist(checklists, since=since, until=until, inrange_label=inrange_label)
        blocks.append(
            f"# ===== 確認表單 開始 =====（{header_prefix}共 {len(checklists)} 筆，已依標題預分類並去重）\n\n"
            f"{body}\n\n"
            f"# ===== 確認表單 結束 ====="
        )
    return "\n\n".join(blocks), bool(checklists), len(regular), len(checklists)


def _build_output_structure_prompt(has_regular, has_checklist):
    """根據實際資料動態產生輸出結構指示，沒資料的 section 不出現。

    一般筆記與確認表單都已 Python 端預分類（4 桶 × 3 段）。AI 只負責萃取條列文字，禁止改動分組。
    """
    if not has_regular and not has_checklist:
        return ""

    parts = ["\n# 輸出結構（極度重要，嚴格遵守）"]

    if has_regular and has_checklist:
        parts.append(
            "**筆記資料與確認表單資料是完全不同的來源，絕對禁止混合。**\n"
            "「一般筆記」的內容只能出現在「# 筆記彙整」區塊；「確認表單」的項目只能出現在「# 確認表單彙整」區塊。\n"
            "違反此規則等同錯誤輸出。\n\n"
            "請將輸出分為兩大區塊（用 Markdown `#` 標題）：「# 筆記彙整」與「# 確認表單彙整」。"
        )
    elif has_regular:
        parts.append("請以「# 筆記彙整」為唯一標題輸出。確認表單區段不存在，禁止虛構。")
    else:
        parts.append("請以「# 確認表單彙整」為唯一標題輸出。一般筆記區段不存在，禁止虛構。")

    if has_regular:
        parts.append(
            "\n## 筆記彙整 規則\n"
            "**僅限**處理「===== 一般筆記 開始 =====」到「===== 一般筆記 結束 =====」之間的內容。\n"
            "一般筆記**已由系統預先分類並依狀態分段**，分為 👥員工 / 🧑‍🤝‍🧑客人 / 🔧機台 / 📋雜項 四大類，每類下有 ✅已完成 / ❌未完成 / 📜 歷史未完成 三段。\n"
            "你的工作：\n"
            "1. **保持四類與三段分組不變** — 禁止移動筆記到其他類別或其他狀態段。\n"
            "2. **萃取每筆筆記的事件條列** — 同段內每筆筆記輸出為 `1. 2. 3.` 數字列表，每條格式「人名 — 事項內容」，刪除口語、抱怨。\n"
            "3. **不要新增、刪除筆記內沒提到的事項**。\n"
            "4. 沒事項的類別/分段直接省略，不要輸出空標題。"
        )

    if has_checklist:
        parts.append(
            "\n## 確認表單彙整 規則\n"
            "**僅限**處理「===== 確認表單 開始 =====」到「===== 確認表單 結束 =====」之間的項目。\n"
            "確認表單**已由系統預先分類並去重完成**，分為 👥員工 / 🧑‍🤝‍🧑客人 / 🔧機台 / 📋雜項 四大類，每類下有 ✅已完成 / ❌未完成 / 📜 歷史未完成 三段。\n"
            "你的工作：\n"
            "1. **保持現有分類不變** — 禁止將項目移到其他類別。\n"
            "2. **保持三段分組不變** — 禁止移動。\n"
            "3. **整理文字** — 每條保留人名，精簡為「人名 — 事項內容」格式。同一人的事項放在一起。\n"
            "4. **不要新增、刪除或重複任何項目** — 輸入幾條就輸出幾條。\n"
            "5. **不要輸出時間資訊**。"
        )

    return "\n".join(parts) + "\n"


def _build_all_content(store_name=None):
    """收集同店所有筆記（排除 AI 摘要產生的 is_ai_summary=True 筆記），
    合併成 all_content（舊介面，供 summarize 使用）"""
    query = Note.query.filter(Note.is_ai_summary.is_(False))
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
                 prompt, 65536, note_id, "ai_summary")
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
                 prompt, 65536, note_id, "ai_outline")
    return jsonify({"status": "accepted", "task_id": task_id})


@notes_bp.route("/ai/summary", methods=["POST"])
@login_required
def notes_ai_summary():
    if not current_user.is_admin():
        return jsonify({"status": "error", "message": "僅限管理員"}), 403
    data = request.get_json(silent=True) or {}
    store = data.get("store", "")
    # Admin 只能摘要自己的店
    if current_user.is_admin() and not current_user.is_super_admin():
        store = current_user.store
    business_day = data.get("business_day", "today")
    offset = -1 if business_day == "yesterday" else 0
    start_utc, end_utc = _get_business_day_range(offset_days=offset)
    inrange_label = "昨天" if business_day == "yesterday" else "今天"
    range_label = inrange_label + " 早上 8 點起 24 小時"
    # 存檔標題用的日期 — 跟按鈕標籤一致（日曆日 today/yesterday），不是營業日起算日
    now_tw = datetime.now(_TW)
    label_base = (now_tw - timedelta(days=1)) if business_day == "yesterday" else now_tw
    business_day_date = label_base.strftime("%Y-%m-%d")

    valid_stores = [s.name for s in Store.query.all()]
    if store not in valid_stores:
        return jsonify({"status": "error", "message": "請選擇店別"}), 400

    # 一般筆記與確認表單都全撈（_build_categorized_notes/_build_categorized_checklist
    # 會在 Python 端依 status/updated_at 分到 完成 / 區間內未完成 / 歷史未完成）
    notes = (Note.query
             .filter(Note.is_ai_summary.is_(False))
             .filter_by(store=store)
             .order_by(Note.updated_at.desc())
             .all())

    # DEBUG: 確認撈到的筆記（部署穩定後可移除）
    import logging
    _log = logging.getLogger("ai_summary")
    _log.warning("=== AI摘要 debug: store=%s, business_day=%s, start_utc=%s, end_utc=%s ===", store, business_day, start_utc, end_utc)
    for _n in notes:
        _log.warning("  note id=%s store=%s type=%s title=%.30s updated_at=%s content_len=%s",
                     _n.id, _n.store, _n.note_type, _n.title, _n.updated_at, len(_n.content or ""))

    if not notes:
        return jsonify({"status": "ok", "summary": "（近期無筆記）", "business_day_date": business_day_date})

    # 拆成一般筆記 / 確認表單兩段（已完成項目只列區間內 updated 的）
    data_block, has_cl, nreg, ncl = _build_ai_sections(notes, since=start_utc, until=end_utc, inrange_label=inrange_label)

    store_label = f"「{store}店」"
    extra = f"\n# 額外要求\n請在輸出最前面標明這是「{store}店」的彙整。\n"

    # 資料量大時拆成筆記 + 確認表單兩批分別呼叫
    if nreg > 0 and has_cl and len(data_block) > 6000:
        task_id = uuid.uuid4().hex[:12]
        _ai_tasks[task_id] = {"status": "processing"}
        import gevent
        gevent.spawn(
            _run_split_summary, task_id, current_app._get_current_object(),
            notes, since=start_utc, until=end_utc, store_label=store_label, range_label=range_label, extra=extra, inrange_label=inrange_label,
        )
    else:
        prompt = (
            MANAGER_PROMPT + extra
            + _build_output_structure_prompt(nreg > 0, has_cl)
            + f"\n# 待整理資料（{store_label}{range_label}，一般筆記 {nreg} 筆 / 確認表單 {ncl} 筆）\n\n{data_block}"
        )
        task_id = uuid.uuid4().hex[:12]
        _ai_tasks[task_id] = {"status": "processing"}
        import gevent
        gevent.spawn(_run_ai_task, task_id, current_app._get_current_object(),
                     prompt, 65536)
    return jsonify({"status": "accepted", "task_id": task_id, "business_day_date": business_day_date})


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

    try:
        note_id = request.form.get("note_id", type=int)
        item_id = request.form.get("item_id", type=int)
    except OSError as e:
        # client 上傳到一半斷線（網路不穩 / 切換 wifi / 按取消）— 不是 server bug
        if "unexpected end of file" in str(e):
            print(f"=== upload cancelled by client at form parse: {e}", flush=True)
            return jsonify({"status": "error", "message": "上傳中斷，請重試"}), 400
        raise
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
                           status_choices=STATUS_CHOICES,
                           is_readonly=is_locked,
                           locker_name=locker.username if locker else None)
