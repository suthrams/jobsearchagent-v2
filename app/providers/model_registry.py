"""ModelRegistry — single source of truth for which (provider, model) pairs the system supports.

Per ADR-053. Built once at backend startup. Holds one LLMClient instance per
unique (provider, model) referenced by the merged effective config plus the
default assignment, so the same provider object is reused across agents that
share the same model.

Public API:
    registry = ModelRegistry.build(prompt_loader, agent_assignment)
    provider = registry.for_agent("research_agent")
    catalog  = ModelRegistry.catalog(openai_available=True)   # for UI dropdowns

The catalog drives the Settings UI; the agent_assignment drives the workflow
graph wiring in app/api/dependencies.py.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable

from app.providers.claude_provider import ClaudeProvider, _PRICING as _CLAUDE_PRICING
from app.providers.llm_client import LLMClient
from app.providers.openai_provider import OpenAIProvider, _PRICING as _OPENAI_PRICING
from app.providers.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)


# ── Registered providers + models ─────────────────────────────────────────────
# Adding a model = (1) extend the matching _PRICING dict in the provider module,
# (2) add the id to the list below.

_KNOWN_MODELS: dict[str, list[str]] = {
    "claude": [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6",
        "claude-opus-4-7",
    ],
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "o1",
    ],
}

# ── Cost guardrails ───────────────────────────────────────────────────────────
# These agents run on every job (10-20 calls per workflow) and dominate per-run
# token volume. Cost is a design decision: high-volume agents are restricted to
# the cheapest tier so a Settings UI slip cannot make a typical run 5-10x more
# expensive overnight. Expensive models are reserved for the low-volume agents
# that produce user-facing analysis (advisor, coach, tailoring).
#
# Update HIGH_VOLUME_SAFE_MODELS only when a new model is registered whose
# combined input+output rate is comparable to Haiku 4.5 ($6 / 1M combined) or
# gpt-4o-mini ($0.75 / 1M combined). Sonnet 4.6 ($18 / 1M) and above are not
# high-volume safe.
HIGH_VOLUME_AGENTS: frozenset[str] = frozenset({"research_agent", "scoring_agent"})
HIGH_VOLUME_SAFE_MODELS: frozenset[str] = frozenset({
    "claude-haiku-4-5-20251001",
    "gpt-4o-mini",
})


def is_cost_capped_agent(agent_name: str) -> bool:
    """True if `agent_name` is in the high-volume tier (cheap-only allowlist)."""
    return agent_name in HIGH_VOLUME_AGENTS


def is_high_volume_safe_model(model_id: str) -> bool:
    """True if `model_id` is in the cost-capped allowlist for high-volume agents."""
    return model_id in HIGH_VOLUME_SAFE_MODELS


class CostCapViolationError(ValueError):
    """Raised when a cost-capped agent is assigned a model outside HIGH_VOLUME_SAFE_MODELS."""

# Default per-agent assignment per ADR-051 (which ADR-053 supersedes for
# *immutability*; the values themselves remain the recommended defaults).
DEFAULT_AGENT_ASSIGNMENT: dict[str, dict[str, str]] = {
    "research_agent":      {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
    "scoring_agent":       {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
    # Cost cut: resume_critic moved off Sonnet — per-agent breakdown showed it
    # was ~80% of run cost (16 calls × ~$0.025 in observed runs). The auditor
    # loop polices critic output, so the quality risk of dropping to Haiku is
    # bounded. Override per-run via Settings if a specific tailoring deserves Sonnet.
    "resume_critic":       {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
    "review_auditor":      {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
    "career_advisor":      {"provider": "claude", "model": "claude-sonnet-4-6"},
    "interview_coach":     {"provider": "claude", "model": "claude-sonnet-4-6"},
    "tailoring_agent":     {"provider": "claude", "model": "claude-sonnet-4-6"},
    "fidelity_reviewer":   {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
    # Non-orchestrator agents (resume parser, custom URL extractor) default to sonnet
    # because they handle one-shot, quality-sensitive extractions.
    "resume_parser":       {"provider": "claude", "model": "claude-sonnet-4-6"},
    "custom_url_extractor": {"provider": "claude", "model": "claude-sonnet-4-6"},
}


# ── UnknownModelError ─────────────────────────────────────────────────────────

class UnknownModelError(ValueError):
    """Raised when a (provider, model) pair is not in the registry's known set."""


# ── Catalog entry for the UI ─────────────────────────────────────────────────

@dataclass(frozen=True)
class ModelCatalogEntry:
    provider: str
    model: str
    input_per_m: float
    output_per_m: float


# ── Registry ──────────────────────────────────────────────────────────────────

class ModelRegistry:
    """Maps an agent name → an LLMClient instance, given a per-agent assignment.

    Eagerly builds one provider per (provider, model) pair to amortise client
    construction. Intended to be built once at backend startup.
    """

    def __init__(
        self,
        agent_assignment: dict[str, dict[str, str]],
        providers_by_key: dict[tuple[str, str], LLMClient],
    ) -> None:
        self._assignment = agent_assignment
        self._providers = providers_by_key

    # ── Construction ──────────────────────────────────────────────────────────

    @classmethod
    def build(
        cls,
        prompt_loader: PromptLoader,
        agent_assignment: dict[str, dict[str, str]] | None = None,
        *,
        openai_available: bool | None = None,
    ) -> "ModelRegistry":
        """Build the registry from an agent assignment (defaults filled in for missing agents).

        openai_available defaults to bool(OPENAI_API_KEY); pass False in tests.
        """
        if openai_available is None:
            openai_available = bool(os.getenv("OPENAI_API_KEY"))

        # Merge user-supplied assignment over defaults so missing agents fall back.
        merged: dict[str, dict[str, str]] = {
            agent: dict(assignment) for agent, assignment in DEFAULT_AGENT_ASSIGNMENT.items()
        }
        for agent, assignment in (agent_assignment or {}).items():
            if agent not in merged:
                logger.info("ModelRegistry: ignoring unknown agent override %r", agent)
                continue
            provider = (assignment.get("provider") or "").strip()
            model = (assignment.get("model") or "").strip()
            if provider and model:
                cls._validate_pair(provider, model)
                # If user picked openai but key is missing, downgrade to default with a warning.
                if provider == "openai" and not openai_available:
                    logger.warning(
                        "ModelRegistry: agent %r requested openai but OPENAI_API_KEY is "
                        "not set — falling back to default %s/%s",
                        agent,
                        merged[agent]["provider"], merged[agent]["model"],
                    )
                    continue
                # Cost guardrail: high-volume agents are pinned to a cheap allowlist.
                # The Settings UI also filters its dropdowns, but this enforcement
                # is the durable line of defense — it catches direct DB / API writes
                # that bypass the UI.
                if agent in HIGH_VOLUME_AGENTS and model not in HIGH_VOLUME_SAFE_MODELS:
                    default = DEFAULT_AGENT_ASSIGNMENT[agent]
                    logger.warning(
                        "ModelRegistry: agent %r is high-volume and cannot use %s/%s "
                        "(allowed: %s). Falling back to default %s/%s.",
                        agent, provider, model, sorted(HIGH_VOLUME_SAFE_MODELS),
                        default["provider"], default["model"],
                    )
                    continue
                merged[agent] = {"provider": provider, "model": model}

        # Build one provider per unique (provider, model) referenced.
        providers_by_key: dict[tuple[str, str], LLMClient] = {}
        for agent, assignment in merged.items():
            key = (assignment["provider"], assignment["model"])
            if key in providers_by_key:
                continue
            providers_by_key[key] = cls._build_provider(prompt_loader, *key)

        return cls(merged, providers_by_key)

    @staticmethod
    def _build_provider(prompt_loader: PromptLoader, provider: str, model: str) -> LLMClient:
        if provider == "claude":
            return ClaudeProvider(prompt_loader, model_name=model)
        if provider == "openai":
            return OpenAIProvider(prompt_loader, model_name=model)
        raise UnknownModelError(f"Unknown provider: {provider!r}")

    @staticmethod
    def _validate_pair(provider: str, model: str) -> None:
        if provider not in _KNOWN_MODELS:
            raise UnknownModelError(f"Unknown provider: {provider!r}")
        if model not in _KNOWN_MODELS[provider]:
            raise UnknownModelError(
                f"Unknown model {model!r} for provider {provider!r}. "
                f"Known: {_KNOWN_MODELS[provider]}"
            )

    # ── Public lookup ─────────────────────────────────────────────────────────

    def for_agent(self, agent_name: str) -> LLMClient:
        if agent_name not in self._assignment:
            # Fall back to defaults for unknown agents — keeps backwards-compat.
            default = DEFAULT_AGENT_ASSIGNMENT.get(agent_name)
            if default is None:
                raise UnknownModelError(f"No assignment for agent {agent_name!r}")
            self._assignment[agent_name] = dict(default)
        a = self._assignment[agent_name]
        key = (a["provider"], a["model"])
        if key not in self._providers:
            # Lazily build if the assignment was injected post-build
            # (kept simple — uses a fresh PromptLoader if needed at the call site).
            raise UnknownModelError(f"No provider built for {key!r}")
        return self._providers[key]

    def assignment(self) -> dict[str, dict[str, str]]:
        """Return the resolved per-agent assignment as a defensive copy."""
        return {k: dict(v) for k, v in self._assignment.items()}

    # ── Catalog (for the Settings UI) ─────────────────────────────────────────

    @staticmethod
    def catalog(*, openai_available: bool | None = None) -> dict[str, dict]:
        """Return a UI-friendly description of providers and their models with pricing.

        Shape:
          {
            "claude": {"available": True, "models": [{"id": ..., "input_per_m": ..., ...}, ...]},
            "openai": {"available": False, "models": [...]},
          }
        """
        if openai_available is None:
            openai_available = bool(os.getenv("OPENAI_API_KEY"))

        def _entries(provider: str, pricing: dict[str, dict[str, float]]) -> list[dict]:
            entries: list[dict] = []
            for model in _KNOWN_MODELS[provider]:
                p = pricing.get(model, {"input": 0.0, "output": 0.0})
                entries.append({
                    "id": model,
                    "input_per_m": p["input"],
                    "output_per_m": p["output"],
                })
            return entries

        return {
            "claude": {"available": True, "models": _entries("claude", _CLAUDE_PRICING)},
            "openai": {"available": openai_available, "models": _entries("openai", _OPENAI_PRICING)},
            # Cost-cap metadata. Lets the Settings UI restrict the model picker
            # for these agents without hard-coding the constants client-side.
            "_meta": {
                "high_volume_agents": sorted(HIGH_VOLUME_AGENTS),
                "high_volume_safe_models": sorted(HIGH_VOLUME_SAFE_MODELS),
            },
        }


# ── Convenience: extract assignment from effective config ────────────────────

def assignment_from_config(effective_config: dict) -> dict[str, dict[str, str]]:
    """Read the agents.* block from a merged config dict. Empty dict if absent."""
    agents = (effective_config or {}).get("agents") or {}
    out: dict[str, dict[str, str]] = {}
    for agent_name, raw in agents.items():
        if not isinstance(raw, dict):
            continue
        provider = raw.get("provider")
        model = raw.get("model")
        if provider and model:
            out[agent_name] = {"provider": str(provider), "model": str(model)}
    return out


# Public re-exports for clean imports elsewhere.
KNOWN_MODELS: dict[str, list[str]] = _KNOWN_MODELS
