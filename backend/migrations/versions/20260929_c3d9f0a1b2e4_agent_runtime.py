"""agent runtime: resume agent context

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


def downgrade() -> None:
    with op.batch_alter_table("resume_versions") as b:
        b.drop_column("assistant_context")
