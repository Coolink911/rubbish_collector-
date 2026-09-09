"""Shared fixtures.

By default every test gets its own SQLite file in a temp directory, so the
suite needs no network, no account and no key. Set TEST_DATABASE_URL to a
Postgres connection string (e.g. a Neon branch) and the same tests run
against the engine the deployed app actually uses - the schema is dropped
and rebuilt around every test, so point it at a throwaway database.

Geocoding is off unless a test explicitly patches it. No test touches the
network.
"""

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

POSTGRES_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    if POSTGRES_URL:
        monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    else:
        monkeypatch.setenv(
            "DATABASE_URL", f"sqlite:///{tmp_path / 'binrun-test.db'}"
        )
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("GEOCODE_ENABLED", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from app import db

    if POSTGRES_URL:
        db.drop_all()
    db.migrate()
    yield
    if POSTGRES_URL:
        db.drop_all()


@pytest.fixture
def client(db_file):
    from app.main import app

    with TestClient(app) as c:
        yield c


def join(client, name, role):
    """Log a client in. 303 means the join worked."""
    response = client.post(
        "/join", data={"name": name, "role": role}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    return response


@pytest.fixture
def resident(client):
    join(client, "Thandi", "resident")
    return client


def make_client(app, name, role):
    """A second, independently logged-in browser against the same app."""
    c = TestClient(app)
    join(c, name, role)
    return c
