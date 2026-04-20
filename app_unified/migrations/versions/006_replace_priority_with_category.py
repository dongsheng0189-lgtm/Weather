"""Replace Note.priority with Note.category

Revision ID: 006
Revises: 005
Create Date: 2026-04-20
"""
revision = '006'
down_revision = '005'

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def upgrade():
    url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(
        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'unclassified'"
    )
    cur.execute("ALTER TABLE notes DROP COLUMN IF EXISTS priority")

    cur.close()
    conn.close()
    print("Migration 006 done.")


if __name__ == "__main__":
    upgrade()
