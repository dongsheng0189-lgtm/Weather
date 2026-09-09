"""命中列的備註與剔除。"""
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


def _setup_one_hit(client, db, store='B店'):
    kw = _uniq('王小明')
    _mk_note(db, store=store, title=f'{kw} 的交接')
    u = _mk_user(db, store=store)
    _login(client, u)
    kid = client.post('/notes/api/tracking', json={'keyword': kw}).get_json()['id']
    from models import TrackedKeywordHit
    hit = TrackedKeywordHit.query.filter_by(keyword_id=kid).first()
    return u, kid, hit


def test_set_note_text(client, db):
    _u, _kid, hit = _setup_one_hit(client, db)
    memo = f'已口頭告誡_{uuid.uuid4().hex[:4]}'

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': memo})

    assert r.status_code == 200
    assert r.get_json()['note_text'] == memo
    db.session.expire_all()
    from models import TrackedKeywordHit
    assert db.session.get(TrackedKeywordHit, hit.id).note_text == memo


def test_note_text_can_be_cleared(client, db):
    _u, _kid, hit = _setup_one_hit(client, db)
    client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': '先寫一段'})

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': ''})

    assert r.status_code == 200
    assert r.get_json()['note_text'] == ''
    db.session.expire_all()
    from models import TrackedKeywordHit
    assert db.session.get(TrackedKeywordHit, hit.id).note_text == ''


def test_note_text_missing_key_is_rejected(client, db):
    """PUT 是整份取代，明確傳 '' 才是「清空」；根本沒帶 text 欄位是另一回事，
    備註是寫死的評語、清掉了找不回來，必須擋在 400，不可靜默清空。"""
    _u, _kid, hit = _setup_one_hit(client, db)
    memo = f'已口頭告誡_{uuid.uuid4().hex[:4]}'
    client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': memo})

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={})

    assert r.status_code == 400
    db.session.expire_all()
    from models import TrackedKeywordHit
    assert db.session.get(TrackedKeywordHit, hit.id).note_text == memo


def test_note_text_null_is_rejected(client, db):
    """{"text": null} 跟沒帶欄位是同一種「缺少」，同樣不可被當成清空。"""
    _u, _kid, hit = _setup_one_hit(client, db)
    memo = f'已口頭告誡_{uuid.uuid4().hex[:4]}'
    client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': memo})

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': None})

    assert r.status_code == 400
    db.session.expire_all()
    from models import TrackedKeywordHit
    assert db.session.get(TrackedKeywordHit, hit.id).note_text == memo


def test_note_text_non_string_is_rejected(client, db):
    """數字之類的非字串不可被隱性轉型／coerce，直接 400 讓呼叫端自己修。"""
    _u, _kid, hit = _setup_one_hit(client, db)

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': 5})

    assert r.status_code == 400
    db.session.expire_all()
    from models import TrackedKeywordHit
    assert db.session.get(TrackedKeywordHit, hit.id).note_text is None


def test_note_text_rejects_overlong_memo(client, db):
    """備註同樣落在 EncryptedText，DB 端沒有長度限制。比照關鍵字的上限，給一句
    話到幾句話的合理空間（500 字），超過就 400，不悄悄截斷、也不讓它無界成長。"""
    _u, _kid, hit = _setup_one_hit(client, db)
    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note',
                   json={'text': 'x' * 501})
    assert r.status_code == 400
    assert '備註' in r.get_json()['error']
    from models import TrackedKeywordHit
    db.session.expire_all()
    assert db.session.get(TrackedKeywordHit, hit.id).note_text is None


def test_note_text_accepts_memo_at_max_len(client, db):
    """邊界值：剛好 500 字要放行。"""
    _u, _kid, hit = _setup_one_hit(client, db)
    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note',
                   json={'text': 'x' * 500})
    assert r.status_code == 200


# ─── 備註異動 log：只有 set_hit_note 寫，且只在文字真的變了才寫 ───

def test_saving_memo_creates_one_log_row_with_old_and_new_text(client, db):
    _u, _kid, hit = _setup_one_hit(client, db)
    from models import TrackedKeywordMemoLog
    first = f'第一版_{uuid.uuid4().hex[:4]}'
    second = f'第二版_{uuid.uuid4().hex[:4]}'

    client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': first})
    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': second})
    assert r.status_code == 200

    logs = TrackedKeywordMemoLog.query.filter_by(hit_id=hit.id).order_by(
        TrackedKeywordMemoLog.id).all()
    assert len(logs) == 2, '兩次不同文字的寫入應各留一筆 log'
    assert logs[0].old_text is None
    assert logs[0].new_text == first
    assert logs[1].old_text == first
    assert logs[1].new_text == second


def test_resaving_identical_text_creates_no_log(client, db):
    """重存同樣的文字不算一次異動，不該留下噪音列。"""
    _u, _kid, hit = _setup_one_hit(client, db)
    from models import TrackedKeywordMemoLog
    memo = f'不變的備註_{uuid.uuid4().hex[:4]}'

    client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': memo})
    count_after_first = TrackedKeywordMemoLog.query.filter_by(hit_id=hit.id).count()
    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': memo})

    assert r.status_code == 200
    count_after_second = TrackedKeywordMemoLog.query.filter_by(hit_id=hit.id).count()
    assert count_after_second == count_after_first == 1, (
        '重存相同文字不應該再新增一筆 log'
    )


def test_clearing_memo_logs_the_old_text(client, db):
    """清空是這個功能存在的理由：舊文字必須被留在 log 裡，不能真的消失。"""
    _u, _kid, hit = _setup_one_hit(client, db)
    from models import TrackedKeywordMemoLog
    memo = f'要被清掉的評語_{uuid.uuid4().hex[:4]}'
    client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': memo})

    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': ''})
    assert r.status_code == 200

    logs = TrackedKeywordMemoLog.query.filter_by(hit_id=hit.id).order_by(
        TrackedKeywordMemoLog.id).all()
    assert len(logs) == 2
    clear_log = logs[-1]
    assert clear_log.old_text == memo, '清空前的舊文字必須完整留在 log 裡'
    assert clear_log.new_text == ''


def test_first_save_from_empty_to_empty_creates_no_log(client, db):
    """從沒寫過（None）存一個空字串：等同於什麼都沒發生，不該產生 log。"""
    _u, _kid, hit = _setup_one_hit(client, db)
    from models import TrackedKeywordMemoLog
    r = client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': ''})
    assert r.status_code == 200
    assert TrackedKeywordMemoLog.query.filter_by(hit_id=hit.id).count() == 0


def test_memo_log_records_writer_identity(client, db):
    """log 要記下是誰寫的，帳號 id 與快照名稱都要對。"""
    u, _kid, hit = _setup_one_hit(client, db)
    from models import TrackedKeywordMemoLog
    client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': '評語內容'})

    log = TrackedKeywordMemoLog.query.filter_by(hit_id=hit.id).first()
    assert log.changed_by == u.id
    assert log.changed_by_name == u.username


def test_exclude_and_restore(client, db):
    u, _kid, hit = _setup_one_hit(client, db)
    from models import TrackedKeywordHit

    r = client.post(f'/notes/api/tracking/hits/{hit.id}/exclude',
                    json={'excluded': True})
    assert r.status_code == 200
    assert r.get_json()['excluded'] is True
    db.session.expire_all()
    row = db.session.get(TrackedKeywordHit, hit.id)
    assert row.excluded_at is not None
    assert row.excluded_by == u.id
    assert row.excluded_by_name == u.username

    r = client.post(f'/notes/api/tracking/hits/{hit.id}/exclude',
                    json={'excluded': False})
    assert r.status_code == 200
    assert r.get_json()['excluded'] is False
    db.session.expire_all()
    row = db.session.get(TrackedKeywordHit, hit.id)
    assert row.excluded_at is None
    assert row.excluded_by is None
    assert row.excluded_by_name is None


def test_exclude_rejects_truthy_string_false(client, db):
    """字串 "false" 是 truthy，若沿用 truthy 判斷會把「想還原」誤判成「剔除」，
    方向整個反過來、而且是靜默發生。必須要求真布林，非布林一律 400、不生效。"""
    _u, _kid, hit = _setup_one_hit(client, db)
    from models import TrackedKeywordHit

    r = client.post(f'/notes/api/tracking/hits/{hit.id}/exclude',
                    json={'excluded': 'false'})

    assert r.status_code == 400
    db.session.expire_all()
    row = db.session.get(TrackedKeywordHit, hit.id)
    assert row.excluded_at is None


def test_exclusion_is_soft_delete_row_survives(client, db):
    """剔除不可以真刪 —— 真刪的話下一輪增量掃描會把它抓回來。"""
    _u, _kid, hit = _setup_one_hit(client, db)
    from models import TrackedKeywordHit
    r = client.post(f'/notes/api/tracking/hits/{hit.id}/exclude', json={'excluded': True})
    assert r.status_code == 200
    row = db.session.get(TrackedKeywordHit, hit.id)
    assert row is not None
    assert row.excluded_at is not None


def test_cross_store_hit_is_forbidden(client, db):
    _u, _kid, hit = _setup_one_hit(client, db, store='B店')
    intruder = _mk_user(db, store='A店')
    _login(client, intruder)

    assert client.put(f'/notes/api/tracking/hits/{hit.id}/note',
                      json={'text': 'x'}).status_code == 403
    assert client.post(f'/notes/api/tracking/hits/{hit.id}/exclude',
                       json={'excluded': True}).status_code == 403


def test_hit_not_found_is_404(client, db):
    """_get_hit_or_404 的找不到分支：跟跨店的 403 是相鄰分支，各自要有測試守著，
    避免日後改動時把兩者混淆。"""
    _u, _kid, _hit = _setup_one_hit(client, db)
    bogus_id = 9_999_999

    assert client.put(f'/notes/api/tracking/hits/{bogus_id}/note',
                      json={'text': 'x'}).status_code == 404
    assert client.post(f'/notes/api/tracking/hits/{bogus_id}/exclude',
                       json={'excluded': True}).status_code == 404


# ─── Fix 4：詳細頁「已剔除」區塊的還原，走的就是這條 exclude API ───
#
# 剔除之前是「一鍵、整店共用、UI 上沒有任何路徑能還原」——API 本來就接受
# {excluded: false}，但沒有任何模板送過這個值。這裡驗證的是還原按鈕實際會呼叫
# 的那個端點，走完整趟：剔除 → 從主清單消失、進入已剔除清單 → 用同一個端點
# 還原 → 回到主清單、從已剔除清單消失。

def test_restore_via_ui_endpoint_returns_hit_to_visible_rows(client, db):
    """還原（{excluded: false}）用的是跟「不相關」同一支 API
    （notes.toggle_hit_exclude），也就是 tracking_detail.html 的還原按鈕實際
    會呼叫的端點 —— 直接驗證 _detail_rows / _excluded_rows 這兩份『看得到』的
    清單，剔除／還原後有沒有正確地互相搬動。"""
    from notes.tracking import _detail_rows, _excluded_rows
    from models import TrackedKeyword
    u, kid, hit = _setup_one_hit(client, db)
    t = db.session.get(TrackedKeyword, kid)
    assert len(_detail_rows(t)) == 1
    assert _excluded_rows(t) == []

    r = client.post(f'/notes/api/tracking/hits/{hit.id}/exclude', json={'excluded': True})
    assert r.status_code == 200
    assert _detail_rows(t) == []
    excluded = _excluded_rows(t)
    assert len(excluded) == 1
    assert excluded[0]['hit_id'] == hit.id
    assert excluded[0]['excluded_by_name'] == u.username

    r = client.post(f'/notes/api/tracking/hits/{hit.id}/exclude', json={'excluded': False})
    assert r.status_code == 200
    assert len(_detail_rows(t)) == 1, '還原後這筆命中應該重新出現在主清單'
    assert _excluded_rows(t) == [], '還原後不該再留在已剔除清單裡'


def test_detail_page_shows_last_memo_editor(client, db):
    """備註被寫過之後，該筆命中卡片內要看得到是誰、什麼時候最後編輯的 ——
    跟「最後更新」同一種不張揚的呈現方式，不是額外一個大區塊。

    刻意把 log 的 changed_at 改成很久以前、跟畫面上其他時間戳（scanned_through
    算出來的「最後更新」）明顯錯開，並且只在這張 hit-card 的區塊內找 ——
    整頁比對 `username in html` 會被 nav bar 的「登出 (username)」矇混過關
    （base.html 一定會印出目前登入者的帳號），比對時間也可能剛好跟同一分鐘
    渲染出的「最後更新」重疊而巧合通過。"""
    u, kid, hit = _setup_one_hit(client, db)
    client.put(f'/notes/api/tracking/hits/{hit.id}/note', json={'text': '已口頭告誡'})
    from models import TrackedKeywordMemoLog
    from datetime import datetime as real_datetime
    log = TrackedKeywordMemoLog.query.filter_by(hit_id=hit.id).first()
    fixed = real_datetime(2020, 1, 1, 3, 0, 0)
    log.changed_at = fixed
    db.session.commit()

    html = client.get(f'/notes/tracking/{kid}').get_data(as_text=True)

    idx = html.find(f'data-hit="{hit.id}"')
    assert idx != -1, f'找不到 hit {hit.id} 的卡片'
    card_html = html[idx:idx + 1500]
    from tw_time import tw_str
    expected_time = tw_str(fixed, fmt='%m/%d %H:%M')
    assert u.username in card_html, '卡片內沒看到編輯者帳號'
    assert expected_time in card_html, f'卡片內沒看到編輯時間 {expected_time!r}'
    assert '最後編輯' in card_html


def test_detail_page_shows_no_editor_hint_before_first_edit(client, db):
    """從沒被編輯過的備註不該冒出「XXX 於 -- 編輯」這種空白資訊。"""
    _u, kid, _hit = _setup_one_hit(client, db)

    html = client.get(f'/notes/tracking/{kid}').get_data(as_text=True)

    assert '最後編輯' not in html


def test_detail_page_renders_excluded_section(client, db):
    """詳細頁要看得到「已剔除 (n)」區塊，列出標題、剔除者、剔除時間、還原按鈕
    ——這四樣資訊過去雖然都有記進 DB（excluded_by / excluded_by_name /
    excluded_at），卻沒有任何模板渲染出來，使用者看不到、也還原不了。"""
    _u, kid, hit = _setup_one_hit(client, db)
    client.post(f'/notes/api/tracking/hits/{hit.id}/exclude', json={'excluded': True})

    html = client.get(f'/notes/tracking/{kid}').get_data(as_text=True)

    assert '已剔除 (1)' in html
    assert f'data-hit="{hit.id}"' in html
    assert 'data-role="restore-exclude"' in html
    assert '還原' in html
