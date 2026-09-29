"""ai providers, privacy mode, usage and onboarding

Revision ID: b7c41e9a2d30
Revises: 865b038f5bcf
Create Date: 2026-09-29 10:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c41e9a2d30"
down_revision: str | None = "865b038f5bcf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rate_limited", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", "day", name="uq_ai_usage_day"),
    )
    op.create_index("ix_ai_usage_user_id", "ai_usage", ["user_id"])
    op.create_table(
        "ai_response_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key", name="uq_ai_cache_key"),
    )
    op.create_index("ix_ai_response_cache_user_id", "ai_response_cache", ["user_id"])

    with op.batch_alter_table("user_settings") as b:
        b.add_column(sa.Column("ai_provider", sa.String(length=20), nullable=False, server_default="auto"))
        b.add_column(sa.Column("ai_fallback_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        b.add_column(sa.Column("ai_privacy_mode", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("gemini_key_encrypted", sa.LargeBinary(), nullable=True))
        b.add_column(sa.Column("openrouter_key_encrypted", sa.LargeBinary(), nullable=True))
        b.add_column(sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table("projects") as b:
        b.add_column(sa.Column("analysis", sa.JSON(), nullable=False, server_default="{}"))

    # Carry over the previous single-provider settings.
    op.execute(
        "UPDATE user_settings SET ai_provider = CASE llm_provider WHEN 'gemini' THEN 'gemini' "
        "WHEN 'none' THEN 'none' ELSE 'auto' END"
    )
    op.execute("UPDATE user_settings SET gemini_key_encrypted = llm_api_key_encrypted WHERE llm_provider = 'gemini'")
    # Accounts that already exist are set up; the setup wizard is for new accounts.
    op.execute("UPDATE user_settings SET onboarded_at = CURRENT_TIMESTAMP")

    with op.batch_alter_table("user_settings") as b:
        b.drop_column("llm_provider")
        b.drop_column("llm_model")
        b.drop_column("llm_base_url")
        b.drop_column("llm_api_key_encrypted")


def downgrade() -> None:
    with op.batch_alter_table("user_settings") as b:
        b.add_column(sa.Column("llm_provider", sa.String(length=32), nullable=False, server_default="server"))
        b.add_column(sa.Column("llm_model", sa.String(length=120), nullable=False, server_default=""))
        b.add_column(sa.Column("llm_base_url", sa.String(length=300), nullable=False, server_default=""))
        b.add_column(sa.Column("llm_api_key_encrypted", sa.LargeBinary(), nullable=True))
    op.execute(
        "UPDATE user_settings SET llm_provider = 'gemini', llm_api_key_encrypted = gemini_key_encrypted "
        "WHERE gemini_key_encrypted IS NOT NULL"
    )
    with op.batch_alter_table("user_settings") as b:
        for col in (
            "ai_provider",
            "ai_fallback_enabled",
            "ai_privacy_mode",
            "gemini_key_encrypted",
            "openrouter_key_encrypted",
            "onboarded_at",
        ):
            b.drop_column(col)
    with op.batch_alter_table("projects") as b:
        b.drop_column("analysis")
    op.drop_index("ix_ai_response_cache_user_id", table_name="ai_response_cache")
    op.drop_table("ai_response_cache")
    op.drop_index("ix_ai_usage_user_id", table_name="ai_usage")
    op.drop_table("ai_usage")
