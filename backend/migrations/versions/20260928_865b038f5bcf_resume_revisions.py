"""resume revisions

Revision ID: 865b038f5bcf
Revises: 5afd8ddf1681
Create Date: 2026-09-28 23:13:35.657667
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "865b038f5bcf"
down_revision: str | None = "5afd8ddf1681"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resume_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("changed", sa.JSON(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["resume_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "number", name="uq_revision_number"),
    )
    with op.batch_alter_table("resume_revisions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_resume_revisions_version_id"), ["version_id"], unique=False)

    with op.batch_alter_table("resume_versions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    with op.batch_alter_table("resume_versions", schema=None) as batch_op:
        batch_op.drop_column("revision")

    with op.batch_alter_table("resume_revisions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_resume_revisions_version_id"))

    op.drop_table("resume_revisions")
