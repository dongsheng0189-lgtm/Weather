"""Add Note.is_ai_summary + migrate personal/unclassified → misc + backfill is_ai_summary

Revision ID: 008
Revises: 007
Create Date: 2026-04-30
"""
revision = '008'
down_revision = '007'

import os
import re
import psycopg2
from dotenv import load_dotenv

load_dotenv()

AI_SUMMARY_TITLE_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*摘要\s*$")


def upgrade():
    url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)

    # Step 1-3: 純 SQL DDL/DML（不需解密）
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS is_ai_summary BOOLEAN NOT NULL DEFAULT FALSE")
    cur.execute("ALTER TABLE notes ALTER COLUMN category SET DEFAULT 'misc'")
    cur.execute("UPDATE notes SET category='misc' WHERE category IN ('personal', 'unclassified')")
    moved = cur.rowcount
    cur.close()
    conn.close()
    print(f"[step 1-3] schema OK; moved {moved} rows from personal/unclassified to misc")

    # Step 4: ORM backfill is_ai_summary=True for "YYYY-MM-DD 摘要" titles
    # 必須在有 DB_ENCRYPTION_KEY 的環境跑（Zeabur container）
    if not os.environ.get("DB_ENCRYPTION_KEY"):
        print("[step 4] DB_ENCRYPTION_KEY not set — skipping is_ai_summary backfill (run again on Zeabur)")
        return

    # 延遲匯入：app context 在這時才需要
    from app import create_app
    from models import db, Note

    app = create_app()
    with app.app_context():
        rows = Note.query.filter_by(category="misc").all()
        flagged = 0
        for n in rows:
            title = n.title or ""
            if AI_SUMMARY_TITLE_RE.match(title):
                n.is_ai_summary = True
                flagged += 1
        db.session.commit()
        print(f"[step 4] backfilled is_ai_summary=True for {flagged} notes (out of {len(rows)} misc)")


if __name__ == "__main__":
    upgrade()
