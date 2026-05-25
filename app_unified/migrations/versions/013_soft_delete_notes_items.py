"""Add deleted_at + deleted_by for soft delete (F1)

Revision ID: 013
Revises: 012
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa


revision = '013'
down_revision = '012'
branch_labels = None
depends_on = None


def upgrade():
    # Note
    op.add_column('notes',
        sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.add_column('notes',
        sa.Column('deleted_by', sa.Integer(), nullable=True))
    op.create_index('ix_notes_deleted_at', 'notes', ['deleted_at'])
    op.create_foreign_key(
        'fk_notes_deleted_by',
        'notes', 'users',
        ['deleted_by'], ['id'],
        ondelete='SET NULL'
    )
    # ChecklistItem
    op.add_column('checklist_items',
        sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.add_column('checklist_items',
        sa.Column('deleted_by', sa.Integer(), nullable=True))
    op.create_index('ix_checklist_items_deleted_at', 'checklist_items', ['deleted_at'])
    op.create_foreign_key(
        'fk_checklist_items_deleted_by',
        'checklist_items', 'users',
        ['deleted_by'], ['id'],
        ondelete='SET NULL'
    )


def downgrade():
    op.drop_constraint('fk_checklist_items_deleted_by', 'checklist_items', type_='foreignkey')
    op.drop_index('ix_checklist_items_deleted_at', 'checklist_items')
    op.drop_column('checklist_items', 'deleted_by')
    op.drop_column('checklist_items', 'deleted_at')
    op.drop_constraint('fk_notes_deleted_by', 'notes', type_='foreignkey')
    op.drop_index('ix_notes_deleted_at', 'notes')
    op.drop_column('notes', 'deleted_by')
    op.drop_column('notes', 'deleted_at')
