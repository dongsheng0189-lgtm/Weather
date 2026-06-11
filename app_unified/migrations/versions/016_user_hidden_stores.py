"""User.hidden_stores: per-super_admin hidden store names (JSON text)

Revision ID: 016
Revises: 015
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa


revision = '016'
down_revision = '015'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('hidden_stores', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('users', 'hidden_stores')
