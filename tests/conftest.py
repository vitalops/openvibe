"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from openvibe.config import Config
from openvibe.db import create_database


@pytest.fixture()
def tmp_db(tmp_path):
    """In-process SQLite database backed by a temp file."""
    db = create_database(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture()
def empty_config() -> Config:
    """Minimal Config with no model, no MCP, no custom agents."""
    return Config()
