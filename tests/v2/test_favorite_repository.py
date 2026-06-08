"""Repository + schema tests for favorite_jobs - "My favorite jobs" (ADR-090).

Covers the bounded working-set semantics (25-cap, UNIQUE idempotency, per-profile
isolation) and the no-application-tracking BOUNDARY as a schema forcing function:
the column set must stay exactly {job ref + display snapshot + timestamp}. If anyone
ever adds a status / applied / pursuing / stage / outcome column, the build fails.
"""
from __future__ import annotations

import pytest

from app.repositories.database import get_connection, init_db
from app.repositories.favorite_repository import (
    MAX_FAVORITES,
    FavoriteRepository,
    FavoritesCapReached,
)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_v2.db"
    init_db(path)
    return path


@pytest.fixture
def repo(db_path):
    return FavoriteRepository(db_path)


# ── Schema boundary (forcing function) ────────────────────────────────────────

def test_favorite_jobs_columns_are_exactly_the_bounded_set(db_path):
    """The boundary, enforced in schema: favorites carry ONLY a job reference, a
    display snapshot, and a timestamp - never status/applied/pursuing/stage/outcome.
    Adding such a column fails this test."""
    with get_connection(db_path) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(favorite_jobs)").fetchall()}
    assert cols == {"id", "user_id", "workflow_id", "job_id", "title", "company", "created_at"}


def test_favorite_jobs_is_not_in_the_run_purge_cascade():
    """ADR-090 retention: favorites are user-owned working data that survive a run
    purge, so favorite_jobs must NOT be a run child table."""
    from app.repositories.database import _RUN_CHILD_TABLES
    assert "favorite_jobs" not in _RUN_CHILD_TABLES


# ── Core behavior ─────────────────────────────────────────────────────────────

def test_add_then_list_and_count(repo):
    row = repo.add("1", "wf-1", "job-a", "Staff Engineer", "Acme")
    assert row["job_id"] == "job-a"
    assert row["title"] == "Staff Engineer" and row["company"] == "Acme"
    assert repo.count_for_user("1") == 1
    favs = repo.list_for_user("1")
    assert [f["job_id"] for f in favs] == ["job-a"]


def test_add_is_idempotent_on_user_and_job(repo):
    repo.add("1", "wf-1", "job-a", "Staff Engineer", "Acme")
    repo.add("1", "wf-2", "job-a", "Staff Engineer (v2)", "Acme")  # same job, refreshed snapshot
    assert repo.count_for_user("1") == 1
    row = repo.list_for_user("1")[0]
    assert row["title"] == "Staff Engineer (v2)" and row["workflow_id"] == "wf-2"


def test_list_is_newest_first(repo):
    repo.add("1", "wf", "job-a", "A", "Acme")
    repo.add("1", "wf", "job-b", "B", "Beta")
    repo.add("1", "wf", "job-c", "C", "Gamma")
    assert [f["job_id"] for f in repo.list_for_user("1")] == ["job-c", "job-b", "job-a"]


def test_favorited_job_ids_batch_lookup(repo):
    repo.add("1", "wf", "job-a", "A", "Acme")
    repo.add("1", "wf", "job-b", "B", "Beta")
    assert repo.favorited_job_ids("1") == {"job-a", "job-b"}
    assert repo.favorited_job_ids("2") == set()


def test_remove_is_idempotent(repo):
    repo.add("1", "wf", "job-a", "A", "Acme")
    repo.remove("1", "job-a")
    repo.remove("1", "job-a")  # no error second time
    assert repo.count_for_user("1") == 0


def test_per_profile_isolation(repo):
    repo.add("1", "wf", "job-a", "A", "Acme")
    repo.add("2", "wf", "job-a", "A", "Acme")  # same job, different profile - allowed
    assert repo.count_for_user("1") == 1
    assert repo.count_for_user("2") == 1
    repo.remove("1", "job-a")
    assert repo.count_for_user("1") == 0
    assert repo.count_for_user("2") == 1  # profile 2 untouched


def test_int_and_str_user_ids_are_equivalent(repo):
    repo.add(1, "wf", "job-a", "A", "Acme")     # int id
    assert repo.count_for_user("1") == 1          # str lookup finds it
    assert repo.favorited_job_ids(1) == {"job-a"}


# ── The cap ───────────────────────────────────────────────────────────────────

def test_cap_blocks_the_twenty_sixth(repo):
    for i in range(MAX_FAVORITES):
        repo.add("1", "wf", f"job-{i}", f"Role {i}", "Acme")
    assert repo.count_for_user("1") == MAX_FAVORITES
    with pytest.raises(FavoritesCapReached):
        repo.add("1", "wf", "job-over", "One too many", "Acme")
    assert repo.count_for_user("1") == MAX_FAVORITES


def test_re_favoriting_at_cap_does_not_raise(repo):
    for i in range(MAX_FAVORITES):
        repo.add("1", "wf", f"job-{i}", f"Role {i}", "Acme")
    # Re-favoriting an EXISTING job at the cap is fine (it is not a new row).
    repo.add("1", "wf", "job-0", "Role 0 refreshed", "Acme")
    assert repo.count_for_user("1") == MAX_FAVORITES


def test_remove_all_for_user(repo):
    repo.add("1", "wf", "job-a", "A", "Acme")
    repo.add("1", "wf", "job-b", "B", "Beta")
    removed = repo.remove_all_for_user("1")
    assert removed == 2
    assert repo.count_for_user("1") == 0
