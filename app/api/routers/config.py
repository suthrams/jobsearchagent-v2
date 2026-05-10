"""Config router — GET /config (effective merged config) and PUT /config (per-key overrides).

Protected keys (LLM models, hard execution limits, retention windows) are read-only:
ConfigService silently ignores any user_config row whose key matches _PROTECTED_KEYS,
and PUT /config rejects writes to those keys with 422.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.providers.model_registry import (
    DEFAULT_AGENT_ASSIGNMENT,
    HIGH_VOLUME_AGENTS,
    HIGH_VOLUME_SAFE_MODELS,
    KNOWN_MODELS,
    ModelRegistry,
    assignment_from_config,
)
from app.repositories.config_repository import ConfigRepository
from app.repositories.database import DEFAULT_DB_PATH
from app.services.config_service import _PROTECTED_KEYS, ConfigService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])


class ConfigUpdate(BaseModel):
    """One PUT body — sets a single dotted key to a JSON value (any type)."""
    key: str
    value: object


@router.get("")
def get_config() -> dict:
    """Return the effective merged config + the list of protected keys for UI labelling."""
    svc = ConfigService()
    return {
        "effective_config": svc.get_effective_config(),
        "protected_keys": sorted(_PROTECTED_KEYS),
    }


@router.put("", status_code=200)
def put_config(body: ConfigUpdate) -> dict:
    """Set or update a user_config override for a single dotted key."""
    if body.key in _PROTECTED_KEYS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "protected_key",
                "message": (
                    f"Key {body.key!r} is protected and cannot be overridden via the API. "
                    "Edit config/config.yaml on the server if you really need to change it."
                ),
            },
        )

    if not body.key or not isinstance(body.key, str):
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_key", "message": "key must be a non-empty dotted path."},
        )

    # ADR-053: per-agent provider/model writes are validated against the registry.
    if body.key.startswith("agents."):
        _validate_agent_key(body.key, body.value)

    repo = ConfigRepository(DEFAULT_DB_PATH)
    # user_id=None == single-user setup (current default).
    # Use a stable id derived from key so repeated PUTs upsert in place.
    config_id = f"user_None__{body.key}"
    try:
        repo.upsert(config_id, None, body.key, body.value)
    except Exception as exc:
        logger.exception("config: upsert failed for key=%s", body.key)
        raise HTTPException(
            status_code=500,
            detail={"error": "persist_failed", "message": str(exc)},
        ) from exc

    return {"key": body.key, "value": body.value, "status": "saved"}


@router.post("/reload", status_code=200)
def reload_config() -> dict:
    """Rebuild the workflow graph + agent bindings from the current user_config.

    ADR-053 addendum: previously a Settings save required a backend restart
    because ModelRegistry caches one provider per (provider, model) at startup
    and agents are wired with provider references baked in. This endpoint
    automates the restart-style swap: rebuild deps + graph atomically, swap
    the cached singletons, return the new effective agent assignment.

    What this DOES pick up:
      - agents.{name}.{provider,model} overrides (per-agent model assignment)
      - any other user_config row read by ConfigService at deps build time

    What this does NOT pick up (still requires process restart):
      - prompt file changes (PromptLoader caches files at first read)
      - code changes
      - new env vars (e.g. setting OPENAI_API_KEY for the first time —
        the registry only registers OpenAI providers when the key is
        present at registry build time; if we rebuild AFTER setting it,
        OpenAI becomes available, so this case actually IS covered)

    In-flight workflows: continue using the OLD graph reference they hold;
    only workflows started AFTER this call use the new assignment.
    """
    # Local import to avoid the circular dependencies (config router -> deps
    # -> workflow graph -> nodes -> ... -> potentially config services).
    from app.api.dependencies import reload_deps_and_graph
    try:
        result = reload_deps_and_graph()
    except Exception as exc:
        logger.exception("config: reload_deps_and_graph failed")
        raise HTTPException(
            status_code=500,
            detail={"error": "reload_failed", "message": str(exc)},
        ) from exc
    return {"status": "reloaded", **result}


def _validate_agent_key(key: str, value: object) -> None:
    """Reject writes to agents.{name}.{provider,model} that don't resolve in the registry.

    Accepts forms:
      - agents.{name}                       → value must be {provider, model}
      - agents.{name}.provider              → value in {claude, openai}
      - agents.{name}.model                 → value in KNOWN_MODELS[provider]
    """
    parts = key.split(".")
    if len(parts) < 2 or parts[0] != "agents":
        return
    agent_name = parts[1]
    if agent_name not in DEFAULT_AGENT_ASSIGNMENT:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "unknown_agent",
                "message": f"Unknown agent {agent_name!r}. Known: {sorted(DEFAULT_AGENT_ASSIGNMENT)}",
            },
        )
    if len(parts) == 2:
        if not isinstance(value, dict):
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_value",
                        "message": f"agents.{agent_name} must be an object with provider+model."},
            )
        provider = value.get("provider")
        model = value.get("model")
        if not provider or not model:
            raise HTTPException(
                status_code=422,
                detail={"error": "incomplete_assignment",
                        "message": "Both provider and model are required."},
            )
        _check_known(provider, model)
        _check_cost_cap(agent_name, model)
    elif parts[2] == "provider":
        if value not in KNOWN_MODELS:
            raise HTTPException(
                status_code=422,
                detail={"error": "unknown_provider",
                        "message": f"Unknown provider {value!r}. Known: {sorted(KNOWN_MODELS)}"},
            )
    elif parts[2] == "model":
        all_models = [m for ms in KNOWN_MODELS.values() for m in ms]
        if value not in all_models:
            raise HTTPException(
                status_code=422,
                detail={"error": "unknown_model",
                        "message": f"Unknown model {value!r}. Known: {sorted(all_models)}"},
            )
        _check_cost_cap(agent_name, value)
    else:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_agent_key",
                    "message": f"agents.{agent_name}.{parts[2]} is not a recognized field."},
        )


def _check_cost_cap(agent_name: str, model: object) -> None:
    """Reject writes that put a high-volume agent on a non-allowlist model.

    The same guardrail also fires at registry build time (which would silently
    snap the assignment back to default), but raising here gives the caller a
    clear 422 with the allowlist instead of the surprise of "I saved Sonnet for
    scoring but it's still running on Haiku."
    """
    if agent_name not in HIGH_VOLUME_AGENTS:
        return
    if model not in HIGH_VOLUME_SAFE_MODELS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "cost_cap_violation",
                "message": (
                    f"Agent {agent_name!r} is high-volume (runs on every job) and "
                    f"cannot be assigned {model!r}. Allowed models: "
                    f"{sorted(HIGH_VOLUME_SAFE_MODELS)}. Cost is a design decision "
                    "for this agent — expensive models are reserved for low-volume, "
                    "high-fidelity agents."
                ),
            },
        )


def _check_known(provider: object, model: object) -> None:
    if provider not in KNOWN_MODELS:
        raise HTTPException(
            status_code=422,
            detail={"error": "unknown_provider",
                    "message": f"Unknown provider {provider!r}. Known: {sorted(KNOWN_MODELS)}"},
        )
    if model not in KNOWN_MODELS[provider]:
        raise HTTPException(
            status_code=422,
            detail={"error": "unknown_model",
                    "message": f"Unknown model {model!r} for provider {provider!r}. "
                               f"Known: {KNOWN_MODELS[provider]}"},
        )


@router.get("/providers", tags=["config"])
def get_providers() -> dict:
    """Return the registered providers + models with pricing, plus the current per-agent assignment.

    Used by the Settings UI to populate per-agent dropdowns. Models are gated by
    API-key presence: openai entries report `available=false` if OPENAI_API_KEY
    is missing on the server.
    """
    eff = ConfigService().get_effective_config()
    user_assignment = assignment_from_config(eff)
    # Merge with defaults so every agent has an entry
    merged: dict[str, dict[str, str]] = {a: dict(v) for a, v in DEFAULT_AGENT_ASSIGNMENT.items()}
    for agent, assignment in user_assignment.items():
        if agent in merged:
            merged[agent].update(assignment)

    return {
        "providers": ModelRegistry.catalog(),
        "agent_assignment": merged,
    }
