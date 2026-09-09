"""建檔追蹤的掃描引擎。

為什麼一定要增量：Note.title / Note.content / ChecklistItem.text 都是
EncryptedText，DB 裡是密文、SQL 無法比對，所以比對只能把資料撈出來在 Python 端
解密後做。開一次頁面就全掃一遍的話，一店一年三萬多筆項目會愈來愈慢。改成
「命中結果存起來、只掃水位之後異動的資料」，開頁面就永遠只是一次索引查詢。

⚠️ 為什麼要分批 + gevent.sleep(0)：這是一個同步的解密迴圈，跑在 gevent worker
上。一次把幾萬列 .all() 出來，SQLAlchemy 會在 result processing 階段一口氣解密
全部，期間整個 hub 動不了 —— 同一個 worker 上其他人的請求全部停擺，hub_watchdog
會判定阻塞、gunicorn 的 timeout=60 甚至可能 SIGKILL 這個 worker。所以先只撈
primary key（不觸發解密），再一批一批載入物件，批與批之間讓出 hub。
"""
from datetime import datetime, timedelta

import gevent
from flask import current_app
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Note, ChecklistItem, TrackedKeywordHit
from notes.tracking_match import find_hits

SCAN_BATCH = 200

# 水位再往回退幾秒，蓋掉「flush 時間 < commit 可見時間」的漏掃窗口：一列在 T1
# flush（updated_at 已寫成 T1），但要到 T3 commit 才會被別的 session 看到；若
# 掃描恰好在 T1~T3 之間的 T2 開始，這列對它是不可見的，卻已經帶著
# updated_at=T1 < T2，下一輪的 `updated_at > watermark` 就會永遠跳過它，除非
# 它被再次編輯。正常情況下這個窗口只有幾毫秒，但 gevent 底下一個 greenlet可能
# 在 flush 之後、commit 之前被切出去，窗口會被拉長。多掃幾秒不會造成問題
# （重複命中被 seen 集合和 Fix 4 的 SAVEPOINT 擋住），但漏掃是這個功能唯一
# 不能接受的失敗模式，所以寧可用這個 lookback 換保險——這個數字不是隨便給的，
# 之後不要為了「看起來更精準」把它拿掉。
WATERMARK_LOOKBACK_SECONDS = 5


def _target_ids(tracked, since):
    """回傳 (note_ids, item_ids) —— 只查 id，不載入加密欄位（不觸發解密）。

    since=None 代表完整掃描（第一次建檔）。
    """
    note_q = db.session.query(Note.id).filter(
        Note.store == tracked.store,
        Note.deleted_at.is_(None),
        Note.is_ai_summary.isnot(True),
    )
    item_q = db.session.query(ChecklistItem.id).join(
        Note, ChecklistItem.note_id == Note.id
    ).filter(
        Note.store == tracked.store,
        Note.deleted_at.is_(None),
        Note.is_ai_summary.isnot(True),
        ChecklistItem.deleted_at.is_(None),
    )
    if since is not None:
        note_q = note_q.filter(Note.updated_at > since)
        item_q = item_q.filter(ChecklistItem.updated_at > since)
    return [r[0] for r in note_q.all()], [r[0] for r in item_q.all()]


def _batches(ids):
    for i in range(0, len(ids), SCAN_BATCH):
        yield ids[i:i + SCAN_BATCH]


def scan_tracked_keyword(tracked):
    """掃描並寫入新命中，推進水位，commit。回傳本次新增的命中數。

    tracked.scanned_through 是 None 時自動走完整掃描。

    ⚠️ 呼叫端注意：本函式共用呼叫端的 db.session（不開自己的 session）。它會在
    批次處理完後把「自己這批載入進 session 的」Note／ChecklistItem 物件
    expunge 掉，藉此避免整店掃完後這些解密物件一路留到 request 結束（見模組
    註解）。但如果呼叫端在呼叫本函式前後也持有同一批物件裡的某個參照（例如
    呼叫端自己也在編輯的那筆 Note），本函式**不會**動到它——只有呼叫前這個
    物件還不在 session 的 identity map 裡（也就是本函式批次查詢載入的）才會
    被 expunge。呼叫端原本就持有、已在 session 裡的物件維持 attached，之後
    照常賦值＋commit 有效。
    """
    # 水位取「掃描開始」而非結束的時刻：掃描期間被改動的資料下一輪還會再掃到，
    # 寧可重掃一次（有唯一約束擋著、不會重複），也不要漏。再扣掉
    # WATERMARK_LOOKBACK_SECONDS，蓋掉 flush/commit 之間的可見度窗口（見上方
    # 模組註解）。
    started = datetime.utcnow()
    note_ids, item_ids = _target_ids(tracked, tracked.scanned_through)

    # 已存在的命中（含已被剔除的）—— 剔除的必須留在這個集合裡，否則下一輪會把
    # 使用者判定為「不相關」的資料重新抓回來。
    #
    # ⚠️ 這裡故意只查 (target_type, target_id) 兩個欄位，不可以改回
    # `TrackedKeywordHit.query.filter_by(...).all()`。那樣會把每一列都建成完整
    # ORM 物件，SQLAlchemy 在 result processing 階段就要把 note_text /
    # excluded_by_name（都是 EncryptedText）逐列解密——這正是本檔案開頭警告的
    # 「一次 .all() 觸發全量解密、卡住 hub」模式，而且是每次掃描都會發生（就算
    # 這次掃描的新增數是 0）。目前看起來還好只是因為這兩欄目前全部是 NULL；
    # note_text 一旦被使用者填了內容，這裡就會變成同步解密全部歷史命中。
    seen = set(
        db.session.query(TrackedKeywordHit.target_type, TrackedKeywordHit.target_id)
        .filter_by(keyword_id=tracked.id)
    )

    added = 0
    for batch in _batches(note_ids):
        # 載入前先記下 identity map 裡已經有哪些 key——呼叫端可能早就把某些
        # Note 載進了同一個 session（例如它自己正在編輯的那筆）。只有「載入前
        # 不存在」的 key 才是這批查詢自己拉進來的，才可以在批次結束後 expunge；
        # 呼叫端原本就持有的物件絕對不能碰，否則呼叫端之後對它賦值、commit 時
        # 會被靜默丟掉、不會有任何錯誤（見本函式 docstring）。
        preexisting = set(db.session.identity_map.keys())
        rows = Note.query.filter(Note.id.in_(batch)).all()
        records = [("note", n.id, [n.title, n.content]) for n in rows]
        added += _persist(tracked, records, seen)
        # 這批已經處理完，把「這批自己載入的」解密物件從 identity map 放掉——
        # 不然掃完整個大店，這些帶著明文的物件會一路留在 session 裡到整個
        # request 結束。不可用 expunge_all()：那會連 `tracked` 自己也踢出去，
        # 水位就寫不進去了。
        for row in rows:
            if sa_inspect(row).key not in preexisting:
                db.session.expunge(row)
        gevent.sleep(0)

    for batch in _batches(item_ids):
        preexisting = set(db.session.identity_map.keys())
        rows = ChecklistItem.query.filter(ChecklistItem.id.in_(batch)).all()
        records = [("item", i.id, [i.text]) for i in rows]
        added += _persist(tracked, records, seen)
        for row in rows:
            if sa_inspect(row).key not in preexisting:
                db.session.expunge(row)
        gevent.sleep(0)

    tracked.scanned_through = started - timedelta(seconds=WATERMARK_LOOKBACK_SECONDS)
    db.session.commit()
    return added


def _persist(tracked, records, seen):
    added = 0
    for target_type, target_id in find_hits(tracked.keyword, records):
        if (target_type, target_id) in seen:
            continue
        # 兩個 greenlet／worker 可能同時掃到同一個 keyword（例如兩個人同時開
        # 追蹤頁），都認為 (keyword_id, target_type, target_id) 還沒被記錄過。
        # `seen` 只是本 process 的快照，真正擋重複的是 DB 的唯一約束
        # uq_tracked_hit_target，而它是用「丟例外」擋，不是靜默忽略。若不隔離，
        # 輸家會在 commit（或 autoflush）時吃到 IntegrityError，整個 session
        # 連水位都保不住。做法比照 app.py 的 _safe_ddl：用 begin_nested()
        # （SAVEPOINT）把每一筆插入包起來，該筆失敗只回滾自己的 SAVEPOINT，
        # 外層 session 照常可用、其他筆繼續處理。輸掉競速的這一筆不算進
        # added——是別的 process 記錄下來的，不是這次掃描新增的。
        try:
            with db.session.begin_nested():
                db.session.add(TrackedKeywordHit(
                    keyword_id=tracked.id,
                    target_type=target_type,
                    target_id=target_id,
                ))
        except IntegrityError as e:
            # 比照 app.py 的 _safe_ddl：吞掉例外不能連痕跡都不留。這裡預期
            # 只會因為 uq_tracked_hit_target 的競速而觸發（見上方註解），但
            # 若是別的原因撞到 IntegrityError，這行 warning 是唯一留下的證據
            # ——不然這筆資料會永遠、靜默地被跳過（水位之後照樣推進）。
            #
            # 記 e.orig（DBAPI 層的原始訊息）、不記整個 exception：
            # StatementError.__str__ 會把 `[SQL: ...] [parameters: ...]` 一併
            # 印出來，今天綁定參數只有 id/NULL 還好，但 TrackedKeywordHit 的
            # note_text / excluded_by_name 是 EncryptedText，未來若有 insert
            # 路徑帶著這兩欄的值撞進來，完整 exception 就會把明文寫進 log。
            current_app.logger.warning(
                "[tracking-scan] skip 重複命中：keyword_id=%s target=%s:%s -> %s: %s",
                tracked.id, target_type, target_id, type(e).__name__, e.orig,
            )
            # 這筆確實存在（輸掉競速，是別的 process 贏了），跟成功路徑一樣
            # 記進 seen——目前一次掃描內 target id 不會重複出現，加了也不影響
            # 行為，純粹避免日後有人重複呼叫 _persist 時，把明明已存在的目標
            # 誤判成「還沒處理過」而重複嘗試插入。
            seen.add((target_type, target_id))
            continue
        seen.add((target_type, target_id))
        added += 1
    return added
