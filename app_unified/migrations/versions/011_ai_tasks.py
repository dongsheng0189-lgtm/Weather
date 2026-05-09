"""Add ai_tasks table for cross-worker AI summary task state

Revision ID: 011
Revises: 010
Create Date: 2026-05-10
"""
from alembic import op
import sqlalchemy as sa


revision = '011'
down_revision = '010'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_tasks",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="processing"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ai_tasks_user_id", "ai_tasks", ["user_id"])
    op.create_index("ix_ai_tasks_expires_at", "ai_tasks", ["expires_at"])
    op.create_index("ix_ai_tasks_status_updated_at", "ai_tasks", ["status", "updated_at"])


def downgrade():
    op.drop_index("ix_ai_tasks_status_updated_at", "ai_tasks")
    op.drop_index("ix_ai_tasks_expires_at", "ai_tasks")
    op.drop_index("ix_ai_tasks_user_id", "ai_tasks")
    op.drop_table("ai_tasks")
