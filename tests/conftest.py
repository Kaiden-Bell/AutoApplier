import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend import accounts, crypto  # noqa: E402
from backend.db import Database  # noqa: E402
from backend.models import Account, Base  # noqa: E402

# A fixed account token so tests can use constant headers.
TEST_TOKEN = "aa_test-token"
TEST_DATA_KEY = crypto.generate_key()


def alembic_config(connection):
    cfg = Config(os.path.join(REPO_ROOT, "alembic.ini"))
    cfg.attributes["connection"] = connection
    return cfg


def migrate(database, revision="head"):
    with database.engine.begin() as connection:
        command.upgrade(alembic_config(connection), revision)


def reset(database):
    with database.engine.begin() as connection:
        Base.metadata.drop_all(connection)
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))


@pytest.fixture
def database():
    """A migrated, empty database: in-memory SQLite, or AUTOAPPLIER_TEST_DATABASE_URL (CI Postgres)."""
    db = Database(os.environ.get("AUTOAPPLIER_TEST_DATABASE_URL") or "sqlite://")
    reset(db)
    migrate(db)
    yield db
    reset(db)
    db.dispose()


@pytest.fixture
def account_id(database):
    with database.session() as session:
        account = Account(label="test", api_token_hash=accounts.hash_token(TEST_TOKEN))
        session.add(account)
        session.flush()
        return account.id
