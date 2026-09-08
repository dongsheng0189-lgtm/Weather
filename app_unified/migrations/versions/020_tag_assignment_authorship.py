"""tag assignment authorship: 誰在什麼時候掛上標籤

Revision ID: 020
Revises: 019
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa

revision = '020'
down_revision = '019'
branch_labels = None
depends_on = None

_TABLES = ("note_tags", "checklist_item_tags")


def upgrade():
    for t in _TABLES:
        op.add_column(t, sa.Column('tagged_by', sa.Integer(), nullable=True))
        op.add_column(t, sa.Column('tagged_by_name', sa.Text(), nullable=True))
        op.add_column(t, sa.Column('tagged_at', sa.DateTime(), nullable=True))
        # 與專案其他「誰做的」欄位一致：刪帳號時自動設 NULL，不留孤兒 id
        op.create_foreign_key(f'fk_{t}_tagged_by', t, 'users',
                              ['tagged_by'], ['id'], ondelete='SET NULL')


def downgrade():
    for t in _TABLES:
        op.drop_constraint(f'fk_{t}_tagged_by', t, type_='foreignkey')
        op.drop_column(t, 'tagged_at')
        op.drop_column(t, 'tagged_by_name')
        op.drop_column(t, 'tagged_by')
