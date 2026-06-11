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
    KIND_FAVORITE,
    KIND_REVIEW_LATER,
    MAX_FAVORITES,
    MAX_REVIEW_LATER,
    FavoriteRepository,
    FavoritesCapReached,
)

# Status/outcome column names that must NEVER appear (no application tracking).
_FORBIDDEN_STATUS_COLS = {
    "status", "applied", "applied_at", "pursuing", "stage", "outcome",
    "application_status", "decision", "rejected", "interviewing",
}


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
    """The boundary, enforced in schema (ADR-100): a saved job carries ONLY a job
    reference, a kind discriminator, a display snapshot, and a timestamp - never
    status/applied/pursuing/stage/outcome, for ANY kind. Adding such a column fails
    this test."""
    with get_connection(db_path) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(favorite_jobs)").fetchall()}
    assert cols == {
        "id", "user_id", "workflow_id", "job_id", "kind",
        "title", "company", "url", "source", "created_at",
    }
    # The no-application-tracking boundary holds across the generalization.
    assert cols.isdisjoint(_FORBIDDEN_STATUS_COLS)


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


# ── ADR-100: review_later kind shares the store but is an isolated list ──────────

def test_review_later_is_a_separate_list_from_favorites(repo):
    repo.add("1", "wf", "job-fav", "Fav", "Acme", kind=KIND_FAVORITE)
    repo.add("1", "wf", "job-later", "Later", "Beta", kind=KIND_REVIEW_LATER,
             url="https://x/later", source="adzuna")
    # Each kind lists only its own jobs.
    assert [f["job_id"] for f in repo.list_for_user("1", kind=KIND_FAVORITE)] == ["job-fav"]
    later = repo.list_for_user("1", kind=KIND_REVIEW_LATER)
    assert [f["job_id"] for f in later] == ["job-later"]
    # The snapshot carries the link + source so the list renders without the run.
    assert later[0]["url"] == "https://x/later" and later[0]["source"] == "adzuna"
    # Counts and id-sets are per-kind; favorites stars never see review-later jobs.
    assert repo.count_for_user("1", kind=KIND_FAVORITE) == 1
    assert repo.count_for_user("1", kind=KIND_REVIEW_LATER) == 1
    assert repo.favorited_job_ids("1") == {"job-fav"}
    assert repo.saved_job_ids("1", kind=KIND_REVIEW_LATER) == {"job-later"}


def test_review_later_has_its_own_cap(repo):
    for i in range(MAX_REVIEW_LATER):
        repo.add("1", "wf", f"later-{i}", f"R{i}", "Acme", kind=KIND_REVIEW_LATER)
    assert repo.count_for_user("1", kind=KIND_REVIEW_LATER) == MAX_REVIEW_LATER
    with pytest.raises(FavoritesCapReached):
        repo.add("1", "wf", "later-over", "too many", "Acme", kind=KIND_REVIEW_LATER)
    # A favorite is unaffected by the review-later cap (separate budget).
    repo.add("1", "wf", "a-fav", "Fav", "Acme", kind=KIND_FAVORITE)
    assert repo.count_for_user("1", kind=KIND_FAVORITE) == 1


def test_remove_scoped_by_kind_does_not_touch_other_bucket(repo):
    # Distinct jobs in each bucket; a kind-scoped remove only hits its own.
    repo.add("1", "wf", "j-fav", "Fav", "Acme", kind=KIND_FAVORITE)
    repo.add("1", "wf", "j-later", "Later", "Beta", kind=KIND_REVIEW_LATER)
    repo.remove("1", "j-later", kind=KIND_REVIEW_LATER)
    assert repo.count_for_user("1", kind=KIND_REVIEW_LATER) == 0
    assert repo.count_for_user("1", kind=KIND_FAVORITE) == 1


def test_remove_all_for_user_clears_every_kind(repo):
    repo.add("1", "wf", "j-fav", "Fav", "Acme", kind=KIND_FAVORITE)
    repo.add("1", "wf", "j-later", "Later", "Beta", kind=KIND_REVIEW_LATER)
    assert repo.remove_all_for_user("1") == 2
    assert repo.count_for_user("1", kind=KIND_FAVORITE) == 0
    assert repo.count_for_user("1", kind=KIND_REVIEW_LATER) == 0
