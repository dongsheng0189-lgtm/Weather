"""VettingWhitelistPhone: 電話白名單表

Revision ID: 018
Revises: 017
Create Date: 2026-06-30
"""
from alembic import op
import sqlalchemy as sa

revision = '018'
down_revision = '017'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'vetting_whitelist_phones',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('phone', sa.Text(), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_by_name', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table('vetting_whitelist_phones')
