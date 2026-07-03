"""attachment thumb_key: 附件縮圖 R2 key

Revision ID: 019
Revises: 018
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa

revision = '019'
down_revision = '018'
branch_labels = None
depends_on = None

_TABLES = ("note_attachments", "private_attachments", "announcement_attachments")


def upgrade():
    for t in _TABLES:
        op.add_column(t, sa.Column('thumb_key', sa.Text(), nullable=True))


def downgrade():
    for t in _TABLES:
        op.drop_column(t, 'thumb_key')
