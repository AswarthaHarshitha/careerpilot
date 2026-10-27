"""profile postal address

Revision ID: d4e8a1c7f2b9
Revises: c3d9f0a1b2e4
Create Date: 2026-10-27 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e8a1c7f2b9"
down_revision: str | None = "c3d9f0a1b2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("street", 200),
    ("address_line2", 200),
    ("city", 100),
    ("state", 100),
    ("postal_code", 20),
    ("country", 100),
)


def upgrade() -> None:
    with op.batch_alter_table("user_profiles") as b:
        for name, length in COLUMNS:
            b.add_column(sa.Column(name, sa.String(length=length), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("user_profiles") as b:
        for name, _ in COLUMNS:
            b.drop_column(name)
