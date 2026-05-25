"""Add checklist_items.locked_by + locked_at for item-level lock (F2)

Revision ID: 012
Revises: 011
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa


revision = '012'
down_revision = '011'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('checklist_items',
        sa.Column('locked_by', sa.Integer(), nullable=True))
    op.add_column('checklist_items',
        sa.Column('locked_at', sa.DateTime(), nullable=True))
    op.create_foreign_key(
        'fk_checklist_items_locked_by',
        'checklist_items', 'users',
        ['locked_by'], ['id'],
        ondelete='SET NULL'
    )


def downgrade():
    op.drop_constraint('fk_checklist_items_locked_by', 'checklist_items', type_='foreignkey')
    op.drop_column('checklist_items', 'locked_at')
    op.drop_column('checklist_items', 'locked_by')
