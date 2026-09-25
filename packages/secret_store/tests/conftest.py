"""Fixtures for the secret_store tests."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from crewquarters_secret_store.db import EncryptedSecret, ProviderProfile
from crewquarters_shared.db.base import Base


@pytest.fixture(scope="session")
def person3_secret_tables(database_url: str) -> None:
    """Create the tables until their reviewed Alembic migration merges (then a no-op)."""
    engine = create_engine(database_url)
    Base.metadata.create_all(engine, tables=[EncryptedSecret.__table__, ProviderProfile.__table__])
    engine.dispose()
