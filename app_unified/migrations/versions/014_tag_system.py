"""Tag system: tags + note_tags + checklist_item_tags

Revision ID: 014
Revises: 013
Create Date: 2026-05-26
"""
from alembic import op
import sqlalchemy as sa


revision = '014'
down_revision = '013'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'tags',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('store', sa.String(8), nullable=False, index=True),
        sa.Column('name', sa.String(20), nullable=False),
        sa.Column('color', sa.String(7), nullable=False),
        sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_by', sa.Integer(),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('store', 'name', name='uq_tags_store_name'),
    )
    op.create_index('ix_tags_pinned', 'tags', ['pinned'])
    op.create_table(
        'note_tags',
        sa.Column('note_id', sa.Integer(),
                  sa.ForeignKey('notes.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('tag_id', sa.Integer(),
                  sa.ForeignKey('tags.id', ondelete='CASCADE'), primary_key=True),
    )
    op.create_table(
        'checklist_item_tags',
        sa.Column('item_id', sa.Integer(),
                  sa.ForeignKey('checklist_items.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('tag_id', sa.Integer(),
                  sa.ForeignKey('tags.id', ondelete='CASCADE'), primary_key=True),
    )


def downgrade():
    op.drop_table('checklist_item_tags')
    op.drop_table('note_tags')
    op.drop_index('ix_tags_pinned', table_name='tags')
    op.drop_table('tags')
