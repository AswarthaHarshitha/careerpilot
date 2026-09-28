from alembic import context
from sqlalchemy import DateTime

from app.core.config import get_settings
from app.core.db import Base, UTCDateTime, get_engine
import app.models  # noqa: F401 - registers every table on Base.metadata

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    # Store UTCDateTime as a plain timezone-aware DateTime so migrations do not import application code.
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True,
                      render_item=render_item, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_item=render_item,
                          render_as_batch=connection.dialect.name == "sqlite", compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
