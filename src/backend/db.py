"""
    Database engine and session handling.

    One engine per process, created lazily from Settings.database_url. Postgres is the production
    database (docker-compose.yml); tests may use SQLite. Schema changes go through Alembic
    (src/backend/migrations); models live in models.py.
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class DatabaseNotConfigured(Exception):
    """AUTOAPPLIER_DATABASE_URL is not set."""


def make_engine(url):
    if not url:
        raise DatabaseNotConfigured()
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url in ("sqlite://", "sqlite:///"):
            kwargs["poolclass"] = StaticPool  # one shared in-memory database
        engine = create_engine(url, **kwargs)

        @event.listens_for(engine, "connect")
        def _sqlite_foreign_keys(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        return engine
    return create_engine(url, pool_pre_ping=True)


class Database:
    """Lazily created engine + session factory for one database URL."""

    def __init__(self, url):
        self.url = url
        self._engine = None
        self._sessionmaker = None

    @property
    def configured(self):
        return bool(self.url)

    @property
    def engine(self):
        if self._engine is None:
            self._engine = make_engine(self.url)
            self._sessionmaker = sessionmaker(self._engine, expire_on_commit=False)
        return self._engine

    @contextmanager
    def session(self):
        """A session that commits on success and rolls back on error."""
        self.engine  # noqa: B018 - creates the sessionmaker on first use
        session = self._sessionmaker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self):
        """True if a trivial query succeeds."""
        if not self.configured:
            return False
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def dispose(self):
        if self._engine is not None:
            self._engine.dispose()
