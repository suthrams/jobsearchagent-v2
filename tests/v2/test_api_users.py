"""Tests for the /users router (ADR-062 profile management)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_user_repo
from app.api.main import app
from app.repositories.database import init_db
from app.repositories.user_repository import UserRepository


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "v2.db"
    init_db(db_path)  # seeds user 0
    app.dependency_overrides[get_user_repo] = lambda: UserRepository(db_path)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_list_users_starts_with_default_profile(client):
    resp = client.get("/users")
    assert resp.status_code == 200
    users = resp.json()["users"]
    assert [u["id"] for u in users] == [0]
    assert users[0]["name"] == "Primary"


def test_create_user_assigns_id_one_then_two(client):
    first = client.post("/users", json={"name": "Son", "note": "new-grad SWE"})
    assert first.status_code == 201
    created = first.json()["user"]
    assert created["id"] == 1
    assert created["name"] == "Son"
    assert created["note"] == "new-grad SWE"

    second = client.post("/users", json={"name": "Friend"})
    assert second.json()["user"]["id"] == 2


def test_create_then_list_shows_all(client):
    client.post("/users", json={"name": "Son"})
    users = client.get("/users").json()["users"]
    assert [u["id"] for u in users] == [0, 1]


def test_create_user_empty_name_rejected(client):
    # Pydantic min_length=1 rejects an empty string.
    resp = client.post("/users", json={"name": ""})
    assert resp.status_code == 422


def test_create_user_whitespace_name_rejected(client):
    # Passes min_length but our handler strips and rejects blank names.
    resp = client.post("/users", json={"name": "   "})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_name"
