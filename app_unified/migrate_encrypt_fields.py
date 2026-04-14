"""
一次性 migration：把既有明文欄位加密寫回。
用法（在 app_unified 目錄執行、env 已設 DB_ENCRYPTION_KEY + DATABASE_URL）：

  python migrate_encrypt_fields.py --dry-run        # 只檢查要加密幾筆，不寫入
  python migrate_encrypt_fields.py                  # 實際執行

安全性：
- 跳過已加密（enc:v1: 前綴）的 row
- 空字串 / NULL 跳過
- 每表 commit 一次，中斷可 resume（已加密的下次不重跑）
"""
import argparse
import sys

from app import create_app
from extensions import db
from crypto import encrypt, is_encrypted


# (table_name, [column_name, ...])
TARGETS = [
    ("notes", ["title", "content", "ai_summary", "ai_outline", "author_name"]),
    ("note_logs", ["note_title", "diff"]),
    ("checklist_items", ["text", "checked_by_name"]),
    ("private_notes", ["title", "content", "ai_summary"]),
    ("private_checklist_items", ["text"]),
]


def migrate_table(table, cols, dry_run=False):
    conn = db.session.connection()
    total_scanned = 0
    total_encrypted = 0

    col_list = ", ".join(['"' + c + '"' for c in cols])
    rows = conn.execute(db.text(f'SELECT id, {col_list} FROM "{table}"')).fetchall()

    for row in rows:
        total_scanned += 1
        row_id = row[0]
        updates = {}
        for idx, c in enumerate(cols, start=1):
            val = row[idx]
            if val is None or val == "":
                continue
            if is_encrypted(val):
                continue
            updates[c] = encrypt(val)
        if not updates:
            continue
        total_encrypted += 1
        if dry_run:
            continue
        set_clause = ", ".join([f'"{k}" = :v_{k}' for k in updates])
        params = {f"v_{k}": v for k, v in updates.items()}
        params["id"] = row_id
        conn.execute(db.text(f'UPDATE "{table}" SET {set_clause} WHERE id = :id'), params)

    if not dry_run:
        db.session.commit()
    return total_scanned, total_encrypted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        print("=" * 60)
        print("DB 欄位加密 migration")
        print(f"模式：{'DRY-RUN（不會寫入）' if args.dry_run else '實際寫入'}")
        print("=" * 60)

        grand_scanned = 0
        grand_encrypted = 0
        for table, cols in TARGETS:
            try:
                s, e = migrate_table(table, cols, dry_run=args.dry_run)
                print(f"  {table:30s}  掃描 {s:5d}  需加密 {e:5d}")
                grand_scanned += s
                grand_encrypted += e
            except Exception as ex:
                print(f"  {table:30s}  ❌ 失敗: {ex}")
                db.session.rollback()
                sys.exit(1)

        print("-" * 60)
        print(f"  合計          掃描 {grand_scanned}  需加密 {grand_encrypted}")
        if args.dry_run and grand_encrypted > 0:
            print("\n重跑時拿掉 --dry-run 才會真的寫入。")
        elif grand_encrypted > 0:
            print("\n✅ 完成。請確認 app 能正常讀取加密後的資料。")
        else:
            print("\n✅ 沒有需要加密的 row（可能已經全加密過）。")


if __name__ == "__main__":
    main()
