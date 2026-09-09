#!/usr/bin/env python3
"""本機測試「建檔追蹤」功能用的種子資料（2026-09-09）。

**為什麼需要這支**：`seed_local.py` 只建了泛用的假筆記，不會出現同一個名字
在多筆筆記／項目裡重複出現，沒辦法用來手動驗證建檔追蹤的累積、增量掃描、
軟刪除排除、跨店隔離這些行為。這支補上需要的資料。

⚠️ 刻意**不**建立任何 `TrackedKeyword`——按「建檔追蹤」建表、看第一次完整
掃描發生，這個動作本身就是要給人手動做的，不能被種子腳本代勞。這支只準備
「按下去之後掃得到什麼」的底層筆記／項目。

資料分成幾群，各自對應一個要手動驗證的行為：

1. **關鍵字 A =「Kevin」（北店，重度群）**：同一個名字橫跨多篇筆記、不同
   日期，分別命中筆記標題（5 筆）、筆記內文（5 筆）、checklist 項目文字
   （5 筆）——共 15 筆命中，讓詳細頁看起來像真的在累積的表格，且三種命中
   路徑都看得到，由新到舊排序。
2. **大小寫 / 語意位置**：標題群裡有一筆刻意全大寫「KEVIN」、內文群裡有一筆
   刻意全小寫「kevin」——證明是不分大小寫的子字串比對，不是完整詞比對；
   多筆內文刻意把關鍵字埋在句子中間，不是開頭。
3. **關鍵字 B =「陳美玲」（北店，輕量群，3 筆）**：用來驗證「兩個追蹤表
   互不干擾」——建兩張表分別搜尋 A、B，命中數不該互相污染。
4. **跨店同名（南店，2 筆）**：南店也有含「Kevin」的筆記。在南店建
   Kevin 的追蹤表應該只看到這 2 筆，驗證跨店隔離（追蹤表用 store 分開）。
5. **軟刪除排除（北店，各 1 筆）**：一篇整篇軟刪除的筆記＋一個母筆記存活
   但自己被軟刪的 checklist 項目，兩者都含「Kevin」，但都不該出現在命中
   （`tracking_scan._target_ids` 過濾 `deleted_at`）。
6. **AI 摘要排除（北店，1 筆）**：`is_ai_summary=True` 的筆記含「Kevin」，
   不該出現在命中（`_target_ids` 過濾 `is_ai_summary`）。
7. **新舊分布**：`updated_at` 從 60 天前到今天都有、刻意分散（且都是明確
   賦值——`updated_at` 沒有 `onupdate=`，不手動設就會全部停在建立當下），
   讓「第一次建表＝完整掃描」之後，使用者之後編輯某一筆舊資料時能實際看到
   增量掃描抓到新命中、水位確實往前推進。

用法：
    cd app_unified
    set -a; source .env.local; set +a
    ../venv/bin/python scripts/seed_tracking_demo.py

清掉重來：
    ../venv/bin/python scripts/seed_tracking_demo.py --reset

Idempotent：以「筆記標題（解密後的明文）是否已存在於該店」判斷是否重建，
重跑不會製造重複資料；會印出這次新建 / 沿用了幾筆。
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 同 seed_local.py：絕不對非 sqlite（例如手滑指到 prod PG）的 DATABASE_URL 動手。
db_url = os.environ.get("DATABASE_URL", "")
if not db_url.startswith("sqlite:"):
    print(f"REFUSE: DATABASE_URL is {db_url!r}, not sqlite:// — refusing to seed.")
    print("Set DATABASE_URL=sqlite:///dev.db (or source .env.local) and rerun.")
    sys.exit(1)

if not os.environ.get("DB_ENCRYPTION_KEY"):
    print("REFUSE: DB_ENCRYPTION_KEY not set. Source .env.local first.")
    sys.exit(1)

from app import create_app                              # noqa: E402
from extensions import db                                # noqa: E402
from models import Store, User, Note, ChecklistItem      # noqa: E402

STORE_A = "北店"
STORE_B = "南店"
KEYWORD_A = "Kevin"
KEYWORD_B = "陳美玲"

REQUIRED_USERS = ["alice", "bob", "admin_north", "carol", "dave", "admin_south"]


def _spec_notes(now):
    """回傳這支腳本要種的所有筆記規格（list of dict）。

    每筆：store / author（seed_local.py 建好的 username）/ title / content /
    note_type / category / is_ai_summary / days_ago（updated_at = now -
    這麼多天）/ deleted_days_ago（給定就代表這篇筆記本身要軟刪）/
    items（可選，checklist 項目規格 list，格式同上但欄位是 text）。
    """
    return [
        # ── 1+2. 關鍵字 A：標題命中（5 筆，含大寫示範 T5） ──────────
        dict(store=STORE_A, author="alice", title="Kevin 請假通知",
             content="本週三整天請假，已核准。",
             note_type="note", category="employee", days_ago=60),
        dict(store=STORE_A, author="bob", title="换班：Kevin 代班",
             content="因臨時有事，晚班換 Kevin 代。",
             note_type="note", category="employee", days_ago=45),
        dict(store=STORE_A, author="admin_north", title="教育訓練 - Kevin 場次",
             content="新進人員教育訓練名單。",
             note_type="note", category="employee", days_ago=20),
        dict(store=STORE_A, author="alice", title="夜班交接紀錄（Kevin）",
             content="交接內容：收銀機核對完成。",
             note_type="note", category="employee", days_ago=9),
        dict(store=STORE_A, author="bob", title="KEVIN 遲到記錄",
             content="今天上班遲到 15 分鐘。",
             note_type="note", category="employee", days_ago=2),

        # ── 1+2. 關鍵字 A：內文命中（5 筆，含小寫示範 C2、關鍵字重複出現 C4） ──
        dict(store=STORE_A, author="bob", title="客訴處理紀錄 #12",
             content="今天客人反映咖啡太苦，Kevin 主動致歉並更換一杯，客人表示滿意。",
             note_type="note", category="customer", days_ago=35),
        dict(store=STORE_A, author="alice", title="每日巡店筆記",
             content="巡店時發現冰箱溫度異常，kevin 說明天請廠商來看。",
             note_type="note", category="machine", days_ago=25),
        dict(store=STORE_A, author="admin_north", title="供應商送貨紀錄",
             content="今日送貨延遲兩小時，物料點收正常，這次是 Kevin 簽收確認的。",
             note_type="note", category="misc", days_ago=15),
        dict(store=STORE_A, author="bob", title="員工排班討論",
             content="下週排班初稿，Kevin 提議週末加開一位人手，待討論後定案，"
                     "Kevin 會再確認一次時段。",
             note_type="note", category="employee", days_ago=8),
        dict(store=STORE_A, author="alice", title="今日交班備忘",
             content="收銀機零錢不足，已請 Kevin 去附近超商換零錢。",
             note_type="note", category="misc", days_ago=1),

        # ── 1+2. 關鍵字 A：checklist 項目命中（5 筆，母筆記標題/內文都不含關鍵字） ──
        dict(store=STORE_A, author="alice", title="開店檢查清單 A",
             content="每日開店例行檢查。", note_type="checklist",
             category="misc", days_ago=40,
             items=[dict(text="檢查冰箱溫度", days_ago=40),
                    dict(text="提醒 Kevin 補簽收據", days_ago=40)]),
        dict(store=STORE_A, author="bob", title="開店檢查清單 B",
             content="每日開店例行檢查。", note_type="checklist",
             category="misc", days_ago=18,
             items=[dict(text="確認 Kevin 完成教育訓練簽名", days_ago=18),
                    dict(text="清潔門口地墊", days_ago=18)]),
        dict(store=STORE_A, author="admin_north", title="收班檢查清單 C",
             content="每日收班例行檢查。", note_type="checklist",
             category="misc", days_ago=12,
             items=[dict(text="kevin 已清點現金無誤", days_ago=12),
                    dict(text="垃圾清運完成", days_ago=12)]),
        dict(store=STORE_A, author="alice", title="收班檢查清單 D",
             content="每日收班例行檢查。", note_type="checklist",
             category="misc", days_ago=5,
             items=[dict(text="與 Kevin 確認明日班表", days_ago=5),
                    dict(text="關閉招牌燈", days_ago=5)]),
        dict(store=STORE_A, author="bob", title="收班檢查清單 E",
             content="每日收班例行檢查。", note_type="checklist",
             category="misc", days_ago=0,
             items=[dict(text="Kevin 負責的區域已消毒完成", days_ago=0),
                    dict(text="鎖後門", days_ago=0)]),

        # ── 5. 軟刪除排除：整篇筆記軟刪（含關鍵字，不該被掃到） ──────
        dict(store=STORE_A, author="admin_north", title="Kevin 離職申請（已下架）",
             content="員工離職申請書，因故已撤回並下架。",
             note_type="note", category="employee", days_ago=30,
             deleted_days_ago=10),

        # ── 5. 軟刪除排除：母筆記存活，項目自己被軟刪（含關鍵字，不該被掃到） ──
        dict(store=STORE_A, author="alice", title="收班檢查清單 F",
             content="每日收班例行檢查。", note_type="checklist",
             category="misc", days_ago=7,
             items=[dict(text="Kevin 忘記填寫的項目（已刪除）", days_ago=7,
                         item_deleted_days_ago=3),
                    dict(text="拖地完成", days_ago=7)]),

        # ── 6. AI 摘要排除（含關鍵字，不該被掃到） ─────────────────
        dict(store=STORE_A, author="admin_north", title="AI 摘要 - 本週重點",
             content="本週由 Kevin 處理多起客訴，表現良好，整體營運正常。",
             note_type="note", category="misc", days_ago=3,
             is_ai_summary=True),

        # ── 3. 關鍵字 B（陳美玲）：小群，3 筆，驗證兩張追蹤表互不污染 ──
        dict(store=STORE_A, author="alice",
             title="陳美玲 到職公告", content="歡迎新同事到職。",
             note_type="note", category="employee", days_ago=6),
        dict(store=STORE_A, author="bob", title="人員異動紀錄",
             content="陳美玲 已完成教育訓練，正式排班。",
             note_type="note", category="employee", days_ago=4),
        dict(store=STORE_A, author="admin_north", title="開店檢查清單 G",
             content="每日開店例行檢查。", note_type="checklist",
             category="misc", days_ago=2,
             items=[dict(text="請 陳美玲 協助盤點", days_ago=2)]),

        # ── 4. 跨店同名（南店）：驗證追蹤表依店別隔離 ───────────────
        dict(store=STORE_B, author="carol", title="Kevin 支援南店紀錄",
             content="今天北店 Kevin 支援南店晚班。",
             note_type="note", category="employee", days_ago=5),
        dict(store=STORE_B, author="dave", title="南店巡店筆記",
             content="巡店正常，順便提到 Kevin 上週也來支援過。",
             note_type="note", category="machine", days_ago=3),
    ]


def _make_note(spec, author, now):
    n = Note(
        user_id=author.id,
        author_name=author.username,
        title=spec["title"],
        content=spec["content"],
        store=spec["store"],
        note_type=spec.get("note_type", "note"),
        category=spec.get("category", "misc"),
        status="pending",
        is_ai_summary=spec.get("is_ai_summary", False),
        created_at=now - timedelta(days=spec["days_ago"]),
        updated_at=now - timedelta(days=spec["days_ago"]),
    )
    if spec.get("deleted_days_ago") is not None:
        n.deleted_at = now - timedelta(days=spec["deleted_days_ago"])
        n.deleted_by = author.id
    return n


def seed(app):
    with app.app_context():
        have_stores = {s.name for s in Store.query.all()}
        missing_stores = {STORE_A, STORE_B} - have_stores
        if missing_stores:
            print(f"✗ 本機 DB 沒有這些店家：{missing_stores}")
            print("  先跑 scripts/seed_local.py 建基礎假資料")
            return 1

        users_by_name = {u.username: u for u in User.query.all()}
        missing_users = [u for u in REQUIRED_USERS if u not in users_by_name]
        if missing_users:
            print(f"✗ 本機 DB 缺少這些使用者：{missing_users}")
            print("  先跑 scripts/seed_local.py 建基礎假資料")
            return 1

        now = datetime.utcnow()
        specs = _spec_notes(now)

        # 先把兩家店現有的筆記全撈出來（含已軟刪的——軟刪筆記的標題也要能比對
        # 到，不然每次重跑都會把 D1「Kevin 離職申請（已下架）」重種一份）。
        # 明文比對：EncryptedText 在屬性存取當下就已透明解密，跟其他程式碼讀
        # `note.title` 的方式一致，不需要（也無法）在 SQL 端過濾密文。
        existing_notes = {}
        for store in (STORE_A, STORE_B):
            for n in Note.query.filter_by(store=store).all():
                existing_notes.setdefault(store, {})[n.title] = n

        created_notes = kept_notes = 0
        created_items = kept_items = 0

        for spec in specs:
            store = spec["store"]
            author = users_by_name[spec["author"]]
            bucket = existing_notes.setdefault(store, {})
            note = bucket.get(spec["title"])
            if note is None:
                note = _make_note(spec, author, now)
                db.session.add(note)
                db.session.flush()   # 拿到 note.id，給下面建 items 用
                bucket[spec["title"]] = note
                created_notes += 1
            else:
                kept_notes += 1

            if not spec.get("items"):
                continue

            existing_items = {
                i.text: i for i in ChecklistItem.query.filter_by(note_id=note.id).all()
            }
            for j, item_spec in enumerate(spec["items"]):
                if item_spec["text"] in existing_items:
                    kept_items += 1
                    continue
                item = ChecklistItem(
                    note_id=note.id,
                    order_index=j,
                    text=item_spec["text"],
                    is_checked=False,
                    created_by=author.id,
                    created_by_name=author.username,
                    created_at=now - timedelta(days=item_spec["days_ago"]),
                    updated_at=now - timedelta(days=item_spec["days_ago"]),
                )
                if item_spec.get("item_deleted_days_ago") is not None:
                    item.deleted_at = now - timedelta(days=item_spec["item_deleted_days_ago"])
                    item.deleted_by = author.id
                db.session.add(item)
                created_items += 1

        db.session.commit()

        print(f"完成：筆記新建 {created_notes}、沿用 {kept_notes}；"
              f"項目新建 {created_items}、沿用 {kept_items}\n")
        print("接下來請手動操作（腳本刻意不建 TrackedKeyword，讓你看第一次完整掃描）：")
        print(f"  1. 以 {STORE_A} 身分（alice / bob / admin_north）開 "
              f"http://localhost:5000/notes/tracking/ → 建檔追蹤「{KEYWORD_A}」")
        print(f"     預期命中：15 筆（5 標題 + 5 內文 + 5 checklist 項目，"
              f"由新到舊；不含已軟刪的 1 篇筆記＋1 個項目、也不含 1 篇 AI 摘要）")
        print(f"  2. 同一家店再建檔追蹤「{KEYWORD_B}」")
        print(f"     預期命中：3 筆（不受「{KEYWORD_A}」那張表影響，反之亦然）")
        print(f"  3. 換 {STORE_B} 身分（carol / dave）建檔追蹤「{KEYWORD_A}」")
        print(f"     預期命中：2 筆（跟 {STORE_A} 那張表是分開的、互不可見）")
        return 0


def reset(app):
    with app.app_context():
        titles = {s["title"] for s in _spec_notes(datetime.utcnow())}
        notes = [n for store in (STORE_A, STORE_B)
                 for n in Note.query.filter_by(store=store).all()
                 if n.title in titles]
        for n in notes:
            # cascade 設在 Note.checklist_items 這端（"all, delete-orphan"），
            # 刪 note 會連同它的 checklist items 一起清掉。
            db.session.delete(n)
        db.session.commit()
        print(f"已清除 {len(notes)} 篇示範筆記（含其 checklist 項目）")


if __name__ == "__main__":
    application = create_app()
    if "--reset" in sys.argv:
        reset(application)
        sys.exit(0)
    sys.exit(seed(application))
