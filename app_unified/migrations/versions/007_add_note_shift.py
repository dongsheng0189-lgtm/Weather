"""Add Note.shift column

Revision ID: 007
Revises: 006
Create Date: 2026-04-29
"""
revision = '007'
down_revision = '006'

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def upgrade():
    url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS shift VARCHAR(1) NULL")

    cur.close()
    conn.close()
    print("Migration 007 done.")


if __name__ == "__main__":
    upgrade()
