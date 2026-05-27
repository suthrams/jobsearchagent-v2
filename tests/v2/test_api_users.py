"""Tests for the /users router (ADR-062 profile management)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_deps, get_user_repo
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


# ── Update (PUT /users/{id}) ──────────────────────────────────────────────────

def test_update_user_changes_name_and_note(client):
    client.post("/users", json={"name": "Son", "note": "old"})  # -> id 1
    resp = client.put("/users/1", json={"name": "Alex", "note": "new-grad SWE"})
    assert resp.status_code == 200
    user = resp.json()["user"]
    assert user["id"] == 1
    assert user["name"] == "Alex"
    assert user["note"] == "new-grad SWE"
    # Persisted: list reflects the change.
    assert client.get("/users").json()["users"][1]["name"] == "Alex"


def test_update_user_clears_note(client):
    client.post("/users", json={"name": "Son", "note": "old"})
    resp = client.put("/users/1", json={"name": "Son", "note": "   "})
    assert resp.status_code == 200
    assert resp.json()["user"]["note"] is None


def test_update_user_unknown_404(client):
    resp = client.put("/users/999", json={"name": "Ghost"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_user"


def test_update_user_blank_name_rejected(client):
    resp = client.put("/users/0", json={"name": "   "})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_name"


# ── Onboarding step 2: resume upload (POST /users/{id}/resume) ────────────────

class _FakeParser:
    """Records the parse_pdf call and returns a ResumeProfile-shaped object."""
    def __init__(self):
        self.calls: list[dict] = []

    def parse_pdf(self, file_path, file_name, workflow_id=None, user_id="0"):
        self.calls.append({"file_path": file_path, "file_name": file_name,
                            "user_id": user_id})
        return SimpleNamespace(resume_id="resume-xyz", file_name=file_name,
                               name="Jane Candidate")


@pytest.fixture
def client_with_parser(tmp_path):
    db_path = tmp_path / "v2.db"
    init_db(db_path)
    parser = _FakeParser()
    app.dependency_overrides[get_user_repo] = lambda: UserRepository(db_path)
    app.dependency_overrides[get_deps] = lambda: SimpleNamespace(resume_parser=parser)
    with TestClient(app) as c:
        yield c, parser
    app.dependency_overrides.clear()


def test_upload_resume_parses_scoped_to_user(client_with_parser):
    client, parser = client_with_parser
    client.post("/users", json={"name": "Son"})  # -> id 1
    resp = client.post(
        "/users/1/resume",
        files={"file": ("cv.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["resume_id"] == "resume-xyz"
    assert body["name"] == "Jane Candidate"
    # The parser was told the resume belongs to profile "1".
    assert parser.calls[0]["user_id"] == "1"
    assert parser.calls[0]["file_name"] == "cv.pdf"


def test_upload_resume_unknown_user_404(client_with_parser):
    client, _ = client_with_parser
    resp = client.post(
        "/users/999/resume",
        files={"file": ("cv.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_user"


def test_upload_resume_parse_failure_422(tmp_path):
    db_path = tmp_path / "v2.db"
    init_db(db_path)

    class _Boom:
        def parse_pdf(self, *a, **k):
            raise ValueError("bad pdf")

    app.dependency_overrides[get_user_repo] = lambda: UserRepository(db_path)
    app.dependency_overrides[get_deps] = lambda: SimpleNamespace(resume_parser=_Boom())
    try:
        with TestClient(app) as c:
            resp = c.post(
                "/users/0/resume",
                files={"file": ("cv.pdf", b"x", "application/pdf")},
            )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "resume_parse_failed"
    finally:
        app.dependency_overrides.clear()
