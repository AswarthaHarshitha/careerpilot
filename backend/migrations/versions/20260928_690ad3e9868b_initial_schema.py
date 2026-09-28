"""initial schema

Revision ID: 690ad3e9868b
Revises: 
Create Date: 2026-09-28 16:29:05.233582
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '690ad3e9868b'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('display_name', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failed_login_count', sa.Integer(), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    op.create_table('achievements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('achievements', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_achievements_user_id'), ['user_id'], unique=False)

    op.create_table('api_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('prefix', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    with op.batch_alter_table('api_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_api_tokens_user_id'), ['user_id'], unique=False)

    op.create_table('auth_sessions',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_agent', sa.String(length=300), nullable=False),
    sa.Column('csrf_token', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('auth_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_auth_sessions_user_id'), ['user_id'], unique=False)

    op.create_table('background_tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('steps', sa.JSON(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('background_tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_background_tasks_user_id'), ['user_id'], unique=False)
        batch_op.create_index('ix_tasks_status_created', ['status', 'created_at'], unique=False)

    op.create_table('certifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('issuer', sa.String(length=200), nullable=False),
    sa.Column('date', sa.String(length=40), nullable=False),
    sa.Column('url', sa.String(length=300), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('certifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_certifications_user_id'), ['user_id'], unique=False)

    op.create_table('educations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('institution', sa.String(length=200), nullable=False),
    sa.Column('degree', sa.String(length=200), nullable=False),
    sa.Column('field_of_study', sa.String(length=200), nullable=False),
    sa.Column('start_date', sa.String(length=40), nullable=False),
    sa.Column('end_date', sa.String(length=40), nullable=False),
    sa.Column('graduation_year', sa.Integer(), nullable=True),
    sa.Column('grade', sa.String(length=80), nullable=False),
    sa.Column('location', sa.String(length=200), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('educations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_educations_user_id'), ['user_id'], unique=False)

    op.create_table('experiences',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('organization', sa.String(length=200), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('location', sa.String(length=200), nullable=False),
    sa.Column('employment_type', sa.String(length=60), nullable=False),
    sa.Column('start_date', sa.String(length=40), nullable=False),
    sa.Column('end_date', sa.String(length=40), nullable=False),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('experiences', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_experiences_user_id'), ['user_id'], unique=False)

    op.create_table('jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('company', sa.String(length=200), nullable=False),
    sa.Column('role_title', sa.String(length=300), nullable=False),
    sa.Column('job_code', sa.String(length=120), nullable=False),
    sa.Column('job_url', sa.String(length=1000), nullable=False),
    sa.Column('application_url', sa.String(length=1000), nullable=False),
    sa.Column('company_careers_url', sa.String(length=1000), nullable=False),
    sa.Column('location', sa.String(length=300), nullable=False),
    sa.Column('workplace_type', sa.String(length=40), nullable=False),
    sa.Column('employment_type', sa.String(length=60), nullable=False),
    sa.Column('experience_text', sa.String(length=300), nullable=False),
    sa.Column('min_years_experience', sa.Float(), nullable=True),
    sa.Column('education_text', sa.String(length=500), nullable=False),
    sa.Column('deadline', sa.Date(), nullable=True),
    sa.Column('deadline_source', sa.String(length=300), nullable=False),
    sa.Column('posted_date', sa.Date(), nullable=True),
    sa.Column('source_platform', sa.String(length=40), nullable=False),
    sa.Column('ingest_method', sa.String(length=20), nullable=False),
    sa.Column('description_text', sa.Text(), nullable=False),
    sa.Column('responsibilities', sa.JSON(), nullable=False),
    sa.Column('qualifications', sa.JSON(), nullable=False),
    sa.Column('keywords', sa.JSON(), nullable=False),
    sa.Column('analysis_state', sa.String(length=20), nullable=False),
    sa.Column('analysis_error', sa.Text(), nullable=False),
    sa.Column('analysis_notes', sa.JSON(), nullable=False),
    sa.Column('analyzed_with_llm', sa.Boolean(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index('ix_jobs_user_created', ['user_id', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_user_id'), ['user_id'], unique=False)

    op.create_table('profile_answers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('question_key', sa.String(length=80), nullable=False),
    sa.Column('label', sa.String(length=300), nullable=False),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('sensitive', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'question_key', name='uq_profile_answer_key')
    )
    with op.batch_alter_table('profile_answers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_profile_answers_user_id'), ['user_id'], unique=False)

    op.create_table('projects',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=False),
    sa.Column('slug', sa.String(length=200), nullable=False),
    sa.Column('origin', sa.String(length=20), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('repo_full_name', sa.String(length=200), nullable=False),
    sa.Column('repo_url', sa.String(length=300), nullable=False),
    sa.Column('homepage_url', sa.String(length=300), nullable=False),
    sa.Column('is_private', sa.Boolean(), nullable=False),
    sa.Column('is_fork', sa.Boolean(), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('primary_language', sa.String(length=60), nullable=False),
    sa.Column('languages', sa.JSON(), nullable=False),
    sa.Column('topics', sa.JSON(), nullable=False),
    sa.Column('readme_excerpt', sa.Text(), nullable=False),
    sa.Column('readme_sha', sa.String(length=64), nullable=False),
    sa.Column('file_count', sa.Integer(), nullable=True),
    sa.Column('stars', sa.Integer(), nullable=False),
    sa.Column('pushed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_scanned_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('scan_error', sa.Text(), nullable=False),
    sa.Column('hidden', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'slug', name='uq_project_user_slug')
    )
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_projects_user_id'), ['user_id'], unique=False)

    op.create_table('resume_templates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('format', sa.String(length=20), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('source_text', sa.Text(), nullable=True),
    sa.Column('source_bytes', sa.LargeBinary(), nullable=True),
    sa.Column('structure', sa.JSON(), nullable=False),
    sa.Column('preview_pdf', sa.LargeBinary(), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('render_error', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('resume_templates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_resume_templates_user_id'), ['user_id'], unique=False)

    op.create_table('skills',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('canonical', sa.String(length=100), nullable=False),
    sa.Column('category', sa.String(length=80), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'canonical', name='uq_skill_user_canonical')
    )
    with op.batch_alter_table('skills', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_skills_user_id'), ['user_id'], unique=False)

    op.create_table('user_profiles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('first_name', sa.String(length=100), nullable=False),
    sa.Column('last_name', sa.String(length=100), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('phone', sa.String(length=40), nullable=False),
    sa.Column('location', sa.String(length=200), nullable=False),
    sa.Column('linkedin_url', sa.String(length=300), nullable=False),
    sa.Column('github_url', sa.String(length=300), nullable=False),
    sa.Column('portfolio_url', sa.String(length=300), nullable=False),
    sa.Column('headline', sa.String(length=200), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('work_authorization', sa.String(length=300), nullable=False),
    sa.Column('requires_sponsorship', sa.Boolean(), nullable=True),
    sa.Column('willing_to_relocate', sa.Boolean(), nullable=True),
    sa.Column('notice_period', sa.String(length=100), nullable=False),
    sa.Column('salary_expectation', sa.String(length=100), nullable=False),
    sa.Column('years_of_experience', sa.Numeric(precision=4, scale=1), nullable=True),
    sa.Column('earliest_start_date', sa.String(length=40), nullable=False),
    sa.Column('preferred_locations', sa.JSON(), nullable=False),
    sa.Column('preferred_job_types', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
    op.create_table('user_settings',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('default_template_id', sa.Integer(), nullable=True),
    sa.Column('resume_max_pages', sa.Integer(), nullable=False),
    sa.Column('max_projects', sa.Integer(), nullable=False),
    sa.Column('max_bullets_per_project', sa.Integer(), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('submit_confirmation_mode', sa.String(length=20), nullable=False),
    sa.Column('deadline_reminder_days', sa.Integer(), nullable=False),
    sa.Column('follow_up_after_days', sa.Integer(), nullable=False),
    sa.Column('llm_provider', sa.String(length=32), nullable=False),
    sa.Column('llm_model', sa.String(length=120), nullable=False),
    sa.Column('llm_base_url', sa.String(length=300), nullable=False),
    sa.Column('llm_api_key_encrypted', sa.LargeBinary(), nullable=True),
    sa.Column('github_username', sa.String(length=100), nullable=False),
    sa.Column('github_token_encrypted', sa.LargeBinary(), nullable=True),
    sa.Column('include_private_repos', sa.Boolean(), nullable=False),
    sa.Column('include_forks', sa.Boolean(), nullable=False),
    sa.Column('github_last_scan_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notify_in_app', sa.Boolean(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['default_template_id'], ['resume_templates.id'], name='fk_settings_default_template', ondelete='SET NULL', use_alter=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('experience_bullets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('experience_id', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['experience_id'], ['experiences.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('experience_bullets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_experience_bullets_experience_id'), ['experience_id'], unique=False)

    op.create_table('job_project_recommendations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('matched', sa.JSON(), nullable=False),
    sa.Column('selected', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'project_id', name='uq_job_project')
    )
    with op.batch_alter_table('job_project_recommendations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_job_project_recommendations_job_id'), ['job_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_job_project_recommendations_project_id'), ['project_id'], unique=False)

    op.create_table('job_requirements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('label', sa.String(length=300), nullable=False),
    sa.Column('canonical', sa.String(length=100), nullable=False),
    sa.Column('category', sa.String(length=40), nullable=False),
    sa.Column('importance', sa.String(length=20), nullable=False),
    sa.Column('source_snippet', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('job_requirements', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_job_requirements_job_id'), ['job_id'], unique=False)

    op.create_table('project_bullets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=30), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('project_bullets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_project_bullets_project_id'), ['project_id'], unique=False)

    op.create_table('project_evidence',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('canonical', sa.String(length=100), nullable=False),
    sa.Column('category', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('source_type', sa.String(length=30), nullable=False),
    sa.Column('source_detail', sa.String(length=300), nullable=False),
    sa.Column('snippet', sa.Text(), nullable=False),
    sa.Column('user_reviewed', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('project_evidence', schema=None) as batch_op:
        batch_op.create_index('ix_evidence_project_canonical', ['project_id', 'canonical'], unique=False)
        batch_op.create_index(batch_op.f('ix_project_evidence_project_id'), ['project_id'], unique=False)

    op.create_table('resume_template_sections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('template_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('entry_count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['template_id'], ['resume_templates.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('resume_template_sections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_resume_template_sections_template_id'), ['template_id'], unique=False)

    op.create_table('resume_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('template_id', sa.Integer(), nullable=True),
    sa.Column('parent_id', sa.Integer(), nullable=True),
    sa.Column('version_number', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=300), nullable=False),
    sa.Column('is_base', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('content', sa.JSON(), nullable=False),
    sa.Column('source_text', sa.Text(), nullable=True),
    sa.Column('source_bytes', sa.LargeBinary(), nullable=True),
    sa.Column('pdf', sa.LargeBinary(), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('render_error', sa.Text(), nullable=False),
    sa.Column('ats_report', sa.JSON(), nullable=False),
    sa.Column('recruiter_report', sa.JSON(), nullable=False),
    sa.Column('changes', sa.JSON(), nullable=False),
    sa.Column('used_llm', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['parent_id'], ['resume_versions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['template_id'], ['resume_templates.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('resume_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_resume_versions_job_id'), ['job_id'], unique=False)
        batch_op.create_index('ix_resume_versions_user_created', ['user_id', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_resume_versions_user_id'), ['user_id'], unique=False)

    op.create_table('suggested_projects',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('gap_skills', sa.JSON(), nullable=False),
    sa.Column('existing_skills', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('plan', sa.JSON(), nullable=False),
    sa.Column('linked_project_id', sa.Integer(), nullable=True),
    sa.Column('verification_notes', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['linked_project_id'], ['projects.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('suggested_projects', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_suggested_projects_job_id'), ['job_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_suggested_projects_user_id'), ['user_id'], unique=False)

    op.create_table('applications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('resume_version_id', sa.Integer(), nullable=True),
    sa.Column('date_applied', sa.Date(), nullable=True),
    sa.Column('follow_up_date', sa.Date(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('submission_verified', sa.Boolean(), nullable=False),
    sa.Column('confirmation_text', sa.Text(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resume_version_id'], ['resume_versions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'job_id', name='uq_application_user_job')
    )
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_applications_user_id'), ['user_id'], unique=False)
        batch_op.create_index('ix_applications_user_status', ['user_id', 'status'], unique=False)

    op.create_table('job_matches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('requirement_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('evidence_refs', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requirement_id'], ['job_requirements.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('requirement_id', name='uq_match_requirement')
    )
    with op.batch_alter_table('job_matches', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_job_matches_job_id'), ['job_id'], unique=False)

    op.create_table('application_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('application_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('application_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_application_events_application_id'), ['application_id'], unique=False)

    op.create_table('application_fields',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('application_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('step', sa.Integer(), nullable=False),
    sa.Column('field_ref', sa.String(length=300), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('input_type', sa.String(length=30), nullable=False),
    sa.Column('required', sa.Boolean(), nullable=False),
    sa.Column('options', sa.JSON(), nullable=False),
    sa.Column('field_key', sa.String(length=80), nullable=False),
    sa.Column('sensitive', sa.Boolean(), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('value_source', sa.String(length=30), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('fill_result', sa.String(length=30), nullable=False),
    sa.Column('validation_message', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('application_fields', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_application_fields_application_id'), ['application_id'], unique=False)

    op.create_table('automation_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('application_id', sa.Integer(), nullable=False),
    sa.Column('resume_version_id', sa.Integer(), nullable=True),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('start_url', sa.String(length=1000), nullable=False),
    sa.Column('platform', sa.String(length=40), nullable=False),
    sa.Column('runner_token_id', sa.Integer(), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('current_url', sa.String(length=1000), nullable=False),
    sa.Column('step', sa.Integer(), nullable=False),
    sa.Column('screenshot_png', sa.LargeBinary(), nullable=True),
    sa.Column('answers_revision', sa.Integer(), nullable=False),
    sa.Column('filled_revision', sa.Integer(), nullable=False),
    sa.Column('submit_confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('result_text', sa.Text(), nullable=False),
    sa.Column('log', sa.JSON(), nullable=False),
    sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resume_version_id'], ['resume_versions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['runner_token_id'], ['api_tokens.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('automation_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_automation_sessions_application_id'), ['application_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_automation_sessions_user_id'), ['user_id'], unique=False)

    op.create_table('reminders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('application_id', sa.Integer(), nullable=True),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('reminders', schema=None) as batch_op:
        batch_op.create_index('ix_reminders_user_due', ['user_id', 'due_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_reminders_user_id'), ['user_id'], unique=False)

    op.create_table('application_answers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('application_id', sa.Integer(), nullable=False),
    sa.Column('field_id', sa.Integer(), nullable=True),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=30), nullable=False),
    sa.Column('confirmed', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['field_id'], ['application_fields.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('application_answers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_application_answers_application_id'), ['application_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('application_answers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_application_answers_application_id'))

    op.drop_table('application_answers')
    with op.batch_alter_table('reminders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reminders_user_id'))
        batch_op.drop_index('ix_reminders_user_due')

    op.drop_table('reminders')
    with op.batch_alter_table('automation_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_automation_sessions_user_id'))
        batch_op.drop_index(batch_op.f('ix_automation_sessions_application_id'))

    op.drop_table('automation_sessions')
    with op.batch_alter_table('application_fields', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_application_fields_application_id'))

    op.drop_table('application_fields')
    with op.batch_alter_table('application_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_application_events_application_id'))

    op.drop_table('application_events')
    with op.batch_alter_table('job_matches', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_job_matches_job_id'))

    op.drop_table('job_matches')
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.drop_index('ix_applications_user_status')
        batch_op.drop_index(batch_op.f('ix_applications_user_id'))

    op.drop_table('applications')
    with op.batch_alter_table('suggested_projects', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_suggested_projects_user_id'))
        batch_op.drop_index(batch_op.f('ix_suggested_projects_job_id'))

    op.drop_table('suggested_projects')
    with op.batch_alter_table('resume_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_resume_versions_user_id'))
        batch_op.drop_index('ix_resume_versions_user_created')
        batch_op.drop_index(batch_op.f('ix_resume_versions_job_id'))

    op.drop_table('resume_versions')
    with op.batch_alter_table('resume_template_sections', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_resume_template_sections_template_id'))

    op.drop_table('resume_template_sections')
    with op.batch_alter_table('project_evidence', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_project_evidence_project_id'))
        batch_op.drop_index('ix_evidence_project_canonical')

    op.drop_table('project_evidence')
    with op.batch_alter_table('project_bullets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_project_bullets_project_id'))

    op.drop_table('project_bullets')
    with op.batch_alter_table('job_requirements', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_job_requirements_job_id'))

    op.drop_table('job_requirements')
    with op.batch_alter_table('job_project_recommendations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_job_project_recommendations_project_id'))
        batch_op.drop_index(batch_op.f('ix_job_project_recommendations_job_id'))

    op.drop_table('job_project_recommendations')
    with op.batch_alter_table('experience_bullets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_experience_bullets_experience_id'))

    op.drop_table('experience_bullets')
    op.drop_table('user_settings')
    op.drop_table('user_profiles')
    with op.batch_alter_table('skills', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_skills_user_id'))

    op.drop_table('skills')
    with op.batch_alter_table('resume_templates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_resume_templates_user_id'))

    op.drop_table('resume_templates')
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_projects_user_id'))

    op.drop_table('projects')
    with op.batch_alter_table('profile_answers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_profile_answers_user_id'))

    op.drop_table('profile_answers')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_jobs_user_id'))
        batch_op.drop_index('ix_jobs_user_created')

    op.drop_table('jobs')
    with op.batch_alter_table('experiences', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_experiences_user_id'))

    op.drop_table('experiences')
    with op.batch_alter_table('educations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_educations_user_id'))

    op.drop_table('educations')
    with op.batch_alter_table('certifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_certifications_user_id'))

    op.drop_table('certifications')
    with op.batch_alter_table('background_tasks', schema=None) as batch_op:
        batch_op.drop_index('ix_tasks_status_created')
        batch_op.drop_index(batch_op.f('ix_background_tasks_user_id'))

    op.drop_table('background_tasks')
    with op.batch_alter_table('auth_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_auth_sessions_user_id'))

    op.drop_table('auth_sessions')
    with op.batch_alter_table('api_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_api_tokens_user_id'))

    op.drop_table('api_tokens')
    with op.batch_alter_table('achievements', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_achievements_user_id'))

    op.drop_table('achievements')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
