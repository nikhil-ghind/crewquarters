"""Alembic environment. Nikhil Hiro Ghind (Person 1) reviews and merges every migration
so the project keeps one linear history (PLAN.md section 6.1).

Migrations run exactly once per deployment under a PostgreSQL advisory lock.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool, text

import crewquarters_broker.models  # noqa: F401 - register tables (Person 3)
import crewquarters_knowledge.models  # noqa: F401
import crewquarters_secret_store.db  # noqa: F401
import crewquarters_shared.db.models
import crewquarters_shared.db.models_gateway  # noqa: F401 - register tables
from crewquarters_shared.config import get_settings
from crewquarters_shared.db.base import Base

MIGRATION_LOCK_KEY = 0x43514D47  # "CQMG"

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
        connection.commit()
        try:
            context.configure(
                connection=connection, target_metadata=target_metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
