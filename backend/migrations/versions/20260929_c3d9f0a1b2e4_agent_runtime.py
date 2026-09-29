"""agent runtime: resume agent context, runner presence, application phases

Revision ID: c3d9f0a1b2e4
Revises: b7c41e9a2d30
Create Date: 2026-09-29 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d9f0a1b2e4"
down_revision: str | None = "b7c41e9a2d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("resume_versions") as b:
        b.add_column(sa.Column("assistant_context", sa.JSON(), nullable=False, server_default="{}"))
    with op.batch_alter_table("api_tokens") as b:
        b.add_column(sa.Column("runner_seen_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("runner_state", sa.String(length=20), nullable=False, server_default=""))
        b.add_column(sa.Column("runner_detail", sa.String(length=300), nullable=False, server_default=""))
        b.add_column(sa.Column("runner_host", sa.String(length=120), nullable=False, server_default=""))
        b.add_column(sa.Column("runner_version", sa.String(length=40), nullable=False, server_default=""))
    with op.batch_alter_table("applications") as b:
        b.add_column(sa.Column("confirmation_id", sa.String(length=120), nullable=False, server_default=""))
    with op.batch_alter_table("jobs") as b:
        b.add_column(sa.Column("company_website", sa.String(length=300), nullable=False, server_default=""))
        b.add_column(sa.Column("research", sa.JSON(), nullable=False, server_default="{}"))
        b.add_column(sa.Column("research_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("interview_prep", sa.JSON(), nullable=False, server_default="{}"))
        b.add_column(sa.Column("interview_prep_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("automation_sessions") as b:
        b.add_column(sa.Column("phase", sa.String(length=30), nullable=False, server_default=""))
        b.add_column(sa.Column("resume_nonce", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("submit_clicked_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("fields_total", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("fields_done", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("resume_uploaded", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("automation_sessions") as b:
        for col in ("phase", "resume_nonce", "submit_clicked_at", "fields_total", "fields_done", "resume_uploaded"):
            b.drop_column(col)
    with op.batch_alter_table("jobs") as b:
        for col in ("company_website", "research", "research_at", "interview_prep", "interview_prep_at"):
            b.drop_column(col)
    with op.batch_alter_table("applications") as b:
        b.drop_column("confirmation_id")
    with op.batch_alter_table("api_tokens") as b:
        for col in ("runner_seen_at", "runner_state", "runner_detail", "runner_host", "runner_version"):
            b.drop_column(col)
    with op.batch_alter_table("resume_versions") as b:
        b.drop_column("assistant_context")
