"""建檔追蹤的路由（頁面 + JSON API），註冊到 notes_bp。

流程刻意分兩段（規格的命名決策）：**搜尋**是暫時的、關掉就沒了；按下**建檔追蹤**
才真的建立一張持續累積的表。兩者都是輸入關鍵字、都列出命中，很容易混淆，所以
UI 上必須維持這個分界。

權限沿用標籤管理：同店的人都可以建立／刪除／剔除／寫備註，不新增權限概念。
"""
from datetime import datetime

from flask import request, jsonify, abort, render_template
from flask_login import login_required, current_user
from sqlalchemy.orm import load_only

from extensions import db
from models import TrackedKeyword, TrackedKeywordHit, TrackedKeywordMemoLog, Note, ChecklistItem
from notes.routes import notes_bp
from notes.store_scope import effective_store, require_same_store
from notes.tracking_match import normalize
from notes.tracking_scan import scan_tracked_keyword
from tw_time import tw_str

# 店別解析／同店授權規則本體在 notes/store_scope.py（與 notes/tags.py 共用，
# 理由見該檔 docstring）。這裡沿用規格原文說的「與 _scope_notes 一致」，但
# _scope_notes 對 super_admin 會撈「所有未隱藏的店」，與「不做跨店」相矛盾，
# 且一張追蹤表只有一個 store 欄位可填，所以實際採用的是 store_scope 這套
# 「super_admin 可用 ?store= 指定、其他人鎖在自己店」的規則。

# 關鍵字通常是人名或短短幾個字的描述（比照 notes/tags.py 的 create_tag 對
# 標籤名稱的限制，同樣是 1-20），備註則是一句話到幾句話的評語，給比關鍵字寬鬆
# 但仍有界的上限。兩者都落在 EncryptedText，DB 端本身沒有長度限制，不擋的話
# 使用者想送多長都插得進去。
KEYWORD_MAX_LEN = 20
MEMO_MAX_LEN = 500


def _get_tracked_or_404(kid):
    t = db.session.get(TrackedKeyword, kid)
    if not t:
        abort(404)
    require_same_store(t)
    return t


def find_tracking_for_keyword(keyword):
    """本店是否已有這個關鍵字的追蹤表？有就回 id，沒有回 None。

    keyword 是 EncryptedText、SQL 比不了，只能撈出來在 Python 端比。一店的追蹤表
    數量是「使用者手動建立的關鍵字」量級（幾個到幾十個），不是資料量級，撈全部
    無妨 —— 不需要為它加雜湊欄位（那還會多一個可被離線比對的側通道）。

    ⚠️ 回傳 None 有兩種含意：「本店沒有這個關鍵字的追蹤表」與「根本解析不出
    店別」。呼叫端必須**先自己確認店別解析得出來**（路由層都已經在解析不出時
    回 400），否則會把「你沒有店別」誤顯示成「還沒建過這個追蹤表」——那正是
    list_tracking 曾經犯過、後來改成明確 400 的同一個錯。
    """
    needle = normalize(keyword)
    if not needle:
        return None
    store = effective_store()
    if not store:
        return None
    for t in TrackedKeyword.query.filter_by(store=store).all():
        if normalize(t.keyword) == needle:
            return t.id
    return None


def _live_target_ids(hits):
    """從一批命中列（已排除 excluded）篩出『原始資料仍在（沒被軟刪）』的
    note 型 / item 型 target_id 集合。

    只查 id 與 deleted_at，不觸發任何 EncryptedText 解密（理由同
    tracking_scan.py 的 `_target_ids`）。這裡刻意獨立成一個函式、被 list 頁的
    計數（`_tracked_to_dict` 的 hit_count）與 detail 頁的列表（`_detail_rows`）
    共用 —— 兩者過去各自維護一份「這筆命中還算不算數」的邏輯，`_tracked_to_dict`
    完全沒檢查目標是否已被軟刪，結果是清單頁寫「2 筆」、詳細頁卻只有 1 筆
    可看，使用者會誤以為資料憑空少了一筆。只有一份判斷，兩邊就不可能再對不上。
    """
    note_target_ids = [h.target_id for h in hits if h.target_type == 'note']
    item_target_ids = [h.target_id for h in hits if h.target_type == 'item']

    live_note_ids = set()
    if note_target_ids:
        live_note_ids = {r[0] for r in db.session.query(Note.id).filter(
            Note.id.in_(note_target_ids), Note.deleted_at.is_(None))}

    live_item_ids = set()
    if item_target_ids:
        # item 本身沒被刪，但它的母筆記可能已經被軟刪 —— 兩者都要活著才算數。
        live_item_ids = {r[0] for r in db.session.query(ChecklistItem.id).join(
            Note, ChecklistItem.note_id == Note.id).filter(
            ChecklistItem.id.in_(item_target_ids),
            ChecklistItem.deleted_at.is_(None),
            Note.deleted_at.is_(None))}

    return live_note_ids, live_item_ids


def _visible_hits(tracked, full=False):
    """這張追蹤表目前『看得到』的命中列：未被剔除、且原始資料仍在。

    full=False（預設）只載入判斷「算不算數」與計數用得到的欄位（id /
    target_type / target_id）。TrackedKeywordHit.note_text /
    excluded_by_name 是 EncryptedText，process_result_value 在**取列
    當下**就會解密、不是屬性存取當下才解密——清單頁（`_tracked_to_dict`）
    只要 `len()`，從來不讀 note_text，撈整列一樣會把這張表累積的每一則備註
    白白解密一次。這裡用 `load_only` 擋掉，理由同 `_detail_rows` 對
    Note/ChecklistItem 做的事。

    full=True 給 `_detail_rows` 用，因為它真的要讀 h.note_text。

    兩種模式共用同一份「這筆命中還算不算數」判斷（下面這段 filter），不因為
    要省欄位就分裂成兩份可能日後對不上的邏輯——那正是這個函式最初被拆出來
    要解決的問題（見上面的說明）。
    """
    query = TrackedKeywordHit.query.filter_by(keyword_id=tracked.id).filter(
        TrackedKeywordHit.excluded_at.is_(None))
    if not full:
        query = query.options(load_only(
            TrackedKeywordHit.id, TrackedKeywordHit.target_type,
            TrackedKeywordHit.target_id))
    hits = query.all()
    live_note_ids, live_item_ids = _live_target_ids(hits)
    return [h for h in hits
            if (h.target_type == 'note' and h.target_id in live_note_ids)
            or (h.target_type == 'item' and h.target_id in live_item_ids)]


def _excluded_hits(tracked):
    """這張追蹤表已被剔除的命中列（供詳細頁的「已剔除」收合區塊使用）。"""
    return TrackedKeywordHit.query.filter_by(keyword_id=tracked.id).filter(
        TrackedKeywordHit.excluded_at.isnot(None)).order_by(
        TrackedKeywordHit.excluded_at.desc()).all()


def _tracked_to_dict(t, hits=None):
    """`hits` 可選：detail 頁已經算過一次 `_visible_hits(t, full=True)`，
    傳進來就不必再算第二次（同一頁面過去曾各自重算一份，見 `_detail_rows`
    docstring 提到的重複）。清單頁沒有現成的 hits 可傳，維持原本各自算一次
    ——但走的是 `_visible_hits` 的 count-only 模式，不解密備註。"""
    return {
        'id': t.id,
        'keyword': t.keyword,
        'created_by_name': t.created_by_name,
        'created_at': tw_str(t.created_at),
        'hit_count': len(hits) if hits is not None else len(_visible_hits(t)),
    }


@notes_bp.route('/api/tracking', methods=['POST'])
@login_required
def create_tracking():
    store = effective_store()
    if not store:
        return jsonify({'error': '找不到店別，請先選擇店別'}), 400
    data = request.get_json(silent=True) or {}
    keyword = (data.get('keyword') or '').strip()
    if not keyword:
        return jsonify({'error': '關鍵字不可為空'}), 400
    if len(keyword) > KEYWORD_MAX_LEN:
        return jsonify({'error': f'關鍵字長度不可超過 {KEYWORD_MAX_LEN}'}), 400

    # check-then-insert、無原子性：keyword 是 EncryptedText，DB 端無法對密文
    # 建唯一約束，所以擋不住「兩個人同時對同一關鍵字按下建檔追蹤」——兩邊都
    # 查到「尚未存在」、都各自 insert，結果是兩張內容相同的追蹤表。後果是
    # 純外觀重複（多一張、不影響資料正確性），不值得為了這麼窄的競態去換一顆
    # hash 欄位 —— 那會多出一個可被離線比對的側通道（理由同
    # find_tracking_for_keyword 的註解），代價遠大於這個競態。刻意不解決，
    # 只在此留下紀錄，讓後人不要誤以為唯一性有被保證。
    existing_id = find_tracking_for_keyword(keyword)
    if existing_id:
        t = db.session.get(TrackedKeyword, existing_id)
        return jsonify(dict(_tracked_to_dict(t), existing=True))

    t = TrackedKeyword(store=store, keyword=keyword,
                       created_by=current_user.id,
                       created_by_name=current_user.username)
    db.session.add(t)
    db.session.commit()
    # 第一次是完整掃描（會慢，僅此一次）。成本與使用者現在按一次「搜尋」相同 ——
    # 現有的關鍵字搜尋本來就是全歷史解密掃描（notes/routes.py 的 keyword 分支
    # 不套 _date_filter）。
    scan_tracked_keyword(t)
    return jsonify(dict(_tracked_to_dict(t), existing=False))


@notes_bp.route('/api/tracking', methods=['GET'])
@login_required
def list_tracking():
    store = effective_store()
    if not store:
        # 不可靜默回空清單：store 為 None（例如沒有本店的 super_admin 忘記帶
        # ?store=）跟「本店真的沒有任何追蹤表」在使用者眼裡看起來一模一樣，
        # 前者是資料看不到、後者是真的沒資料——這個規格明文禁止的模糊地帶，
        # 與 create_tracking 用同一句錯誤訊息、同樣回 400。
        return jsonify({'error': '找不到店別，請先選擇店別'}), 400
    rows = TrackedKeyword.query.filter_by(store=store).order_by(
        TrackedKeyword.created_at.desc()).all()
    return jsonify({'tracking': [_tracked_to_dict(t) for t in rows]})


@notes_bp.route('/api/tracking/<int:kid>', methods=['DELETE'])
@login_required
def delete_tracking(kid):
    t = _get_tracked_or_404(kid)
    db.session.delete(t)   # cascade 清掉命中列
    db.session.commit()
    return jsonify({'ok': True})


def _get_hit_or_404(hid):
    h = db.session.get(TrackedKeywordHit, hid)
    if not h:
        abort(404)
    _get_tracked_or_404(h.keyword_id)   # 同店檢查（不同店 403）
    return h


@notes_bp.route('/api/tracking/hits/<int:hid>/note', methods=['PUT'])
@login_required
def set_hit_note(hid):
    """備註：整店共用，同店的人都能寫、也能覆蓋別人寫的（與剔除同一套規則）。

    覆蓋不再是無聲的 —— 只要文字真的變了，就在 TrackedKeywordMemoLog 留一筆
    「誰、何時、舊文字 → 新文字」，被覆蓋或清空的內容因此可以回溯（見該
    model 的 docstring）。這裡仍然要求「明確傳空字串」才算清空、缺欄位/null
    一律 400——那個規則跟有沒有 log 無關，理由是 PUT 的整份取代語意本來就該
    把「呼叫端沒打算清空」與「呼叫端真的要清空」分開，不能把呼叫端自己的
    bug（漏帶欄位）悄悄吃成清空操作。
    """
    h = _get_hit_or_404(hid)
    data = request.get_json(silent=True) or {}
    if 'text' not in data or data.get('text') is None:
        return jsonify({'error': '缺少 text'}), 400
    text = data.get('text')
    if not isinstance(text, str):
        return jsonify({'error': 'text 必須是字串'}), 400
    text = text.strip()
    if len(text) > MEMO_MAX_LEN:
        return jsonify({'error': f'備註長度不可超過 {MEMO_MAX_LEN}'}), 400
    old_text = h.note_text
    # 只在文字「真的變了」才留 log——重存跟原本一樣的內容不是一次異動，不該
    # 產生噪音列。None（從沒寫過）視同空字串，避免「從沒寫過」到「存空字串」
    # 這種等同於什麼都沒發生的操作也留下一筆。
    if text != (old_text or ''):
        db.session.add(TrackedKeywordMemoLog(
            hit_id=h.id, old_text=old_text, new_text=text,
            changed_by=current_user.id, changed_by_name=current_user.username))
    h.note_text = text
    db.session.commit()
    return jsonify({'note_text': h.note_text})


@notes_bp.route('/api/tracking/hits/<int:hid>/exclude', methods=['POST'])
@login_required
def toggle_hit_exclude(hid):
    """「不相關」：軟刪除，不可改成真刪 —— 真刪的話下一輪增量掃描會再抓回來
    （那筆原始資料仍然含有關鍵字）。整店共用：一個人剔除，大家都不再看到。"""
    h = _get_hit_or_404(hid)
    data = request.get_json(silent=True) or {}
    excluded = data.get('excluded')
    # 這是店範圍的軟刪除開關，要求真布林、不看 truthy——字串 "false" 是 truthy，
    # 若沿用 if data.get('excluded') 會把「客戶端想還原」誤判成「剔除」，方向整個
    # 反過來，而且是靜默發生、沒有任何錯誤訊息可以察覺。
    if excluded is not True and excluded is not False:
        return jsonify({'error': 'excluded 必須是布林值'}), 400
    if excluded:
        h.excluded_at = datetime.utcnow()
        h.excluded_by = current_user.id
        h.excluded_by_name = current_user.username
    else:
        h.excluded_at = None
        h.excluded_by = None
        h.excluded_by_name = None
    db.session.commit()
    return jsonify({'excluded': h.excluded_at is not None,
                    'excluded_by_name': h.excluded_by_name})


@notes_bp.route('/tracking/')
@login_required
def tracking_list_page():
    store = effective_store()
    if not store:
        # 同 list_tracking 的理由：不可讓「看不到店別」偽裝成「本店空清單」
        # 靜默呈現。這裡選擇渲染同一個模板、帶一個明確的錯誤狀態（而非
        # abort(400) 丟出 Flask 預設的除錯風格錯誤頁）——使用者是拿瀏覽器
        # 點進這個頁面，明確的中文訊息＋維持頁面框架，比一片空清單或一個陌生
        # 的系統錯誤頁更容易看懂發生了什麼、該怎麼辦（先選店別）。狀態碼仍是
        # 400，讓自動化測試／工具能分辨這不是正常的 200 空清單。
        return render_template('notes/tracking_list.html',
                               tracking=[], current_store=None,
                               store_error='找不到店別，請先選擇店別'), 400
    rows = TrackedKeyword.query.filter_by(store=store).order_by(
        TrackedKeyword.created_at.desc()).all()
    return render_template('notes/tracking_list.html',
                           tracking=[_tracked_to_dict(t) for t in rows],
                           current_store=store)


def _detail_rows(tracked, hits=None):
    """組出詳細頁的每一列。

    `hits` 可選：呼叫端（`tracking_detail_page`）若已經算過
    `_visible_hits(tracked, full=True)` 就直接傳進來，這裡不重算第二次。

    每一列是**參照**：文字一律即時從原始 Note / ChecklistItem 讀，不是命中當下的
    複本 —— 原文被修正後追蹤表要跟著變，否則會出現「追蹤表寫著遲到 10 分鐘、原始
    項目已更正成 5 分鐘」的落差。

    過濾兩層：已剔除的（excluded_at 有值）、以及原始資料已軟刪的。後者必須在
    顯示時再檢查一次 —— 命中列是掃描當下寫的，資料可能之後才被刪；而命中列刻意
    不主動清除（原始資料可能只是暫時被刪、24 小時內可還原）。

    兩層過濾都交給 `_visible_hits()` 做（與清單頁的 `_tracked_to_dict` 共用同
    一份「這筆命中還算不算數」判斷，理由見該函式），這裡拿到的 hits 已經保證
    是活的 —— 下面的 Note / ChecklistItem 查詢因此不再需要重複套一次
    `deleted_at.is_(None)`，那樣寫等於把同一個規則寫兩份，兩份哪天被改得不一致
    就是這次要修的「清單筆數與詳細頁筆數對不上」重演。
    """
    hits = hits if hits is not None else _visible_hits(tracked, full=True)
    note_ids = [h.target_id for h in hits if h.target_type == 'note']
    item_ids = [h.target_id for h in hits if h.target_type == 'item']

    # .options(load_only(...)) 限制要撈的欄位：EncryptedText 是 TypeDecorator，
    # process_result_value 在**取列（row fetch）當下**就會跑，不是屬性存取當下
    # 才跑——所以就算不碰 n.author_name，只要 Note.query 把整列選出來，
    # author_name / ai_summary / ai_outline / reminder_set_by_name 這些
    # EncryptedText 欄位一樣會被 AES-GCM 解密一次，白白解出這裡用不到的欄位。
    # ChecklistItem 同理：checked_by_name / created_by_name / updated_by_name /
    # reminder_set_by_name 四個快照欄位這裡從來不讀，不限制的話每一筆 item 型
    # 命中都會多解密這四欄。
    notes = {n.id: n for n in Note.query.options(
        load_only(Note.id, Note.title, Note.content, Note.updated_at)).filter(
        Note.id.in_(note_ids)).all()} if note_ids else {}
    items = {i.id: i for i in ChecklistItem.query.options(
        load_only(ChecklistItem.id, ChecklistItem.note_id, ChecklistItem.text,
                  ChecklistItem.updated_at)).filter(
        ChecklistItem.id.in_(item_ids)).all()} if item_ids else {}

    # item 分支需要母筆記的 title，但絕對不能靠 `i.note` 的 lazy load 逐筆去
    # 拿 —— 那是每個「不同的」母筆記各一次 SELECT。改成批次查詢，一次撈完；
    # 但這個 parent_notes 查詢**不能**併進上面的 notes 字典 —— 兩者故意分開
    # 存放：parent_notes 是欄位受限的物件（只選了 id/title），notes 才是完整
    # 物件，若合併進去，一旦哪天有人改成從 notes 讀 note 型命中的 content，
    # 會在不知情下重新對這批欄位受限的物件觸發 lazy load（連帶解密），且是
    # 靜默發生。
    # 用 `- notes.keys()` 排掉已經查過的 id 純粹省一點查詢範圍，不影響正確性。
    #
    # 不需要再套 `Note.deleted_at.is_(None)`：這裡的 items 已經是
    # `_visible_hits()` 篩過的——item 型命中要進到這個集合，join 條件本來就
    # 要求它的母筆記也活著（見 `_live_target_ids`），所以這裡的母筆記必定存在
    # 且未刪，重複過濾只會是同一條規則的第二份抄本。
    parent_note_ids = {i.note_id for i in items.values()} - notes.keys()
    parent_notes = {}
    if parent_note_ids:
        for n in Note.query.options(
                load_only(Note.id, Note.title)).filter(
                Note.id.in_(parent_note_ids)).all():
            parent_notes[n.id] = n

    # 每一筆命中「最後一次備註異動」的編輯者／時間，給每列顯示用（不做完整
    # 歷史檢視，只要看得到最新是誰改的——見 TrackedKeywordMemoLog 的
    # docstring）。一次批次查完，避免每列各查一次。同一 hit_id 若有多筆
    # log，`changed_at DESC` 讓每個 hit_id 第一次出現的那筆就是最新一筆，
    # 之後同一 hit_id 再出現時跳過即可。
    last_memo_edit = {}
    hit_ids = [h.id for h in hits]
    if hit_ids:
        logs = TrackedKeywordMemoLog.query.filter(
            TrackedKeywordMemoLog.hit_id.in_(hit_ids)).order_by(
            TrackedKeywordMemoLog.changed_at.desc()).all()
        for log in logs:
            last_memo_edit.setdefault(log.hit_id, log)

    rows = []
    for h in hits:
        if h.target_type == 'note':
            n = notes.get(h.target_id)
            if not n:
                continue          # 原文已軟刪或已硬刪 → 這一列自然消失
            title, text, note_id, when = n.title, n.content, n.id, n.updated_at
        else:
            i = items.get(h.target_id)
            if not i:
                continue
            # 先查 notes（同一張追蹤表裡，這個母筆記可能『自己也是』一筆
            # note 型命中，已經被上面的批次查詢完整載入），沒有才落到
            # parent_notes——不能反過來：parent_notes 是欄位受限的物件，
            # notes 才是完整物件，兩者對同一個 id 都命中時要用完整的那份。
            n = notes.get(i.note_id) or parent_notes.get(i.note_id)
            if not n:
                continue          # 母筆記已軟刪或已硬刪 → 這一列自然消失
            title, text, note_id, when = n.title, i.text, i.note_id, i.updated_at
        last_edit = last_memo_edit.get(h.id)
        # 用 (when, dict) tuple 排序，不在 dict 裡塞排序用的暫時欄位再事後
        # pop 掉 —— 一次到位，也不會有「忘記 pop」把內部欄位外洩給呼叫端的
        # 風險。
        rows.append((when, {
            'hit_id': h.id,
            'target_type': h.target_type,
            'note_id': note_id,
            'title': title or '（未命名）',
            'text': text or '',
            'memo': h.note_text or '',
            'when': tw_str(when, fmt='%m/%d'),
            'memo_edited_by': last_edit.changed_by_name if last_edit else None,
            'memo_edited_at': tw_str(last_edit.changed_at, fmt='%m/%d %H:%M') if last_edit else '',
        }))
    rows.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in rows]


def _excluded_rows(tracked):
    """詳細頁「已剔除 (n)」收合區塊要顯示的列。

    刻意**不**套 `_visible_hits()` 的「原始資料還在」過濾——剔除紀錄本身就是
    要留給人核對「誰、什麼時候，把哪一筆判定成不相關」的稽核用途，原文事後
    被刪掉不該讓這筆紀錄跟著憑空消失。查得到原文就顯示，查不到（已硬刪，或
    暫時軟刪但還沒到還原的必要）就退回一個明確的提示字串，而不是讓整列
    消失或整頁噴錯。
    """
    hits = _excluded_hits(tracked)
    note_ids = [h.target_id for h in hits if h.target_type == 'note']
    item_ids = [h.target_id for h in hits if h.target_type == 'item']

    notes = {n.id: n for n in Note.query.options(
        load_only(Note.id, Note.title)).filter(Note.id.in_(note_ids)).all()} if note_ids else {}
    items = {i.id: i for i in ChecklistItem.query.options(
        load_only(ChecklistItem.id, ChecklistItem.note_id)).filter(
        ChecklistItem.id.in_(item_ids)).all()} if item_ids else {}
    parent_note_ids = {i.note_id for i in items.values()} - notes.keys()
    parent_notes = {}
    if parent_note_ids:
        for n in Note.query.options(load_only(Note.id, Note.title)).filter(
                Note.id.in_(parent_note_ids)).all():
            parent_notes[n.id] = n

    MISSING = '（原文已不存在）'
    rows = []
    for h in hits:
        if h.target_type == 'note':
            n = notes.get(h.target_id)
            title = n.title if n else MISSING
        else:
            i = items.get(h.target_id)
            n = (notes.get(i.note_id) or parent_notes.get(i.note_id)) if i else None
            title = n.title if n else MISSING
        rows.append({
            'hit_id': h.id,
            'target_type': h.target_type,
            'title': title or '（未命名）',
            'excluded_by_name': h.excluded_by_name or '—',
            'excluded_at': tw_str(h.excluded_at, fmt='%m/%d %H:%M') if h.excluded_at else '',
        })
    return rows


@notes_bp.route('/tracking/<int:kid>')
@login_required
def tracking_detail_page(kid):
    t = _get_tracked_or_404(kid)
    scan_tracked_keyword(t)   # 每次開啟跑一次增量
    # scanned_through 要在掃描跑完之後才讀 —— 增量掃描完全無聲，使用者看不出
    # 系統剛剛有沒有檢查過新命中，「最後更新」就是唯一能證明「這次開頁確實
    # 掃過」的線索。scan_tracked_keyword commit 後這個屬性會過期重讀，這裡讀到
    # 的就是這次掃描寫入的水位，不是掃描前的舊值。
    #
    # 這裡的 hits 用 full=True 只算一次（detail 頁真的要顯示 note_text），
    # 同時餵給 _tracked_to_dict（算 hit_count）跟 _detail_rows（組列），
    # 兩者過去各自呼叫 _visible_hits() 重算一次，是同一頁面的重複查詢。
    hits = _visible_hits(t, full=True)
    return render_template('notes/tracking_detail.html',
                           tracked=_tracked_to_dict(t, hits=hits),
                           rows=_detail_rows(t, hits=hits),
                           excluded_rows=_excluded_rows(t),
                           last_updated=tw_str(t.scanned_through, fmt='%m/%d %H:%M'),
                           # 讓模板的 maxlength／提示字直接引用同一個常數，不在
                           # 模板裡另外寫死一份數字——兩份數字哪天改動不同步，
                           # 使用者會在前端看似還能打，一送出才被伺服器擋下。
                           memo_max_len=MEMO_MAX_LEN)
