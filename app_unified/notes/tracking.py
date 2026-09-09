"""建檔追蹤的路由（頁面 + JSON API），註冊到 notes_bp。

流程刻意分兩段（規格的命名決策）：**搜尋**是暫時的、關掉就沒了；按下**建檔追蹤**
才真的建立一張持續累積的表。兩者都是輸入關鍵字、都列出命中，很容易混淆，所以
UI 上必須維持這個分界。

權限沿用標籤管理：同店的人都可以建立／刪除／剔除／寫備註，不新增權限概念。
"""
from flask import request, jsonify, abort, render_template
from flask_login import login_required, current_user

from extensions import db
from models import TrackedKeyword, TrackedKeywordHit
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


@notes_bp.route('/tracking/<int:kid>')
@login_required
def tracking_detail_page(kid):
    t = _get_tracked_or_404(kid)
    scan_tracked_keyword(t)   # 每次開啟跑一次增量
    return render_template('notes/tracking_detail.html',
                           tracked=_tracked_to_dict(t),
                           rows=[])
