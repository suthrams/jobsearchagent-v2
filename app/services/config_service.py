"""ConfigService — merges YAML defaults with per-user DB overrides at runtime.

Resolution order: user DB overrides → config/config.yaml defaults → hardcoded fallbacks.
Keys in _PROTECTED_KEYS are silently ignored if a user attempts to override them —
users must never be able to change LLM models, execution limits, safety thresholds,
or retention windows regardless of what they store in user_config.
"""
import json
from pathlib import Path

import yaml

from app.repositories.config_repository import ConfigRepository
from app.repositories.database import DEFAULT_DB_PATH

CONFIG_PATH = Path("config/config.yaml")

# Keys that users must never override — enforced regardless of DB contents.
# Per ADR-053, the legacy `llm.*` keys remain protected (they're no longer used,
# but unprotecting them would re-enable an unbounded model selection surface).
# Per-agent assignment lives under `agents.{name}.{provider,model}` and is
# validated against ModelRegistry.KNOWN_MODELS instead of being absolutely banned.
_PROTECTED_KEYS = {
    "llm.default_model",
    "llm.scoring_model",
    "llm.provider",
    "limits.max_selected_jobs",
    "limits.max_research_steps",
    "limits.max_review_rounds",
    "limits.max_llm_calls_per_job",
    "limits.max_llm_calls_per_run",
    "scoring.deep_review_threshold",
    "scoring.interview_prep_threshold",
    "retention.workflow_runs_days",
    "retention.observability_days",
    "retention.security_events_days",
    "retention.memory_items_days",
    "retention.jobs_days",
    "retention.resumes_days",
}

# ADR-061: this is the discovery-SERVICE backstop (how many postings the scraper
# layer may return), raised to the discovery ceiling so it no longer throttles
# the manual-mode wide net. The precise per-run discovery and scoring caps are
# applied in the discover_jobs / score_jobs nodes via get_max_discovered_jobs /
# get_max_scored. Kept numerically in sync with MAX_DISCOVERED_JOBS.
_SYSTEM_MAX_JOBS = 50


class ConfigService:
    def __init__(self, config_path: Path = CONFIG_PATH,
                 db_path: Path = DEFAULT_DB_PATH):
        self._config_path = config_path
        self._config_repo = ConfigRepository(db_path)

    def get_effective_config(self, user_id: str | None = None) -> dict:
        base = self._load_yaml()
        overrides = self._load_user_overrides(user_id)
        merged = self._deep_merge(base, overrides)
        return self._enforce_limits(merged)

    def _load_yaml(self) -> dict:
        if not self._config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {self._config_path}. "
                "Copy config/config.example.yaml to config/config.yaml and fill in your preferences."
            )
        with open(self._config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _load_user_overrides(self, user_id: str | None) -> dict:
        rows = self._config_repo.get_by_user(user_id)
        overrides: dict = {}
        for row in rows:
            key = row["config_key"]
            # Skip protected keys — silently ignore, never raise
            if key in _PROTECTED_KEYS:
                continue
            value = json.loads(row["config_value_json"])
            self._set_nested(overrides, key.split("."), value)
        return overrides

    def _deep_merge(self, base: dict, overrides: dict) -> dict:
        result = dict(base)
        for key, value in overrides.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def _enforce_limits(self, config: dict) -> dict:
        # Import here to avoid any import-time coupling between the config and
        # workflow packages; the ceilings live with the workflow limits.
        from app.workflows.limits import MAX_DISCOVERED_JOBS, MAX_SCORED_CEILING

        search = config.setdefault("search", {})
        search["max_jobs"] = min(
            int(search.get("max_jobs", _SYSTEM_MAX_JOBS)),
            _SYSTEM_MAX_JOBS,
        )
        # ADR-061: clamp the configurable funnel-width knobs to their hard
        # ceilings. Mirrors the limits.py helper clamps; this keeps the
        # system-wide effective config (and what the Settings UI shows) clean.
        if "max_discovered" in search:
            try:
                search["max_discovered"] = max(
                    1, min(int(search["max_discovered"]), MAX_DISCOVERED_JOBS)
                )
            except (TypeError, ValueError):
                del search["max_discovered"]
        scoring = config.setdefault("scoring", {})
        if "max_scored" in scoring:
            try:
                scoring["max_scored"] = max(
                    1, min(int(scoring["max_scored"]), MAX_SCORED_CEILING)
                )
            except (TypeError, ValueError):
                del scoring["max_scored"]
        return config

    @staticmethod
    def _set_nested(d: dict, keys: list[str], value: object) -> None:
        for key in keys[:-1]:
            d = d.setdefault(key, {})
        d[keys[-1]] = value
