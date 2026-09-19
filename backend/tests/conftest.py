"""Shared fixtures.

The DB-backed tests DROP and recreate the tables, so they never run against
DATABASE_URL by accident. Point TEST_DATABASE_URL at a scratch Postgres, e.g.:

    docker compose up -d db
    export TEST_DATABASE_URL=postgresql://gem:gem@localhost:5433/gem_test
    pytest -q

(`docker compose run --rm tests` does all of that for you.)
"""
import os
import pytest

from backend import database

# every setting the app reads, cleared so a developer's .env can't change test outcomes
_APP_ENV_VARS = ("OFFICER_TOKENS", "ADMIN_TOKEN", "ENABLE_RESET", "APP_ENV", "MAX_UPLOAD_MB",
                 "RATE_LIMIT_PER_MIN", "TRUST_PROXY")


@pytest.fixture(scope="session")
def test_db_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail(
            "TEST_DATABASE_URL is not set. These tests wipe the database, so they refuse to use "
            "DATABASE_URL. Start a scratch Postgres (`docker compose up -d db`) and set "
            "TEST_DATABASE_URL=postgresql://gem:gem@localhost:5433/gem_test",
            pytrace=False,
        )
    if url == os.environ.get("DATABASE_URL") and os.environ.get("ALLOW_DESTRUCTIVE_TESTS") != "1":
        pytest.fail(
            "TEST_DATABASE_URL equals DATABASE_URL — refusing to wipe it. Use a separate scratch "
            "database (or set ALLOW_DESTRUCTIVE_TESTS=1 if you really mean it).",
            pytrace=False,
        )
    return url


@pytest.fixture()
def db(monkeypatch, test_db_url):
    """Clean schema on the scratch database, app settings reset to defaults."""
    for k in _APP_ENV_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(database, "DATABASE_URL", test_db_url)
    database.close_pool()
    database.reset_db()
    yield database
    database.close_pool()


@pytest.fixture()
def client(db, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main
    monkeypatch.setenv("ENABLE_RESET", "true")        # tests use /api/reset
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "0")      # limiter has its own test
    main._limiter.reset()
    with TestClient(main.app) as c:
        yield c
