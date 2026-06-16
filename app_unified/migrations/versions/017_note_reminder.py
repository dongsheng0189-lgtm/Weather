"""notes + checklist_items reminder_date + audit snapshot

Revision ID: 017
Revises: 016
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa


revision = '017'
down_revision = '016'
branch_labels = None
depends_on = None


def upgrade():
    for tbl in ('notes', 'checklist_items'):
        op.add_column(tbl, sa.Column('reminder_date', sa.Date(), nullable=True))
        op.add_column(tbl, sa.Column('reminder_set_by', sa.Integer(),
                                     sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))
        op.add_column(tbl, sa.Column('reminder_set_by_name', sa.Text(), nullable=True))
        op.add_column(tbl, sa.Column('reminder_set_at', sa.DateTime(), nullable=True))


def downgrade():
    for tbl in ('notes', 'checklist_items'):
        op.drop_column(tbl, 'reminder_set_at')
        op.drop_column(tbl, 'reminder_set_by_name')
        op.drop_column(tbl, 'reminder_set_by')
        op.drop_column(tbl, 'reminder_date')
