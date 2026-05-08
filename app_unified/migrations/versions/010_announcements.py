"""Add announcements + announcement_attachments tables

Revision ID: 010
Revises: 009
Create Date: 2026-05-08
"""
from alembic import op
import sqlalchemy as sa


revision = '010'
down_revision = '009'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_name", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("store", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_announcements_created_at_desc",
        "announcements",
        [sa.text("created_at DESC")],
    )
    op.create_index("ix_announcements_store", "announcements", ["store"])

    op.create_table(
        "announcement_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("announcement_id", sa.Integer(),
                  sa.ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_announcement_attachments_announcement_id",
        "announcement_attachments",
        ["announcement_id"],
    )


def downgrade():
    op.drop_index("ix_announcement_attachments_announcement_id", "announcement_attachments")
    op.drop_table("announcement_attachments")
    op.drop_index("ix_announcements_store", "announcements")
    op.drop_index("ix_announcements_created_at_desc", "announcements")
    op.drop_table("announcements")
