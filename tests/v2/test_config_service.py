import json
import tempfile
from pathlib import Path

import pytest
import yaml

from app.repositories.config_repository import ConfigRepository
from app.repositories.database import init_db, utcnow_iso
from app.services.config_service import ConfigService


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_config.db"
    init_db(path)
    return path


@pytest.fixture
def config_path(tmp_path):
    cfg = {
        "llm": {
            "default_model": "claude-sonnet-4-6",
            "scoring_model": "claude-haiku-4-5-20251001",
            "provider": "anthropic",
        },
        "search": {"max_jobs": 20, "roles": [], "locations": []},
        "limits": {
            "max_selected_jobs": 3,
            "max_research_steps": 2,
            "max_review_rounds": 3,
            "max_llm_calls_per_job": 10,
            "max_llm_calls_per_run": 50,
        },
        "scoring": {
            "deep_review_threshold": 70,
            "interview_prep_threshold": 75,
        },
        "tailoring": {"style": "conservative"},
        "retention": {
            "workflow_runs_days": 90,
            "observability_days": 30,
            "security_events_days": 180,
            "memory_items_days": 365,
            "jobs_days": 90,
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(cfg))
    return path


@pytest.fixture
def service(config_path, db_path):
    return ConfigService(config_path=config_path, db_path=db_path)


# ─── YAML loading ────────────────────────────────────────────────────────────

def test_yaml_defaults_load(service):
    config = service.get_effective_config()
    assert config["llm"]["default_model"] == "claude-sonnet-4-6"
    assert config["limits"]["max_llm_calls_per_run"] == 50
    assert config["retention"]["workflow_runs_days"] == 90


def test_missing_config_file_raises(tmp_path, db_path):
    svc = ConfigService(config_path=tmp_path / "nonexistent.yaml", db_path=db_path)
    with pytest.raises(FileNotFoundError):
        svc.get_effective_config()


# ─── User overrides ──────────────────────────────────────────────────────────

def test_user_override_merges_over_yaml(service, db_path):
    repo = ConfigRepository(db_path)
    repo.upsert("cfg_001", "user_1", "tailoring.style",
                "standard")
    config = service.get_effective_config(user_id="user_1")
    assert config["tailoring"]["style"] == "standard"


def test_user_override_does_not_affect_other_users(service, db_path):
    repo = ConfigRepository(db_path)
    repo.upsert("cfg_002", "user_1", "tailoring.style", "standard")
    config = service.get_effective_config(user_id="user_2")
    assert config["tailoring"]["style"] == "conservative"


# ─── System limit enforcement ────────────────────────────────────────────────

def test_user_cannot_exceed_max_jobs(service, db_path):
    repo = ConfigRepository(db_path)
    repo.upsert("cfg_003", "user_1", "search.max_jobs", 999)
    config = service.get_effective_config(user_id="user_1")
    assert config["search"]["max_jobs"] == 20  # capped at system max


def test_user_cannot_override_llm_model(service, db_path):
    repo = ConfigRepository(db_path)
    repo.upsert("cfg_004", "user_1", "llm.default_model", "gpt-4")
    config = service.get_effective_config(user_id="user_1")
    assert config["llm"]["default_model"] == "claude-sonnet-4-6"


def test_user_cannot_override_limits(service, db_path):
    repo = ConfigRepository(db_path)
    repo.upsert("cfg_005", "user_1", "limits.max_llm_calls_per_run", 500)
    config = service.get_effective_config(user_id="user_1")
    assert config["limits"]["max_llm_calls_per_run"] == 50


def test_user_cannot_override_retention(service, db_path):
    repo = ConfigRepository(db_path)
    repo.upsert("cfg_006", "user_1", "retention.workflow_runs_days", 1)
    config = service.get_effective_config(user_id="user_1")
    assert config["retention"]["workflow_runs_days"] == 90


def test_user_cannot_override_scoring_thresholds(service, db_path):
    repo = ConfigRepository(db_path)
    repo.upsert("cfg_007", "user_1", "scoring.deep_review_threshold", 10)
    config = service.get_effective_config(user_id="user_1")
    assert config["scoring"]["deep_review_threshold"] == 70


# ─── Effective config completeness ───────────────────────────────────────────

def test_effective_config_has_all_sections(service):
    config = service.get_effective_config()
    for section in ("llm", "search", "limits", "scoring", "tailoring", "retention"):
        assert section in config, f"Missing section: {section}"
