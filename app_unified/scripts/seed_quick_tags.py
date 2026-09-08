#!/usr/bin/env python3
"""本機測試「快速標籤鍵」用的種子資料（2026-09-08）。

**為什麼需要這支**：快速標籤鍵只會把**已釘選**的標籤渲染成按鈕。本機的
`seed_local.py` 不會建任何標籤，所以直接開頁面會什麼都看不到 —— 不是壞了，
是沒有資料。這支補上測試需要的標籤組合。

建立的東西（idempotent，重跑不會重複建）：

  北店
    ★ 陳店長知道   已釘選 → 會變成一鍵按鈕
    ★ 待追蹤       已釘選 → 會變成一鍵按鈕
      一般標籤     未釘選 → **不該**變成按鈕（用來驗證只有釘選的才有）
      填充1..填充4 未釘選 → 用來把某個項目掛滿 5 個標籤，測上限提示

  南店
    ★ 南店專用     已釘選 → 用來驗證跨店隔離（北店的人看不到）

用法：
    cd app_unified
    set -a; source .env.local; set +a
    ../venv/bin/python scripts/seed_quick_tags.py

清掉重來：
    ../venv/bin/python scripts/seed_quick_tags.py --reset
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                      # noqa: E402
from extensions import db                        # noqa: E402
from models import ChecklistItemTag, NoteTag, Store, Tag  # noqa: E402

# (店別, 名稱, 顏色, 是否釘選)
SEED = [
    ("北店", "陳店長知道", "#3498db", True),
    ("北店", "待追蹤", "#e67e22", True),
    ("北店", "一般標籤", "#95a5a6", False),
    ("北店", "填充1", "#7f8c8d", False),
    ("北店", "填充2", "#7f8c8d", False),
    ("北店", "填充3", "#7f8c8d", False),
    ("北店", "填充4", "#7f8c8d", False),
    ("南店", "南店專用", "#27ae60", True),
]


def reset(app):
    with app.app_context():
        names = [n for _, n, _, _ in SEED]
        tags = Tag.query.filter(Tag.name.in_(names)).all()
        ids = [t.id for t in tags]
        if ids:
            ChecklistItemTag.query.filter(
                ChecklistItemTag.tag_id.in_(ids)).delete(synchronize_session=False)
            NoteTag.query.filter(
                NoteTag.tag_id.in_(ids)).delete(synchronize_session=False)
            for t in tags:
                db.session.delete(t)
            db.session.commit()
        print(f"已清除 {len(ids)} 個測試標籤（含它們的所有指派）")


def seed(app):
    with app.app_context():
        have = {s.name for s in Store.query.all()}
        missing = {store for store, _, _, _ in SEED} - have
        if missing:
            print(f"✗ 本機 DB 沒有這些店家：{missing}")
            print("  先跑 scripts/seed_local.py 建基礎假資料")
            return 1

        created = kept = 0
        for store, name, color, pinned in SEED:
            existing = Tag.query.filter_by(store=store, name=name).first()
            if existing:
                # 釘選狀態以本檔為準（可能上次測試時被手動改過）
                if existing.pinned != pinned:
                    existing.pinned = pinned
                    print(f"  ~ {store} 「{name}」釘選狀態改為 {pinned}")
                kept += 1
                continue
            db.session.add(Tag(store=store, name=name, color=color, pinned=pinned))
            created += 1
        db.session.commit()

        print(f"完成：新建 {created} 個、沿用 {kept} 個\n")
        for store in ("北店", "南店"):
            print(f"{store}：")
            for t in Tag.query.filter_by(store=store).order_by(
                    Tag.pinned.desc(), Tag.name).all():
                mark = "★ 釘選" if t.pinned else "  一般"
                print(f"  {mark}  {t.name}")
        print("\n⚠ 釘選的才會變成一鍵按鈕。每店最多釘 3 個（PIN_LIMIT=3）。")
        return 0


if __name__ == "__main__":
    application = create_app()
    if "--reset" in sys.argv:
        reset(application)
        sys.exit(0)
    sys.exit(seed(application))
