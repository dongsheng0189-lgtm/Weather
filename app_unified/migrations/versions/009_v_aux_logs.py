"""Add v_aux_logs table + backfill super_admin/admin features

Revision ID: 009
Revises: 008
Create Date: 2026-05-04
"""
from alembic import op
import sqlalchemy as sa
import json


revision = '009'
down_revision = '008'
branch_labels = None
depends_on = None


def upgrade():
    # 1. 建 table
    op.create_table(
        "v_aux_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("queried_uino", sa.String(length=10), nullable=False),
        sa.Column("matched", sa.Boolean(), nullable=False),
        sa.Column(
            "queried_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_v_aux_logs_user_id", "v_aux_logs", ["user_id"])
    op.create_index("ix_v_aux_logs_queried_uino", "v_aux_logs", ["queried_uino"])
    op.create_index("ix_v_aux_logs_queried_at", "v_aux_logs", ["queried_at"])

    # 2. Backfill features：super_admin / admin 自動加 v_aux
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, features FROM users WHERE role IN ('super_admin', 'admin')")
    ).fetchall()
    for row in rows:
        try:
            current = json.loads(row.features or "[]")
            if not isinstance(current, list):
                current = []
        except Exception:
            current = []
        if "v_aux" not in current:
            current.append("v_aux")
            new_features = json.dumps(current)
            bind.execute(
                sa.text("UPDATE users SET features = :f WHERE id = :uid"),
                {"f": new_features, "uid": row.id},
            )


def downgrade():
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, features FROM users")).fetchall()
    for row in rows:
        try:
            current = json.loads(row.features or "[]")
            if not isinstance(current, list):
                continue
        except Exception:
            continue
        if "v_aux" in current:
            current.remove("v_aux")
            bind.execute(
                sa.text("UPDATE users SET features = :f WHERE id = :uid"),
                {"f": json.dumps(current), "uid": row.id},
            )

    op.drop_index("ix_v_aux_logs_queried_at", "v_aux_logs")
    op.drop_index("ix_v_aux_logs_queried_uino", "v_aux_logs")
    op.drop_index("ix_v_aux_logs_user_id", "v_aux_logs")
    op.drop_table("v_aux_logs")
