#!/usr/bin/env python
"""一次性事故診斷：找出某張 checklist 筆記發生了什麼（含已軟刪項目、解密內容）。

用法（在 app_unified/ 目錄下，指向 prod DB，唯讀不改資料）：

    DATABASE_URL="<Zeabur 的 PostgreSQL 連線字串>" \
    DB_ENCRYPTION_KEY="<與 prod 相同的金鑰>" \
    SECRET_KEY=x FLASK_DEV_MODE=0 \
    ../venv/bin/python scripts/diagnose_lost_checklist.py --since "2026-07-24 20:30" --until "2026-07-24 22:00"

或直接指定 note_id：

    ...  ../venv/bin/python scripts/diagnose_lost_checklist.py --note-id 123

重點：DB_ENCRYPTION_KEY 必須是 prod 的金鑰，否則 title/text/diff 解不開會噴錯或亂碼。
時間一律用「台灣時間」輸入，腳本會轉成 UTC 去比對（DB 存 UTC）。
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

# `python scripts/xxx.py` 的 sys.path[0] 是 scripts/、不是 app_unified/，
# 所以下面的 `from app import create_app` 一定要先把上層目錄補進去
# （同 seed_local.py / backfill_thumbnails.py 的做法）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TW = timezone(timedelta(hours=8))


def tw(dt):
    if dt is None:
        return "-"
    return dt.replace(tzinfo=timezone.utc).astimezone(TW).strftime("%Y-%m-%d %H:%M:%S")


def parse_tw(s):
    # 輸入台灣時間字串 → 對應 UTC naive datetime（DB 欄位是 naive UTC）
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
    return dt.replace(tzinfo=TW).astimezone(timezone.utc).replace(tzinfo=None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--note-id", type=int)
    ap.add_argument("--since", help="台灣時間 'YYYY-MM-DD HH:MM'")
    ap.add_argument("--until", help="台灣時間 'YYYY-MM-DD HH:MM'")
    ap.add_argument("--store", help="只看某店別")
    ap.add_argument("--all", action="store_true",
                    help="列出所有 checklist（不分時間），用解密標題+項目數自己找")
    ap.add_argument("--title", help="只列標題含此字串的 checklist（解密後比對）")
    args = ap.parse_args()

    # 讓 import app 能吃到最小組態
    os.environ.setdefault("SECRET_KEY", "diag")
    from app import create_app
    from extensions import db
    from models import Note, ChecklistItem, NoteLog, User

    app = create_app()
    with app.app_context():
        def uname(uid):
            if not uid:
                return "-"
            u = User.query.get(uid)
            return u.username if u else f"(uid={uid} 已刪除)"

        # ---- --all / --title：掃全部 checklist，用解密標題自己找 ----
        if args.all or args.title:
            all_cl = Note.query.filter(Note.note_type == "checklist").order_by(
                Note.updated_at.desc()).all()
            print(f"== 全部 checklist 共 {len(all_cl)} 張"
                  + (f"（標題含 '{args.title}'）" if args.title else "") + " ==\n")
            for n in all_cl:
                try:
                    t = n.title or ""
                except Exception:
                    t = "<解密失敗>"
                if args.title and args.title not in t:
                    continue
                items = ChecklistItem.query.filter_by(note_id=n.id).all()
                alive = sum(1 for i in items if i.deleted_at is None)
                dead = len(items) - alive
                dmark = f"  ★軟刪@{tw(n.deleted_at)}" if n.deleted_at else ""
                print(f"  id={n.id:<5} store={n.store or '-':<8} status={n.status:<10} "
                      f"存活{alive}/軟刪{dead}  updated={tw(n.updated_at)}  by={uname(n.updated_by)}"
                      f"{dmark}  title={t!r}")
            print("\n找到可疑那張後，用 --note-id <id> 再跑一次看完整項目與 log。")
            return

        # ---- 找目標筆記 ----
        q = Note.query.filter(Note.note_type == "checklist")
        if args.note_id:
            q = Note.query.filter(Note.id == args.note_id)  # 指定 id 時不限型別
        if args.store:
            q = q.filter(Note.store == args.store)
        if args.since:
            since = parse_tw(args.since)
            q = q.filter(db.or_(Note.updated_at >= since, Note.deleted_at >= since,
                                Note.created_at >= since))
        if args.until:
            until = parse_tw(args.until)
            q = q.filter(db.or_(Note.updated_at <= until, Note.created_at <= until,
                                Note.deleted_at.is_(None), Note.deleted_at <= until))
        notes = q.order_by(Note.updated_at.desc()).limit(50).all()

        if not notes:
            print("!! 這個條件下找不到任何 checklist 筆記。")
            print("   → 若項目/筆記被『硬刪』(24h cleanup 或 cascade)，資料已不在表中，查不到是正常的。")
            return

        print(f"== 找到 {len(notes)} 張候選筆記 ==\n")
        for n in notes:
            # 注意：relationship 只列 active 項目；這裡直接抓「全部」含軟刪
            all_items = ChecklistItem.query.filter_by(note_id=n.id).order_by(
                ChecklistItem.order_index).all()
            alive = [i for i in all_items if i.deleted_at is None]
            dead = [i for i in all_items if i.deleted_at is not None]

            print("─" * 70)
            print(f"note_id={n.id}  type={n.note_type}  store={n.store}")
            try:
                print(f"  title      : {n.title!r}")
            except Exception as e:
                print(f"  title      : <解密失敗: {e}>  ← 可能金鑰不符")
            print(f"  created_at : {tw(n.created_at)}  by {uname(n.user_id)}")
            print(f"  updated_at : {tw(n.updated_at)}  by {uname(n.updated_by)}")
            print(f"  deleted_at : {tw(n.deleted_at)}  by {uname(n.deleted_by)}"
                  + ("   ★ 整張被軟刪" if n.deleted_at else ""))
            print(f"  items: 現存 {len(alive)} 筆 / 已軟刪 {len(dead)} 筆 / 表中共 {len(all_items)} 筆")

            for i in all_items:
                flag = "  [已軟刪]" if i.deleted_at else ""
                try:
                    txt = repr(i.text)
                except Exception as e:
                    txt = f"<解密失敗: {e}>"
                print(f"    - item {i.id:<6} checked={int(i.is_checked)} "
                      f"upd={tw(i.updated_at) if hasattr(i,'updated_at') else '-'} "
                      f"del={tw(i.deleted_at)} by={uname(i.deleted_by)}{flag}  {txt}")

            # ---- 相關 note_logs（解密後）----
            logs = NoteLog.query.filter_by(note_id=n.id).order_by(
                NoteLog.created_at.desc()).limit(30).all()
            print(f"  note_logs: {len(logs)} 筆")
            for l in logs:
                try:
                    diff = repr(l.diff)
                except Exception as e:
                    diff = f"<解密失敗: {e}>"
                print(f"    · {tw(l.created_at)}  {l.action:<8} by {uname(l.user_id)}  {diff}")
        print("─" * 70)


if __name__ == "__main__":
    main()
