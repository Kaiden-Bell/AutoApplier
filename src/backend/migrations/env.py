"""Alembic environment: migrates the database named by AUTOAPPLIER_DATABASE_URL."""

from alembic import context

from backend.config import Settings
from backend.db import make_engine
from backend.models import Base

config = context.config
target_metadata = Base.metadata


def _url():
    # Tests pass a URL through the Alembic config; normal runs use the environment.
    return config.get_main_option("sqlalchemy.url") or Settings().database_url


def run_migrations_offline():
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = make_engine(_url())
    with engine.connect() as connection:
        _run(connection)
    engine.dispose()


def _run(connection):
    # Batch mode lets ALTERs work on SQLite (used in tests); it is a no-op on Postgres.
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
