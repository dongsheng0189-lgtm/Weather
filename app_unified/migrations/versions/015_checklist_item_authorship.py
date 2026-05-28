"""F11 ChecklistItem authorship: created_by/created_by_name/updated_by/updated_by_name

Revision ID: 015
Revises: 014
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa


revision = '015'
down_revision = '014'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('checklist_items', sa.Column('created_by', sa.Integer(), nullable=True))
    op.add_column('checklist_items', sa.Column('created_by_name', sa.Text(), nullable=True))
    op.add_column('checklist_items', sa.Column('updated_by', sa.Integer(), nullable=True))
    op.add_column('checklist_items', sa.Column('updated_by_name', sa.Text(), nullable=True))
    op.create_foreign_key('fk_ci_created_by', 'checklist_items', 'users',
                          ['created_by'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_ci_updated_by', 'checklist_items', 'users',
                          ['updated_by'], ['id'], ondelete='SET NULL')


def downgrade():
    op.drop_constraint('fk_ci_updated_by', 'checklist_items', type_='foreignkey')
    op.drop_constraint('fk_ci_created_by', 'checklist_items', type_='foreignkey')
    op.drop_column('checklist_items', 'updated_by_name')
    op.drop_column('checklist_items', 'updated_by')
    op.drop_column('checklist_items', 'created_by_name')
    op.drop_column('checklist_items', 'created_by')
