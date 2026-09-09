"""建檔追蹤的備註（TrackedKeyword.memo_text）——整張表共用一欄，放在詳細頁
最上面。

2026-09-09 改版：PO 手動測試「建檔追蹤」後回饋「備註只要有一欄位就可以，在
最上面，不用每一個項目就一個備註」，原本掛在每一筆命中列上的備註
（TrackedKeywordHit.note_text，PUT /api/tracking/hits/<hid>/note）整個移除，
改成掛在追蹤表本身（TrackedKeyword.memo_text，PUT /api/tracking/<kid>/memo）。

這裡的測試是從舊版 test_tracking_hits_api.py 的備註測試遷移過來的：行為（覆蓋
留 log、清空留 log、重存相同文字不留 log、驗證身分、跨店 403、長度上限……）
全部照舊，只是 identity 從「一筆命中」換成「一張追蹤表」。
"""
import json as _json
import uuid
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _uniq(p='T'):
    return f'{p}_{uuid.uuid4().hex[:6]}'


def _mk_user(db, store='B店'):
    from models import User
    u = User(username=_uniq('u'), role='user', store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def _mk_note(db, store='B店', title='日報'):
    from models import Note
    n = Note(store=store, title=title, content='', note_type='note')
    db.session.add(n)
    db.session.commit()
    return n


def _setup_tracked(client, db, store='B店'):
    """建一張空的追蹤表（不需要任何命中——備註是掛在表上，不是掛在命中列上，
    測備註不需要底層筆記存在）。"""
    kw = _uniq('王小明')
    u = _mk_user(db, store=store)
    _login(client, u)
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']
    return u, kid


def test_set_memo_text(client, db):
    _u, kid = _setup_tracked(client, db)
    memo = f'已口頭告誡_{uuid.uuid4().hex[:4]}'

    r = client.put(f'/notes/api/tracking/{kid}/memo', json={'text': memo})

    assert r.status_code == 200
    assert r.get_json()['memo_text'] == memo
    db.session.expire_all()
    from models import TrackedKeyword
    assert db.session.get(TrackedKeyword, kid).memo_text == memo


def test_memo_text_can_be_cleared(client, db):
    _u, kid = _setup_tracked(client, db)
    client.put(f'/notes/api/tracking/{kid}/memo', json={'text': '先寫一段'})

    r = client.put(f'/notes/api/tracking/{kid}/memo', json={'text': ''})

    assert r.status_code == 200
    assert r.get_json()['memo_text'] == ''
    db.session.expire_all()
    from models import TrackedKeyword
    assert db.session.get(TrackedKeyword, kid).memo_text == ''


def test_memo_text_missing_key_is_rejected(client, db):
    """PUT 是整份取代，明確傳 '' 才是「清空」；根本沒帶 text 欄位是另一回事，
    備註是寫死的評語、清掉了找不回來，必須擋在 400，不可靜默清空。"""
    _u, kid = _setup_tracked(client, db)
    memo = f'已口頭告誡_{uuid.uuid4().hex[:4]}'
    client.put(f'/notes/api/tracking/{kid}/memo', json={'text': memo})

    r = client.put(f'/notes/api/tracking/{kid}/memo', json={})

    assert r.status_code == 400
    db.session.expire_all()
    from models import TrackedKeyword
    assert db.session.get(TrackedKeyword, kid).memo_text == memo


def test_memo_text_null_is_rejected(client, db):
    """{"text": null} 跟沒帶欄位是同一種「缺少」，同樣不可被當成清空。"""
    _u, kid = _setup_tracked(client, db)
    memo = f'已口頭告誡_{uuid.uuid4().hex[:4]}'
    client.put(f'/notes/api/tracking/{kid}/memo', json={'text': memo})

    r = client.put(f'/notes/api/tracking/{kid}/memo', json={'text': None})

    assert r.status_code == 400
    db.session.expire_all()
    from models import TrackedKeyword
    assert db.session.get(TrackedKeyword, kid).memo_text == memo


def test_memo_text_non_string_is_rejected(client, db):
    """數字之類的非字串不可被隱性轉型／coerce，直接 400 讓呼叫端自己修。"""
    _u, kid = _setup_tracked(client, db)

    r = client.put(f'/notes/api/tracking/{kid}/memo', json={'text': 5})

    assert r.status_code == 400
    db.session.expire_all()
    from models import TrackedKeyword
    assert db.session.get(TrackedKeyword, kid).memo_text is None


def test_memo_text_rejects_overlong_memo(client, db):
    """備註同樣落在 EncryptedText，DB 端沒有長度限制。比照關鍵字的上限，給一句
    話到幾句話的合理空間（500 字），超過就 400，不悄悄截斷、也不讓它無界成長。"""
    _u, kid = _setup_tracked(client, db)
    r = client.put(f'/notes/api/tracking/{kid}/memo',
                   json={'text': 'x' * 501})
    assert r.status_code == 400
    assert '備註' in r.get_json()['error']
    from models import TrackedKeyword
    db.session.expire_all()
    assert db.session.get(TrackedKeyword, kid).memo_text is None


def test_memo_text_accepts_memo_at_max_len(client, db):
    """邊界值：剛好 500 字要放行。"""
    _u, kid = _setup_tracked(client, db)
    r = client.put(f'/notes/api/tracking/{kid}/memo',
                   json={'text': 'x' * 500})
    assert r.status_code == 200


def test_memo_missing_table_is_404(client, db):
    _u, _kid = _setup_tracked(client, db)
    bogus_id = 9_999_999

    r = client.put(f'/notes/api/tracking/{bogus_id}/memo', json={'text': 'x'})

    assert r.status_code == 404


def test_memo_cross_store_table_is_forbidden(client, db):
    _u, kid = _setup_tracked(client, db, store='B店')
    intruder = _mk_user(db, store='A店')
    _login(client, intruder)

    r = client.put(f'/notes/api/tracking/{kid}/memo', json={'text': 'x'})

    assert r.status_code == 403


# ─── 備註異動 log：只有 set_tracking_memo 寫，且只在文字真的變了才寫 ───

def test_saving_memo_creates_one_log_row_with_old_and_new_text(client, db):
    _u, kid = _setup_tracked(client, db)
    from models import TrackedKeywordMemoLog
    first = f'第一版_{uuid.uuid4().hex[:4]}'
    second = f'第二版_{uuid.uuid4().hex[:4]}'

    client.put(f'/notes/api/tracking/{kid}/memo', json={'text': first})
    r = client.put(f'/notes/api/tracking/{kid}/memo', json={'text': second})
    assert r.status_code == 200

    logs = TrackedKeywordMemoLog.query.filter_by(keyword_id=kid).order_by(
        TrackedKeywordMemoLog.id).all()
    assert len(logs) == 2, '兩次不同文字的寫入應各留一筆 log'
    assert logs[0].old_text is None
    assert logs[0].new_text == first
    assert logs[1].old_text == first
    assert logs[1].new_text == second


def test_resaving_identical_text_creates_no_log(client, db):
    """重存同樣的文字不算一次異動，不該留下噪音列。"""
    _u, kid = _setup_tracked(client, db)
    from models import TrackedKeywordMemoLog
    memo = f'不變的備註_{uuid.uuid4().hex[:4]}'

    client.put(f'/notes/api/tracking/{kid}/memo', json={'text': memo})
    count_after_first = TrackedKeywordMemoLog.query.filter_by(keyword_id=kid).count()
    r = client.put(f'/notes/api/tracking/{kid}/memo', json={'text': memo})

    assert r.status_code == 200
    count_after_second = TrackedKeywordMemoLog.query.filter_by(keyword_id=kid).count()
    assert count_after_second == count_after_first == 1, (
        '重存相同文字不應該再新增一筆 log'
    )


def test_clearing_memo_logs_the_old_text(client, db):
    """清空是這個功能存在的理由：舊文字必須被留在 log 裡，不能真的消失。"""
    _u, kid = _setup_tracked(client, db)
    from models import TrackedKeywordMemoLog
    memo = f'要被清掉的評語_{uuid.uuid4().hex[:4]}'
    client.put(f'/notes/api/tracking/{kid}/memo', json={'text': memo})

    r = client.put(f'/notes/api/tracking/{kid}/memo', json={'text': ''})
    assert r.status_code == 200

    logs = TrackedKeywordMemoLog.query.filter_by(keyword_id=kid).order_by(
        TrackedKeywordMemoLog.id).all()
    assert len(logs) == 2
    clear_log = logs[-1]
    assert clear_log.old_text == memo, '清空前的舊文字必須完整留在 log 裡'
    assert clear_log.new_text == ''


def test_first_save_from_empty_to_empty_creates_no_log(client, db):
    """從沒寫過（None）存一個空字串：等同於什麼都沒發生，不該產生 log。"""
    _u, kid = _setup_tracked(client, db)
    from models import TrackedKeywordMemoLog
    r = client.put(f'/notes/api/tracking/{kid}/memo', json={'text': ''})
    assert r.status_code == 200
    assert TrackedKeywordMemoLog.query.filter_by(keyword_id=kid).count() == 0


def test_memo_log_records_writer_identity(client, db):
    """log 要記下是誰寫的，帳號 id 與快照名稱都要對。"""
    u, kid = _setup_tracked(client, db)
    from models import TrackedKeywordMemoLog
    client.put(f'/notes/api/tracking/{kid}/memo', json={'text': '評語內容'})

    log = TrackedKeywordMemoLog.query.filter_by(keyword_id=kid).first()
    assert log.changed_by == u.id
    assert log.changed_by_name == u.username


# ─── 詳細頁：頁面最上面要顯示這張表的備註，而不是每一列各自一份 ───

def test_detail_page_shows_table_memo_at_top_not_per_row(client, db):
    """存好的備註要出現在詳細頁最上面的備註框，且**只出現一次**——不是像改版
    前那樣每一列各自複製一份。這是這次改版的核心行為，必須真的斷言『只有
    一個備註框』，不能只驗證文字有出現在頁面某處（那樣就算退化回每列各一份、
    測試也不會發現）。

    B1 修正：改版前這裡用 `_setup_tracked` 建一張**空**追蹤表（它的
    docstring 說得沒錯——備註本身測試不需要底層筆記存在），但「只有一個
    備註框、不是每列各一份」這句斷言，恰恰需要頁面上真的有列才測得出來：
    沒有任何 hit-card 時，就算把每一列各自一份的舊版備註框原封不動搬回
    模板，因為根本沒有列可以渲染，計數還是 1，測試照樣綠燈——完全測不出
    這個改版真正要守的迴歸。這裡改成先建兩筆命中，並且額外斷言真的有列
    渲染出來，讓「只有一個」這句話是在『有列』的前提下成立，不是在『沒有
    東西可比較』的空頁面上碰巧為真。
    """
    kw = _uniq('王小明')
    _mk_note(db, title=f'{kw} 的交接一')
    _mk_note(db, title=f'{kw} 的交接二')
    _login(client, _mk_user(db))
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']
    memo = f'整張表的備註_{uuid.uuid4().hex[:4]}'
    client.put(f'/notes/api/tracking/{kid}/memo', json={'text': memo})

    html = client.get(f'/notes/tracking/{kid}').get_data(as_text=True)

    assert html.count('class="hit-card"') >= 2, (
        '這個測試必須在有命中列的頁面上驗證，不能像改版前那樣落在空清單—— '
        '否則「只有一個備註框」這句斷言即使退化回每列各一份也照樣成立'
        '（因為根本沒有列可以渲染出第二個備註框）'
    )
    # 數 `<textarea class="tracking-memo"` 這個開始標籤本身，不能直接數
    # `data-role="memo"` 出現幾次——頁面下方的 JS 原始碼裡也會有一份一模一樣
    # 的字面字串（`document.querySelector('[data-role="memo"]')`），數整份
    # HTML 會把那份 JS 字面值也算進去，巧合湊出跟真的有兩個備註框一樣的數字。
    assert html.count('<textarea class="tracking-memo"') == 1, (
        '備註框應該只有一個（整張表共用），不是每一列各自一個'
    )
    assert memo in html


def test_detail_page_shows_last_memo_editor(client, db):
    """備註被寫過之後，頁面最上面要看得到是誰、什麼時候最後編輯的 —— 跟
    「最後更新」同一種不張揚的呈現方式。

    刻意把 log 的 changed_at 改成很久以前、跟畫面上其他時間戳（scanned_through
    算出來的「最後更新」）明顯錯開，避免比對時間巧合通過。也刻意換一個
    **不同於編輯者**的人來看這一頁：若直接用同一個人存備註又看頁面，
    `editor.username in html` 這個斷言連 nav bar 的「登出 (username)」都能
    矇混過關（因為當下登入者剛好跟編輯者是同一人）——原本掛在命中列上的
    同款測試就是靠這招才躲過這個陷阱，這裡沿用同一套防呆。"""
    editor, kid = _setup_tracked(client, db)
    client.put(f'/notes/api/tracking/{kid}/memo', json={'text': '已口頭告誡'})
    from models import TrackedKeywordMemoLog
    from datetime import datetime as real_datetime
    log = TrackedKeywordMemoLog.query.filter_by(keyword_id=kid).first()
    fixed = real_datetime(2020, 1, 1, 3, 0, 0)
    log.changed_at = fixed
    db.session.commit()

    viewer = _mk_user(db, store='B店')
    _login(client, viewer)

    html = client.get(f'/notes/tracking/{kid}').get_data(as_text=True)

    from tw_time import tw_str
    expected_time = tw_str(fixed, fmt='%m/%d %H:%M')
    assert editor.username in html, '頁面上沒看到編輯者帳號'
    assert expected_time in html, f'頁面上沒看到編輯時間 {expected_time!r}'
    assert '最後編輯' in html


def test_detail_page_shows_no_editor_hint_before_first_edit(client, db):
    """從沒被編輯過的備註不該冒出「XXX 於 -- 編輯」這種空白資訊。"""
    _u, kid = _setup_tracked(client, db)

    html = client.get(f'/notes/tracking/{kid}').get_data(as_text=True)

    assert '最後編輯' not in html


def test_detail_page_shows_latest_editor_not_earliest(client, db):
    """改過兩次備註，頁面要顯示最新一次的編輯者，不是第一次那個人。

    第三個人（跟 u1、u2 都不同）來看這一頁：若頁面誤把「目前登入者」當成
    「最後編輯者」顯示（而不是真的讀 log），這裡會抓到——因為看頁面的人
    既不是 u1 也不是 u2，u2 只可能是因為備註真的讀到了最新一筆 log 才會出現。"""
    from models import TrackedKeywordMemoLog
    u1, kid = _setup_tracked(client, db)
    client.put(f'/notes/api/tracking/{kid}/memo', json={'text': '第一版'})
    u2 = _mk_user(db, store='B店')
    _login(client, u2)
    client.put(f'/notes/api/tracking/{kid}/memo', json={'text': '第二版'})

    viewer = _mk_user(db, store='B店')
    _login(client, viewer)
    html = client.get(f'/notes/tracking/{kid}').get_data(as_text=True)

    assert u2.username in html, '應該顯示最後一次編輯者 u2'
    assert u1.username not in html, '不該顯示第一次的編輯者 u1'
    logs = TrackedKeywordMemoLog.query.filter_by(keyword_id=kid).order_by(
        TrackedKeywordMemoLog.id).all()
    assert len(logs) == 2
    assert logs[0].changed_by_name == u1.username


def test_detail_page_last_editor_tiebreaks_by_id_when_timestamps_tie(client, db):
    """B6：`_memo_edit_info` 只排 `changed_at` 時，兩筆 log 剛好落在同一個
    時間戳（同一微秒內寫入，或 DB 把時間戳截斷到秒）會給出不確定的答案。

    這裡直接把兩筆 log 的 `changed_at` 設成完全相同的值，讓 `changed_at`
    完全無法分辨順序，只留 `id`（嚴格遞增，寫入順序一定跟 id 大小一致）
    可以判斷——若 `_memo_edit_info` 沒有以 id 做次要排序鍵，這個測試在
    SQLite 上的實際行為取決於查詢計畫（通常會退回插入順序或隨機），沒有
    id tiebreak 就無法保證穩定顯示『後寫入』的 u2。"""
    from models import TrackedKeywordMemoLog
    from datetime import datetime as real_datetime
    u1, kid = _setup_tracked(client, db)
    client.put(f'/notes/api/tracking/{kid}/memo', json={'text': '第一版'})
    u2 = _mk_user(db, store='B店')
    _login(client, u2)
    client.put(f'/notes/api/tracking/{kid}/memo', json={'text': '第二版'})

    logs = TrackedKeywordMemoLog.query.filter_by(keyword_id=kid).order_by(
        TrackedKeywordMemoLog.id).all()
    assert len(logs) == 2
    tied = real_datetime(2020, 1, 1, 3, 0, 0)
    logs[0].changed_at = tied
    logs[1].changed_at = tied
    db.session.commit()

    viewer = _mk_user(db, store='B店')
    _login(client, viewer)
    html = client.get(f'/notes/tracking/{kid}').get_data(as_text=True)

    assert u2.username in html, (
        'changed_at 完全相同時，應該以 id 較大（較後寫入）的那筆 log 為準'
    )
    assert u1.username not in html
