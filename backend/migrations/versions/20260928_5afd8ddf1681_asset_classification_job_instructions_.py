"""asset classification, job instructions, quality gate

Revision ID: 5afd8ddf1681
Revises: 690ad3e9868b
Create Date: 2026-09-28 22:37:21.865198
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '5afd8ddf1681'
down_revision: str | None = '690ad3e9868b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('resume_instructions', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch_op.add_column(sa.Column('notes', sa.Text(), nullable=False, server_default=''))

    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('asset_type', sa.String(length=30), nullable=False, server_default='project'))
        batch_op.add_column(sa.Column('asset_type_locked', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('resume_eligible', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('status', sa.String(length=20), nullable=False, server_default='built'))

    with op.batch_alter_table('resume_versions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('config', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch_op.add_column(sa.Column('quality_report', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch_op.add_column(sa.Column('export_blocked', sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table('suggested_projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('verification', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
        batch_op.add_column(sa.Column('auto_generated', sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table('user_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('header_portfolio', sa.String(length=10), nullable=False, server_default='template'))
        batch_op.add_column(sa.Column('header_github', sa.String(length=10), nullable=False, server_default='template'))
        batch_op.add_column(sa.Column('header_linkedin', sa.String(length=10), nullable=False, server_default='template'))

    # Suggested projects move to the idea -> planned -> building -> built -> verified lifecycle.
    op.execute("UPDATE suggested_projects SET status = 'idea' WHERE status = 'not_built'")
    op.execute("UPDATE suggested_projects SET status = 'building' WHERE status = 'in_progress'")


def downgrade() -> None:
    with op.batch_alter_table('user_settings', schema=None) as batch_op:
        batch_op.drop_column('header_linkedin')
        batch_op.drop_column('header_github')
        batch_op.drop_column('header_portfolio')

    with op.batch_alter_table('suggested_projects', schema=None) as batch_op:
        batch_op.drop_column('auto_generated')
        batch_op.drop_column('verification')

    with op.batch_alter_table('resume_versions', schema=None) as batch_op:
        batch_op.drop_column('export_blocked')
        batch_op.drop_column('quality_report')
        batch_op.drop_column('config')

    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_column('status')
        batch_op.drop_column('resume_eligible')
        batch_op.drop_column('asset_type_locked')
        batch_op.drop_column('asset_type')

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('notes')
        batch_op.drop_column('resume_instructions')

