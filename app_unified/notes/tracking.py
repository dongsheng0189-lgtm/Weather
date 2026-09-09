"""建檔追蹤的路由（頁面 + JSON API），註冊到 notes_bp。

流程刻意分兩段（規格的命名決策）：**搜尋**是暫時的、關掉就沒了；按下**建檔追蹤**
才真的建立一張持續累積的表。兩者都是輸入關鍵字、都列出命中，很容易混淆，所以
UI 上必須維持這個分界。

權限沿用標籤管理：同店的人都可以建立／刪除／剔除／寫備註，不新增權限概念。
"""
from datetime import datetime

from flask import request, jsonify, abort, render_template
from flask_login import login_required, current_user

from extensions import db
from models import TrackedKeyword, TrackedKeywordHit, Note, ChecklistItem
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


def _tracked_to_dict(t):
    return {
        'id': t.id,
        'keyword': t.keyword,
        'created_by_name': t.created_by_name,
        'created_at': tw_str(t.created_at),
        'hit_count': TrackedKeywordHit.query.filter_by(
            keyword_id=t.id).filter(TrackedKeywordHit.excluded_at.is_(None)).count(),
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
    """備註：整店共用，同店的人都能寫、也能覆蓋別人寫的（與剔除同一套規則）。"""
    h = _get_hit_or_404(hid)
    data = request.get_json(silent=True) or {}
    # PUT 是整份取代語意，所以「明確傳空字串」＝清空備註是合法操作；但「根本沒帶
    # text 欄位」或「text: null」是另一回事 —— 備註是對人的書面評語，加密存放、
    # 沒有版本紀錄也沒有 log，一旦被誤清空就找不回來，所以這兩種情況一律擋在
    # 400，不當成清空處理。同理，數字之類的非字串也直接拒絕，不做隱性轉型
    # （那是呼叫端自己的 bug，應該讓它出錯而不是被吃掉）。
    if 'text' not in data or data.get('text') is None:
        return jsonify({'error': '缺少 text'}), 400
    text = data.get('text')
    if not isinstance(text, str):
        return jsonify({'error': 'text 必須是字串'}), 400
    h.note_text = text.strip()
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


def _detail_rows(tracked):
    """組出詳細頁的每一列。

    每一列是**參照**：文字一律即時從原始 Note / ChecklistItem 讀，不是命中當下的
    複本 —— 原文被修正後追蹤表要跟著變，否則會出現「追蹤表寫著遲到 10 分鐘、原始
    項目已更正成 5 分鐘」的落差。

    過濾兩層：已剔除的（excluded_at 有值）、以及原始資料已軟刪的。後者必須在
    顯示時再檢查一次 —— 命中列是掃描當下寫的，資料可能之後才被刪；而命中列刻意
    不主動清除（原始資料可能只是暫時被刪、24 小時內可還原）。
    """
    hits = TrackedKeywordHit.query.filter_by(keyword_id=tracked.id).filter(
        TrackedKeywordHit.excluded_at.is_(None)).all()
    note_ids = [h.target_id for h in hits if h.target_type == 'note']
    item_ids = [h.target_id for h in hits if h.target_type == 'item']

    notes = {n.id: n for n in Note.query.filter(
        Note.id.in_(note_ids), Note.deleted_at.is_(None)).all()} if note_ids else {}
    items = {i.id: i for i in ChecklistItem.query.filter(
        ChecklistItem.id.in_(item_ids),
        ChecklistItem.deleted_at.is_(None)).all()} if item_ids else {}

    # item 分支需要母筆記的 title/deleted_at，但絕對不能靠 `i.note` 的 lazy
    # load 逐筆去拿 —— 那是每個「不同的」母筆記各一次 SELECT，且會把整個 Note
    # 物件（含 content）解密出來，而這裡只用得到 title。一張追蹤表會無上限
    # 累積命中，命中愈多、這個 N+1 愈嚴重，且是同步跑在 gevent worker 上
    # （見 notes/tracking_scan.py 開頭對同一種模式的警告）。改成跟上面
    # note_ids 一樣的批次查詢，一次撈完、合併進同一個 notes 字典。
    # 用 `- notes.keys()` 排掉已經查過的 id 純粹省一點查詢範圍，不影響正確性
    # ——就算重疊，兩邊套的都是同一個 deleted_at.is_(None) 過濾條件。
    parent_note_ids = {i.note_id for i in items.values()} - notes.keys()
    if parent_note_ids:
        for n in Note.query.filter(
                Note.id.in_(parent_note_ids), Note.deleted_at.is_(None)).all():
            notes[n.id] = n

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
            n = notes.get(i.note_id)
            if not n:
                continue          # 母筆記已軟刪或已硬刪 → 這一列自然消失
            title, text, note_id, when = n.title, i.text, i.note_id, i.updated_at
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
        }))
    rows.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in rows]


@notes_bp.route('/tracking/<int:kid>')
@login_required
def tracking_detail_page(kid):
    t = _get_tracked_or_404(kid)
    scan_tracked_keyword(t)   # 每次開啟跑一次增量
    # scanned_through 要在掃描跑完之後才讀 —— 增量掃描完全無聲，使用者看不出
    # 系統剛剛有沒有檢查過新命中，「最後更新」就是唯一能證明「這次開頁確實
    # 掃過」的線索。scan_tracked_keyword commit 後這個屬性會過期重讀，這裡讀到
    # 的就是這次掃描寫入的水位，不是掃描前的舊值。
    return render_template('notes/tracking_detail.html',
                           tracked=_tracked_to_dict(t),
                           rows=_detail_rows(t),
                           last_updated=tw_str(t.scanned_through, fmt='%m/%d %H:%M'))
