"""Migrations: they apply cleanly, match the models, and downgrade."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from backend.models import Base
from conftest import migrate


def test_migrations_match_models(database):
    with database.engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"models and migrations differ; add an Alembic revision: {diff}"


def test_downgrade_to_base_and_back(database):
    migrate(database, "head")
    from alembic import command
    from conftest import alembic_config
    with database.engine.begin() as connection:
        command.downgrade(alembic_config(connection), "base")
    assert set(inspect(database.engine).get_table_names()) <= {"alembic_version"}
    migrate(database, "head")
    assert {"accounts", "profiles"} <= set(inspect(database.engine).get_table_names())
