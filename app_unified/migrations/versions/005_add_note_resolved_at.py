"""Add Note.resolved_at for auto-delete after 30 days

Revision ID: 005
Revises: 004
Create Date: 2026-04-15
"""
revision = '005'
down_revision = '004'

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def upgrade():
    url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ NULL")

    cur.close()
    conn.close()
    print("Migration 005 done.")


if __name__ == "__main__":
    upgrade()
