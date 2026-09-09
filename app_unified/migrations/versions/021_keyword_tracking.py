"""keyword tracking: 建檔追蹤的兩張表

Revision ID: 021
Revises: 020
Create Date: 2026-09-09

⚠️ 這個 migrations/ 系列目前**不會被執行** —— 全 repo 沒有任何地方 import
flask_migrate、Migrate() 一次都沒被呼叫，requirements 也沒有它。實際維護 schema
的是 app.py 開機時的 db.create_all() 加上那批 PG-only 的 ADD COLUMN DDL。
這個檔案是為了維持版本序列不斷、將來若真的接回 Alembic 時有跡可循。
"""
from alembic import op
import sqlalchemy as sa

revision = '021'
down_revision = '020'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'tracked_keywords',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('store', sa.String(length=8), nullable=False),
        sa.Column('keyword', sa.Text(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_by_name', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('scanned_through', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_tracked_keywords_store', 'tracked_keywords', ['store'])

    op.create_table(
        'tracked_keyword_hits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('keyword_id', sa.Integer(), nullable=False),
        sa.Column('target_type', sa.String(length=8), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=False),
        sa.Column('note_text', sa.Text(), nullable=True),
        sa.Column('excluded_at', sa.DateTime(), nullable=True),
        sa.Column('excluded_by', sa.Integer(), nullable=True),
        sa.Column('excluded_by_name', sa.Text(), nullable=True),
        sa.Column('matched_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['keyword_id'], ['tracked_keywords.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['excluded_by'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('keyword_id', 'target_type', 'target_id',
                            name='uq_tracked_hit_target'),
    )
    op.create_index('ix_tracked_keyword_hits_keyword_id', 'tracked_keyword_hits', ['keyword_id'])


def downgrade():
    op.drop_index('ix_tracked_keyword_hits_keyword_id', 'tracked_keyword_hits')
    op.drop_table('tracked_keyword_hits')
    op.drop_index('ix_tracked_keywords_store', 'tracked_keywords')
    op.drop_table('tracked_keywords')
